from __future__ import annotations

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
            }
        }
        with (
            mock.patch.object(CATALOG, "read_archive_index", return_value=(123, [])),
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


if __name__ == "__main__":
    unittest.main()
