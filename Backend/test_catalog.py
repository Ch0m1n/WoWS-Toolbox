from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
SPEC = importlib.util.spec_from_file_location("catalog_tested", BACKEND / "catalog.py")
assert SPEC is not None and SPEC.loader is not None
CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CATALOG)


class FakeTranslations:
    def __init__(self, value: str):
        self.value = value

    def gettext(self, _message: str) -> str:
        return self.value


class CatalogTranslationTests(unittest.TestCase):
    def catalog_with_translation(self, value: str) -> list[dict]:
        params = {
            "PTEST001_Test_Ship": {
                "index": "PTEST001",
                "name": "Test Ship Internal",
                "level": 7,
                "typeinfo": {
                    "type": "Ship",
                    "nation": "USA",
                    "species": "Cruiser",
                },
                "ShipUpgradeInfo": {
                    "PTEST_HULL_A": {
                        "ucType": "_Hull",
                        "components": {"hull": ["A_Hull"]},
                    }
                },
                "A_Hull": {
                    "model": "content/gameplay/usa/ship/cruiser/TEST001/TEST001.model"
                },
            }
        }
        with (
            mock.patch.object(
                CATALOG,
                "read_archive_index",
                return_value=(
                    123,
                    {
                        "/content/gameplay/usa/ship/cruiser/test001/test001.geometry": object()
                    },
                ),
            ),
            mock.patch.object(CATALOG, "find_entry", return_value=object()),
            mock.patch.object(CATALOG, "extract_entry", return_value=b"params"),
            mock.patch.object(CATALOG, "decode_game_params", return_value=params),
            mock.patch.object(CATALOG, "root_params", return_value=params),
            mock.patch.object(
                CATALOG,
                "_translation",
                return_value=(FakeTranslations(value), "ko"),
            ),
        ):
            return CATALOG.pc_catalog(Path("game"), "ko", "pc")

    def test_whitespace_only_translation_uses_fallback(self) -> None:
        rows = self.catalog_with_translation(" \t\r\n ")
        self.assertEqual(rows[0]["LocalizedName"], "Test Ship")

    def test_translation_is_trimmed(self) -> None:
        rows = self.catalog_with_translation("  시험함  ")
        self.assertEqual(rows[0]["LocalizedName"], "시험함")

    def test_permanent_camouflages_use_stable_exterior_ids(self) -> None:
        vehicle = {"permoflages": ["PCEM017_Steel_10lvl", "PCEM188_NY25_10lvl"]}
        exteriors = {
            "PCEM017_Steel_10lvl": {
                "name": "PCEM017_Steel_10lvl",
                "index": "PCEC017",
                "camouflage": "colorSchemes/steel",
                "typeinfo": {"species": "Permoflage", "nation": "USA"},
            },
            "PCEM188_NY25_10lvl": {
                "name": "PCEM188_NY25_10lvl",
                "index": "PCEC188",
                "camouflage": "colorSchemes/ny25",
                "typeinfo": {"species": "Permoflage", "nation": "USA"},
            },
        }

        class MappingTranslations:
            def gettext(self, message: str) -> str:
                return {
                    "IDS_PCEM017_STEEL_10LVL": "Steel",
                    "IDS_PCEM188_NY25_10LVL": "New Year 2025",
                }.get(message, message)

        choices = CATALOG._pc_camouflages(vehicle, exteriors, {}, MappingTranslations())
        self.assertEqual(
            {choice["Id"] for choice in choices},
            {"PCEM017_Steel_10lvl", "PCEM188_NY25_10lvl"},
        )
        by_id = {choice["Id"]: choice for choice in choices}
        self.assertEqual(by_id["PCEM017_Steel_10lvl"]["Name"], "Steel")
        self.assertEqual(by_id["PCEM188_NY25_10lvl"]["Scheme"], "colorSchemes/ny25")


    def test_ship_specific_camouflage_keeps_default_and_secondary_colors(self) -> None:
        xml = b"""
        <data>
          <shipgroups.xml />
          <colorschemes.xml>
            <colorScheme>
              <name>scheme_default</name>
              <color0>1 0 0 1</color0><color1>0 1 0 1</color1>
              <color2>0 0 1 1</color2><color3>1 1 1 1</color3>
            </colorScheme>
            <colorScheme>
              <name>scheme_alt</name>
              <color0>0.2 0.4 0.6 1</color0>
            </colorScheme>
          </colorschemes.xml>
          <camouflages.xml>
            <camouflage>
              <name>camo_permanent_1</name>
              <tiled>false</tiled>
              <useColorScheme>True</useColorScheme>
              <targetShip>PTEST001_Test_Ship</targetShip>
              <colorSchemes>scheme_default<colorUI>1 0 0 1</colorUI></colorSchemes>
              <colorSchemes>scheme_alt<colorUI>0.2 0.4 0.6 1</colorUI></colorSchemes>
            </camouflage>
            <camouflage>
              <name>camo_permanent_1</name>
              <targetShip>POTHER002_Other</targetShip>
              <colorSchemes>wrong_scheme</colorSchemes>
            </camouflage>
          </camouflages.xml>
        </data>
        """
        database = CATALOG._camouflage_database(xml)
        exterior = {
            "PCEP001_Default": {
                "name": "PCEP001_Default",
                "index": "PCEP001",
                "camouflage": "camo_permanent_1",
                "typeinfo": {"species": "Permoflage", "nation": "USA"},
            }
        }
        choices = CATALOG._pc_camouflages(
            {"permoflages": ["PCEP001_Default"]},
            exterior,
            {},
            FakeTranslations(""),
            ship_param_name="PTEST001_Test_Ship",
            camouflage_database=database,
        )
        colors = choices[0]["ColorSchemes"]
        self.assertEqual([item["Id"] for item in colors], ["scheme_default", "scheme_alt"])
        self.assertEqual(colors[0]["PreviewColor"], "#FF0000")
        self.assertEqual(colors[1]["Palette"], ["#336699"])
        self.assertEqual(CATALOG.CAMOUFLAGE_CATALOG_VERSION, 2)


    def test_pc_rows_advertise_camouflage_catalog_version(self) -> None:
        rows = self.catalog_with_translation("")
        self.assertEqual(
            rows[0]["CamouflageCatalogVersion"],
            CATALOG.CAMOUFLAGE_CATALOG_VERSION,
        )
        self.assertEqual(rows[0]["Camouflages"], [])


