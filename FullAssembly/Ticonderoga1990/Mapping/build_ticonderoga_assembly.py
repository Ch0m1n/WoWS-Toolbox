#!/usr/bin/env python3
"""Build a static Ticonderoga assembly graph from read-only Legends data.

Inputs are extracted workspace copies of GameParams.data, assets.bin,
prototypes.index.data, and prototypes.data. The game installation is never
opened for writing.
"""

from __future__ import annotations

import argparse
import bisect
import functools
import hashlib
import json
import math
import pickle
import re
import struct
from pathlib import Path
from typing import Any


IDENTITY = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def i64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<q", data, offset)[0]


def u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


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

def source_file_record(label: str, path: Path) -> dict[str, Any]:
    """Describe source content without embedding its machine-local path."""
    if Path(label).name != label:
        raise ValueError(f"source label must be a plain filename: {label!r}")
    return {
        "logical_source": f"content/{label}",
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "access": "read-only",
    }




def mat_mul(a: list[float], b: list[float]) -> list[float]:
    result = [0.0] * 16
    for column in range(4):
        for row in range(4):
            result[column * 4 + row] = sum(
                a[k * 4 + row] * b[column * 4 + k] for k in range(4)
            )
    return result


def mat_rows(matrix: list[float]) -> list[list[float]]:
    return [
        [matrix[column * 4 + row] for column in range(4)]
        for row in range(4)
    ]


