#!/usr/bin/env python3
"""Unit tests for the selected-ship converter adapter."""

from __future__ import annotations

import importlib.util
import re
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("convert_selected_ship.py")
SPEC = importlib.util.spec_from_file_location("selected_ship_converter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class ConverterAdapterTests(unittest.TestCase):
    def test_mapping_filter_replaces_filename_damage_heuristic(self) -> None:
        core_main = mock.Mock(return_value=7)
        core = types.SimpleNamespace(
            DAMAGE_RE=re.compile(r"(?:crack|patch|dead)", re.IGNORECASE),
            main=core_main,
        )

        with mock.patch.object(ADAPTER, "_load_core", return_value=core):
            result = ADAPTER.main(["manifest.json"])

        self.assertEqual(result, 7)
        core_main.assert_called_once_with(["manifest.json"])
        self.assertIsNone(core.DAMAGE_RE.search("Hull_patch_JoinShape"))
        self.assertIsNone(core.DAMAGE_RE.search("Hull_crack_JoinShape"))


if __name__ == "__main__":
    unittest.main()
