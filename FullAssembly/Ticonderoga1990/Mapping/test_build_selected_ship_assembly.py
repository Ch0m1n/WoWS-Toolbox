from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_selected_ship_assembly as selected  # noqa: E402


class FakeAssets:
    def __init__(self, paths: list[str]) -> None:
        self.path_to_id = {path: index + 1 for index, path in enumerate(paths)}

    def resource_id(self, path: str) -> int | None:
        return self.path_to_id.get(path)


class FakePrototypes:
    def __init__(self, available: set[int]) -> None:
        self.available = available

    def locate(self, resource_id: int) -> dict[str, int] | None:
        return {"index": resource_id} if resource_id in self.available else None


class SelectedShipMappingTests(unittest.TestCase):
    def test_render_semantics_keep_patches_and_exterior_joint_faces(self) -> None:
        patch = selected.classify_render_set(
            "MidFront_patch_MidBackShape.indices"
        )
        exterior = selected.classify_render_set(
            "MidFront_crack_MidBack_DeckHouse_lod1Shape.indices"
        )
        bare = selected.classify_render_set(
            "MidFront_crack_MidBackShape.indices"
        )
        inner = selected.classify_render_set(
            "MidFront_crack_MidBack_inShape.indices"
        )
        self.assertIs(patch["include_in_intact"], True)
        self.assertIs(exterior["include_in_intact"], True)
        self.assertIs(bare["include_in_intact"], False)
        self.assertIs(inner["include_in_intact"], False)

    def test_hull_discovery_uses_direct_prototype_backed_siblings(self) -> None:
        folder = "content/gameplay/usa/ship/destroyer/ASD310_Test"
        root = f"{folder}/ASD310_Test.model"
        paths = [
            root,
            f"{folder}/ASD310_Test_Bow.model",
            f"{folder}/ASD310_Test_Bow_ports.model",
            f"{folder}/ASD310_Test_MidFront.model",
            f"{folder}/ASD310_Test_MidFront_ports.model",
            f"{folder}/ASD310_Test_MidBack.model",
            f"{folder}/ASD310_Test_MidBack_ports.model",
            f"{folder}/ASD310_Test_MidBack_ports_dock.model",
            f"{folder}/ASD310_Test_Stern.model",
            f"{folder}/ASD310_Test_Stern_ports.model",
            f"{folder}/lods/ASD310_Test_Bow_lod1.model",
            f"{folder}/ASD310_Test_dead.model",
        ]
        assets = FakeAssets(paths)
        prototypes = FakePrototypes(set(assets.path_to_id.values()))
        result = selected.discover_hull_model_paths(
            root, assets, prototypes
        )
        self.assertEqual(len(result), 10)
        self.assertNotIn(paths[-2], result)
        self.assertNotIn(paths[-1], result)
        mesh = selected.hull_part_contract(
            f"{folder}/ASD310_Test_MidFront.model", root, set(result)
        )
        ports = selected.hull_part_contract(
            f"{folder}/ASD310_Test_MidFront_ports.model",
            root,
            set(result),
        )
        self.assertEqual(mesh["role"], "mesh")
        self.assertIs(mesh["render_required"], True)
        self.assertEqual(ports["role"], "ports")
        self.assertIs(ports["render_required"], False)

    def test_catalog_directory_resolves_exact_hull_variant(self) -> None:
        a_dir = "content/gameplay/usa/ship/battleship/ASB099_Connecticut_1944"
        b_dir = "content/gameplay/usa/ship/battleship/ASB100_Connecticut_EA22"
        a_model = f"{a_dir}/ASB099_Connecticut_1944.model"
        b_model = f"{b_dir}/ASB100_Connecticut_EA22.model"
        assets = FakeAssets([a_model, b_model])
        prototypes = FakePrototypes(set(assets.path_to_id.values()))
        components = {
            "A_Hull": {"model": a_model},
            "B_Hull": {"model": b_model},
        }

        selected_component, selected_path = selected._select_hull_model_path(
            components, assets, prototypes, b_dir
        )

        self.assertEqual(selected_component, "B_Hull")
        self.assertEqual(selected_path, b_model)
        self.assertEqual(selected._normalize_selected_model_path(a_dir), a_model)

    def test_dynamic_mount_components_follow_selected_hull_family(self) -> None:
        class FakeShip:
            def __init__(self, components: dict[str, object]) -> None:
                self.state = (None, None, components)

        a_dir = "content/gameplay/usa/ship/battleship/ASB099_A"
        b_dir = "content/gameplay/usa/ship/battleship/ASB100_B"
        a_model = f"{a_dir}/ASB099_A.model"
        b_model = f"{b_dir}/ASB100_B.model"
        components = {
            "A_Hull": {"model": a_model},
            "B_Hull": {"model": b_model},
            "A_Artillery": {
                "HP_AGM_1": {"models": ["content/gameplay/usa/gun/AGM001/AGM001.model"]}
            },
            "B_Artillery": {
                "HP_AGM_2": {"models": ["content/gameplay/usa/gun/AGM002/AGM002.model"]}
            },
            "AB_Directors": {
                "HP_AD_1": {"models": ["content/gameplay/usa/director/AD001/AD001.model"]}
            },
            "R_Radars": {
                "HP_ARS_1": {"models": ["content/gameplay/usa/radar/ARS001/ARS001.model"]}
            },
        }
        assets = FakeAssets([a_model, b_model])
        prototypes = FakePrototypes(set(assets.path_to_id.values()))

        mounts, evidence = selected.gameparams_mounts(
            {"PASB207_Test": FakeShip(components)},
            "PASB207_Test",
            assets,
            prototypes,
            b_dir,
        )

        self.assertEqual(evidence["hull_component"], "B_Hull")
        self.assertIs(evidence["selected_model_exact"], True)
        self.assertEqual(
            evidence["mapped_components"],
            ["B_Artillery", "AB_Directors", "R_Radars"],
        )
        self.assertEqual(
            [mount["hardpoint"] for mount in mounts],
            ["HP_AD_1", "HP_AGM_2", "HP_ARS_1"],
        )
        self.assertNotIn("HP_AGM_1", [mount["hardpoint"] for mount in mounts])

    def test_numbered_module_family_belongs_to_selected_hull(self) -> None:
        class FakeShip:
            def __init__(self, components: dict[str, object]) -> None:
                self.state = (None, None, components)

        hull_dir = "content/gameplay/usa/ship/cruiser/ASC026_Alaska_1944"
        hull_model = f"{hull_dir}/ASC026_Alaska_1944.model"
        components = {
            "A_Hull": {"model": hull_model},
            "A1_Artillery": {
                "HP_AGM_1": {
                    "models": [
                        "content/gameplay/usa/gun/main/AGM050/AGM050.model"
                    ]
                }
            },
        }
        assets = FakeAssets([hull_model])
        prototypes = FakePrototypes(set(assets.path_to_id.values()))

        mounts, evidence = selected.gameparams_mounts(
            {"PASC510_Alaska": FakeShip(components)},
            "PASC510_Alaska_1944",
            assets,
            prototypes,
            hull_dir,
        )

        self.assertEqual(evidence["mapped_components"], ["A1_Artillery"])
        self.assertEqual(evidence["skipped_mount_components"], [])
        self.assertEqual([mount["hardpoint"] for mount in mounts], ["HP_AGM_1"])

    def test_mount_selection_separates_vls_auxiliary_model(self) -> None:
        item = {
            "models": [
                "content/gameplay/usa/misc/AM5032/AM5032.model",
                "content/gameplay/usa/gun/main/AGM2024/AGM2024_dead.model",
                "content/gameplay/usa/gun/main/AGM2024/AGM2024.model",
            ]
        }
        mount = selected._mount_from_item(
            "R_UnguidedMissiles",
            "vertical_launch_system",
            "HP_AGM_2",
            item,
        )
        self.assertTrue(mount["model_path"].endswith("/AGM2024.model"))
        self.assertEqual(
            mount["action_model_paths"],
            ["content/gameplay/usa/misc/AM5032/AM5032.model"],
        )
        self.assertEqual(len(mount["dead_model_paths"]), 1)


if __name__ == "__main__":
    unittest.main()
