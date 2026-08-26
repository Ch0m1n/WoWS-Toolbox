from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
import sys

TOOL_DIR = Path(__file__).resolve().parent
BLENDER_EXTRACTOR_DIR = TOOL_DIR.parent / "blender_extractor"
if str(BLENDER_EXTRACTOR_DIR) not in sys.path:
    sys.path.insert(0, str(BLENDER_EXTRACTOR_DIR))

from legends_assets.core import AssetEntry, ExtractionError, FileInfo  # noqa: E402

from decode_geometry import (  # noqa: E402
    GeometryError,
    MeshPart,
    Vertex,
    VERTEX_LAYOUTS,
    is_intact_part_name,
    orient_vertex_normals,
    parse_vertices,
    part_lod,
    select_intact_parts,
)
from extract_legends_ship import find_ship_index, select_hull_assets  # noqa: E402
from ship_catalog import (  # noqa: E402
    HULL_SUFFIXES,
    MoFormatError,
    NeutralObject,
    _candidate_row,
    _game_params_catalog_rows,
    deduplicate_catalog,
    find_global_mo,
    localize_catalog_rows,
    find_complete_hull_geometries,
    parse_index_identity,
    localized_name_for_code,
    normalize_language,
    read_mo_exact,
)


def asset(virtual_path: str, *, resource_id: int = 1) -> AssetEntry:
    return AssetEntry(
        idx_path=Path("fixture.idx"),
        package_path=Path("fixture.pkg"),
        virtual_path=virtual_path,
        file_info=FileInfo(
            offset=0,
            reserved=0,
            packed_size=16,
            crc32=0,
            unpacked_size=16,
            compression_type_1=5,
            compression_type_2=1,
            resource_id=resource_id,
            volume_id=1,
        ),
    )


def mesh_part(name: str) -> MeshPart:
    return MeshPart(
        name=name,
        vertex_format="fixture",
        vertex_stride=0,
        index_format="list",
        vertices=[],
        triangles=[],
        primitive_groups=[],
    )


def hull_assets(base: str, parent: str | None = None) -> list[AssetEntry]:
    parent = parent or f"content/gameplay/japan/ship/{base}"
    result = [
        asset(f"{parent}/{base}{suffix}.geometry", resource_id=index + 1)
        for index, suffix in enumerate(HULL_SUFFIXES)
    ]
    texture_root = f"content/gameplay/japan/ship/textures/{base}"
    result.extend(
        [
            asset(f"{texture_root}/{base}_a.dds", resource_id=100),
            asset(f"{texture_root}/{base}_DeckHouse_a.dds", resource_id=101),
        ]
    )
    return result


def game_params_ship(model_path: str | None) -> NeutralObject:
    ship = NeutralObject()
    components = {"A_Hull": {"model": model_path}} if model_path is not None else {}
    ship.state = (None, None, components)
    return ship


def mo_fixture(
    entries: list[tuple[bytes, bytes]],
    *,
    byte_order: str = "<",
) -> bytes:
    """Build a minimal GNU MO fixture without gettext tooling."""
    count = len(entries)
    original_table_offset = 28
    translation_table_offset = original_table_offset + count * 8
    original_pool_offset = translation_table_offset + count * 8

    original_blob = bytearray()
    original_descriptors: list[tuple[int, int]] = []
    for original, _translation in entries:
        original_descriptors.append(
            (len(original), original_pool_offset + len(original_blob))
        )
        original_blob.extend(original)
        original_blob.append(0)

    translation_pool_offset = original_pool_offset + len(original_blob)
    translation_blob = bytearray()
    translation_descriptors: list[tuple[int, int]] = []
    for _original, translation in entries:
        translation_descriptors.append(
            (len(translation), translation_pool_offset + len(translation_blob))
        )
        translation_blob.extend(translation)
        translation_blob.append(0)

    header = struct.pack(
        f"{byte_order}7I",
        0x950412DE,
        0,
        count,
        original_table_offset,
        translation_table_offset,
        0,
        0,
    )
    original_table = b"".join(
        struct.pack(f"{byte_order}2I", *descriptor)
        for descriptor in original_descriptors
    )
    translation_table = b"".join(
        struct.pack(f"{byte_order}2I", *descriptor)
        for descriptor in translation_descriptors
    )
    return bytes(
        header + original_table + translation_table + original_blob + translation_blob
    )


