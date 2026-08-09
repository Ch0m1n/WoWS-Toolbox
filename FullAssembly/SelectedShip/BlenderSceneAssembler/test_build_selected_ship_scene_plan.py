#!/usr/bin/env python3
"""Unit tests for the generic selected-ship Blender scene plan."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("build_selected_ship_scene_plan.py")
SPEC = importlib.util.spec_from_file_location("selected_ship_scene_plan", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SCENE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCENE)


IDENTITY = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


class ScenePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mapping_path = self.root / "selected_ship_assembly.json"
        self.summary_path = self.root / "selected_ship_pbr_models.summary.json"
        self.model_paths = [
            "content/usa/ship/shared.model",
            "content/uk/ship/shared.model",
            "content/weapon/gun.model",
            "content/misc/boat.model",
            "content/misc/flag.model",
            "content/weapon/gun_action.model",
        ]
        self.mapping = {
            "schema": "wows-legends-static-ship-assembly/v1",
            "validation": {"static_assembly_acceptance": True},
            "ship": {"ship_key": "PASD310_Arleigh_Burke_1991"},
            "visibility_profiles": {"custom_overlay": {"dock": False, "overlay": True}},
            "hull_parts": [
                {
                    "path": self.model_paths[0],
                    "role": "mesh",
                    "render_required": True,
                },
                {
                    "path": self.model_paths[1],
                    "role": "mesh",
                    "render_required": True,
                },
                {
                    "path": "content/ship/root.model",
                    "role": "root",
                    "render_required": False,
                },
            ],
            "combat_mounts": [
                {
                    "hardpoint": "HP_MainGun",
                    "category": "artillery",
                    "model_path": self.model_paths[2],
                    "render_required": True,
                    "corrected_gltf_rh_y_up_matrix": {"column_major": list(IDENTITY)},
                }
            ],
            "misc_instances": [
                {
                    "instance_name": "MP_Boat",
                    "model_path": self.model_paths[3],
                    "visibility_condition": "always",
                    "render_required": True,
                    "corrected_gltf_rh_y_up_matrix": {"column_major": list(IDENTITY)},
                },
                {
                    "instance_name": "MP_Flag",
                    "model_path": self.model_paths[4],
                    "visibility_condition": "dock",
                    "render_required": True,
                    "corrected_gltf_rh_y_up_matrix": {"column_major": list(IDENTITY)},
                },
            ],
            "runtime_action_overlays": [
                {
                    "parent_hardpoint": "HP_MainGun",
                    "instance_name": "HP_MainGun_ACTION_1",
                    "model_path": self.model_paths[5],
                    "visibility_condition": "overlay",
                    "render_required": True,
                    "corrected_gltf_rh_y_up_matrix": {"column_major": list(IDENTITY)},
                }
            ],
        }
        self._write_fixture()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_fixture(self) -> None:
        self.mapping_path.write_text(json.dumps(self.mapping), encoding="utf-8")
        digest = hashlib.sha256(self.mapping_path.read_bytes()).hexdigest().upper()
        results = []
        for index, model_path in enumerate(self.model_paths):
            glb = self.root / f"model_{index}.glb"
            glb.write_bytes(b"glTF-test-placeholder")
            results.append(
                {
                    "status": "OK",
                    "model_path": model_path,
                    "output_glb": str(glb),
                }
            )
        summary = {
            "source_mapping": str(self.mapping_path),
            "source_mapping_sha256": digest,
            "model_count": len(results),
            "strict_validation": {"accepted": True},
            "results": results,
        }
        self.summary_path.write_text(json.dumps(summary), encoding="utf-8")

    def test_harbor_profile_builds_dynamic_counts_and_visibility(self) -> None:
        plan = SCENE.build_plan(self.mapping_path, self.summary_path)

        self.assertEqual(plan["counts"]["hull_glbs"], 2)
        self.assertEqual(plan["counts"]["mounts"], 4)
        self.assertEqual(plan["counts"]["combat"], 1)
        self.assertEqual(plan["counts"]["misc"], 2)
        self.assertEqual(plan["counts"]["runtime_overlay"], 1)
        self.assertEqual(plan["counts"]["default_visible"], 3)
        self.assertEqual(plan["counts"]["default_hidden"], 1)
        self.assertNotEqual(plan["hull_glbs"][0], plan["hull_glbs"][1])

    def test_neutral_profile_hides_dock_and_overlay(self) -> None:
        plan = SCENE.build_plan(
            self.mapping_path,
            self.summary_path,
            visibility_profile="neutral_battle_intact",
        )
        self.assertEqual(plan["counts"]["default_visible"], 2)
        self.assertEqual(plan["counts"]["default_hidden"], 2)

    def test_mapping_defined_profile_can_show_overlay(self) -> None:
        plan = SCENE.build_plan(
            self.mapping_path,
            self.summary_path,
            visibility_profile="custom_overlay",
        )
        by_name = {item["hardpoint"]: item for item in plan["mounts"]}
        self.assertTrue(by_name["HP_MainGun_ACTION_1"]["visible"])
        self.assertFalse(by_name["MP_Flag"]["visible"])

    def test_repeated_authored_misc_names_remain_distinct_occurrences(self) -> None:
        duplicate = dict(self.mapping["misc_instances"][0])
        duplicate["corrected_gltf_rh_y_up_matrix"] = {"column_major": list(IDENTITY)}
        self.mapping["misc_instances"].append(duplicate)
        self._write_fixture()

        plan = SCENE.build_plan(self.mapping_path, self.summary_path)

        repeated = [item for item in plan["mounts"] if item["hardpoint"] == "MP_Boat"]
        self.assertEqual(len(repeated), 2)
        self.assertEqual(plan["counts"]["mounts"], 5)

    def test_rejected_mapping_is_not_assembled(self) -> None:
        self.mapping["validation"]["static_assembly_acceptance"] = False
        self._write_fixture()
        with self.assertRaisesRegex(ValueError, "static assembly is not accepted"):
            SCENE.build_plan(self.mapping_path, self.summary_path)

    def test_mapping_hash_mismatch_is_rejected(self) -> None:
        summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
        summary["source_mapping_sha256"] = "0" * 64
        self.summary_path.write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            SCENE.build_plan(self.mapping_path, self.summary_path)

    def test_identical_mapping_can_be_relocated(self) -> None:
        relocated = self.root / "relocated" / "assembly.json"
        relocated.parent.mkdir()
        relocated.write_bytes(self.mapping_path.read_bytes())

        plan = SCENE.build_plan(relocated, self.summary_path)

        self.assertEqual(plan["counts"]["hull_glbs"], 2)
        self.assertEqual(plan["source_files"]["assembly"], str(relocated.resolve()))

    def test_unknown_visibility_condition_is_rejected(self) -> None:
        self.mapping["misc_instances"][0]["visibility_condition"] = "moonlight"
        self._write_fixture()
        with self.assertRaisesRegex(ValueError, "unsupported visibility condition"):
            SCENE.build_plan(self.mapping_path, self.summary_path)

    def test_non_finite_matrix_is_rejected(self) -> None:
        self.mapping["combat_mounts"][0]["corrected_gltf_rh_y_up_matrix"][
            "column_major"
        ][0] = float("nan")
        self._write_fixture()
        with self.assertRaisesRegex(ValueError, "non-finite"):
            SCENE.build_plan(self.mapping_path, self.summary_path)


if __name__ == "__main__":
    unittest.main()
