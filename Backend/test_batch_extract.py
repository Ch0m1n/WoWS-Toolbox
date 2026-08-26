from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import time
import threading
import unittest


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BATCH = load_module("batch_extract_tested", "batch_extract.py")
EXTRACT = load_module("extract_ship_validation_tested", "extract_ship.py")


class BatchOutputTests(unittest.TestCase):
    def legends_args(self, root: Path):
        return SimpleNamespace(
            source="legends",
            output_root=root,
            run_slug="PXTEST_Ship",
            ship_key="PXTEST_Ship",
            display_name="Test Ship",
            ship_index="",
            overwrite=False,
        )

    def test_legends_duplicate_queue_gets_unique_run_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reserved: set[str] = set()
            first = self.legends_args(root)
            second = self.legends_args(root)
            self.assertEqual(BATCH.output_for(first, reserved).name, "PXTEST_Ship_Full")
            self.assertEqual(BATCH.output_for(second, reserved).name, "PXTEST_Ship_02_Full")
            self.assertEqual(second.run_slug, "PXTEST_Ship_02")

    def test_legends_existing_output_is_not_reused_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PXTEST_Ship_Full").mkdir()
            args = self.legends_args(root)
            output = BATCH.output_for(args, set())
            self.assertEqual(output.name, "PXTEST_Ship_02_Full")


    def test_direct_legends_output_matches_run_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, run_slug = EXTRACT.next_legends_output_dir(
                root, "v504_Connecticut_Validation", False
            )
            self.assertEqual(run_slug, "v504_Connecticut_Validation")
            self.assertEqual(output.name, "v504_Connecticut_Validation_Full")

    def test_direct_legends_existing_output_updates_run_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PXTEST_Ship_Full").mkdir()
            output, run_slug = EXTRACT.next_legends_output_dir(
                root, "PXTEST_Ship", False
            )
            self.assertEqual(run_slug, "PXTEST_Ship_02")
            self.assertEqual(output.name, "PXTEST_Ship_02_Full")

    def test_legends_run_slug_output_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(
                formats="obj", output_root=root, run_slug="Expected"
            )
            with self.assertRaisesRegex(ValueError, "run-slug"):
                EXTRACT.extract_legends(args, root / "Wrong_Full")


    def test_successful_legends_output_is_published_at_ship_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "PXTEST_Ship_Full"
            scene = run_root / "scene"
            textures = scene / "textures"
            logs = run_root / "logs"
            generated = run_root / "generated"
            diagnostics = root / "diagnostics"
            textures.mkdir(parents=True)
            logs.mkdir()
            generated.mkdir()
            (scene / "PXTEST_Ship_Editable.obj").write_text("mtllib ship.mtl\\n")
            (scene / "ship.mtl").write_text("newmtl hull\\n")
            (textures / "hull.png").write_bytes(b"texture")
            (logs / "pipeline.log").write_text("ok")
            (generated / "intermediate.obj").write_text("temporary")
            manifest = run_root / "selected_ship_full.pipeline.json"
            manifest.write_text('{"acceptance":{"passed":true}}')

            result = EXTRACT.publish_legends_output(
                run_root, scene, manifest, diagnostics
            )

            self.assertTrue((run_root / "PXTEST_Ship_Editable.obj").is_file())
            self.assertTrue((run_root / "ship.mtl").is_file())
            self.assertTrue((run_root / "textures" / "hull.png").is_file())
            self.assertFalse(scene.exists())
            self.assertFalse(generated.exists())
            self.assertFalse((run_root / "logs").exists())
            self.assertFalse(manifest.exists())
            diagnostics_dir = Path(result["diagnostics_dir"])
            self.assertTrue((diagnostics_dir / "logs" / "pipeline.log").is_file())
            self.assertTrue(
                (diagnostics_dir / "selected_ship_full.pipeline.json").is_file()
            )
            self.assertEqual(result["cleanup_warnings"], [])

    def test_legends_publish_refuses_name_collision_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "PXTEST_Ship_Full"
            scene = run_root / "scene"
            scene.mkdir(parents=True)
            (scene / "ship.obj").write_text("scene")
            (run_root / "ship.obj").write_text("existing")
            manifest = run_root / "selected_ship_full.pipeline.json"
            manifest.write_text("{}")
            with self.assertRaises(FileExistsError):
                EXTRACT.publish_legends_output(
                    run_root, scene, manifest, root / "diagnostics"
                )
            self.assertTrue((scene / "ship.obj").is_file())
            self.assertTrue(manifest.is_file())