class VertexLayoutTests(unittest.TestCase):
    def test_skinned_grid_layout_decodes_connecticut_radar_format(self):
        vertex_format = b"set3/xyznuviiiwwpc"
        header = vertex_format + b"\0" * (64 - len(vertex_format))
        vertex = struct.pack("<3fI2e", 1.0, 2.0, 3.0, 0, 0.25, -0.25)
        vertex += b"\0" * (28 - len(vertex))
        parsed_format, stride, vertices = parse_vertices(
            header + struct.pack("<I", 1) + vertex
        )
        self.assertEqual("set3/xyznuviiiwwpc", parsed_format)
        self.assertEqual(28, stride)
        self.assertEqual(1, len(vertices))
        self.assertTrue(VERTEX_LAYOUTS[parsed_format].is_skinned)
        self.assertEqual((1.0, 3.0, 2.0), vertices[0].position)
        self.assertEqual((0.75, 0.75), vertices[0].uv)

    def test_face_winding_consensus_flips_globally_inverted_normals(self):
        vertices = [
            Vertex((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 0.0)),
            Vertex((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0)),
            Vertex((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0)),
        ]

        repaired, flipped = orient_vertex_normals(vertices, [(0, 1, 2)])

        self.assertTrue(flipped)
        self.assertEqual([(0.0, 0.0, 1.0)] * 3, [v.normal for v in repaired])

    def test_face_winding_consensus_preserves_aligned_normals(self):
        vertices = [
            Vertex((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0)),
            Vertex((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0)),
            Vertex((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0)),
        ]

        repaired, flipped = orient_vertex_normals(vertices, [(0, 1, 2)])

        self.assertFalse(flipped)
        self.assertEqual(vertices, repaired)


