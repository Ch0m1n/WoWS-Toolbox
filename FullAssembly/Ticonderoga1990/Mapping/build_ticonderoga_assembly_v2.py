#!/usr/bin/env python3
"""Corrected entry point for build_ticonderoga_assembly.py.

Legends MaterialPrototype stores property_count as u16, and value type tags
0/1 mean bool/int respectively. This wrapper replaces only that parser while
reusing the main read-only assembly implementation.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path
from typing import Any


CORE_PATH = Path(__file__).with_name("build_ticonderoga_assembly.py")
SPEC = importlib.util.spec_from_file_location("ticon_assembly_core", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {CORE_PATH}")
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def parse_material(
    blob: bytes, base: int, assets: core.AssetsV0
) -> dict[str, Any]:
    fx_path_id = core.u64(blob, base)
    property_count = struct.unpack_from("<H", blob, base + 0x0C)[0]
    names_offset = base + core.i64(blob, base + 0x18)
    codes_offset = base + core.i64(blob, base + 0x20)
    pointers = {
        0: base + core.i64(blob, base + 0x28),  # bool
        1: base + core.i64(blob, base + 0x30),  # int
        2: base + core.i64(blob, base + 0x38),  # float
        3: base + core.i64(blob, base + 0x40),  # texture resource id
        4: base + core.i64(blob, base + 0x48),  # vector3
        5: base + core.i64(blob, base + 0x50),  # vector2
        6: base + core.i64(blob, base + 0x58),  # matrix4
        7: base + core.i64(blob, base + 0x60),  # vector4
    }
    type_names = {
        0: "bool",
        1: "int",
        2: "float",
        3: "texture",
        4: "vector3",
        5: "vector2",
        6: "matrix4",
        7: "vector4",
    }
    properties = []
    for property_index in range(property_count):
        name_id = core.u32(blob, names_offset + property_index * 4)
        code = blob[codes_offset + property_index]
        value_type = code & 7
        value_index = code >> 3
        value_base = pointers[value_type]
        if value_type == 0:
            value: Any = bool(blob[value_base + value_index])
        elif value_type == 1:
            value = struct.unpack_from(
                "<i", blob, value_base + value_index * 4
            )[0]
        elif value_type == 2:
            value = core.f32(blob, value_base + value_index * 4)
        elif value_type == 3:
            texture_id = core.u64(blob, value_base + value_index * 8)
            texture_path = assets.resolve_resource(texture_id)
            value = {
                "resource_id": core.hex64(texture_id),
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
                "name_id": core.hex32(name_id),
                "name": assets.get_string(name_id),
                "encoded_type_index": f"0x{code:02X}",
                "type": type_names[value_type],
                "value_index": value_index,
                "value": value,
            }
        )
    return {
        "header_offset": base,
        "fx_path_id": core.hex64(fx_path_id),
        "fx_path": assets.resolve_resource(fx_path_id),
        "property_count": property_count,
        "properties": properties,
    }


core.parse_material = parse_material


if __name__ == "__main__":
    core.main()
