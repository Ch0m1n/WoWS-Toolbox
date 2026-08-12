from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
SPEC = importlib.util.spec_from_file_location("extract_ship_parallel_tested", BACKEND / "extract_ship.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OutputSafetyTests(unittest.TestCase):
    def test_output_and_game_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            output = root / "output"
            game.mkdir()
            output.mkdir()
            self.assertEqual(
                (game.resolve(), output.resolve()),
                MODULE.validate_output_location(game, output),
            )
            for unsafe in (game, game / "exports", root):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ValueError):
                        MODULE.validate_output_location(game, unsafe)

    def test_output_child_cannot_escape_or_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            outside = root / "outside"
            output.mkdir()
            outside.mkdir()
            safe = output / "ship"
            self.assertEqual(safe.resolve(), MODULE.validate_output_child(output, safe))
            with self.assertRaises(ValueError):
                MODULE.validate_output_child(output, outside / "ship")
            linked = output / "linked"
            try:
                os.symlink(outside, linked, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                if os.name != "nt":
                    self.skipTest(f"directory symlink unavailable: {exc}")
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                    capture_output=True,
                    text=True,
                )
                if created.returncode:
                    self.skipTest(f"directory junction unavailable: {created.stderr}")
            with self.assertRaises(ValueError):
                MODULE.validate_output_child(output, linked)


class ParallelExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_export = MODULE._export_glb_to_cache

    def tearDown(self) -> None:
        MODULE._export_glb_to_cache = self.original_export

    @staticmethod
    def jobs(root: Path):
        return {
            "model": ([], root / "model.glb", root / "model.cache.glb", "model invalid"),
            "armor": ([], root / "armor.glb", root / "armor.cache.glb", "armor invalid"),
        }

    def test_two_cold_exports_overlap(self) -> None:
        def fake_export(*args, **kwargs):
            time.sleep(0.20)
            return 0.20

        MODULE._export_glb_to_cache = fake_export
        durations, errors, wall = MODULE._run_export_jobs(
            self.jobs(BACKEND), {}
        )
        self.assertEqual(errors, {})
        self.assertEqual(set(durations), {"model", "armor"})
        self.assertLess(wall, 0.35)

    def test_armor_failure_does_not_hide_model_success(self) -> None:
        def fake_export(*args, **kwargs):
            if kwargs["label"] == "ARMOR":
                raise RuntimeError("armor failed")
            return 0.01

        MODULE._export_glb_to_cache = fake_export
        durations, errors, _ = MODULE._run_export_jobs(
            self.jobs(BACKEND), {}
        )
        self.assertIn("model", durations)
        self.assertNotIn("model", errors)
        self.assertIsInstance(errors.get("armor"), RuntimeError)


class LodContractTests(unittest.TestCase):
    def test_missing_requested_lod_never_silently_publishes_lod0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "ship.glb"
            cache = root / "cache" / "ship.glb"
            with mock.patch.object(MODULE, "run_stream") as run:
                with self.assertRaisesRegex(RuntimeError, "LOD0"):
                    MODULE._export_glb_to_cache(
                        ["engine", "--lod", "2"],
                        env={},
                        output_glb=output,
                        cache_glb=cache,
                        label="MODEL",
                        invalid_message="invalid",
                    )
            self.assertEqual(run.call_count, 1)
            self.assertFalse(cache.exists())


class ExporterFailureMessageTests(unittest.TestCase):
    def test_unknown_rpc_type_has_update_guidance(self) -> None:
        failure = MODULE.StreamedProcessError(
            101,
            ["engine"],
            ["thread panicked", "Unrecognized type FLOAT64"],
        )
        message = MODULE.exporter_failure_message(failure, "함선 모델")
        self.assertIn("FLOAT64", message)
        self.assertIn("업데이트", message)

    def test_missing_geometry_names_current_build_resource(self) -> None:
        failure = MODULE.StreamedProcessError(
            1,
            ["engine"],
            ["Could not open geometry: content/gameplay/ship/REMOVED.geometry"],
        )
        message = MODULE.exporter_failure_message(failure, "함선 모델")
        self.assertIn("현재 게임 빌드", message)
        self.assertIn("REMOVED.geometry", message)


class HullSelectionCommandTests(unittest.TestCase):
    def test_hull_upgrade_is_passed_to_model_and_armor_commands(self) -> None:
        source = (BACKEND / "extract_ship.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count('*(["--hull", args.hull_upgrade] if args.hull_upgrade else [])'),
            2,
        )


class ArmorSidecarPublicationTests(unittest.TestCase):
    def test_failed_exact_conversion_preserves_blender_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "input.armor.json"
            final = root / "ship.armor.json"
            metadata.write_text("{}", encoding="utf-8")
            final.write_text('{"schema":"legacy-fallback"}', encoding="utf-8")

            with mock.patch.object(
                MODULE,
                "run_stream",
                side_effect=subprocess.CalledProcessError(2, ["converter"]),
            ):
                payload, error = MODULE.publish_exact_armor_sidecar(
                    metadata, final, root / "converter.py", {}
                )

            self.assertIsNone(payload)
            self.assertIn("CalledProcessError", error or "")
            self.assertEqual(
                final.read_text(encoding="utf-8"),
                '{"schema":"legacy-fallback"}',
            )
            self.assertEqual(list(root.glob("*.part")), [])

    def test_valid_exact_conversion_replaces_fallback_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "input.armor.json"
            final = root / "ship.armor.json"
            metadata.write_text("{}", encoding="utf-8")
            final.write_text('{"schema":"legacy-fallback"}', encoding="utf-8")

            def fake_run(command, **_kwargs):
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema": "wows-toolbox-armor-viewer/v3",
                            "exact_thickness": True,
                            "groups": [],
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(MODULE, "run_stream", side_effect=fake_run):
                payload, error = MODULE.publish_exact_armor_sidecar(
                    metadata, final, root / "converter.py", {}
                )

            self.assertIsNone(error)
            self.assertEqual(payload["schema"], "wows-toolbox-armor-viewer/v3")
            self.assertEqual(
                json.loads(final.read_text(encoding="utf-8"))["schema"],
                "wows-toolbox-armor-viewer/v3",
            )


if __name__ == "__main__":
    unittest.main()
