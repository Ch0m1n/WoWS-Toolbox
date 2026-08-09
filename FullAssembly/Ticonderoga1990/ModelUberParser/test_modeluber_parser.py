#!/usr/bin/env python3
"""Regression tests for the Legends v0 ModelUber parser."""

from __future__ import annotations

import json
import math
import os
import unittest
from pathlib import Path

from build_ticon_lod0_manifest import build_manifest
from modeluber_parser import (
    AssetsV0,
    ParseError,
    PrototypeIndex,
    matrix_multiply,
    parse_geometry_sections,
    parse_modeluber,
)


HERE = Path(__file__).resolve().parent
WORK = HERE.parent
FIXTURE_ROOT = Path(
    os.environ.get("WOWS_TOOLBOX_MODELUBER_FIXTURES", HERE.parents[3])
).resolve()
CONTENT = FIXTURE_ROOT / "_hierarchy_tmp" / "content"
GEOMETRY_DIR = (
    FIXTURE_ROOT
    / "full_assembly_probe"
    / "content"
    / "gameplay"
    / "usa"
    / "ship"
    / "cruiser"
    / "ASC307_Ticonderoga_1990"
)
REFERENCE = (
    FIXTURE_ROOT
    / "ticonderoga_assembly_mapping"
    / "ticonderoga_1990_static_assembly.rootcheck.json"
)
BOW_MODEL = (
    "content/gameplay/usa/ship/cruiser/ASC307_Ticonderoga_1990/"
    "ASC307_Ticonderoga_1990_Bow.model"
)
MIDFRONT_MODEL = (
    "content/gameplay/usa/ship/cruiser/ASC307_Ticonderoga_1990/"
    "ASC307_Ticonderoga_1990_MidFront.model"
)
PORTS_MODEL = (
    "content/gameplay/usa/ship/cruiser/ASC307_Ticonderoga_1990/"
    "ASC307_Ticonderoga_1990_MidFront_ports.model"
)
REAL_DATA_AVAILABLE = all(
    path.is_file()
    for path in (
        CONTENT / "assets.bin",
        CONTENT / "prototypes.index.data",
        CONTENT / "prototypes.data",
        GEOMETRY_DIR / "ASC307_Ticonderoga_1990_Bow.geometry",
        REFERENCE,
    )
)


@unittest.skipUnless(
    REAL_DATA_AVAILABLE,
    "real ModelUber fixtures are unavailable; set WOWS_TOOLBOX_MODELUBER_FIXTURES",
)
class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assets = AssetsV0(CONTENT / "assets.bin")
        cls.index = PrototypeIndex(
            CONTENT / "prototypes.index.data",
            CONTENT / "prototypes.data",
        )

    def parse_path(self, path: str) -> tuple[bytes, dict]:
        resource_id = self.assets.resource_id(path)
        self.assertIsNotNone(resource_id)
        assert resource_id is not None
        blob, _ = self.index.blob(resource_id)
        return blob, parse_modeluber(
            blob,
            self.assets,
            resource_id=resource_id,
            resource_path=path,
        )

    def test_bow_lod0_exact_bindings(self) -> None:
        _, parsed = self.parse_path(BOW_MODEL)
        lod0 = parsed["visual_descriptors"][0]
        self.assertEqual(lod0["render_count"], 4)
        self.assertEqual(
            [item["vertices_section"] for item in lod0["render_sets"]],
            [
                "BowShape.vertices",
                "Bow_crack_MidFrontShape.vertices",
                "Bow_crack_MidFront_inShape.vertices",
                "Bow_patch_MidFrontShape.vertices",
            ],
        )
        self.assertTrue(parsed["pointer_bounds_valid"])
        self.assertEqual(parsed["variant_chain_errors"], [])

    def test_optional_zero_material_offsets_for_ports(self) -> None:
        _, parsed = self.parse_path(PORTS_MODEL)
        self.assertEqual(parsed["material_count"], 0)
        self.assertEqual(
            parsed["top_level_offsets"]["raw_header_values"][3:],
            [0, 0, 0],
        )
        self.assertEqual(parsed["visual_descriptors"][0]["render_count"], 0)
        self.assertGreater(parsed["visual_nodes"]["node_count"], 20)

    def test_material_count_is_low_u16_and_flags_are_separate(self) -> None:
        _, parsed = self.parse_path(MIDFRONT_MODEL)
        self.assertEqual(
            [item["property_count"] for item in parsed["material_prototypes"]],
            [11, 2, 11, 11, 3],
        )
        self.assertEqual(
            parsed["material_prototypes"][1]["material_flags"], "0x0001"
        )

    def test_truncated_blob_is_rejected(self) -> None:
        blob, _ = self.parse_path(BOW_MODEL)
        with self.assertRaises(ParseError):
            parse_modeluber(
                blob[:-500],
                self.assets,
                resource_path="intentionally truncated Bow model",
            )

    def test_geometry_section_table_contains_lod0_pair(self) -> None:
        sections = parse_geometry_sections(
            (GEOMETRY_DIR / "ASC307_Ticonderoga_1990_Bow.geometry").read_bytes()
        )
        names = {section.name for section in sections}
        self.assertIn("BowShape.vertices", names)
        self.assertIn("BowShape.indices", names)

    def test_full_manifest_acceptance(self) -> None:
        manifest = build_manifest(
            CONTENT / "assets.bin",
            CONTENT / "prototypes.index.data",
            CONTENT / "prototypes.data",
            GEOMETRY_DIR,
            REFERENCE,
        )
        validation = manifest["validation"]
        self.assertEqual(validation["status"], "PASS")
        self.assertTrue(validation["acceptance"])
        self.assertEqual(validation["models_parsed"], 10)
        self.assertEqual(validation["lod0_render_sets"], 31)
        self.assertEqual(validation["combat_hardpoints"], 17)
        self.assertEqual(validation["unresolved_lod0_bindings"], [])
        # Exercise JSON serialization of every parsed field.
        json.dumps(manifest, ensure_ascii=False)


class PureFunctionTests(unittest.TestCase):
    def test_matrix_multiply_parent_then_local(self) -> None:
        parent = [
            [1.0, 0.0, 0.0, 2.0],
            [0.0, 1.0, 0.0, 3.0],
            [0.0, 0.0, 1.0, 4.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        local = [
            [1.0, 0.0, 0.0, 5.0],
            [0.0, 1.0, 0.0, 6.0],
            [0.0, 0.0, 1.0, 7.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        world = matrix_multiply(parent, local)
        self.assertEqual([world[0][3], world[1][3], world[2][3]], [7, 9, 11])
        self.assertTrue(all(math.isfinite(value) for row in world for value in row))


if __name__ == "__main__":
    unittest.main(verbosity=2)