class ItemContractTests(unittest.TestCase):
    def test_legends_quality_profile_is_forced_and_requested_values_preserved(self) -> None:
        common = {
            "toolbox_root": str(BACKEND.parent),
            "output_root": str(BACKEND.parent / "output"),
            "lod": 2,
            "texture_max_size": 1024,
            "formats": "obj",
        }
        item = {
            "source": "legends",
            "game_dir": str(BACKEND.parent),
            "ship_key": "PXTEST_Ship",
            "selected_model_path": "content/gameplay/usa/ship/test/PXTEST_Ship",
            "display_name": "Test Ship",
        }
        args = BATCH.item_namespace(common, item)
        self.assertEqual((args.requested_lod, args.lod), (2, 0))
        self.assertEqual(args.camouflage, "default")
        self.assertEqual(args.camouflage_color_scheme, "")
        self.assertEqual(
            (args.requested_texture_max_size, args.texture_max_size),
            (1024, 0),
        )
        self.assertTrue(BATCH.item_contract(args, 1)["ok"])

    def test_pc_hull_upgrade_is_preserved_and_unsafe_value_is_rejected(self) -> None:
        common = {
            "toolbox_root": str(BACKEND.parent),
            "output_root": str(BACKEND.parent / "output"),
            "formats": "obj",
            "camouflage": "native",
        }
        item = {
            "source": "pc",
            "game_dir": str(BACKEND.parent),
            "ship_index": "PTEST001",
            "hull_upgrade": "B_UPGRADE",
            "display_name": "Test Ship",
        }
        args = BATCH.item_namespace(common, item)
        self.assertEqual(args.hull_upgrade, "B_UPGRADE")
        self.assertEqual(args.camouflage, "native")
        self.assertTrue(BATCH.item_contract(args, 1)["ok"])
        args.hull_upgrade = "../unsafe"
        result = BATCH.item_contract(args, 1)
        self.assertFalse(result["ok"])
        self.assertIn("안전하지", result["message"])

    def test_item_camouflage_overrides_common_and_separates_output(self) -> None:
        common = {
            "toolbox_root": str(BACKEND.parent),
            "output_root": str(BACKEND.parent / "output"),
            "formats": "obj",
            "camouflage": "default",
        }
        item = {
            "source": "pc",
            "game_dir": str(BACKEND.parent),
            "ship_index": "PJSD001",
            "display_name": "Aki G",
            "camouflage": "PJES397_Golden_Aki",
            "camouflage_color_scheme": "colorSchemeIJN_alt",
        }
        args = BATCH.item_namespace(common, item)
        self.assertEqual(args.camouflage, "PJES397_Golden_Aki")
        self.assertEqual(args.camouflage_color_scheme, "colorSchemeIJN_alt")
        with tempfile.TemporaryDirectory() as temporary:
            args.output_root = Path(temporary)
            output = BATCH.output_for(args, set())
        self.assertIn("camo-PJES397_Golden_Aki", output.name)
        self.assertIn("color-colorSchemeIJN_alt", output.name)

    def test_legends_rejects_non_default_camouflage(self) -> None:
        common = {
            "toolbox_root": str(BACKEND.parent),
            "output_root": str(BACKEND.parent / "output"),
            "formats": "obj",
        }
        item = {
            "source": "legends",
            "game_dir": str(BACKEND.parent),
            "ship_key": "PXTEST_Ship",
            "selected_model_path": "content/gameplay/usa/ship/test/PXTEST_Ship",
            "display_name": "Test Ship",
            "camouflage": "PCEM017_Steel_10lvl",
        }
        result = BATCH.item_contract(BATCH.item_namespace(common, item), 1)
        self.assertFalse(result["ok"])
        self.assertIn("지원하지", result["message"])

    def test_unsafe_legends_model_path_is_rejected(self) -> None:
        args = SimpleNamespace(
            source="legends",
            ship_key="PXTEST_Ship",
            selected_model_path="../outside",
            toolbox_root=BACKEND.parent,
            display_name="Unsafe",
        )
        result = BATCH.item_contract(args, 1)
        self.assertFalse(result["ok"])
        self.assertIn("안전하지", result["message"])


