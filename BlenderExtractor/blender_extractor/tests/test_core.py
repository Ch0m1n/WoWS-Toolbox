from __future__ import annotations

import binascii
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from legends_assets.core import (
    ExtractionError,
    FormatSupport,
    IdxFormatError,
    UnsafePathError,
    assets_from_index,
    classify_asset,
    extract_asset,
    normalize_virtual_path,
    parse_legends_idx,
    read_asset_bytes,
    safe_output_path,
)
from legends_assets.exporters import run_ship_exporter


ROOT_PARENT = 0xDBB1A1D1B108B927
VOLUME_ID = 0x103478E815D07F7F


def package_container(payload: bytes) -> bytes:
    chunks = [payload[pos : pos + 65536] for pos in range(0, len(payload), 65536)]
    descriptors: list[int] = []
    stored: list[bytes] = []
    for chunk in chunks:
        compressor = zlib.compressobj(level=9, wbits=-15)
        compressed = compressor.compress(chunk) + compressor.flush()
        if len(compressed) < len(chunk):
            data = compressed
            flag = 0x10000
        else:
            data = chunk
            flag = 0
        descriptors.append(flag | (len(data) - 1))
        stored.append(data)
    return (
        struct.pack("<II", 0, len(chunks))
        + struct.pack(f"<{len(descriptors)}I", *descriptors)
        + b"".join(stored)
    )


def make_fixture(root: Path) -> tuple[Path, Path, dict[str, bytes]]:
    package_dir = root / "res_packages"
    package_dir.mkdir()
    idx_path = package_dir / "fixture.idx"
    pkg_path = package_dir / "fixture_0001.pkg"

    payloads = {
        "content/ship/test.geometry": (b"geometry-section-" * 6000),
        "content/ship/test_a.dds": b"DDS " + bytes(range(64)),
    }
    resources = [
        (1, ROOT_PARENT, "content"),
        (2, 1, "ship"),
        (3, 2, "test.geometry"),
        (4, 2, "test_a.dds"),
    ]

    containers: list[bytes] = []
    package_offsets: list[int] = []
    package_cursor = 0
    for virtual_path in payloads:
        container = package_container(payloads[virtual_path])
        package_offsets.append(package_cursor)
        containers.append(container)
        package_cursor += len(container)
    pkg_path.write_bytes(b"".join(containers))

    resource_base = 64
    resource_strings_base = resource_base + len(resources) * 32
    resource_strings = bytearray()
    resource_records = bytearray()
    for index, (resource_id, parent_id, name) in enumerate(resources):
        entry_offset = resource_base + index * 32
        encoded = name.encode("utf-8") + b"\x00"
        string_offset = resource_strings_base + len(resource_strings)
        relative_pointer = string_offset - (entry_offset + 16)
        resource_records.extend(
            struct.pack(
                "<QQQq",
                resource_id,
                parent_id,
                len(encoded),
                relative_pointer,
            )
        )
        resource_strings.extend(encoded)

    file_base = resource_strings_base + len(resource_strings)
    file_records = bytearray()
    for index, (_virtual_path, payload) in enumerate(payloads.items()):
        container = containers[index]
        resource_id = 3 + index
        file_records.extend(
            struct.pack(
                "<QIIIIIIQQ",
                package_offsets[index],
                0,
                len(container),
                binascii.crc32(payload) & 0xFFFFFFFF,
                len(payload),
                5,
                1,
                resource_id,
                VOLUME_ID,
            )
        )

    volume_base = file_base + len(file_records)
    volume_name = b"//.//fixture_0001.pkg\x00"
    volume_record = struct.pack(
        "<QQQq",
        VOLUME_ID,
        0x12345678,
        len(volume_name),
        16,
    )
    header = struct.pack(
        "<4sIIIQQQQQQ",
        b"ISFP",
        0x01010005,
        0xAABBCCDD,
        0x40,
        len(resources),
        resource_base - 16,
        len(payloads),
        file_base - 16,
        1,
        volume_base - 16,
    )
    idx_path.write_bytes(
        header
        + bytes(resource_records)
        + bytes(resource_strings)
        + bytes(file_records)
        + volume_record
        + volume_name
    )
    return idx_path, pkg_path, payloads


class LegendsPackageTests(unittest.TestCase):
    def test_parse_and_verified_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx_path, _, payloads = make_fixture(root)
            parsed = parse_legends_idx(idx_path)
            entries = list(assets_from_index(parsed, root / "res_packages"))
            self.assertEqual(
                [entry.virtual_path for entry in entries],
                list(payloads),
            )

            output = root / "output"
            dry_run = extract_asset(entries[0], output)
            self.assertEqual(dry_run.status, "dry-run")
            self.assertFalse(output.exists())

            for entry in entries:
                result = extract_asset(entry, output, execute=True)
                self.assertEqual(result.status, "extracted")
                self.assertEqual(
                    result.target.read_bytes(),
                    payloads[entry.virtual_path],
                )

    def test_crc_mismatch_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx_path, pkg_path, _ = make_fixture(root)
            parsed = parse_legends_idx(idx_path)
            package = bytearray(pkg_path.read_bytes())
            package[-1] ^= 0xFF
            pkg_path.write_bytes(package)
            with self.assertRaises(ExtractionError):
                # Select the second entry whose container includes the final byte.
                entries = list(assets_from_index(parsed, root / "res_packages"))
                read_asset_bytes(entries[-1])

    def test_wrong_variant_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx_path, _, _ = make_fixture(root)
            data = bytearray(idx_path.read_bytes())
            struct.pack_into("<I", data, 4, 0x02000000)
            idx_path.write_bytes(data)
            with self.assertRaises(IdxFormatError):
                parse_legends_idx(idx_path)

    def test_path_safety(self):
        with self.assertRaises(UnsafePathError):
            normalize_virtual_path("../../outside.obj")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "safe"
            target = safe_output_path(root, "content/ship/test.obj")
            self.assertEqual(target.name, "test.obj")
            with self.assertRaises(UnsafePathError):
                safe_output_path(root, "../escape.obj")

    def test_support_classification_is_truthful(self):
        self.assertEqual(
            classify_asset("ship.glb"), FormatSupport.BLENDER_DIRECT
        )
        self.assertEqual(
            classify_asset("ship.geometry"), FormatSupport.EXTERNAL_CONVERTER
        )
        self.assertEqual(
            classify_asset("ship.visual"), FormatSupport.DESCRIPTOR_ONLY
        )
        self.assertEqual(
            classify_asset("ship.model"), FormatSupport.DESCRIPTOR_ONLY
        )
        self.assertEqual(
            classify_asset("ship.primitives"), FormatSupport.EXTERNAL_CONVERTER
        )
        self.assertEqual(
            classify_asset("ship.dds"), FormatSupport.BLENDER_TEXTURE
        )

    def test_external_ship_exporter_is_explicitly_experimental(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_ship_exporter(
                root,
                "Richelieu",
                root / "richelieu.glb",
                root / "wows-gltf-exporter.exe",
                execute=False,
            )
            self.assertEqual(result["status"], "dry-run")
            self.assertIn("EXPERIMENTAL", result["compatibility"])


if __name__ == "__main__":
    unittest.main()
