from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("build_resource_profile.py")
SPEC = importlib.util.spec_from_file_location("build_resource_profile", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def render_set(name: str, material_index: int = 0) -> dict:
    return {
        "vertices_section": f"{name}.vertices",
        "indices_section": f"{name}.indices",
        "render_set_name": f"{name}.indices",
        "material_prototype_index": material_index,
    }


def model_record(model_path: str, geometry_path: str) -> dict:
    return {
        "model_uber": {
            "visual_prototypes": [
                {
                    "lod_index": 0,
                    "derived_geometry_path": geometry_path,
                    "render_sets": [
                        render_set("BodyShape"),
                        render_set("MidFront_patch_MidBackShape"),
                        render_set("MidFront_crack_MidBack_DeckHouseShape"),
                        render_set("MidFront_crack_MidBackShape"),
                        render_set("MidFront_hideShape"),
                        render_set("Body_deadShape"),
                    ],
                }
            ],
            "material_prototypes": [
                {
                    "properties": [
                        {
                            "name": "diffuseMap",
                            "type": "texture",
                            "value": {
                                "path": (
                                    "content/gameplay/usa/ship/textures/"
                                    "body_a.dds"
                                )
                            },
                        },
                        {
                            "name": "unusedTexture",
                            "type": "texture",
                            "value": {"path": "content/unused.dds"},
                        },
                    ]
                }
            ],
        }
    }


class ResourceProfileTests(unittest.TestCase):
    def test_damage_contract_keeps_patch_and_exterior_joint_faces(self):
        self.assertTrue(
            module.is_intact_render_set(
                render_set("MidFront_patch_MidBackShape")
            )
        )
        self.assertTrue(
            module.is_intact_render_set(
                render_set("MidFront_crack_MidBack_DeckHouseShape")
            )
        )
        self.assertTrue(
            module.is_intact_render_set(
                render_set("MidFront_crack_MidBack_HullShape")
            )
        )
        self.assertFalse(
            module.is_intact_render_set(
                render_set("MidFront_crack_MidBackShape")
            )
        )
        self.assertFalse(
            module.is_intact_render_set(render_set("MidFront_hideShape"))
        )
        self.assertFalse(
            module.is_intact_render_set(render_set("Body_deadShape"))
        )

    def test_profile_collects_only_used_models_and_deduplicates_resources(self):
        hull_model = "content/gameplay/usa/ship/ASD310_MidFront.model"
        gun_model = "content/gameplay/usa/gun/AGS2026.model"
        mapping = {
            "schema": "wows-legends-static-ship-assembly/v1",
            "ship": {"ship_key": "PXSD310_Arleigh_Burke_1991"},
            "hull_parts": [{"role": "mesh", "path": hull_model}],
            "combat_mounts": [
                {
                    "hardpoint": "HP_AGS_1",
                    "model_path": gun_model,
                },
                {
                    "hardpoint": "HP_AGS_2",
                    "model_path": gun_model,
                },
            ],
            "misc_instances": [],
            "runtime_action_overlays": [],
            "models": {
                hull_model: model_record(
                    hull_model,
                    "content/gameplay/usa/ship/ASD310_MidFront.geometry",
                ),
                gun_model: model_record(
                    gun_model,
                    "content/gameplay/usa/gun/AGS2026.geometry",
                ),
            },
        }
        profile = module.build_profile(mapping)
        counts = profile["expected_counts"]
        self.assertEqual(4, counts["system_sidecars"])
        self.assertEqual(2, counts["lod0_geometry"])
        self.assertEqual(1, counts["declared_textures"])
        self.assertEqual(2, counts["unique_models"])
        self.assertEqual(7, counts["total"])
        self.assertEqual(
            {"hull": 1, "combat": 1, "misc": 0, "runtime_overlay": 0},
            profile["model_counts"],
        )
        paths = [item["path"] for item in profile["resources"]]
        self.assertEqual(len(paths), len({path.casefold() for path in paths}))
        self.assertNotIn("content/unused.dds", paths)


if __name__ == "__main__":
    unittest.main()