class ShipCatalogTests(unittest.TestCase):
    def test_powershell_wrappers_disable_bytecode_cache(self):
        for filename in (
            "List-LegendsShips.ps1",
            "Extract-LegendsShip.ps1",
        ):
            with self.subTest(filename=filename):
                source = (TOOL_DIR / filename).read_text(encoding="utf-8-sig")
                self.assertIn("& $Python -B @arguments", source)
                self.assertNotIn("& $Python @arguments", source)

    def test_t8l_and_update_filename_identity(self):
        identity = parse_index_identity("zupd007_T8L_PJSB018_Yamato_1944.idx")
        self.assertIsNotNone(identity)
        self.assertEqual(identity["tier"], 8)
        self.assertEqual(identity["ship_code"], "PJSB018")
        self.assertEqual(identity["label"], "Yamato 1944")

    def test_multi_variant_idx_is_enumerated_per_complete_hull(self):
        variants = [
            "JSB403_Yamato_StarTrek",
            "JSB015_Yamato_ARP2020",
            "JSB311_Sabaton_Yamato",
            "JSB409_Yamato_NY",
            "JSB039_Yamato_1945",
        ]
        entries = [entry for variant in variants for entry in hull_assets(variant)]
        discovered = find_complete_hull_geometries(entries)
        self.assertEqual(
            {base for _, base, _ in discovered},
            set(variants),
        )
        identity = parse_index_identity("zupd007_T8L_PJSB018_Yamato_1944.idx")
        rows = [
            _candidate_row(
                Path("zupd007_T8L_PJSB018_Yamato_1944.idx"),
                identity,
                hull,
                entries,
                False,
            )
            for hull in discovered
        ]
        self.assertEqual(len(rows), 5)
        self.assertEqual(len({row["id"] for row in rows}), 5)
        self.assertTrue(all(row["selectable"] for row in rows))
        self.assertTrue(
            any(
                row["display_label"] == "Yamato StarTrek [JSB403] — Tier 8"
                for row in rows
            )
        )

    def test_live_game_params_hull_without_legacy_diffuse_is_selectable(self):
        base = "GSB047_Mecklenburg_1945"
        parent = f"content/gameplay/germany/ship/battleship/{base}"
        model_path = f"{parent}/{base}.model"
        entries = [
            entry
            for entry in hull_assets(base, parent)
            if entry.extension == ".geometry"
        ]
        identity = parse_index_identity("zupd127_PGSB610_Mecklenburg.idx")
        package_row = _candidate_row(
            Path("zupd127_PGSB610_Mecklenburg.idx"),
            identity,
            (parent, base, entries),
            entries,
            False,
        )

        rows = _game_params_catalog_rows(
            [package_row],
            {"PGSB610_Mecklenburg": game_params_ship(model_path)},
        )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["selectable"])
        self.assertEqual(rows[0]["support_level"], "full-assembly")
        self.assertEqual(rows[0]["game_params_key"], "PGSB610_Mecklenburg")
        self.assertEqual(rows[0]["model_path"], model_path)

    def test_package_only_hull_is_not_emitted_as_a_live_ship(self):
        base = "ASC003_Albany_1898"
        parent = f"content/gameplay/usa/ship/cruiser/{base}"
        entries = hull_assets(base, parent)
        package_row = _candidate_row(
            Path("PASC003_Albany_1898.idx"),
            parse_index_identity("PASC003_Albany_1898.idx"),
            (parent, base, entries[: len(HULL_SUFFIXES)]),
            entries,
            False,
        )

        rows = _game_params_catalog_rows(
            [package_row],
            {"PASC003_Albany_1898": game_params_ship(None)},
        )

        self.assertEqual(rows, [])

    def test_distinct_ship_keys_sharing_one_live_model_are_preserved(self):
        base = "GSB047_Mecklenburg_1945"
        parent = f"content/gameplay/germany/ship/battleship/{base}"
        model_path = f"{parent}/{base}.model"
        entries = hull_assets(base, parent)
        package_row = _candidate_row(
            Path("zupd127_PGSB610_Mecklenburg.idx"),
            parse_index_identity("zupd127_PGSB610_Mecklenburg.idx"),
            (parent, base, entries[: len(HULL_SUFFIXES)]),
            entries,
            False,
        )

        rows = _game_params_catalog_rows(
            [package_row],
            {
                "PGSB610_Mecklenburg": game_params_ship(model_path),
                "PGSB810_Mecklenburg_GOLDEN": game_params_ship(model_path),
            },
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["game_params_key"] for row in rows},
            {"PGSB610_Mecklenburg", "PGSB810_Mecklenburg_GOLDEN"},
        )

    def test_dedup_prefers_selectable_then_latest_update(self):
        def row(index: str, selectable: bool, update: int) -> dict[str, object]:
            return {
                "id": f"{Path(index).stem}::ASC001_Same",
                "index_filename": index,
                "hull_resource": "ASC001_Same",
                "selectable": selectable,
                "_update_sequence": update,
                "nation": "USA",
                "class": "Cruiser",
                "tier": 1,
                "display_label": "Same",
            }

        result = deduplicate_catalog(
            [
                row("zupd099_T1_PASC001_Same.idx", False, 99),
                row("zupd010_T1_PASC001_Same.idx", True, 10),
                row("zupd011_T1_PASC001_Same.idx", True, 11),
            ]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["index_filename"],
            "zupd011_T1_PASC001_Same.idx",
        )
        self.assertNotIn("_update_sequence", result[0])

    def test_duplicate_resource_basename_is_not_silently_selectable(self):
        base = "ASC001_Duplicate"
        entries = [
            *hull_assets(base, f"content/gameplay/a/ship/{base}"),
            *hull_assets(base, f"content/gameplay/b/ship/{base}"),
        ]
        discovered = find_complete_hull_geometries(entries)
        self.assertEqual(len(discovered), 2)
        identity = parse_index_identity("T1_PASC001_Duplicate.idx")
        rows = [
            _candidate_row(
                Path("T1_PASC001_Duplicate.idx"),
                identity,
                hull,
                entries,
                True,
            )
            for hull in discovered
        ]
        self.assertTrue(all(not row["selectable"] for row in rows))
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual(len({row["output_slug"] for row in rows}), 2)


