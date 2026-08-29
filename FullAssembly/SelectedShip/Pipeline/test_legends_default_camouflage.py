from __future__ import annotations

import unittest

import legends_default_camouflage as camouflage


class DefaultCamouflageTests(unittest.TestCase):
    def test_steel_style_preserves_authored_textures(self) -> None:
        mapping = {
            "ship": {"ship_key": "PBSB710_Incomparable_STEEL"},
            "native_exterior": {
                "id": "PBES709_INCOMPARABLE_EXCLUSIVE",
                "camouflage_styles": ["mat_SteelStyle2021"],
            },
        }
        diffuse = camouflage.MAT_CAMO_PREFIX + "mat_Steel_01_a.dds"
        mgn = camouflage.MAT_CAMO_PREFIX + "mat_Steel_01_mgn.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [], [diffuse, mgn]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "preserve_authored_textures")
        self.assertEqual(profile["style"], "mat_SteelStyle2021")
        self.assertEqual(profile["style_texture_maps"]["a"], diffuse)
        self.assertEqual(profile["style_texture_maps"]["mg"], mgn)
        self.assertEqual(profile["resources"], [])
        self.assertIn("authored model textures", profile["warnings"][-1])

    def test_permanent_style_uses_ship_specific_rgb_masks(self) -> None:
        mapping = {
            "ship": {"ship_key": "PASB001_Test"},
            "native_exterior": {
                "id": "PAES001_TEST",
                "camouflage_styles": ["camo_permanent_1"],
            },
        }
        hull = "content/gameplay/usa/ship/textures/ASB001_Test_Hull_a.dds"
        deck = "content/gameplay/usa/ship/textures/ASB001_Test_DeckHouse_a.dds"
        hull_mask = camouflage.MASK_PREFIX + "ASB001_Test_Hull_camo_01.dds"
        deck_mask = camouflage.MASK_PREFIX + "ASB001_Test_DeckHouse_camo_01.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [hull, deck], [hull_mask, deck_mask]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "palette_mask")
        self.assertEqual(profile["palette"], list(camouflage.NATION_PALETTES["A"]))
        self.assertEqual(
            {item["mask"] for item in profile["masks"]},
            {hull_mask, deck_mask},
        )

    def test_shared_hull_uses_playable_ship_identity_mask(self) -> None:
        mapping = {
            "ship": {
                "ship_key": "PJSD706_Shinonome",
                "display_identity": "Shinonome",
                "native_exterior": {
                    "id": "PCEP901_Permo_Prem_Low_lvl_CONSOLE",
                    "camouflage_styles": ["camo_permanent_1"],
                },
            }
        }
        diffuse = (
            "content/gameplay/japan/ship/destroyer/textures/"
            "JSD013_Fubuki_1942_a.dds"
        )
        mask = camouflage.MASK_PREFIX + "JSD026_Shinonome_camo_01.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [diffuse], [mask]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "palette_mask")
        self.assertEqual(
            profile["masks"],
            [{"base_texture_stem": "JSD013_Fubuki_1942", "mask": mask}],
        )

    def test_direct_mask_can_target_non_hull_component_texture(self) -> None:
        mapping = {
            "ship": {
                "ship_key": "PASB001_Test",
                "native_exterior": {
                    "id": "PAES001_TEST",
                    "camouflage_styles": ["camo_permanent_1"],
                },
            }
        }
        diffuse = "content/gameplay/usa/gun/main/textures/AGM034_Test_a.dds"
        mask = camouflage.MASK_PREFIX + "AGM034_Test_camo_01.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [diffuse], [mask]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["masks"][0]["base_texture_stem"], "AGM034_Test")

    def test_audited_material_alias_resolves_installed_dds(self) -> None:
        mapping = {
            "ship": {
                "ship_key": "PISB999_Test",
                "native_exterior": {
                    "id": "PIES606_TEST",
                    "camouflage_styles": ["mat_BA_Binah"],
                },
            }
        }
        diffuse = camouflage.MAT_CAMO_PREFIX + "MC_BA_Binah_a.dds"
        mgn = camouflage.MAT_CAMO_PREFIX + "MC_BA_Binah_mgn.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [], [diffuse, mgn]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "preserve_authored_textures")
        self.assertEqual(profile["style_texture_maps"]["a"], diffuse)
        self.assertEqual(profile["style_texture_maps"]["mg"], mgn)
        self.assertEqual(camouflage.resource_definitions(profile), [])

    def test_golden_style_never_replaces_authored_ship_maps(self) -> None:
        mapping = {
            "ship": {
                "ship_key": "PGSB810_Mecklenburg_GOLDEN",
                "native_exterior": {
                    "id": "PGES744_MECKLENBURG_GOLDEN",
                    "camouflage_styles": ["mat_Golden_tint"],
                },
            }
        }
        diffuse = camouflage.MAT_CAMO_PREFIX + "mat_Gold_01_a.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping,
            ["content/gameplay/germany/ship/textures/authored_a.dds"],
            [diffuse],
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "preserve_authored_textures")
        self.assertEqual(profile["resources"], [])

    def test_collaboration_style_uses_authored_replacement_models(self) -> None:
        base = "content/gameplay/japan/ship/JSB006.model"
        authored = "content/gameplay/japan/ship/JSB053_Azur.model"
        mapping = {
            "ship": {"ship_key": "PJSB516_AL_Fusou"},
            "native_exterior": {
                "id": "PJES526_AZUR_FUSOU",
                "camouflage_styles": ["camo_white_tint2"],
                "model_replacements": {base: authored},
            },
        }
        white = camouflage.MAT_CAMO_PREFIX + "mat_White_02_a.dds"

        profile = camouflage.resolve_default_camouflage(
            mapping, [], [white]
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "authored_models")
        self.assertEqual(profile["style"], "camo_white_tint2")
        self.assertEqual(profile["resources"], [])
        self.assertEqual(camouflage.resource_definitions(profile), [])

    def test_unresolved_style_is_explicit_and_extracts_nothing(self) -> None:
        mapping = {
            "ship": {"ship_key": "PZSB001_Test"},
            "native_exterior": {
                "id": "PZES001_TEST",
                "camouflage_styles": ["mat_Unknown_tint"],
            },
        }

        profile = camouflage.resolve_default_camouflage(mapping, [], [])

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["mode"], "preserve_authored_textures")
        self.assertEqual(profile["style_texture_maps"], {})
        self.assertEqual(camouflage.resource_definitions(profile), [])
        self.assertTrue(profile["warnings"])


if __name__ == "__main__":
    unittest.main()