class CatalogHullValidationTests(unittest.TestCase):
    def test_later_existing_hull_is_selected_and_missing_ship_is_unsupported(
        self,
    ) -> None:
        params = {
            "PTEST001_Multi": {
                "index": "PTEST001",
                "name": "PTEST001_Multi",
                "level": 7,
                "typeinfo": {"type": "Ship", "nation": "USA", "species": "Cruiser"},
                "ShipUpgradeInfo": {
                    "A_UPGRADE": {
                        "ucType": "_Hull",
                        "components": {"hull": ["A_Hull"]},
                    },
                    "B_UPGRADE": {
                        "ucType": "_Hull",
                        "components": {"hull": ["B_Hull"]},
                    },
                },
                "A_Hull": {
                    "model": "content/gameplay/usa/ship/cruiser/MISSING/MISSING.model"
                },
                "B_Hull": {
                    "model": "content/gameplay/usa/ship/cruiser/PRESENT/PRESENT.model"
                },
                "Finder": {"model": "content/gameplay/usa/finder/WRONG/WRONG.model"},
            },
            "PTEST002_Removed": {
                "index": "PTEST002",
                "name": "PTEST002_Removed",
                "level": 7,
                "typeinfo": {"type": "Ship", "nation": "USA", "species": "Cruiser"},
                "ShipUpgradeInfo": {
                    "A_UPGRADE": {"ucType": "_Hull", "components": {"hull": ["A_Hull"]}}
                },
                "A_Hull": {
                    "model": "content/gameplay/usa/ship/cruiser/REMOVED/REMOVED.model"
                },
            },
        }
        entries = {
            "/content/gameplay/usa/ship/cruiser/present/present.geometry": object()
        }
        with (
            mock.patch.object(
                CATALOG, "read_archive_index", return_value=(123, entries)
            ),
            mock.patch.object(CATALOG, "find_entry", return_value=object()),
            mock.patch.object(CATALOG, "extract_entry", return_value=b"params"),
            mock.patch.object(CATALOG, "decode_game_params", return_value=params),
            mock.patch.object(CATALOG, "root_params", return_value=params),
            mock.patch.object(
                CATALOG,
                "_translation",
                return_value=(FakeTranslations(""), "ko"),
            ),
        ):
            rows = CATALOG.pc_catalog(Path("game"), "ko", "pc")

        multi = next(row for row in rows if row["ShipCode"] == "PTEST001")
        removed = next(row for row in rows if row["ShipCode"] == "PTEST002")
        self.assertTrue(multi["Supported"])
        self.assertEqual(multi["HullUpgrade"], "B_UPGRADE")
        self.assertEqual(multi["ShipResource"], "PRESENT")
        self.assertFalse(removed["Supported"])
        self.assertNotIn("WRONG", removed["ModelPath"])


class LegendsCatalogBridgeTests(unittest.TestCase):
    def test_exact_game_params_key_and_model_path_are_preserved(self) -> None:
        legacy = {
            "ship_code": "PGSB610",
            "game_params_key": "PGSB610_Mecklenburg",
            "hull_resource": "GSB047_Mecklenburg_1945",
            "hull_resource_path": "content/gameplay/germany/ship/battleship/GSB047_Mecklenburg_1945",
            "model_path": "content/gameplay/germany/ship/battleship/GSB047_Mecklenburg_1945/GSB047_Mecklenburg_1945.model",
            "hull_component": "A_Hull",
            "variant_label": "Mecklenburg 1945 [GSB047]",
            "localized_name": "Mecklenburg",
            "localized_language": "ko",
            "nation": "Germany",
            "nation_code": "G",
            "class": "Battleship",
            "class_code": "B",
            "tier": None,
            "id": "PGSB610_Mecklenburg::GSB047_Mecklenburg_1945",
            "selectable": True,
            "support_reason": "verified fixture",
        }
        process = mock.Mock(stdout=json.dumps([legacy]))
        with mock.patch.object(CATALOG.subprocess, "run", return_value=process):
            rows = CATALOG.legends_catalog(Path("toolbox"), Path("game"), "ko")

        self.assertEqual(rows[0]["GameParamsKey"], "PGSB610_Mecklenburg")
        self.assertEqual(rows[0]["InternalName"], "PGSB610_Mecklenburg")
        self.assertEqual(rows[0]["ModelPath"], legacy["model_path"])
        self.assertEqual(rows[0]["HullComponent"], "A_Hull")
        self.assertTrue(rows[0]["ArchiveHullVerified"])


if __name__ == "__main__":
    unittest.main()
