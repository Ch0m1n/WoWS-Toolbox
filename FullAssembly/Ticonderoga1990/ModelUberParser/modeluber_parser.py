#!/usr/bin/env python3
"""Bounds-checked parser for Legends v0 ModelUber prototype sidecars.

The Steam build inspected on 2026-08-03 stores ModelUber records outside
``assets.bin`` in ``prototypes.data`` / ``prototypes.index.data``.  This module
parses those records without importing or executing game code.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


class ParseError(ValueError):
    """Raised when an input is truncated or violates a verified invariant."""


def hex32(value: int) -> str:
    return f"0x{value:08X}"


def hex64(value: int) -> str:
    return f"0x{value:016X}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CheckedReader:
    """Little-endian reader that labels every failed bounds check."""

    def __init__(self, data: bytes, label: str) -> None:
        self.data = data
        self.label = label

    def ensure(self, offset: int, size: int, what: str) -> None:
        if offset < 0 or size < 0 or offset > len(self.data) - size:
            raise ParseError(
                f"{self.label}: {what} outside input "
                f"(offset=0x{offset:X}, size={size}, input={len(self.data)})"
            )

    def unpack(self, fmt: str, offset: int, what: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        self.ensure(offset, size, what)
        return struct.unpack_from(fmt, self.data, offset)

    def u8(self, offset: int, what: str) -> int:
        self.ensure(offset, 1, what)
        return self.data[offset]

    def u16(self, offset: int, what: str) -> int:
        return self.unpack("<H", offset, what)[0]

    def u32(self, offset: int, what: str) -> int:
        return self.unpack("<I", offset, what)[0]

    def i32(self, offset: int, what: str) -> int:
        return self.unpack("<i", offset, what)[0]

    def u64(self, offset: int, what: str) -> int:
        return self.unpack("<Q", offset, what)[0]

    def i64(self, offset: int, what: str) -> int:
        return self.unpack("<q", offset, what)[0]

    def f32(self, offset: int, what: str) -> float:
        return self.unpack("<f", offset, what)[0]

    def bytes(self, offset: int, size: int, what: str) -> bytes:
        self.ensure(offset, size, what)
        return self.data[offset : offset + size]


@dataclass(frozen=True)
class PrototypeLocation:
    index: int
    data_offset: int
    size: int
    trailing_u32: int


class PrototypeIndex:
    """Reader for ``prototypes.index.data`` and its paired data file."""

    VALUE_STRIDE = 12

    def __init__(self, index_path: Path, data_path: Path) -> None:
        self.index_path = index_path
        self.data_path = data_path
        raw = index_path.read_bytes()
        reader = CheckedReader(raw, str(index_path))
        reader.ensure(0, 24, "prototype index header")
        self.count = reader.u32(0, "prototype count")
        self.seed_or_checksum = reader.u32(4, "prototype index seed/checksum")
        self.values_offset = reader.u64(8, "values offset")
        self.keys_offset = reader.u64(16, "keys offset")
        if self.count > 10_000_000:
            raise ParseError(f"unreasonable prototype count {self.count}")
        reader.ensure(
            self.values_offset,
            self.count * self.VALUE_STRIDE,
            "prototype values",
        )
        reader.ensure(self.keys_offset, self.count * 8, "prototype keys")
        self.keys = list(
            reader.unpack(
                f"<{self.count}Q",
                self.keys_offset,
                "prototype key array",
            )
        )
        if any(left >= right for left, right in zip(self.keys, self.keys[1:])):
            raise ParseError("prototype keys are not strictly increasing")
        self.values = [
            reader.unpack(
                "<QI",
                self.values_offset + index * self.VALUE_STRIDE,
                f"prototype value {index}",
            )
            for index in range(self.count)
        ]
        self.data = data_path.read_bytes()

    def lookup(self, resource_id: int) -> PrototypeLocation | None:
        index = bisect.bisect_left(self.keys, resource_id)
        if index >= self.count or self.keys[index] != resource_id:
            return None
        packed, trailing_u32 = self.values[index]
        offset = packed >> 32
        size = packed & 0xFFFFFFFF
        if offset > len(self.data) - size:
            raise ParseError(
                f"prototype {hex64(resource_id)} outside data file "
                f"(offset={offset}, size={size}, input={len(self.data)})"
            )
        return PrototypeLocation(index, offset, size, trailing_u32)

    def blob(self, resource_id: int) -> tuple[bytes, PrototypeLocation]:
        location = self.lookup(resource_id)
        if location is None:
            raise ParseError(f"prototype not found for {hex64(resource_id)}")
        start = location.data_offset
        return self.data[start : start + location.size], location


class AssetsV0:
    """Resolve Legends ``assets.bin`` v0 path and string IDs."""

    MAGIC = b"BDWB"
    VERSION = 0x01000000
    PATH_ENTRY_STRIDE = 32

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        reader = CheckedReader(self.data, str(path))
        reader.ensure(0, 0x60, "assets v0 header")
        if self.data[:4] != self.MAGIC:
            raise ParseError("assets.bin has no BDWB magic")
        version = reader.u32(4, "assets version")
        if version != self.VERSION:
            raise ParseError(
                f"assets.bin version {hex32(version)} is not Legends v0"
            )

        self.paths_base = 0x28
        self.paths_count = reader.u32(self.paths_base, "path count")
        self.paths_offset = self.paths_base + reader.i64(
            self.paths_base + 8, "path array relative pointer"
        )
        if self.paths_count > 10_000_000:
            raise ParseError(f"unreasonable assets path count {self.paths_count}")
        reader.ensure(
            self.paths_offset,
            self.paths_count * self.PATH_ENTRY_STRIDE,
            "assets path entries",
        )

        self.strings_base = 0x38
        self.string_capacity = reader.u32(
            self.strings_base, "string hash capacity"
        )
        if not 0 < self.string_capacity <= 10_000_000:
            raise ParseError(
                f"unreasonable string hash capacity {self.string_capacity}"
            )
        self.string_buckets = self.strings_base + reader.i64(
            self.strings_base + 8, "string bucket relative pointer"
        )
        self.string_values = self.strings_base + reader.i64(
            self.strings_base + 16, "string value relative pointer"
        )
        self.string_data_size = reader.u32(
            self.strings_base + 24, "string data size"
        )
        self.string_data = self.strings_base + reader.i64(
            self.strings_base + 32, "string data relative pointer"
        )
        reader.ensure(
            self.string_buckets,
            self.string_capacity * 8,
            "string hash buckets",
        )
        reader.ensure(
            self.string_values,
            self.string_capacity * 4,
            "string hash values",
        )
        reader.ensure(self.string_data, self.string_data_size, "string data")

        self.entries: dict[int, tuple[int, str]] = {}
        self.id_by_path: dict[str, int] = {}
        self._path_cache: dict[int, str] = {}
        for index in range(self.paths_count):
            base = self.paths_offset + index * self.PATH_ENTRY_STRIDE
            self_id = reader.u64(base, f"path {index} self id")
            parent_id = reader.u64(base + 8, f"path {index} parent id")
            name_size = reader.u32(base + 16, f"path {index} name size")
            name_anchor = base + 16
            name_offset = name_anchor + reader.i64(
                base + 24, f"path {index} name relative pointer"
            )
            raw = reader.bytes(name_offset, name_size, f"path {index} name")
            if raw.endswith(b"\0"):
                raw = raw[:-1]
            name = raw.decode("utf-8", errors="strict")
            self.entries[self_id] = (parent_id, name)

        for resource_id in self.entries:
            resolved = self.resolve_resource(resource_id)
            if resolved is not None:
                self.id_by_path.setdefault(resolved.lower(), resource_id)

    def get_string(self, string_id: int) -> str | None:
        reader = CheckedReader(self.data, str(self.path))
        slot = string_id % self.string_capacity
        for _ in range(self.string_capacity):
            bucket = self.string_buckets + slot * 8
            key = reader.u32(bucket, "string bucket key")
            sentinel = reader.u32(bucket + 4, "string bucket sentinel")
            if key == 0 and sentinel == 0:
                return None
            if key == string_id:
                offset = reader.u32(
                    self.string_values + slot * 4, "string value offset"
                )
                if offset >= self.string_data_size:
                    raise ParseError(
                        f"string {hex32(string_id)} offset {offset} outside data"
                    )
                start = self.string_data + offset
                end_limit = self.string_data + self.string_data_size
                end = self.data.find(b"\0", start, end_limit)
                if end < 0:
                    end = end_limit
                return self.data[start:end].decode("utf-8", errors="strict")
            slot = (slot + 1) % self.string_capacity
        return None

    def resolve_resource(self, resource_id: int) -> str | None:
        if resource_id == 0:
            return None
        cached = self._path_cache.get(resource_id)
        if cached is not None:
            return cached
        parts: list[str] = []
        current = resource_id
        visited: set[int] = set()
        while current:
            if current in visited:
                raise ParseError(f"path parent cycle at {hex64(current)}")
            visited.add(current)
            entry = self.entries.get(current)
            if entry is None:
                return None
            parent_id, name = entry
            if name:
                parts.append(name)
            current = parent_id
        resolved = "/".join(reversed(parts))
        self._path_cache[resource_id] = resolved
        return resolved

    def resource_id(self, path: str) -> int | None:
        return self.id_by_path.get(path.replace("\\", "/").lower())


def rows_from_column_major(values: Sequence[float]) -> list[list[float]]:
    if len(values) != 16:
        raise ParseError("matrix must contain 16 values")
    return [
        [float(values[column * 4 + row]) for column in range(4)]
        for row in range(4)
    ]


def column_major_from_rows(rows: Sequence[Sequence[float]]) -> list[float]:
    if len(rows) != 4 or any(len(row) != 4 for row in rows):
        raise ParseError("matrix must contain four rows of four values")
    return [float(rows[row][column]) for column in range(4) for row in range(4)]


def matrix_multiply(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> list[list[float]]:
    return [
        [
            sum(float(left[row][inner]) * float(right[inner][column]) for inner in range(4))
            for column in range(4)
        ]
        for row in range(4)
    ]


def determinant3(rows: Sequence[Sequence[float]]) -> float:
    return (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )


def matrix_record(rows: Sequence[Sequence[float]]) -> dict[str, Any]:
    column_major = column_major_from_rows(rows)
    return {
        "storage": "column-major",
        "column_major": column_major,
        "rows": [[float(value) for value in row] for row in rows],
        "translation_xyz": column_major[12:15],
        "rotation_scale_determinant": determinant3(rows),
        "finite": all(math.isfinite(value) for value in column_major),
    }


def _checked_count(count: int, label: str, maximum: int = 1_000_000) -> None:
    if count > maximum:
        raise ParseError(f"unreasonable {label} {count}")


def parse_visual_nodes(
    reader: CheckedReader, assets: AssetsV0, base: int = 0x38
) -> dict[str, Any]:
    reader.ensure(base, 0x30, "VisualNodes header")
    count = reader.u32(base, "VisualNodes count")
    _checked_count(count, "VisualNodes count")
    relptrs = [
        reader.i64(base + 8 + index * 8, f"VisualNodes pointer {index}")
        for index in range(5)
    ]
    offsets = [base + relative for relative in relptrs]
    labels_and_sizes = [
        ("name-map name IDs", count * 4),
        ("name-map node IDs", count * 2),
        ("node name IDs", count * 4),
        ("node matrices", count * 16 * 4),
        ("node parent IDs", count * 2),
    ]
    for offset, (label, size) in zip(offsets, labels_and_sizes):
        reader.ensure(offset, size, f"VisualNodes {label}")

    name_map_name_ids = [
        reader.u32(offsets[0] + index * 4, f"name-map name ID {index}")
        for index in range(count)
    ]
    name_map_node_ids = [
        reader.u16(offsets[1] + index * 2, f"name-map node ID {index}")
        for index in range(count)
    ]
    name_ids = [
        reader.u32(offsets[2] + index * 4, f"node name ID {index}")
        for index in range(count)
    ]
    parents = [
        reader.u16(offsets[4] + index * 2, f"node parent ID {index}")
        for index in range(count)
    ]
    local_rows: list[list[list[float]]] = []
    for node_index in range(count):
        values = list(
            reader.unpack(
                "<16f",
                offsets[3] + node_index * 64,
                f"node matrix {node_index}",
            )
        )
        if not all(math.isfinite(value) for value in values):
            raise ParseError(f"node {node_index} matrix has non-finite values")
        local_rows.append(rows_from_column_major(values))

    for index, mapped_node in enumerate(name_map_node_ids):
        if mapped_node >= count:
            raise ParseError(
                f"name map {index} points to node {mapped_node}, count={count}"
            )
    for index, parent in enumerate(parents):
        if parent != 0xFFFF and parent >= count:
            raise ParseError(
                f"node {index} parent {parent} outside node count {count}"
            )

    world_cache: dict[int, list[list[float]]] = {}

    def world(index: int, active: set[int] | None = None) -> list[list[float]]:
        cached = world_cache.get(index)
        if cached is not None:
            return cached
        if active is None:
            active = set()
        if index in active:
            raise ParseError(f"VisualNodes parent cycle at node {index}")
        active.add(index)
        parent = parents[index]
        if parent == 0xFFFF:
            result = local_rows[index]
        else:
            result = matrix_multiply(world(parent, active), local_rows[index])
        active.remove(index)
        world_cache[index] = result
        return result

    nodes = []
    for index in range(count):
        name_id = name_ids[index]
        nodes.append(
            {
                "index": index,
                "name_id": hex32(name_id),
                "name": assets.get_string(name_id),
                "parent_index": parents[index],
                "local_matrix": matrix_record(local_rows[index]),
                "world_matrix": matrix_record(world(index)),
            }
        )

    arrays_end = max(
        offset + size for offset, (_, size) in zip(offsets, labels_and_sizes)
    )
    return {
        "base_offset": base,
        "node_count": count,
        "relative_pointers": relptrs,
        "absolute_offsets": offsets,
        "arrays_end": arrays_end,
        "name_map": [
            {
                "name_id": hex32(name_id),
                "name": assets.get_string(name_id),
                "node_index": node_id,
            }
            for name_id, node_id in zip(name_map_name_ids, name_map_node_ids)
        ],
        "nodes": nodes,
    }


MATERIAL_VALUE_TYPES = {
    0: "bool",
    1: "int",
    2: "float",
    3: "texture",
    4: "vector3",
    5: "vector2",
    6: "matrix4",
    7: "vector4",
}


def parse_material_prototype(
    reader: CheckedReader, base: int, assets: AssetsV0
) -> dict[str, Any]:
    reader.ensure(base, 0x70, "material prototype header")
    fx_path_id = reader.u64(base, "material shader path ID")
    # The low 16 bits are the property count. The high 16 bits carry
    # material flags for some shaders (for example glass/grid).
    property_count = reader.u16(base + 0x0C, "material property count")
    material_flags = reader.u16(base + 0x0E, "material flags")
    _checked_count(property_count, "material property count", maximum=65_536)
    names_offset = base + reader.i64(
        base + 0x18, "material property-name relative pointer"
    )
    codes_offset = base + reader.i64(
        base + 0x20, "material property-code relative pointer"
    )
    value_offsets = {
        value_type: base
        + reader.i64(
            base + 0x28 + value_type * 8,
            f"material {MATERIAL_VALUE_TYPES[value_type]} relative pointer",
        )
        for value_type in range(8)
    }
    reader.ensure(names_offset, property_count * 4, "material property names")
    reader.ensure(codes_offset, property_count, "material property codes")

    properties = []
    for index in range(property_count):
        name_id = reader.u32(
            names_offset + index * 4, f"material property {index} name"
        )
        code = reader.u8(
            codes_offset + index, f"material property {index} type/index"
        )
        value_type = code & 7
        value_index = code >> 3
        value_base = value_offsets[value_type]
        if value_type == 0:
            value: Any = bool(
                reader.u8(
                    value_base + value_index, f"material bool {value_index}"
                )
            )
        elif value_type == 1:
            value = reader.i32(
                value_base + value_index * 4, f"material int {value_index}"
            )
        elif value_type == 2:
            value = reader.f32(
                value_base + value_index * 4, f"material float {value_index}"
            )
        elif value_type == 3:
            texture_id = reader.u64(
                value_base + value_index * 8,
                f"material texture {value_index}",
            )
            value = {
                "resource_id": hex64(texture_id),
                "path": assets.resolve_resource(texture_id),
            }
        elif value_type == 4:
            value = list(
                reader.unpack(
                    "<3f",
                    value_base + value_index * 12,
                    f"material vector3 {value_index}",
                )
            )
        elif value_type == 5:
            value = list(
                reader.unpack(
                    "<2f",
                    value_base + value_index * 8,
                    f"material vector2 {value_index}",
                )
            )
        elif value_type == 6:
            value = list(
                reader.unpack(
                    "<16f",
                    value_base + value_index * 64,
                    f"material matrix4 {value_index}",
                )
            )
        else:
            value = list(
                reader.unpack(
                    "<4f",
                    value_base + value_index * 16,
                    f"material vector4 {value_index}",
                )
            )
        properties.append(
            {
                "name_id": hex32(name_id),
                "name": assets.get_string(name_id),
                "encoded_type_index": f"0x{code:02X}",
                "type": MATERIAL_VALUE_TYPES[value_type],
                "value_index": value_index,
                "value": value,
            }
        )

    return {
        "header_offset": base,
        "fx_path_id": hex64(fx_path_id),
        "fx_path": assets.resolve_resource(fx_path_id),
        "property_count": property_count,
        "material_flags": f"0x{material_flags:04X}",
        "properties": properties,
    }


def _strict_top_level_offsets(
    offsets: Sequence[int], blob_size: int
) -> None:
    if any(offset < 0 or offset > blob_size for offset in offsets):
        raise ParseError(
            f"ModelUber top-level offset outside blob: {offsets}, size={blob_size}"
        )
    if list(offsets) != sorted(offsets):
        raise ParseError(f"ModelUber top-level offsets are not monotonic: {offsets}")


def parse_modeluber(
    blob: bytes,
    assets: AssetsV0,
    *,
    resource_id: int | None = None,
    resource_path: str | None = None,
) -> dict[str, Any]:
    """Parse one externalized Legends ModelUber record.

    Top-level pointer values are offsets from the start of the prototype blob.
    Nested render/palette/material pointers are signed offsets from their
    containing record.
    """

    reader = CheckedReader(blob, resource_path or "ModelUber blob")
    reader.ensure(0, 0x68, "ModelUber fixed header and VisualNodes header")
    lod_count = reader.u32(0, "LOD/variant count")
    materials_count = reader.u32(4, "material count")
    _checked_count(lod_count, "LOD count", maximum=65_536)
    _checked_count(materials_count, "material count", maximum=65_536)
    raw_offsets = [
        reader.u64(8 + index * 8, f"ModelUber top-level pointer {index}")
        for index in range(6)
    ]
    # Port-only records have no material arrays and store zero in the final
    # three optional slots. Their effective empty arrays start after visuals.
    offsets = list(raw_offsets)
    if materials_count == 0 and offsets[3:] == [0, 0, 0]:
        empty_material_offset = offsets[2] + lod_count * 0x30
        offsets[3:] = [empty_material_offset] * 3
    _strict_top_level_offsets(offsets, len(blob))
    (
        variants_offset,
        model_ids_offset,
        visuals_offset,
        material_mfm_ids_offset,
        material_name_ids_offset,
        material_prototypes_offset,
    ) = offsets

    visual_nodes = parse_visual_nodes(reader, assets)
    if visual_nodes["arrays_end"] > variants_offset:
        raise ParseError(
            "VisualNodes arrays overlap ModelUber variant records "
            f"({visual_nodes['arrays_end']} > {variants_offset})"
        )

    variant_end = variants_offset + lod_count * 0x48
    reader.ensure(variants_offset, lod_count * 0x48, "variant records")
    if variant_end != model_ids_offset:
        raise ParseError(
            f"variant records end at 0x{variant_end:X}, "
            f"model IDs begin at 0x{model_ids_offset:X}"
        )
    model_ids_end = model_ids_offset + lod_count * 8
    reader.ensure(model_ids_offset, lod_count * 8, "model ID array")
    if model_ids_end != visuals_offset:
        raise ParseError(
            f"model IDs end at 0x{model_ids_end:X}, "
            f"visual descriptors begin at 0x{visuals_offset:X}"
        )
    visual_headers_end = visuals_offset + lod_count * 0x30
    reader.ensure(visuals_offset, lod_count * 0x30, "visual descriptors")
    if visual_headers_end > material_mfm_ids_offset:
        raise ParseError("visual descriptor headers overlap material table")
    mfm_ids_end = material_mfm_ids_offset + materials_count * 8
    reader.ensure(
        material_mfm_ids_offset,
        materials_count * 8,
        "material MFM ID array",
    )
    if mfm_ids_end != material_name_ids_offset:
        raise ParseError(
            f"MFM IDs end at 0x{mfm_ids_end:X}, "
            f"material names begin at 0x{material_name_ids_offset:X}"
        )
    material_names_end = material_name_ids_offset + materials_count * 4
    reader.ensure(
        material_name_ids_offset,
        materials_count * 4,
        "material name ID array",
    )
    if material_names_end != material_prototypes_offset:
        raise ParseError(
            f"material names end at 0x{material_names_end:X}, "
            f"material prototypes begin at 0x{material_prototypes_offset:X}"
        )

    variants = []
    for index in range(lod_count):
        base = variants_offset + index * 0x48
        next_model_id = reader.u64(base, f"variant {index} next model ID")
        visual_id = reader.u64(base + 8, f"variant {index} visual ID")
        variants.append(
            {
                "index": index,
                "header_offset": base,
                "next_lod_model_id": hex64(next_model_id),
                "next_lod_model_path": assets.resolve_resource(next_model_id),
                "visual_resource_id": hex64(visual_id),
                "visual_resource_path": assets.resolve_resource(visual_id),
                "extent": reader.f32(base + 0x10, f"variant {index} extent"),
                "raw_u32_14": hex32(
                    reader.u32(base + 0x14, f"variant {index} u32@0x14")
                ),
                "raw_tail_hex": reader.bytes(
                    base + 0x18, 0x30, f"variant {index} raw tail"
                ).hex(),
            }
        )

    model_ids = [
        reader.u64(model_ids_offset + index * 8, f"model ID {index}")
        for index in range(lod_count)
    ]
    model_id_records = [
        {
            "index": index,
            "resource_id": hex64(model_id),
            "path": assets.resolve_resource(model_id),
        }
        for index, model_id in enumerate(model_ids)
    ]
    chain_errors: list[str] = []
    if resource_id is not None and model_ids and model_ids[0] != resource_id:
        chain_errors.append(
            f"model_ids[0]={hex64(model_ids[0])} != resource={hex64(resource_id)}"
        )
    for index in range(max(0, lod_count - 1)):
        expected = model_ids[index + 1]
        actual = int(variants[index]["next_lod_model_id"], 16)
        if actual != expected:
            chain_errors.append(
                f"variant[{index}].next={hex64(actual)} != "
                f"model_ids[{index + 1}]={hex64(expected)}"
            )
    if variants and int(variants[-1]["next_lod_model_id"], 16) != 0:
        chain_errors.append("last variant next_lod_model_id is non-zero")

    nodes_by_name: dict[str, list[dict[str, Any]]] = {}
    for node in visual_nodes["nodes"]:
        name = node["name"]
        if name is not None:
            nodes_by_name.setdefault(name, []).append(node)

    visual_descriptors = []
    referenced_ranges: list[tuple[int, int, str]] = []
    for lod_index in range(lod_count):
        base = visuals_offset + lod_index * 0x30
        bounds = list(reader.unpack("<6f", base, f"visual {lod_index} bounds"))
        if not all(math.isfinite(value) for value in bounds):
            raise ParseError(f"visual {lod_index} has non-finite bounds")
        geometry_id = reader.u64(
            base + 0x18, f"visual {lod_index} geometry path ID"
        )
        render_count = reader.u32(
            base + 0x20, f"visual {lod_index} render count"
        )
        _checked_count(render_count, f"visual {lod_index} render count")
        descriptor_pad = reader.u32(
            base + 0x24, f"visual {lod_index} descriptor padding"
        )
        render_relative = reader.i64(
            base + 0x28, f"visual {lod_index} render relative pointer"
        )
        if descriptor_pad != 0:
            raise ParseError(
                f"visual {lod_index} descriptor padding is {descriptor_pad}"
            )
        render_offset = base + render_relative if render_count else None
        if render_count:
            assert render_offset is not None
            reader.ensure(
                render_offset,
                render_count * 0x20,
                f"visual {lod_index} render records",
            )
            if render_offset < visual_headers_end:
                raise ParseError(
                    f"visual {lod_index} renders overlap descriptor headers"
                )
            if render_offset + render_count * 0x20 > material_mfm_ids_offset:
                raise ParseError(
                    f"visual {lod_index} renders overlap material tables"
                )
            referenced_ranges.append(
                (
                    render_offset,
                    render_offset + render_count * 0x20,
                    f"lod{lod_index}.renders",
                )
            )

        render_sets = []
        for render_index in range(render_count):
            assert render_offset is not None
            render_base = render_offset + render_index * 0x20
            mfm_id = reader.u64(
                render_base, f"LOD {lod_index} render {render_index} MFM ID"
            )
            material_name_id = reader.u32(
                render_base + 8,
                f"LOD {lod_index} render {render_index} material name ID",
            )
            vertices_id = reader.u32(
                render_base + 0x0C,
                f"LOD {lod_index} render {render_index} vertices ID",
            )
            indices_id = reader.u32(
                render_base + 0x10,
                f"LOD {lod_index} render {render_index} indices ID",
            )
            skinned = bool(
                reader.u8(
                    render_base + 0x14,
                    f"LOD {lod_index} render {render_index} skinned",
                )
            )
            palette_count = reader.u8(
                render_base + 0x15,
                f"LOD {lod_index} render {render_index} palette count",
            )
            padding = reader.bytes(
                render_base + 0x16,
                2,
                f"LOD {lod_index} render {render_index} padding",
            )
            if padding != b"\0\0":
                raise ParseError(
                    f"LOD {lod_index} render {render_index} has "
                    f"non-zero 2-byte padding {padding.hex()}"
                )
            palette_relative = reader.i64(
                render_base + 0x18,
                f"LOD {lod_index} render {render_index} palette relative pointer",
            )
            palette_offset = (
                render_base + palette_relative if palette_count else None
            )
            palette_ids: list[int] = []
            if palette_count:
                assert palette_offset is not None
                reader.ensure(
                    palette_offset,
                    palette_count * 4,
                    f"LOD {lod_index} render {render_index} palette",
                )
                if palette_offset < visual_headers_end:
                    raise ParseError(
                        f"LOD {lod_index} render {render_index} palette "
                        "overlaps descriptor headers"
                    )
                if palette_offset + palette_count * 4 > material_mfm_ids_offset:
                    raise ParseError(
                        f"LOD {lod_index} render {render_index} palette "
                        "overlaps material tables"
                    )
                palette_ids = [
                    reader.u32(
                        palette_offset + palette_index * 4,
                        f"LOD {lod_index} render {render_index} "
                        f"palette item {palette_index}",
                    )
                    for palette_index in range(palette_count)
                ]
                referenced_ranges.append(
                    (
                        palette_offset,
                        palette_offset + palette_count * 4,
                        f"lod{lod_index}.render{render_index}.palette",
                    )
                )

            palette = []
            for name_id in palette_ids:
                name = assets.get_string(name_id)
                matches = nodes_by_name.get(name or "", [])
                palette.append(
                    {
                        "name_id": hex32(name_id),
                        "name": name,
                        "node_indices": [match["index"] for match in matches],
                        "node_match_count": len(matches),
                    }
                )
            render_sets.append(
                {
                    "index": render_index,
                    "header_offset": render_base,
                    "material_mfm_path_id": hex64(mfm_id),
                    "material_mfm_path": assets.resolve_resource(mfm_id),
                    "material_name_id": hex32(material_name_id),
                    "material_name": assets.get_string(material_name_id),
                    "vertices_mapping_id": hex32(vertices_id),
                    "vertices_section": assets.get_string(vertices_id),
                    "indices_mapping_id": hex32(indices_id),
                    "indices_section": assets.get_string(indices_id),
                    "skinned": skinned,
                    "palette_count": palette_count,
                    "palette_offset": palette_offset,
                    "correction_bones": palette,
                }
            )

        geometry_path = assets.resolve_resource(geometry_id)
        visual_descriptors.append(
            {
                "lod_index": lod_index,
                "header_offset": base,
                "bounding_box": {
                    "min_xyz": bounds[:3],
                    "max_xyz": bounds[3:],
                },
                "geometry_path_id": hex64(geometry_id),
                "primitives_path": geometry_path,
                "derived_geometry_path": (
                    (
                        geometry_path[: -len(".primitives")]
                        if geometry_path.endswith(".primitives")
                        else geometry_path
                    )
                    + ".geometry"
                    if geometry_path and geometry_path.endswith(".primitives")
                    else None
                ),
                "render_count": render_count,
                "render_offset": render_offset,
                "render_sets": render_sets,
            }
        )

    mfm_ids = [
        reader.u64(
            material_mfm_ids_offset + index * 8, f"material {index} MFM ID"
        )
        for index in range(materials_count)
    ]
    material_name_ids = [
        reader.u32(
            material_name_ids_offset + index * 4,
            f"material {index} name ID",
        )
        for index in range(materials_count)
    ]
    reader.ensure(
        material_prototypes_offset,
        materials_count * 0x70,
        "material prototype headers",
    )
    materials = []
    for index in range(materials_count):
        item = parse_material_prototype(
            reader, material_prototypes_offset + index * 0x70, assets
        )
        item.update(
            {
                "index": index,
                "mfm_path_id": hex64(mfm_ids[index]),
                "mfm_path": assets.resolve_resource(mfm_ids[index]),
                "material_name_id": hex32(material_name_ids[index]),
                "material_name": assets.get_string(material_name_ids[index]),
            }
        )
        materials.append(item)

    material_pairs = {
        (material["mfm_path_id"], material["material_name_id"]): material["index"]
        for material in materials
    }
    for visual in visual_descriptors:
        for render in visual["render_sets"]:
            render["material_prototype_index"] = material_pairs.get(
                (
                    render["material_mfm_path_id"],
                    render["material_name_id"],
                )
            )

    pointer_ranges = {
        "visual_nodes": [0x38, visual_nodes["arrays_end"]],
        "variants": [variants_offset, variant_end],
        "model_ids": [model_ids_offset, model_ids_end],
        "visual_descriptor_headers": [visuals_offset, visual_headers_end],
        "visual_render_and_palette_region": [
            visual_headers_end,
            material_mfm_ids_offset,
        ],
        "material_mfm_ids": [material_mfm_ids_offset, mfm_ids_end],
        "material_name_ids": [
            material_name_ids_offset,
            material_names_end,
        ],
        "material_prototypes_and_values": [
            material_prototypes_offset,
            len(blob),
        ],
    }
    return {
        "format": "Legends external ModelUberProto v0",
        "resource_id": hex64(resource_id) if resource_id is not None else None,
        "resource_path": resource_path,
        "blob_size": len(blob),
        "fixed_header_size": 0x38,
        "lod_count": lod_count,
        "material_count": materials_count,
        "top_level_offsets": {
            "raw_header_values": raw_offsets,
            "variants": variants_offset,
            "model_ids": model_ids_offset,
            "visual_descriptors": visuals_offset,
            "material_mfm_ids": material_mfm_ids_offset,
            "material_name_ids": material_name_ids_offset,
            "material_prototypes": material_prototypes_offset,
        },
        "pointer_ranges": pointer_ranges,
        "pointer_bounds_valid": True,
        "variant_chain_errors": chain_errors,
        "variants": variants,
        "model_ids": model_id_records,
        "visual_nodes": visual_nodes,
        "visual_descriptors": visual_descriptors,
        "material_prototypes": materials,
        "referenced_nested_ranges": [
            {"start": start, "end": end, "label": label}
            for start, end, label in sorted(referenced_ranges)
        ],
    }


@dataclass(frozen=True)
class GeometrySection:
    name: str
    offset: int
    size: int


def _align4(value: int) -> int:
    return (value + 3) & ~3


def parse_geometry_sections(data: bytes, label: str = "geometry") -> list[GeometrySection]:
    """Parse the trailing BigWorld section table without decoding mesh payloads."""

    reader = CheckedReader(data, label)
    reader.ensure(0, 12, "geometry file")
    table_size = reader.u32(len(data) - 4, "geometry table size")
    table_start = len(data) - 4 - table_size
    if table_size == 0 or table_start < 4 or table_start >= len(data) - 4:
        raise ParseError(
            f"{label}: invalid section table size={table_size}, "
            f"start=0x{table_start:X}"
        )
    cursor = table_start
    data_cursor = 4
    sections: list[GeometrySection] = []
    seen: set[str] = set()
    while cursor < len(data) - 4:
        reader.ensure(cursor, 24, "geometry section-table record")
        section_size = reader.u32(cursor, "geometry section size")
        name_length = reader.u32(cursor + 20, "geometry section name length")
        cursor += 24
        if not 0 < name_length <= 4096:
            raise ParseError(f"{label}: invalid section name length {name_length}")
        raw_name = reader.bytes(cursor, name_length, "geometry section name")
        name = raw_name.rstrip(b"\0").decode("utf-8", errors="strict")
        cursor += _align4(name_length)
        if data_cursor + section_size > table_start:
            raise ParseError(f"{label}: section {name!r} extends into table")
        if name in seen:
            raise ParseError(f"{label}: duplicate section {name!r}")
        seen.add(name)
        sections.append(GeometrySection(name, data_cursor, section_size))
        data_cursor += _align4(section_size)
    if cursor != len(data) - 4:
        raise ParseError(f"{label}: section table has trailing partial record")
    return sections


def resolved_strings(values: Iterable[int], assets: AssetsV0) -> list[dict[str, Any]]:
    return [
        {"id": hex32(value), "value": assets.get_string(value)}
        for value in values
    ]
