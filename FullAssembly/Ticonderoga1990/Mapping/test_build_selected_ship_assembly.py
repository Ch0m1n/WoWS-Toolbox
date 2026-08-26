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

    def test_nested_combat_hardpoint_composes_parent_mount_transform(self) -> None:
        def matrix(x: float, y: float, z: float) -> dict[str, object]:
            values = list(selected.core.IDENTITY)
            values[12:15] = [x, y, z]
            return selected.core.matrix_record(values)

        hull_path = "content/gameplay/test/ship/Test.model"
        gun_path = "content/gameplay/test/gun/GGM001.model"
        aa_path = "content/gameplay/test/gun/GGA001.model"
        parent_node = {
            "index": 2,
            "parent_index": 0,
            "name": "HP_GGM_2",
            "world_matrix": matrix(10.0, 0.0, 3.0),
        }
        child_node = {
            "index": 7,
            "parent_index": 1,
            "name": "HP_GGA_1",
            "world_matrix": matrix(0.25, 2.0, -0.5),
        }

        def model(path: str, nodes: list[dict[str, object]]) -> dict[str, object]:
            return {
                "resource_id": path,
                "prototype_location": {"path": path},
                "model_uber": {"visual_nodes": {"nodes": nodes}},
            }

        gp_mounts = [
            {
                "hardpoint": "HP_GGM_2",
                "model_path": gun_path,
                "action_model_paths": [],
            },
            {
                "hardpoint": "HP_GGM_2_HP_GGA_1",
                "model_path": aa_path,
                "action_model_paths": [],
            },
        ]
        models = {
            hull_path: model(hull_path, [parent_node]),
            gun_path: model(gun_path, [child_node]),
            aa_path: model(aa_path, []),
        }
        identity = {
            "correction_matrix": selected.core.matrix_record(
                list(selected.core.IDENTITY)
            )
        }
        mirrored_parent = list(selected.core.IDENTITY)
        mirrored_parent[10] = -1.0

        mounts, duplicates = selected.resolve_combat_mounts(
            gp_mounts,
            {"HP_GGM_2": [(hull_path, parent_node)]},
            models,
            {
                gun_path: {
                    "correction_matrix": selected.core.matrix_record(
                        mirrored_parent
                    )
                },
                aa_path: identity,
            },
        )

        self.assertEqual(duplicates, {})
        self.assertEqual(len(mounts), 2)
        child = mounts[1]
        self.assertEqual(child["attachment_parent_hardpoint"], "HP_GGM_2")
        self.assertEqual(child["local_hardpoint"], "HP_GGA_1")
        self.assertEqual(child["attachment_depth"], 1)
        self.assertEqual(child["source_hull_model_path"], gun_path)
        self.assertEqual(
            child["corrected_gltf_rh_y_up_matrix"]["translation_xyz"],
            [10.25, 2.0, 2.5],
        )
        self.assertEqual(
            child["hp_world_matrix"]["rotation_scale_determinant"], 1.0
        )

    def test_native_exterior_replaces_hull_and_authored_guns(self) -> None:
        ship_key = "PGSB810_Test_GOLDEN"
        exterior_key = "PGES744_TEST_GOLDEN"
        base_hull = "content/gameplay/germany/ship/battleship/GSB047_Base/GSB047_Base.model"
        gold_hull = "content/gameplay/germany/ship/battleship/GSB423_Gold/GSB423_Gold.model"
        base_main = "content/gameplay/germany/gun/main/GGM133/GGM133.model"
        gold_main = "content/gameplay/germany/gun/main/GGM3162/GGM3162.model"
        base_secondary = "content/gameplay/germany/gun/secondary/GGS123/GGS123.model"
        gold_secondary = "content/gameplay/germany/gun/secondary/GGS3163/GGS3163.model"

        components = {
            "A_Hull": {"model": base_hull},
            "A_Artillery": {"HP_GGM_1": {"models": [base_main]}},
            "A_ATBA": {"HP_GGS_1": {"models": [base_secondary]}},
        }
        general = selected.core.NeutralObject()
        general_state = [None] * 34
        general_state[27] = exterior_key
        general.state = tuple(general_state)
        ship = selected.core.NeutralObject()
        ship.state = (general, None, components)

        skin = selected.core.NeutralObject(
            "GameParamsData.Exterior", "__pyx_unpickle_Skin"
        )
        skin_state = [None] * 58
        skin_state[38] = {
            "A_Hull": {"model": gold_hull, "customMiscs": {}, "caps": {}}
        }
        skin_state[41] = {
            "A_Artillery": {
                "HP_GGM_1": {
                    "model": gold_main,
                    "deadMesh": gold_main.replace(".model", "_dead.model"),
                    "miscFilter": [],
                    "filterMode": False,
                }
            },
            "A_ATBA": {
                "HP_GGS_1": {
                    "model": gold_secondary,
                    "deadMesh": gold_secondary.replace(".model", "_dead.model"),
                    "miscFilter": [],
                    "filterMode": False,
                }
            },
        }
        skin_state[18] = "mat_Golden_tint"
        skin.state = tuple(skin_state)
        exterior = selected.core.NeutralObject()
        exterior.state = (skin, None)

        paths = [
            base_hull,
            gold_hull,
            base_main,
            gold_main,
            base_secondary,
            gold_secondary,
        ]
        assets = FakeAssets(paths)
        prototypes = FakePrototypes(set(assets.path_to_id.values()))
        mounts, evidence = selected.gameparams_mounts(
            {ship_key: ship, exterior_key: exterior},
            ship_key,
            assets,
            prototypes,
            base_hull,
        )

        by_hardpoint = {mount["hardpoint"]: mount for mount in mounts}
        self.assertEqual(evidence["base_hull_model_path"], base_hull)
        self.assertEqual(evidence["hull_model_path"], gold_hull)
        self.assertEqual(evidence["native_exterior"]["id"], exterior_key)
        self.assertEqual(
            evidence["native_exterior"]["material_tints"], ["mat_Golden_tint"]
        )
        self.assertEqual(by_hardpoint["HP_GGM_1"]["model_path"], gold_main)
        self.assertEqual(
            by_hardpoint["HP_GGS_1"]["model_path"], gold_secondary
        )
        self.assertEqual(
            by_hardpoint["HP_GGM_1"]["original_model_path"], base_main
        )

        optional_state = list(general.state)
        optional_state[27] = ""
        optional_state[33] = [exterior_key]
        general.state = tuple(optional_state)
        base_mounts, base_evidence = selected.gameparams_mounts(
            {ship_key: ship, exterior_key: exterior},
            ship_key,
            assets,
            prototypes,
            base_hull,
        )
        self.assertIsNone(base_evidence["native_exterior"])
        self.assertEqual(base_evidence["hull_model_path"], base_hull)
        self.assertEqual(base_mounts[0]["model_path"], base_main)

    def test_native_exterior_can_be_material_tint_only(self) -> None:
        ship_key = "PBSB710_Test_STEEL"
        exterior_key = "PBES709_TEST_EXCLUSIVE"
        base_hull = "content/gameplay/uk/ship/battleship/BSB030_Test/BSB030_Test.model"
        base_main = "content/gameplay/uk/gun/main/BGM001/BGM001.model"

        components = {
            "A_Hull": {"model": base_hull},
            "A_Artillery": {"HP_BGM_1": {"models": [base_main]}},
        }
        general = selected.core.NeutralObject()
        general_state = [None] * 34
        general_state[27] = exterior_key
        general.state = tuple(general_state)
        ship = selected.core.NeutralObject()
        ship.state = (general, None, components)

        skin = selected.core.NeutralObject(
            "GameParamsData.Exterior", "__pyx_unpickle_Skin"
        )
        skin_state = [None] * 58
        skin_state[18] = "mat_SteelStyle2021"
        skin.state = tuple(skin_state)
        exterior = selected.core.NeutralObject()
        exterior.state = (skin, None)

        assets = FakeAssets([base_hull, base_main])
        prototypes = FakePrototypes(set(assets.path_to_id.values()))
        mounts, evidence = selected.gameparams_mounts(
            {ship_key: ship, exterior_key: exterior},
            ship_key,
            assets,
            prototypes,
            base_hull,
        )

        self.assertEqual(evidence["hull_model_path"], base_hull)
        self.assertFalse(evidence["native_exterior"]["hull_model_overridden"])
        self.assertEqual(
            evidence["native_exterior"]["material_tints"],
            ["mat_SteelStyle2021"],
        )
        self.assertEqual(mounts[0]["model_path"], base_main)
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

    def test_default_named_mount_components_are_neutral_ship_modules(self) -> None:
        class FakeShip:
            def __init__(self, components: dict[str, object]) -> None:
                self.state = (None, None, components)

        hull_dir = "content/gameplay/usa/ship/battleship/ASB017_Montana_1945"
        hull_model = f"{hull_dir}/ASB017_Montana_1945.model"
        main_model = "content/gameplay/usa/gun/main/AGM216/AGM216.model"
        secondary_model = (
            "content/gameplay/usa/gun/secondary/AGS145/AGS145.model"
        )
        components = {
            "A_Hull": {"model": hull_model},
            "ArtilleryDefault": {
                "HP_AGM_1": {"models": [main_model]},
            },
            "ATBADefault": {
                "HP_AGS_1": {"models": [secondary_model]},
            },
        }
        assets = FakeAssets([hull_model])
        prototypes = FakePrototypes(set(assets.path_to_id.values()))

        mounts, evidence = selected.gameparams_mounts(
            {"PASB118_Maine": FakeShip(components)},
            "PASB118_Maine",
            assets,
            prototypes,
            hull_dir,
        )

        self.assertEqual(
            evidence["mapped_components"],
            ["ArtilleryDefault", "ATBADefault"],
        )
        self.assertEqual(
            [mount["hardpoint"] for mount in mounts],
            ["HP_AGM_1", "HP_AGS_1"],
        )
        self.assertEqual(
            selected._component_category("AirDefenseDefault"),
            ("AirDefense", "air_defense"),
        )
        self.assertEqual(
            selected._variant_family("ArtilleryDefault", "Artillery"), ""
        )

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
