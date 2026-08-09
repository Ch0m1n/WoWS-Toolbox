from __future__ import annotations

import unittest

import armor_sidecar


class ExactArmorSidecarTests(unittest.TestCase):
    def test_exact_values_and_viewer_axes_are_preserved(self) -> None:
        payload = {
            "schema": "wowsunpack-interactive-armor/v1",
            "meshes": [
                {
                    "name": "HullArmor",
                    "positions": [
                        [0.0, 0.0, -4.0],
                        [1.0, 0.0, -4.0],
                        [0.0, 1.0, -4.0],
                        [0.0, 0.0, 4.0],
                        [1.0, 0.0, 4.0],
                        [0.0, 1.0, 4.0],
                    ],
                    "indices": [0, 1, 2, 3, 4, 5],
                    "triangle_info": [
                        {
                            "zone": "Bow",
                            "thickness_mm": 25.0,
                            "layers": [25.0],
                            "material_id": 10,
                            "material_name": "BowPlate",
                            "hidden": False,
                        },
                        {
                            "zone": "Stern",
                            "thickness_mm": 26.5,
                            "layers": [13.0, 13.5],
                            "material_id": 11,
                            "material_name": "SternPlate",
                            "hidden": False,
                        },
                    ],
                }
            ],
        }

        converted = armor_sidecar.convert(payload)

        self.assertEqual(converted["schema"], "wows-toolbox-armor-viewer/v3")
        self.assertTrue(converted["exact_thickness"])
        self.assertEqual(converted["coordinate_system"]["axis_forward"], "-Z")
        self.assertEqual(converted["coordinate_system"]["axis_up"], "Y")
        self.assertEqual(
            [bucket["label"] for bucket in converted["buckets"]],
            ["25 mm", "26.5 mm"],
        )
        self.assertEqual(converted["triangle_count"], 2)
        groups = {group["zone"]: group for group in converted["groups"]}
        self.assertLess(max(groups["Bow"]["positions"][2::3]), 0)
        self.assertGreater(min(groups["Stern"]["positions"][2::3]), 0)
        self.assertEqual(groups["Stern"]["layers_mm"], [13.0, 13.5])

    def test_column_major_mesh_transform_is_applied(self) -> None:
        payload = {
            "schema": "wowsunpack-interactive-armor/v1",
            "meshes": [
                {
                    "name": "TurretArmor",
                    "positions": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                    "indices": [0, 1, 2],
                    "transform": [
                        1, 0, 0, 0,
                        0, 1, 0, 0,
                        0, 0, 1, 0,
                        2, 3, -5, 1,
                    ],
                    "triangle_info": [
                        {
                            "zone": "MainGun",
                            "thickness_mm": 305,
                            "layers": [305],
                            "material_id": 20,
                            "material_name": "TurretFace",
                        }
                    ],
                }
            ],
        }

        converted = armor_sidecar.convert(payload)
        group = converted["groups"][0]
        self.assertEqual(group["positions"][:3], [2.0, 3.0, -5.0])
        self.assertEqual(converted["buckets"][0]["label"], "305 mm")


if __name__ == "__main__":
    unittest.main()
