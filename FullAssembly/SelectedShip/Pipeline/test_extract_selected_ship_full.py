from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("extract_selected_ship_full.py")
SPEC = importlib.util.spec_from_file_location(
    "extract_selected_ship_full", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SelectedShipPipelineTests(unittest.TestCase):
    def test_safe_slug_preserves_gameparams_key(self):
        self.assertEqual(
            "PXSD310_Arleigh_Burke_1991",
            module.safe_slug("PXSD310_Arleigh_Burke_1991"),
        )
        self.assertEqual("PXSD310_test", module.safe_slug("PXSD310 test"))
        with self.assertRaises(module.PipelineError):
            module.safe_slug("...")

    def test_output_must_be_disjoint_from_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            output = root / "output"
            game.mkdir()
            output.mkdir()
            module.validate_output_location(game, output)
            with self.assertRaises(module.PipelineError):
                module.validate_output_location(game, game / "exports")

    def test_run_root_cannot_redirect_outside_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            outside = root / "outside"
            output.mkdir()
            outside.mkdir()
            safe = output / "ship_Full"
            self.assertEqual(safe.resolve(), module.validate_run_root(output, safe))
            linked = output / "linked_Full"
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
            with self.assertRaises(module.PipelineError):
                module.validate_run_root(output, linked)

    def test_resource_definition_requires_exact_system_set(self):
        resources = module._system_definitions() + [
            {
                "kind": "geometry",
                "path": "content/gameplay/ship.geometry",
            }
        ]
        normalized = module.validate_resource_definitions(resources)
        self.assertEqual(5, len(normalized))
        with self.assertRaises(module.PipelineError):
            module.validate_resource_definitions(resources[:-1])

    def test_mapping_acceptance_rejects_missing_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mapping.json"
            module.write_json_atomic(
                path,
                {
                    "schema": "wows-legends-static-ship-assembly/v1",
                    "ship": {"ship_key": "PXSD310_Test"},
                    "validation": {
                        "static_assembly_acceptance": True,
                        "missing_combat_hardpoints": ["HP_AGS_1"],
                        "duplicate_combat_hardpoint_sources": {},
                        "model_uber_parse_failures": [],
                        "unresolved_render_set_fields": [],
                        "unresolved_texture_paths": [],
                        "all_output_matrices_finite": True,
                    },
                },
            )
            with self.assertRaises(module.PipelineError):
                module.mapping_acceptance(path, "PXSD310_Test")

    def test_mapping_acceptance_allows_verified_catalog_key_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mapping.json"
            module.write_json_atomic(
                path,
                {
                    "schema": "wows-legends-static-ship-assembly/v1",
                    "ship": {
                        "ship_key": "PASB207_Connecticut",
                        "requested_ship_key": "PASB207_Connecticut_1944",
                        "key_resolution": "ship_index_fallback",
                    },
                    "validation": {
                        "static_assembly_acceptance": True,
                        "missing_combat_hardpoints": [],
                        "duplicate_combat_hardpoint_sources": {},
                        "model_uber_parse_failures": {},
                        "unresolved_render_set_fields": [],
                        "unresolved_texture_paths": [],
                        "all_output_matrices_finite": True,
                    },
                },
            )
            result = module.mapping_acceptance(
                path, "PASB207_Connecticut_1944"
            )
            self.assertEqual("PASB207_Connecticut", result["resolved_ship_key"])
            self.assertEqual("ship_index_fallback", result["key_resolution"])

    def test_mapping_acceptance_rejects_unrelated_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mapping.json"
            module.write_json_atomic(
                path,
                {
                    "schema": "wows-legends-static-ship-assembly/v1",
                    "ship": {
                        "ship_key": "PASB999_Unrelated",
                        "requested_ship_key": "PASB207_Connecticut_1944",
                        "key_resolution": "ship_index_fallback",
                    },
                    "validation": {
                        "static_assembly_acceptance": True,
                        "missing_combat_hardpoints": [],
                        "duplicate_combat_hardpoint_sources": {},
                        "model_uber_parse_failures": {},
                        "unresolved_render_set_fields": [],
                        "unresolved_texture_paths": [],
                        "all_output_matrices_finite": True,
                    },
                },
            )
            with self.assertRaises(module.PipelineError):
                module.mapping_acceptance(path, "PASB207_Connecticut_1944")
    def test_run_checked_forwards_output_and_writes_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary)
            captured = io.StringIO()
            command = [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "print('[PROGRESS] ' + json.dumps("
                    "{'stage':'extract','percent':42,'message':'한글 \ufffd streamed'}, ensure_ascii=False), "
                    "flush=True)"
                ),
            ]
            with contextlib.redirect_stdout(captured):
                result = module.run_checked(
                    "streaming child", command, log_dir=log_dir, timeout=5
                )
            self.assertEqual(0, result["returncode"])
            self.assertIn("[PROGRESS]", captured.getvalue())
            log_text = Path(result["log"]).read_text(encoding="utf-8")
            self.assertIn("한글", log_text)
            self.assertIn("\ufffd", log_text)
            self.assertIn("streamed", log_text)

    def test_run_checked_reports_child_liveness(self):
        with tempfile.TemporaryDirectory() as temporary:
            captured = io.StringIO()
            command = [
                sys.executable,
                "-c",
                "import time; time.sleep(0.09); print('done', flush=True)",
            ]
            with contextlib.redirect_stdout(captured):
                module.run_checked(
                    "slow child",
                    command,
                    log_dir=Path(temporary),
                    timeout=5,
                    heartbeat_interval=0.02,
                )
            stream = captured.getvalue()
            self.assertIn('"event": "child_start"', stream)
            self.assertIn('"event": "child_heartbeat"', stream)
            self.assertIn('"step": "slow child"', stream)
            self.assertIn('"event": "child_complete"', stream)


    def test_work_cleanup_is_scoped_to_run_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "ship_Full"
            owned = run_root / "extracted"
            owned.mkdir(parents=True)
            payload = owned / "payload.bin"
            payload.write_bytes(b"1234")
            stats = module._remove_pipeline_directory(owned, run_root)
            self.assertFalse(owned.exists())
            self.assertEqual(1, stats["files"])
            self.assertEqual(4, stats["bytes"])

            outside = root / "outside"
            outside.mkdir()
            with self.assertRaises(module.PipelineError):
                module._remove_pipeline_directory(outside, run_root)


if __name__ == "__main__":
    unittest.main()