def mat_rot_inverse(matrix: list[float]) -> list[float]:
    """Public exporter rule: transpose only the local 3x3 rotation block."""
    return [
        matrix[0], matrix[4], matrix[8], 0.0,
        matrix[1], matrix[5], matrix[9], 0.0,
        matrix[2], matrix[6], matrix[10], 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def det3(matrix: list[float]) -> float:
    a, b, c = matrix[0], matrix[4], matrix[8]
    d, e, f = matrix[1], matrix[5], matrix[9]
    g, h, i = matrix[2], matrix[6], matrix[10]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def matrix_record(matrix: list[float]) -> dict[str, Any]:
    return {
        "storage": "column-major glTF MAT4",
        "column_major": matrix,
        "rows": mat_rows(matrix),
        "translation_xyz": matrix[12:15],
        "rotation_scale_determinant": det3(matrix),
        "finite": all(math.isfinite(value) for value in matrix),
    }


class AssetsV0:
    """Legends PrototypeDatabase v0 tables needed by the assembly builder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        if self.data[:4] != b"BDWB":
            raise ValueError("assets.bin magic is not BDWB")
        if u32(self.data, 4) != 0x01000000:
            raise ValueError("assets.bin is not Legends version 0x01000000")

        # Legends v0 section order differs from public PC v0x01010000.
        self.r2p_base = 0x10
        self.paths_base = 0x28
        self.strings_base = 0x38
        self.databases_base = 0x60

        self.paths_count = u32(self.data, self.paths_base)
        self.paths_offset = self.paths_base + i64(self.data, self.paths_base + 8)
        self.string_capacity = u32(self.data, self.strings_base)
        self.string_buckets = self.strings_base + i64(
            self.data, self.strings_base + 8
        )
        self.string_values = self.strings_base + i64(
            self.data, self.strings_base + 16
        )
        self.string_data_size = u32(self.data, self.strings_base + 24)
        self.string_data = self.strings_base + i64(
            self.data, self.strings_base + 32
        )

        self.entries: dict[int, tuple[int, str]] = {}
        self._path_cache: dict[int, str] = {}
        for index in range(self.paths_count):
            base = self.paths_offset + index * 32
            self_id = u64(self.data, base)
            parent_id = u64(self.data, base + 8)
            count = u32(self.data, base + 16)
            packed_string_base = base + 16
            name_offset = packed_string_base + i64(self.data, base + 24)
            raw = self.data[name_offset : name_offset + count]
            if raw.endswith(b"\0"):
                raw = raw[:-1]
            self.entries[self_id] = (
                parent_id,
                raw.decode("utf-8", errors="replace"),
            )

        self.path_to_id: dict[str, int] = {}
        for self_id in self.entries:
            self.path_to_id[self.full_path(self_id)] = self_id

    def full_path(self, self_id: int) -> str:
        if self_id in self._path_cache:
            return self._path_cache[self_id]
        parts: list[str] = []
        current = self_id
        visited: set[int] = set()
        while current and current not in visited:
            visited.add(current)
            parent, name = self.entries[current]
            if name:
                parts.append(name)
            current = parent
        path = "/".join(reversed(parts))
        self._path_cache[self_id] = path
        return path

    def resolve_resource(self, resource_id: int) -> str | None:
        if resource_id == 0 or resource_id not in self.entries:
            return None
        return self.full_path(resource_id)

    def resource_id(self, path: str) -> int | None:
        return self.path_to_id.get(path)

    def get_string(self, name_id: int) -> str | None:
        slot = name_id % self.string_capacity
        for _ in range(self.string_capacity):
            bucket = self.string_buckets + slot * 8
            key = u32(self.data, bucket)
            sentinel = u32(self.data, bucket + 4)
            if key == 0 and sentinel == 0:
                return None
            if key == name_id:
                relative = u32(self.data, self.string_values + slot * 4)
                start = self.string_data + relative
                end = self.data.find(
                    b"\0", start, self.string_data + self.string_data_size
                )
                if end < 0:
                    end = self.string_data + self.string_data_size
                return self.data[start:end].decode("utf-8", errors="replace")
            slot = (slot + 1) % self.string_capacity
        return None


class PrototypeIndex:
    def __init__(self, index_path: Path, data_path: Path) -> None:
        self.index_path = index_path
        self.data_path = data_path
        raw = index_path.read_bytes()
        self.data = data_path.read_bytes()
        self.count = u32(raw, 0)
        self.seed_or_checksum = u32(raw, 4)
        values_offset = u64(raw, 8)
        keys_offset = u64(raw, 16)
        self.keys = list(struct.unpack_from(f"<{self.count}Q", raw, keys_offset))
        self.values = [
            struct.unpack_from("<QI", raw, values_offset + index * 12)
            for index in range(self.count)
        ]

    def locate(self, resource_id: int) -> dict[str, int] | None:
        index = bisect.bisect_left(self.keys, resource_id)
        if index >= self.count or self.keys[index] != resource_id:
            return None
        packed, trailing = self.values[index]
        return {
            "index": index,
            "data_offset": packed >> 32,
            "size": packed & 0xFFFFFFFF,
            "index_trailing_u32": trailing,
        }

    def blob(self, resource_id: int) -> tuple[bytes, dict[str, int]]:
        location = self.locate(resource_id)
        if location is None:
            raise KeyError(hex64(resource_id))
        start = location["data_offset"]
        end = start + location["size"]
        return self.data[start:end], location


def parse_nodes(blob: bytes, assets: AssetsV0, base: int = 0x38) -> dict[str, Any]:
    count = u32(blob, base)
    relptrs = [i64(blob, base + 8 + index * 8) for index in range(5)]
    offsets = [base + value for value in relptrs]
    name_map_name_ids = struct.unpack_from(f"<{count}I", blob, offsets[0])
    name_map_node_ids = struct.unpack_from(f"<{count}H", blob, offsets[1])
    name_ids = struct.unpack_from(f"<{count}I", blob, offsets[2])
    values = struct.unpack_from(f"<{count * 16}f", blob, offsets[3])
    parents = struct.unpack_from(f"<{count}H", blob, offsets[4])
    locals_ = [
        list(values[index * 16 : (index + 1) * 16])
        for index in range(count)
    ]
    cache: dict[int, list[float]] = {}

    def world(index: int, active: set[int] | None = None) -> list[float]:
        if index in cache:
            return cache[index]
        active = set() if active is None else active
        if index in active:
            raise ValueError(f"node parent cycle at {index}")
        active.add(index)
        parent = parents[index]
        if parent == 0xFFFF:
            result = list(locals_[index])
        elif parent >= count:
            raise ValueError(f"node parent outside array: node={index} parent={parent}")
        else:
            result = mat_mul(world(parent, active), locals_[index])
        active.remove(index)
        cache[index] = result
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
                "local_matrix": matrix_record(locals_[index]),
                "world_matrix": matrix_record(world(index)),
            }
        )
    return {
        "base_offset": base,
        "node_count": count,
        "relative_pointers": relptrs,
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


def parse_material(
    blob: bytes, base: int, assets: AssetsV0
) -> dict[str, Any]:
    fx_path_id = u64(blob, base)
    property_count = u32(blob, base + 0x0C)
    names_offset = base + i64(blob, base + 0x18)
    codes_offset = base + i64(blob, base + 0x20)
    pointers = {
        0: base + i64(blob, base + 0x28),
        1: base + i64(blob, base + 0x30),
        2: base + i64(blob, base + 0x38),
        3: base + i64(blob, base + 0x40),
        4: base + i64(blob, base + 0x48),
        5: base + i64(blob, base + 0x50),
        6: base + i64(blob, base + 0x58),
        7: base + i64(blob, base + 0x60),
    }
    type_names = {
        0: "int",
        1: "bool",
        2: "float",
        3: "texture",
        4: "vector3",
        5: "vector2",
        6: "matrix4",
        7: "vector4",
    }
    properties = []
    for property_index in range(property_count):
        name_id = u32(blob, names_offset + property_index * 4)
        code = blob[codes_offset + property_index]
        value_type = code & 7
        value_index = code >> 3
        value_base = pointers[value_type]
        if value_type == 0:
            value: Any = struct.unpack_from(
                "<i", blob, value_base + value_index * 4
            )[0]
        elif value_type == 1:
            value = bool(blob[value_base + value_index])
        elif value_type == 2:
            value = f32(blob, value_base + value_index * 4)
        elif value_type == 3:
            texture_id = u64(blob, value_base + value_index * 8)
            texture_path = assets.resolve_resource(texture_id)
            value = {
                "resource_id": hex64(texture_id),
                "path": texture_path,
                "stem": Path(texture_path).stem if texture_path else None,
            }
        elif value_type == 4:
            value = list(
                struct.unpack_from("<3f", blob, value_base + value_index * 12)
            )
        elif value_type == 5:
            value = list(
                struct.unpack_from("<2f", blob, value_base + value_index * 8)
            )
        elif value_type == 6:
            value = list(
                struct.unpack_from("<16f", blob, value_base + value_index * 64)
            )
        else:
            value = list(
                struct.unpack_from("<4f", blob, value_base + value_index * 16)
            )
        properties.append(
            {
                "name_id": hex32(name_id),
                "name": assets.get_string(name_id),
                "encoded_type_index": f"0x{code:02X}",
                "type": type_names[value_type],
                "value_index": value_index,
                "value": value,
            }
        )
    return {
        "header_offset": base,
        "fx_path_id": hex64(fx_path_id),
        "fx_path": assets.resolve_resource(fx_path_id),
        "property_count": property_count,
        "properties": properties,
    }


def parse_model_uber(blob: bytes, assets: AssetsV0) -> dict[str, Any]:
    lod_count, materials_count = struct.unpack_from("<II", blob, 0)
    model_prototypes_offset = u64(blob, 0x08)
    model_ids_offset = u64(blob, 0x10)
    visual_prototypes_offset = u64(blob, 0x18)
    material_mfm_ids_offset = u64(blob, 0x20)
    material_name_ids_offset = u64(blob, 0x28)
    material_prototypes_offset = u64(blob, 0x30)
    nodes = parse_nodes(blob, assets)

    model_prototypes = []
    for index in range(lod_count):
        base = model_prototypes_offset + index * 0x48
        parent_id = u64(blob, base)
        visual_id = u64(blob, base + 8)
        model_prototypes.append(
            {
                "index": index,
                "header_offset": base,
                "parent_resource_id": hex64(parent_id),
                "parent_resource_path": assets.resolve_resource(parent_id),
                "visual_resource_id": hex64(visual_id),
                "visual_resource_path": assets.resolve_resource(visual_id),
                "extent": f32(blob, base + 0x10),
                "raw_tail_hex": blob[base + 0x14 : base + 0x48].hex(),
            }
        )

    model_ids = []
    for index in range(lod_count):
        resource_id = u64(blob, model_ids_offset + index * 8)
        model_ids.append(
            {
                "resource_id": hex64(resource_id),
                "path": assets.resolve_resource(resource_id),
            }
        )

    visuals = []
    for lod_index in range(lod_count):
        base = visual_prototypes_offset + lod_index * 0x30
        bbox = struct.unpack_from("<6f", blob, base)
        geometry_id = u64(blob, base + 0x18)
        render_count = u32(blob, base + 0x20)
        render_offset = base + i64(blob, base + 0x28) if render_count else 0
        render_sets = []
        for render_index in range(render_count):
            render_base = render_offset + render_index * 0x20
            mfm_id = u64(blob, render_base)
            material_name_id = u32(blob, render_base + 8)
            vertices_id = u32(blob, render_base + 0x0C)
            indices_id = u32(blob, render_base + 0x10)
            skinned = bool(blob[render_base + 0x14])
            palette_count = blob[render_base + 0x15]
            palette_offset = (
                render_base + i64(blob, render_base + 0x18)
                if palette_count
                else 0
            )
            palette_ids = (
                struct.unpack_from(
                    f"<{palette_count}I", blob, palette_offset
                )
                if palette_count
                else ()
            )
            vertices_name = assets.get_string(vertices_id)
            indices_name = assets.get_string(indices_id)
            render_sets.append(
                {
                    "index": render_index,
                    "header_offset": render_base,
                    "material_mfm_path_id": hex64(mfm_id),
                    "material_mfm_path": assets.resolve_resource(mfm_id),
                    "material_name_id": hex32(material_name_id),
                    "material_name": assets.get_string(material_name_id),
                    "vertices_mapping_id": hex32(vertices_id),
                    "vertices_section": vertices_name,
                    "indices_mapping_id": hex32(indices_id),
                    "indices_section": indices_name,
                    "render_set_name": indices_name or vertices_name,
                    "render_set_name_basis": (
                        "v0 has no separate render-set name field; "
                        "indices mapping string is the stable section label"
                    ),
                    "skinned": skinned,
                    "skin_node_palette": [
                        {
                            "name_id": hex32(name_id),
                            "name": assets.get_string(name_id),
                        }
                        for name_id in palette_ids
                    ],
                }
            )
        geometry_path = assets.resolve_resource(geometry_id)
        visuals.append(
            {
                "lod_index": lod_index,
                "header_offset": base,
                "bounding_box": {
                    "min_xyz": list(bbox[:3]),
                    "max_xyz": list(bbox[3:]),
                },
                "merged_geometry_path_id": hex64(geometry_id),
                "merged_geometry_path": geometry_path,
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
                "render_sets_count": render_count,
                "render_sets": render_sets,
            }
        )

    mfm_ids = [
        u64(blob, material_mfm_ids_offset + index * 8)
        for index in range(materials_count)
    ]
    material_name_ids = [
        u32(blob, material_name_ids_offset + index * 4)
        for index in range(materials_count)
    ]
    materials = []
    for index in range(materials_count):
        material = parse_material(
            blob, material_prototypes_offset + index * 0x70, assets
        )
        material.update(
            {
                "index": index,
                "mfm_path_id": hex64(mfm_ids[index]),
                "mfm_path": assets.resolve_resource(mfm_ids[index]),
                "material_name_id": hex32(material_name_ids[index]),
                "material_name": assets.get_string(material_name_ids[index]),
            }
        )
        materials.append(material)

    by_mfm = {
        material["mfm_path_id"]: material["index"] for material in materials
    }
    for visual in visuals:
        for render_set in visual["render_sets"]:
            render_set["material_prototype_index"] = by_mfm.get(
                render_set["material_mfm_path_id"]
            )

    return {
        "format": "Legends external ModelUberProto v0",
        "fixed_header_size": 0x38,
        "visual_nodes_base": 0x38,
        "lod_count": lod_count,
        "materials_count": materials_count,
        "model_prototypes": model_prototypes,
        "model_ids": model_ids,
        "visual_nodes": nodes,
        "visual_prototypes": visuals,
        "material_prototypes": materials,
    }


class NeutralObject:
    """Inert target for Cython pickle reconstruction."""

    def __init__(self, *args: Any) -> None:
        self.constructor_args = args
        self.state: Any = None

    def __setstate__(self, state: Any) -> None:
        self.state = state


def neutral_factory(module: str, name: str, *args: Any) -> NeutralObject:
    return NeutralObject(module, name, *args)


class InertGameParamsUnpickler(pickle.Unpickler):
    """Disallow imported callables; only inert objects and set containers."""

    def find_class(self, module: str, name: str) -> Any:
        if module in ("__builtin__", "builtins") and name in ("set", "frozenset"):
            return {"set": set, "frozenset": frozenset}[name]
        if name.startswith("__pyx_unpickle_"):
            return functools.partial(neutral_factory, module, name)
        return type(name, (NeutralObject,), {"__module__": "__inert__"})


def load_game_params(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        if handle.read(4) != b"%bin":
            raise ValueError("GameParams.data lacks %bin header")
        root = InertGameParamsUnpickler(handle, encoding="latin1").load()
    if not isinstance(root, dict):
        raise TypeError("GameParams root is not a dict")
    return root


def flatten_strings(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        result.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            result.extend(flatten_strings(key))
            result.extend(flatten_strings(item))
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            result.extend(flatten_strings(item))
    elif isinstance(value, NeutralObject):
        result.extend(flatten_strings(value.constructor_args))
        result.extend(flatten_strings(value.state))
    return result


def find_hp_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        object_id = id(item)
        if object_id in seen:
            return
        seen.add(object_id)
        if isinstance(item, dict):
            if any(
                isinstance(key, str) and key.startswith("HP_")
                for key in item
            ):
                found.append(item)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (tuple, list)):
            for nested in item:
                visit(nested)
        elif isinstance(item, NeutralObject):
            visit(item.state)

    visit(value)
    return found


def natural_key(text: str) -> list[Any]:
    return [
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", text)
    ]


def gameparams_mounts(root: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ship = root["PXSD307_Ticonderoga_1990"]
    components = ship.state[2]
    category_by_component = {
        "R_GuidedMissiles": "guided_missile_launcher",
        "A_AirDefense": "air_defense",
        "A_Radars": "radar",
        "A_ATBA": "secondary_artillery",
        "R_UnguidedMissiles": "vertical_launch_system",
    }
    mounts: list[dict[str, Any]] = []
    for component, category in category_by_component.items():
        candidates = [
            mapping
            for mapping in find_hp_dicts(components[component])
            if any(str(key).startswith(("HP_AG", "HP_ARS")) for key in mapping)
        ]
        if not candidates:
            raise ValueError(f"no HP dictionary in component {component}")
        mapping = max(candidates, key=len)
        for hardpoint, item in mapping.items():
            if not isinstance(hardpoint, str) or not hardpoint.startswith("HP_"):
                continue
            models = list(
                dict.fromkeys(
                    text
                    for text in flatten_strings(item)
                    if text.endswith(".model")
                )
            )
            live = [
                path
                for path in models
                if not path.endswith("_dead.model")
                and "/misc/AM5058/" not in path
            ]
            dead = [path for path in models if path.endswith("_dead.model")]
            action = [path for path in models if "/misc/AM5058/" in path]
            if len(live) != 1:
                raise ValueError(
                    f"{component}/{hardpoint}: expected one live model, got {live}"
                )
            mounts.append(
                {
                    "component": component,
                    "category": category,
                    "hardpoint": hardpoint,
                    "model_path": live[0],
                    "dead_model_paths": dead,
                    "action_model_paths": action,
                }
            )
    mounts.sort(key=lambda item: natural_key(item["hardpoint"]))
    metadata = {
        "ship_key": "PXSD307_Ticonderoga_1990",
        "component_order": list(components.keys()),
        "hull_model_path": next(
            text
            for text in flatten_strings(components["A_Hull"])
            if text.endswith("ASC307_Ticonderoga_1990.model")
        ),
    }
    return mounts, metadata


def correction_for(model: dict[str, Any]) -> dict[str, Any]:
    nodes = model["model_uber"]["visual_nodes"]["nodes"]
    selected = next(
        (node for node in nodes if node["name"] == "Rotate_Y_BlendBone"),
        None,
    )
    if selected is None:
        selected = next(
            (node for node in nodes if node["name"] == "Root_BlendBone"),
            None,
        )
    rotate_nodes = [
        {"index": node["index"], "name": node["name"]}
        for node in nodes
        if node["name"] and "Rotate" in node["name"]
    ]
    if selected is None:
        return {
            "rule": (
                "inverse local rotation of Rotate_Y_BlendBone, then Root_BlendBone; "
                "identity when neither exists"
            ),
            "selected_bone": None,
            "available_rotate_nodes": rotate_nodes,
            "correction_matrix": matrix_record(list(IDENTITY)),
        }
    local = selected["local_matrix"]["column_major"]
    correction = mat_rot_inverse(local)
    return {
        "rule": (
            "inverse local 3x3 of Rotate_Y_BlendBone; "
            "public exporter uses transpose and clears translation"
        ),
        "selected_bone": {
            "index": selected["index"],
            "name": selected["name"],
            "parent_index": selected["parent_index"],
            "local_matrix": selected["local_matrix"],
        },
        "available_rotate_nodes": rotate_nodes,
        "correction_matrix": matrix_record(correction),
    }


def model_record(
    path: str, assets: AssetsV0, prototypes: PrototypeIndex
) -> dict[str, Any]:
    resource_id = assets.resource_id(path)
    if resource_id is None:
        raise KeyError(f"model path absent from assets paths: {path}")
    blob, location = prototypes.blob(resource_id)
    return {
        "path": path,
        "resource_id": hex64(resource_id),
        "prototype_location": {
            "index": location["index"],
            "data_offset": location["data_offset"],
            "size": location["size"],
            "index_trailing_u32": hex32(location["index_trailing_u32"]),
            "index_trailing_u32_semantics": "unknown/checksum-like; not treated as a type hash",
        },
        "model_uber": parse_model_uber(blob, assets),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    assets = AssetsV0(args.assets)
    prototypes = PrototypeIndex(args.prototype_index, args.prototype_data)
    game_params = load_game_params(args.game_params)
    gp_mounts, gp_metadata = gameparams_mounts(game_params)

    ship_dir = "content/gameplay/usa/ship/cruiser/ASC307_Ticonderoga_1990"
    hull_paths = [
        f"{ship_dir}/ASC307_Ticonderoga_1990.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_Bow.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_Bow_ports.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_MidFront.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_MidFront_ports.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_MidBack.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_MidBack_ports.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_MidBack_ports_dock.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_Stern.model",
        f"{ship_dir}/ASC307_Ticonderoga_1990_Stern_ports.model",
    ]

    misc_key_paths: dict[str, str] = {}
    combat_paths = {item["model_path"] for item in gp_mounts}
    action_paths = {
        path
        for item in gp_mounts
        for path in item["action_model_paths"]
    }
    all_model_paths = set(hull_paths) | combat_paths | action_paths

    # First parse hull sources to discover every authored MP_* instance.
    models: dict[str, dict[str, Any]] = {}
    for path in hull_paths:
        models[path] = model_record(path, assets, prototypes)
    mp_nodes: list[tuple[str, dict[str, Any]]] = []
    hp_sources: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for path in hull_paths:
        nodes = models[path]["model_uber"]["visual_nodes"]["nodes"]
        for node in nodes:
            name = node["name"] or ""
            if name.startswith("MP_"):
                mp_nodes.append((path, node))
                match = re.match(r"MP_(AM\d+)", name)
                if not match:
                    raise ValueError(f"cannot derive misc model from {name}")
                key = match.group(1)
                misc_path = f"content/gameplay/usa/misc/{key}/{key}.model"
                misc_key_paths[key] = misc_path
                all_model_paths.add(misc_path)
            if name.startswith(("HP_AG", "HP_ARS")):
                hp_sources.setdefault(name, []).append((path, node))

    for path in sorted(all_model_paths):
        if path not in models:
            models[path] = model_record(path, assets, prototypes)

    corrections = {
        path: correction_for(models[path])
        for path in sorted(combat_paths | action_paths | set(misc_key_paths.values()))
    }

    combat_mounts = []
    for sequence, mount in enumerate(gp_mounts):
        hardpoint = mount["hardpoint"]
        sources = hp_sources.get(hardpoint, [])
        if len(sources) != 1:
            continue
        source_path, node = sources[0]
        hp_world = node["world_matrix"]["column_major"]
        correction = corrections[mount["model_path"]]
        correction_matrix = correction["correction_matrix"]["column_major"]
        corrected = mat_mul(hp_world, correction_matrix)
        item = dict(mount)
        item.update(
            {
                "sequence": sequence,
                "source_hull_model_path": source_path,
                "source_hull_model_resource_id": models[source_path]["resource_id"],
                "source_hull_prototype_location": models[source_path][
                    "prototype_location"
                ],
                "source_node_index": node["index"],
                "source_node_parent_index": node["parent_index"],
                "hp_world_matrix": node["world_matrix"],
                "model_resource_id": models[mount["model_path"]]["resource_id"],
                "model_prototype_location": models[mount["model_path"]][
                    "prototype_location"
                ],
                "correction": correction,
                "corrected_gltf_rh_y_up_matrix": matrix_record(corrected),
            }
        )
        combat_mounts.append(item)

    action_overlays = []
    for mount in combat_mounts:
        for action_path in mount["action_model_paths"]:
            correction = corrections[action_path]
            corrected = mat_mul(
                mount["hp_world_matrix"]["column_major"],
                correction["correction_matrix"]["column_major"],
            )
            action_overlays.append(
                {
                    "parent_hardpoint": mount["hardpoint"],
                    "role": "ShooterLaunchAction front model; runtime hatch/launch visual",
                    "static_default_policy": "recorded but not forced visible",
                    "model_path": action_path,
                    "model_resource_id": models[action_path]["resource_id"],
                    "model_prototype_location": models[action_path][
                        "prototype_location"
                    ],
                    "source_hull_model_path": mount["source_hull_model_path"],
                    "hp_world_matrix": mount["hp_world_matrix"],
                    "correction": correction,
                    "corrected_gltf_rh_y_up_matrix": matrix_record(corrected),
                }
            )

    misc_instances = []
    for sequence, (source_path, node) in enumerate(mp_nodes):
        name = node["name"]
        key = re.match(r"MP_(AM\d+)", name).group(1)
        path = misc_key_paths[key]
        correction = corrections[path]
        corrected = mat_mul(
            node["world_matrix"]["column_major"],
            correction["correction_matrix"]["column_major"],
        )
        misc_instances.append(
            {
                "sequence": sequence,
                "instance_name": name,
                "asset_key": key,
                "role": (
                    name[len(f"MP_{key}_") :]
                    if name.startswith(f"MP_{key}_")
                    else name
                ),
                "visibility_condition": (
                    "dock" if source_path.endswith("_ports_dock.model") else "always"
                ),
                "source_hull_model_path": source_path,
                "source_hull_model_resource_id": models[source_path]["resource_id"],
                "source_node_index": node["index"],
                "source_node_parent_index": node["parent_index"],
                "model_path": path,
                "model_resource_id": models[path]["resource_id"],
                "model_prototype_location": models[path]["prototype_location"],
                "mp_world_matrix": node["world_matrix"],
                "correction": correction,
                "corrected_gltf_rh_y_up_matrix": matrix_record(corrected),
            }
        )

    hierarchy_roles = {
        hull_paths[0]: ("root", None, "whole-ship far LOD and root markers"),
        hull_paths[1]: ("mesh", hull_paths[0], "Bow detailed segment"),
        hull_paths[2]: ("ports", hull_paths[1], "Bow effects/ports"),
        hull_paths[3]: ("mesh", hull_paths[0], "MidFront detailed segment"),
        hull_paths[4]: ("ports", hull_paths[3], "MidFront combat/misc ports"),
        hull_paths[5]: ("mesh", hull_paths[0], "MidBack detailed segment"),
        hull_paths[6]: ("ports", hull_paths[5], "MidBack combat/misc ports"),
        hull_paths[7]: ("conditional_ports", hull_paths[5], "dock-state launcher caps"),
        hull_paths[8]: ("mesh", hull_paths[0], "Stern detailed segment"),
        hull_paths[9]: ("ports", hull_paths[8], "Stern propeller/effect ports"),
    }
    hull_parts = []
    for path in hull_paths:
        role, parent, note = hierarchy_roles[path]
        nodes = models[path]["model_uber"]["visual_nodes"]["nodes"]
        hull_parts.append(
            {
                "path": path,
                "resource_id": models[path]["resource_id"],
                "prototype_location": models[path]["prototype_location"],
                "role": role,
                "parent_path": parent,
                "attachment_matrix": matrix_record(list(IDENTITY)),
                "note": note,
                "authored_hp_nodes": [
                    node["name"] for node in nodes if (node["name"] or "").startswith("HP_")
                ],
                "authored_mp_nodes": [
                    node["name"] for node in nodes if (node["name"] or "").startswith("MP_")
                ],
            }
        )

    expected_hps = [item["hardpoint"] for item in gp_mounts]
    resolved_hps = [item["hardpoint"] for item in combat_mounts]
    missing = sorted(set(expected_hps) - set(resolved_hps), key=natural_key)
    duplicate = {
        hardpoint: [source for source, _ in sources]
        for hardpoint, sources in hp_sources.items()
        if hardpoint in expected_hps and len(sources) != 1
    }

    render_sets = [
        render_set
        for model in models.values()
        for visual in model["model_uber"]["visual_prototypes"]
        for render_set in visual["render_sets"]
    ]
    texture_properties = [
        prop
        for model in models.values()
        for material in model["model_uber"]["material_prototypes"]
        for prop in material["properties"]
        if prop["type"] == "texture"
    ]
    unresolved_render_sets = [
        {
            "material_mfm_path": item["material_mfm_path"],
            "material_name": item["material_name"],
            "vertices_section": item["vertices_section"],
            "indices_section": item["indices_section"],
        }
        for item in render_sets
        if not all(
            (
                item["material_mfm_path"],
                item["material_name"],
                item["vertices_section"],
                item["indices_section"],
            )
        )
    ]
    unresolved_textures = [
        item for item in texture_properties if not item["value"]["path"]
    ]

    source_files = {}
    for label, path in (
        ("GameParams.data", args.game_params),
        ("assets.bin", args.assets),
        ("prototypes.index.data", args.prototype_index),
        ("prototypes.data", args.prototype_data),
    ):
        source_files[label] = source_file_record(label, path)

    validations = {
        "expected_combat_hardpoints": len(expected_hps),
        "resolved_combat_hardpoints": len(resolved_hps),
        "missing_combat_hardpoints": missing,
        "duplicate_combat_hardpoint_sources": duplicate,
        "unique_combat_model_paths": len(combat_paths),
        "action_overlay_instances": len(action_overlays),
        "misc_instances": len(misc_instances),
        "hull_part_models": len(hull_parts),
        "all_referenced_model_prototypes_resolved": len(models) == len(all_model_paths),
        "render_sets_parsed": len(render_sets),
        "unresolved_render_set_fields": unresolved_render_sets,
        "texture_properties_parsed": len(texture_properties),
        "unresolved_texture_paths": unresolved_textures,
        "all_output_matrices_finite": all(
            item["corrected_gltf_rh_y_up_matrix"]["finite"]
            for item in combat_mounts + action_overlays + misc_instances
        ),
    }
    validations["static_assembly_acceptance"] = (
        validations["expected_combat_hardpoints"] == 17
        and validations["resolved_combat_hardpoints"] == 17
        and not missing
        and not duplicate
        and validations["all_referenced_model_prototypes_resolved"]
        and not unresolved_render_sets
        and not unresolved_textures
        and validations["all_output_matrices_finite"]
    )

    return {
        "schema": "wows-legends-static-ship-assembly/v1",
        "generated_by": "build_ticonderoga_assembly.py",
        "source_files": source_files,
        "ship": {
            **gp_metadata,
            "index": "PXSD307",
            "display_identity": "Ticonderoga 1990",
        },
        "coordinate_system": {
            "source": "BigWorld right-handed Y-up; bow is -Z",
            "target": "glTF 2.0 right-handed Y-up",
            "axis_conversion": "identity",
            "matrix_storage": "column-major",
            "placement_formula": (
                "corrected = HP_world * inverse_rotation("
                "Rotate_Y_BlendBone or Root_BlendBone); identity if absent"
            ),
            "hull_segment_policy": (
                "Bow/MidFront/MidBack/Stern geometry is authored in one ship "
                "coordinate space; segment and *_ports parents use identity"
            ),
        },
        "binary_layout": {
            "assets_magic": "BDWB",
            "assets_version": "0x01000000",
            "assets_v0_sections": {
                "resource_to_prototype_map": "0x10",
                "paths_storage": "0x28",
                "strings": "0x38",
                "databases": "0x60",
            },
            "prototype_index": {
                "count": prototypes.count,
                "seed_or_checksum": hex32(prototypes.seed_or_checksum),
                "key": "assets path selfId (u64, sorted)",
                "value": "packed u64(data_offset<<32 | size) + trailing u32",
            },
            "model_uber": {
                "fixed_header": "0x38 bytes",
                "visual_nodes_base": "blob+0x38",
                "visual_node_arrays": (
                    "nameMapNameIds u32[], nameMapNodeIds u16[], nameIds u32[], "
                    "matrices 16*f32[], parentIds u16[]"
                ),
                "model_prototype_stride": "0x48",
                "visual_prototype_stride": "0x30",
                "render_set_stride": "0x20",
                "material_prototype_stride": "0x70",
            },
        },
        "hull_parts": hull_parts,
        "combat_mounts": combat_mounts,
        "runtime_action_overlays": action_overlays,
        "misc_instances": misc_instances,
        "models": {path: models[path] for path in sorted(models)},
        "validation": validations,
        "scope": {
            "accepted": (
                "static intact LOD assembly: hull segments, 17 GameParams combat "
                "mounts, authored MP_* misc instances, materials/textures"
            ),
            "recorded_not_forced_visible": "AM5058 ShooterLaunchAction overlay",
            "not_claimed": (
                "runtime firing animation, hatch state, damage/dead swaps, particle "
                "effects, wake simulation, or dynamic turret aiming"
            ),
        },
    }


def acceptance_markdown(data: dict[str, Any]) -> str:
    validation = data["validation"]
    result = "PASS" if validation["static_assembly_acceptance"] else "FAIL"
    combat_rows = "\n".join(
        f"| `{item['hardpoint']}` | `{Path(item['model_path']).stem}` | "
        f"`{Path(item['source_hull_model_path']).stem}` | "
        f"{tuple(round(v, 6) for v in item['hp_world_matrix']['translation_xyz'])} |"
        for item in data["combat_mounts"]
    )
    misc_rows = "\n".join(
        f"| `{item['instance_name']}` | `{item['asset_key']}` | "
        f"`{Path(item['source_hull_model_path']).stem}` | "
        f"`{item['visibility_condition']}` |"
        for item in data["misc_instances"]
    )
    return f"""# Ticonderoga 1990 static assembly acceptance

Result: **{result}**

## Acceptance checks

- GameParams combat HP expected/resolved: **{validation['expected_combat_hardpoints']} / {validation['resolved_combat_hardpoints']}**
- Missing HP: `{validation['missing_combat_hardpoints']}`
- Duplicate HP sources: `{validation['duplicate_combat_hardpoint_sources']}`
- Hull ModelUber prototypes: **{validation['hull_part_models']}**
- Authored `MP_*` instances: **{validation['misc_instances']}**
- Runtime action overlays recorded: **{validation['action_overlay_instances']}**
- Render sets parsed: **{validation['render_sets_parsed']}**
- Texture properties parsed/resolved: **{validation['texture_properties_parsed']} / {validation['texture_properties_parsed'] - len(validation['unresolved_texture_paths'])}**
- All output matrices finite: **{validation['all_output_matrices_finite']}**

## Combat placement

| HP | model | source ports model | BigWorld/glTF translation (x,y,z) |
|---|---|---|---|
{combat_rows}

## Authored misc placement

| node | model key | source ports model | condition |
|---|---|---|---|
{misc_rows}

## Assembly contract

The intact static model is complete when the four detailed hull segments are
loaded in their shared ship coordinate space, all 17 combat models are placed
with `HP_world * correction`, and every authored `MP_*` node is instanced.
BigWorld and glTF are both right-handed Y-up here, so no axis conversion is used.

`AM5058.model` is a `ShooterLaunchAction` front model for each VLS. It is
recorded with its exact placement but is not forced visible in the static default
scene. Dead models, dynamic aiming, hatch animation, particles, wakes, and damage
states are outside this static-intact acceptance.

## Reproducibility

Run `build_ticonderoga_assembly_v2.py` against read-only copies of the four source
files. The output stores path-independent logical source names and SHA-256 values,
every prototype offset/size, local/world matrices, correction bones, render-set
section names, MFM paths, material properties, texture resource paths, and skin
palettes.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-params", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--prototype-index", type=Path, required=True)
    parser.add_argument("--prototype-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    args = parser.parse_args()

    result = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.acceptance.write_text(acceptance_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "acceptance": str(args.acceptance),
                "validation": result["validation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