class MoLocalizationTests(unittest.TestCase):
    def test_exact_reader_ignores_malformed_metadata_and_unrelated_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            mo_path = Path(temp) / "global.mo"
            mo_path.write_bytes(
                mo_fixture(
                    [
                        (b"", b"Content-Type: text/plain; charset=BAD\xff"),
                        (b"IDS_PASB705", "텍사스".encode("utf-8")),
                        (b"IDS_PASB705_FULL", b"SHOULD NOT MATCH"),
                        (b"\xff", b"unrelated invalid key"),
                    ]
                )
            )
            messages = read_mo_exact(mo_path, {"IDS_PASB705"})
            self.assertEqual(messages, {"IDS_PASB705": "텍사스"})

    def test_big_endian_mo_is_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            mo_path = Path(temp) / "global.mo"
            mo_path.write_bytes(
                mo_fixture(
                    [(b"IDS_PJSB018", "大和".encode("utf-8"))],
                    byte_order=">",
                )
            )
            self.assertEqual(
                read_mo_exact(mo_path, ["IDS_PJSB018"]),
                {"IDS_PJSB018": "大和"},
            )

    def test_truncated_and_out_of_range_mo_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            truncated = root / "truncated.mo"
            truncated.write_bytes(b"\xde\x12\x04\x95")
            with self.assertRaisesRegex(MoFormatError, "header is truncated"):
                read_mo_exact(truncated, ["IDS_PASB705"])

            out_of_range = root / "out_of_range.mo"
            out_of_range.write_bytes(
                struct.pack(
                    "<7I",
                    0x950412DE,
                    0,
                    1,
                    0xFFFFFF00,
                    28,
                    0,
                    0,
                )
            )
            with self.assertRaisesRegex(MoFormatError, "range"):
                read_mo_exact(out_of_range, ["IDS_PASB705"])

            missing_nul = root / "missing_nul.mo"
            missing_nul.write_bytes(mo_fixture([(b"IDS_PASB705", b"Texas")])[:-1])
            with self.assertRaisesRegex(MoFormatError, "range|terminating NUL"):
                read_mo_exact(missing_nul, ["IDS_PASB705"])

    def test_exact_selected_translation_must_be_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            mo_path = Path(temp) / "global.mo"
            mo_path.write_bytes(mo_fixture([(b"IDS_PASB705", b"Texas\xff")]))
            with self.assertRaisesRegex(MoFormatError, "is not UTF-8"):
                read_mo_exact(mo_path, ["IDS_PASB705"])

    def test_language_token_fallback_and_preferred_path(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            fallback = game / "texts" / "ko" / "LC_MESSAGES" / "global.mo"
            fallback.parent.mkdir(parents=True)
            fallback.write_bytes(mo_fixture([(b"IDS_TEST", b"fallback")]))

            resolved, language = find_global_mo(game, "KO")
            self.assertEqual(resolved, fallback.resolve())
            self.assertEqual(language, "ko")

            preferred = game / "res" / "texts" / "ko" / "LC_MESSAGES" / "global.mo"
            preferred.parent.mkdir(parents=True)
            preferred.write_bytes(mo_fixture([(b"IDS_TEST", b"preferred")]))
            resolved, _language = find_global_mo(game, "ko")
            self.assertEqual(resolved, preferred.resolve())

            for unsafe in ("../ko", "ko/../../en", "ko\\..\\en", "ko-kr"):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ValueError):
                        normalize_language(unsafe)
            with self.assertRaisesRegex(FileNotFoundError, "global.mo"):
                find_global_mo(game, "ja")

    def test_name_preference_fallback_and_exact_text_preservation(self):
        nbsp_name = "USS\u00a0Texas"
        self.assertEqual(
            localized_name_for_code(
                {
                    "IDS_PASB705": nbsp_name,
                    "IDS_PASB705_FULL": "Fallback",
                },
                "PASB705",
            ),
            nbsp_name,
        )
        self.assertEqual(
            localized_name_for_code(
                {
                    "IDS_PASB705": "!",
                    "IDS_PASB705_FULL": "텍사스 1944",
                },
                "PASB705",
            ),
            "텍사스 1944",
        )
        self.assertIsNone(
            localized_name_for_code(
                {"IDS_PASB705": " ", "IDS_PASB705_FULL": "!"},
                "PASB705",
            )
        )

    def test_multi_hull_rows_share_playable_name_but_keep_variants(self):
        variants = ["JSB403_Yamato_StarTrek", "JSB039_Yamato_1945"]
        entries = [entry for variant in variants for entry in hull_assets(variant)]
        identity = parse_index_identity("zupd007_T8L_PJSB018_Yamato_1944.idx")
        rows = [
            _candidate_row(
                Path("zupd007_T8L_PJSB018_Yamato_1944.idx"),
                identity,
                hull,
                entries,
                False,
            )
            for hull in find_complete_hull_geometries(entries)
        ]
        original_ids = {str(row["id"]) for row in rows}
        localized = localize_catalog_rows(rows, {"IDS_PJSB018": "야마토"}, "ko")
        self.assertEqual({row["display_label"] for row in localized}, {"야마토"})
        self.assertEqual({row["localized_name"] for row in localized}, {"야마토"})
        self.assertEqual({row["ship_code"] for row in localized}, {"PJSB018"})
        self.assertEqual(
            {row["localization_key"] for row in localized},
            {"IDS_PJSB018"},
        )
        self.assertEqual({str(row["id"]) for row in localized}, original_ids)
        self.assertEqual(len({row["variant_label"] for row in localized}), 2)
        self.assertEqual(
            [str(row["variant_label"]).casefold() for row in localized],
            sorted(str(row["variant_label"]).casefold() for row in localized),
        )

    def test_powershell_wrapper_forwards_language(self):
        source = (TOOL_DIR / "List-LegendsShips.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('[string]$Language = "ko"', source)
        self.assertIn('"--language", $Language', source)
        self.assertIn("ValidatePattern", source)


class ExactShipSelectorTests(unittest.TestCase):
    def test_exact_index_basename_and_traversal_rejection(self):
        # Import after the implementation patch so this test also guards the
        # public exact-selector contract used by the GUI.
        from extract_legends_ship import find_ship_index_file

        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            package_dir = game / "res_packages"
            package_dir.mkdir()
            exact = package_dir / "zupd601_PXSD307_Ticonderoga_1990.idx"
            exact.write_bytes(b"fixture")
            similar = package_dir / "zupd601_PXSD307_Ticonderoga_1990_extra.idx"
            similar.write_bytes(b"fixture")
            self.assertEqual(
                find_ship_index_file(game, exact.name),
                exact,
            )
            with self.assertRaises(ExtractionError):
                find_ship_index_file(game, f"..\\{exact.name}")

    def test_exact_resource_resolves_one_variant(self):
        first = "JSB403_Yamato_StarTrek"
        second = "JSB039_Yamato_1945"
        entries = [*hull_assets(first), *hull_assets(second)]
        with self.assertRaises(ExtractionError):
            select_hull_assets(entries)
        base, geometry, diffuse = select_hull_assets(entries, second)
        self.assertEqual(base, second)
        self.assertEqual(len(geometry), 5)
        self.assertEqual(len(diffuse), 2)
        with self.assertRaises(ExtractionError):
            select_hull_assets(entries, "JSB999_NotPresent")

    def test_hull_named_base_diffuse_is_supported(self):
        base = "JSB039_Yamato_1945"
        parent = f"content/gameplay/japan/ship/battleship/{base}"
        entries = [
            asset(
                f"{parent}/{base}{suffix}.geometry",
                resource_id=index + 1,
            )
            for index, suffix in enumerate(HULL_SUFFIXES)
        ]
        texture_root = "content/gameplay/japan/ship/battleship/textures"
        entries.extend(
            [
                asset(f"{texture_root}/{base}_Hull_a.dds", resource_id=100),
                asset(f"{texture_root}/{base}_DeckHouse_a.dds", resource_id=101),
            ]
        )
        _, _, diffuse = select_hull_assets(entries, base)
        self.assertTrue(diffuse[0].virtual_path.endswith("_Hull_a.dds"))

    def test_duplicate_resource_parent_remains_ambiguous(self):
        base = "ASC001_Duplicate"
        entries = [
            *hull_assets(base, f"content/gameplay/a/ship/{base}"),
            *hull_assets(base, f"content/gameplay/b/ship/{base}"),
        ]
        with self.assertRaises(ExtractionError):
            select_hull_assets(entries, base)

    def test_legacy_substring_selector_still_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            package_dir = game / "res_packages"
            package_dir.mkdir()
            (package_dir / "T8_PJSB018_Yamato.idx").touch()
            (package_dir / "T8_PJSB918_Yamato_Black.idx").touch()
            with self.assertRaises(ExtractionError):
                find_ship_index(game, ["Yamato"])


class GeometrySelectionTests(unittest.TestCase):
    def test_part_lod_accepts_both_legends_naming_orders(self):
        cases = {
            "Bow_lodShape1": 1,
            "MidFront_lod1Shape": 1,
            "Hull_LODSHAPE3": 3,
            "MidBack_lod2Shape": 2,
            "MidFrontShape": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(part_lod(name), expected)

    def test_burke_joint_parts_keep_only_intact_exterior_surfaces(self):
        names = [
            "MidFrontShape",
            "MidFront_patch_MidBackShape",
            "MidFront_patch_MidBack_DeckHouseShape",
            "MidFront_patch_MidBack_wireShape",
            "MidFront_crack_MidBack_DeckHouseShape",
            "MidFront_crack_MidBack_HullShape",
            "MidFront_crack_MidBackShape",
            "MidFront_crack_MidBack_inShape",
            "MidFront_crack_MidBack_wireShape",
            "MidFront_deadShape",
            "MidFront_crack_MidBack_DeckHouse_deadShape",
            "MidFront_crack_MidBack_DeckHouse_lodShape1",
        ]
        selected, selected_lod, fallback_used = select_intact_parts(
            [mesh_part(name) for name in names], intact_lod=0
        )
        self.assertEqual(selected_lod, 0)
        self.assertFalse(fallback_used)
        self.assertEqual(
            {part.name for part in selected},
            {
                "MidFrontShape",
                "MidFront_patch_MidBackShape",
                "MidFront_patch_MidBack_DeckHouseShape",
                "MidFront_patch_MidBack_wireShape",
                "MidFront_crack_MidBack_DeckHouseShape",
                "MidFront_crack_MidBack_HullShape",
            },
        )

    def test_intact_name_filter_handles_lod_exterior_and_damage_cases(self):
        intact_names = (
            "MidFront_patch_MidBackShape",
            "MidFront_crack_MidBack_DeckHouseShape",
            "MidFront_crack_MidBack_DeckHouse_lod1Shape",
            "MidFront_crack_MidBack_Hull_lodShape1",
        )
        damage_names = (
            "MidFront_crack_MidBackShape",
            "MidFront_crack_MidBack_inShape",
            "MidFront_crack_MidBack_wireShape",
            "MidFront_deadShape",
            "MidFront_crack_MidBack_DeckHouse_deadShape",
        )
        for name in intact_names:
            with self.subTest(name=name):
                self.assertTrue(is_intact_part_name(name))
        for name in damage_names:
            with self.subTest(name=name):
                self.assertFalse(is_intact_part_name(name))

    def test_bow_lodshape_variants_are_not_duplicated_in_lod_zero(self):
        parts = [
            mesh_part("BowShape"),
            mesh_part("Bow_lodShape1"),
            mesh_part("Bow_lod1Shape"),
        ]
        selected, selected_lod, fallback_used = select_intact_parts(parts, intact_lod=0)
        self.assertEqual([part.name for part in selected], ["BowShape"])
        self.assertEqual(selected_lod, 0)
        self.assertFalse(fallback_used)

    def test_numbered_lod_fallback_behavior_is_preserved(self):
        parts = [
            mesh_part("Bow_lodShape2"),
            mesh_part("Bow_lod3Shape"),
            mesh_part("Bow_deadShape"),
        ]
        selected, selected_lod, fallback_used = select_intact_parts(parts, intact_lod=0)
        self.assertEqual([part.name for part in selected], ["Bow_lodShape2"])
        self.assertEqual(selected_lod, 2)
        self.assertTrue(fallback_used)

    def test_all_parts_mode_still_bypasses_intact_filter(self):
        parts = [
            mesh_part("MidFront_crack_MidBackShape"),
            mesh_part("MidFront_patch_MidBackShape"),
            mesh_part("MidFront_deadShape"),
        ]
        selected, selected_lod, fallback_used = select_intact_parts(
            parts, intact_lod=None
        )
        self.assertEqual(selected, parts)
        self.assertIsNone(selected_lod)
        self.assertFalse(fallback_used)

    def test_damage_only_input_still_fails_closed(self):
        parts = [
            mesh_part("MidFront_crack_MidBackShape"),
            mesh_part("MidFront_deadShape"),
        ]
        with self.assertRaises(GeometryError):
            select_intact_parts(parts, intact_lod=0)


if __name__ == "__main__":
    unittest.main()