class ValidationTests(unittest.TestCase):
    def test_corrupt_history_rows_are_ignored(self) -> None:
        history = {
            "pc": [
                None,
                "broken",
                {"seconds": "not-a-number", "bytes": "bad"},
                {"seconds": math.nan, "bytes": -1},
                {"seconds": "12.5", "bytes": "4096"},
            ]
        }
        self.assertEqual(BATCH.history_estimate(history, "pc"), (12.5, 4096))

    def test_corrupt_source_history_is_replaced_on_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "ship"
            output.mkdir()
            (output / "model.obj").write_bytes(b"1234")
            history = {"pc": {"unexpected": "object"}}
            BATCH.update_history(
                history,
                "pc",
                3.25,
                {"output_dir": str(output)},
            )
        self.assertIsInstance(history["pc"], list)
        self.assertEqual(history["pc"][0]["bytes"], 4)

    def test_supported_formats_are_normalized(self) -> None:
        self.assertEqual(EXTRACT.parse_formats(" OBJ "), {"obj"})
        with self.assertRaisesRegex(ValueError, "지원하지 않는 출력 형식"):
            EXTRACT.parse_formats("obj,glb")

    def test_unknown_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "지원하지 않는 출력 형식"):
            EXTRACT.parse_formats("obj,usd")

    def test_camouflage_selection_validation_and_output_stem(self) -> None:
        self.assertEqual(
            EXTRACT.validate_camouflage_selection("PCEM017_Steel_10lvl"),
            "PCEM017_Steel_10lvl",
        )
        self.assertEqual(
            EXTRACT.ship_output_stem(
                "Aki G", "PJSD001", camouflage="PJES397_Golden_Aki"
            ),
            "Aki G_PJSD001_camo-PJES397_Golden_Aki",
        )
        with self.assertRaisesRegex(ValueError, "안전하지"):
            EXTRACT.validate_camouflage_selection("../unsafe")
        self.assertEqual(
            EXTRACT.validate_camouflage_color_scheme("colorSchemeGER07"),
            "colorSchemeGER07",
        )
        self.assertEqual(
            EXTRACT.ship_output_stem(
                "Mecklenburg",
                "PGSB610",
                camouflage="PCEP120_Permo_Upgr_10_lvl",
                camouflage_color_scheme="colorSchemeGER07",
            ),
            "Mecklenburg_PGSB610_camo-PCEP120_Permo_Upgr_10_lvl_color-colorSchemeGER07",
        )
        with self.assertRaisesRegex(ValueError, "안전하지"):
            EXTRACT.validate_camouflage_color_scheme("../unsafe")

    def test_prefetch_cancel_only_applies_before_promotion(self) -> None:
        args = SimpleNamespace(
            promoted_event=threading.Event(),
            cancel_check=lambda: True,
        )
        self.assertTrue(EXTRACT.prefetch_cancelled(args))
        args.promoted_event.set()
        self.assertFalse(EXTRACT.prefetch_cancelled(args))

    def test_silent_prefetch_process_is_cancelled_promptly(self) -> None:
        started = time.perf_counter()
        with self.assertRaisesRegex(RuntimeError, "미리 준비 작업을 취소"):
            EXTRACT.run_stream(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                cancel_check=lambda: True,
            )
        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
