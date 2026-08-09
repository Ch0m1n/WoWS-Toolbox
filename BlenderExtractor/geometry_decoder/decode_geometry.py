#!/usr/bin/env python3
"""Decode legacy BigWorld sectioned geometry files to Wavefront OBJ.

This is a deliberately small decoder for the geometry variant used by the
Steam build of World of Warships: Legends inspected on 2026-08-03.

It does not read or modify the game installation. Inputs are already extracted
.geometry files and all output paths are supplied by the caller.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence


class GeometryError(RuntimeError):
    """Raised when a geometry file is malformed or unsupported."""


@dataclass(frozen=True)
class Section:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class Vertex:
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    uv: tuple[float, float]


@dataclass(frozen=True)
class PrimitiveGroup:
    start_index: int
    primitive_count: int
    start_vertex: int
    vertex_count: int


@dataclass
class MeshPart:
    name: str
    vertex_format: str
    vertex_stride: int
    index_format: str
    vertices: list[Vertex]
    triangles: list[tuple[int, int, int]]
    primitive_groups: list[PrimitiveGroup]
    source: str | None = None
    normal_orientation_flipped: bool = False


@dataclass
class DecodedGeometryFile:
    input_path: Path
    sections: list[Section]
    all_parts: list[MeshPart]
    selected_parts: list[MeshPart]
    requested_lod: int | None
    selected_lod: int | None
    fallback_used: bool


@dataclass(frozen=True)
class VertexLayout:
    stride: int
    is_new_normal: bool
    is_skinned: bool


VERTEX_LAYOUTS: dict[str, VertexLayout] = {
    "set3/xyznuvtbpc": VertexLayout(28, True, False),
    "set3/xyznuvpc": VertexLayout(20, True, False),
    "set3/xyznuvrpc": VertexLayout(24, True, False),
    "set3/xyznuviiiwwpc": VertexLayout(28, True, True),
    "set3/xyznuviiiwwtbpc": VertexLayout(40, True, True),
    "xyznuvtb": VertexLayout(32, False, False),
    "xyznuv": VertexLayout(24, False, False),
    "xyznuviiiwwtb": VertexLayout(37, False, True),
}

_DAMAGE_RE = re.compile(r"(?:crack|dead)", re.IGNORECASE)
_DEAD_RE = re.compile(r"dead", re.IGNORECASE)
_CRACK_RE = re.compile(r"_crack_", re.IGNORECASE)
_LOD_RE = re.compile(r"_lod(?:shape)?(\d+)", re.IGNORECASE)


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise GeometryError(f"u32 read outside file at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _fixed_string(data: bytes, offset: int, size: int = 64) -> str:
    end = offset + size
    if offset < 0 or end > len(data):
        raise GeometryError(f"string read outside section at 0x{offset:X}")
    return data[offset:end].split(b"\0", 1)[0].decode("utf-8", "strict")


def parse_sections(data: bytes) -> list[Section]:
    """Read the trailing BigWorld section table."""
    if len(data) < 12:
        raise GeometryError("file is too small to contain a section table")

    table_size = _u32(data, len(data) - 4)
    table_start = len(data) - 4 - table_size
    if table_size == 0 or table_start < 4 or table_start >= len(data) - 4:
        raise GeometryError(
            f"invalid section table: size={table_size}, start=0x{table_start:X}"
        )

    cursor = table_start
    data_cursor = 4
    sections: list[Section] = []
    while cursor < len(data) - 4:
        if cursor + 24 > len(data) - 4:
            raise GeometryError("truncated section-table record")

        section_size = _u32(data, cursor)
        # The next 16 bytes are opaque metadata in this format variant.
        name_length = _u32(data, cursor + 20)
        cursor += 24

        if name_length == 0 or name_length > 4096:
            raise GeometryError(f"invalid section name length {name_length}")
        if cursor + name_length > len(data) - 4:
            raise GeometryError("section name extends beyond table")

        raw_name = data[cursor : cursor + name_length]
        name = raw_name.rstrip(b"\0").decode("utf-8", "strict")
        cursor += _align4(name_length)

        if data_cursor + section_size > table_start:
            raise GeometryError(
                f"section {name!r} extends into table "
                f"(0x{data_cursor:X}+{section_size})"
            )

        sections.append(Section(name=name, offset=data_cursor, size=section_size))
        data_cursor += _align4(section_size)

    if cursor != len(data) - 4:
        raise GeometryError("section table did not end at footer")
    return sections


def _decode_new_component(value: int) -> float:
    if value > 0x7F:
        return -float(value & 0x7F) / 0x7F
    return float(value ^ 0x7F) / 0x7F


def decode_packed_normal(packed: int, is_new: bool) -> tuple[float, float, float]:
    """Decode BigWorld's packed normal and map game axes to Blender/OBJ axes."""
    if is_new:
        packed_x = (packed & 0xFF) ^ 0xFF
        packed_y = ((packed >> 8) & 0xFF) ^ 0xFF
        packed_z = ((packed >> 16) & 0xFF) ^ 0xFF
        x = _decode_new_component(packed_x)
        y = _decode_new_component(packed_y)
        z = _decode_new_component(packed_z)
    else:
        packed_x = packed & 0x7FF
        packed_y = (packed >> 11) & 0x7FF
        packed_z = (packed >> 22) & 0x3FF

        if packed_x > 0x3FF:
            x = -float(((packed_x & 0x3FF) ^ 0x3FF) + 1) / 0x3FF
        else:
            x = float(packed_x) / 0x3FF
        if packed_y > 0x3FF:
            y = -float(((packed_y & 0x3FF) ^ 0x3FF) + 1) / 0x3FF
        else:
            y = float(packed_y) / 0x3FF
        if packed_z > 0x1FF:
            z = -float(((packed_z & 0x1FF) ^ 0x1FF) + 1) / 0x1FF
        else:
            z = float(packed_z) / 0x1FF

    # Stored coordinates are X, Z, Y. Match the model-converter convention.
    normal = (x, z, y)
    length = math.sqrt(sum(component * component for component in normal))
    if length > 1e-12:
        normal = tuple(component / length for component in normal)
    return normal


def parse_vertices(section_data: bytes) -> tuple[str, int, list[Vertex]]:
    if len(section_data) < 68:
        raise GeometryError("vertex section is too small")

    vertex_format = _fixed_string(section_data, 0)
    cursor = 64
    vertex_count = _u32(section_data, cursor)
    cursor += 4

    # Some processed BigWorld files wrap a concrete layout in a BPVT header.
    if "BPVT" in vertex_format:
        if cursor + 68 > len(section_data):
            raise GeometryError("truncated BPVT vertex header")
        vertex_format = _fixed_string(section_data, cursor)
        cursor += 64
        vertex_count = _u32(section_data, cursor)
        cursor += 4

    layout = VERTEX_LAYOUTS.get(vertex_format)
    if layout is None:
        raise GeometryError(f"unsupported vertex format {vertex_format!r}")

    stride = layout.stride
    payload_size = len(section_data) - cursor
    if vertex_count and payload_size % vertex_count == 0:
        inferred_stride = payload_size // vertex_count
        if 20 <= inferred_stride <= 64:
            stride = inferred_stride

    required = cursor + vertex_count * stride
    if required > len(section_data):
        raise GeometryError(
            f"vertex payload truncated: need {required}, have {len(section_data)}"
        )

    vertices: list[Vertex] = []
    for index in range(vertex_count):
        offset = cursor + index * stride
        x, stored_z, stored_y = struct.unpack_from("<3f", section_data, offset)
        packed_normal = _u32(section_data, offset + 12)
        if vertex_format.startswith("set3/"):
            stored_u, stored_v = struct.unpack_from("<2e", section_data, offset + 16)
            u, v = stored_u + 0.5, stored_v + 0.5
        else:
            u, v = struct.unpack_from("<2f", section_data, offset + 16)
        vertices.append(
            Vertex(
                position=(x, stored_y, stored_z),
                normal=decode_packed_normal(packed_normal, layout.is_new_normal),
                uv=(u, 1.0 - v),
            )
        )

    return vertex_format, stride, vertices


def parse_indices(
    section_data: bytes,
) -> tuple[str, list[tuple[int, int, int]], list[PrimitiveGroup]]:
    if len(section_data) < 72:
        raise GeometryError("index section is too small")

    index_format = _fixed_string(section_data, 0)
    if index_format not in ("list", "list32"):
        raise GeometryError(f"unsupported index format {index_format!r}")
    index_size = 4 if index_format == "list32" else 2
    scalar_format = "I" if index_size == 4 else "H"
    index_count = _u32(section_data, 64)
    group_count = _u32(section_data, 68)
    cursor = 72

    if index_count % 3:
        raise GeometryError(f"index count {index_count} is not divisible by three")

    index_bytes = index_count * index_size
    group_bytes = group_count * 16
    if cursor + index_bytes + group_bytes > len(section_data):
        raise GeometryError("index/group payload is truncated")

    triangles: list[tuple[int, int, int]] = []
    triangle_format = "<3" + scalar_format
    for _ in range(index_count // 3):
        stored = struct.unpack_from(triangle_format, section_data, cursor)
        cursor += index_size * 3
        # BigWorld stores the opposite winding from the OBJ convention used here.
        triangles.append((stored[2], stored[1], stored[0]))

    groups: list[PrimitiveGroup] = []
    for _ in range(group_count):
        groups.append(PrimitiveGroup(*struct.unpack_from("<4I", section_data, cursor)))
        cursor += 16

    return index_format, triangles, groups


def orient_vertex_normals(
    vertices: list[Vertex], triangles: list[tuple[int, int, int]]
) -> tuple[list[Vertex], bool]:
    """Flip globally inverted custom normals using face-winding consensus."""

    if not vertices or not triangles:
        return vertices, False
    step = max(1, len(triangles) // 4096)
    alignment = 0.0
    compared = 0
    for triangle in triangles[::step]:
        first, second, third = (vertices[index] for index in triangle)
        edge_a = tuple(
            second.position[index] - first.position[index] for index in range(3)
        )
        edge_b = tuple(
            third.position[index] - first.position[index] for index in range(3)
        )
        face = (
            edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
            edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
            edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
        )
        averaged = tuple(
            first.normal[index] + second.normal[index] + third.normal[index]
            for index in range(3)
        )
        face_length = math.sqrt(sum(component * component for component in face))
        normal_length = math.sqrt(
            sum(component * component for component in averaged)
        )
        if face_length <= 1e-12 or normal_length <= 1e-12:
            continue
        alignment += sum(
            face[index] * averaged[index] for index in range(3)
        ) / (face_length * normal_length)
        compared += 1
    if not compared or alignment >= -0.05 * compared:
        return vertices, False
    return [
        replace(vertex, normal=tuple(-value for value in vertex.normal))
        for vertex in vertices
    ], True

def decode_geometry(data: bytes) -> tuple[list[Section], list[MeshPart]]:
    """Decode all paired mesh parts in one legacy .geometry byte string."""
    sections = parse_sections(data)
    by_name = {section.name: section for section in sections}
    parts: list[MeshPart] = []

    for vertex_section in sections:
        if not vertex_section.name.endswith(".vertices"):
            continue
        part_name = vertex_section.name[: -len(".vertices")]
        index_name = part_name + ".indices"
        index_section = by_name.get(index_name)
        if index_section is None:
            raise GeometryError(
                f"vertex section {vertex_section.name!r} has no {index_name!r}"
            )

        vertex_blob = data[
            vertex_section.offset : vertex_section.offset + vertex_section.size
        ]
        index_blob = data[index_section.offset : index_section.offset + index_section.size]
        vertex_format, vertex_stride, vertices = parse_vertices(vertex_blob)
        index_format, triangles, groups = parse_indices(index_blob)
        vertices, normal_orientation_flipped = orient_vertex_normals(
            vertices, triangles
        )

        for triangle in triangles:
            if any(index < 0 or index >= len(vertices) for index in triangle):
                raise GeometryError(
                    f"part {part_name!r} has out-of-range triangle {triangle} "
                    f"for {len(vertices)} vertices"
                )

        parts.append(
            MeshPart(
                name=part_name,
                vertex_format=vertex_format,
                vertex_stride=vertex_stride,
                index_format=index_format,
                vertices=vertices,
                triangles=triangles,
                primitive_groups=groups,
                normal_orientation_flipped=normal_orientation_flipped,
            )
        )

    if not parts:
        raise GeometryError("no paired .vertices/.indices sections were found")
    return sections, parts


def part_lod(name: str) -> int | None:
    match = _LOD_RE.search(name)
    return int(match.group(1)) if match else None


def is_intact_part_name(name: str) -> bool:
    """Return whether one render-set name belongs in an intact hull.

    ``patch`` meshes close the joints between intact segmented hull pieces and
    must be retained. Bare crack meshes and dead variants are damage-only. The
    exterior DeckHouse/Hull joint surfaces are the one crack exception: they
    are required to close the visible shell around an intact joint.
    """
    if _DEAD_RE.search(name):
        return False
    if not _DAMAGE_RE.search(name):
        return True
    if not _CRACK_RE.search(name):
        return False

    normalized = _LOD_RE.sub("", name)
    normalized = re.sub(r"shape$", "", normalized, flags=re.IGNORECASE)
    return normalized.casefold().endswith(("_deckhouse", "_hull"))


def select_intact_parts(
    parts: Sequence[MeshPart], intact_lod: int | None = 0
) -> tuple[list[MeshPart], int | None, bool]:
    """Select intact parts for one LOD, with a highest-detail fallback.

    ``intact_lod=None`` disables filtering. Otherwise dead and bare crack
    variants are excluded, while joint patches and exterior DeckHouse/Hull
    crack surfaces are retained. Untagged parts are treated as belonging to the
    requested LOD so shared meshes are retained. If nothing remains, the
    smallest available LOD number (highest detail) is selected. If no numbered
    LOD exists, every intact part is returned.
    """
    if intact_lod is None:
        return list(parts), None, False
    if intact_lod < 0:
        raise GeometryError("intact LOD must be zero or greater")

    intact_parts = [part for part in parts if is_intact_part_name(part.name)]
    selected = [
        part
        for part in intact_parts
        if part_lod(part.name) in (None, intact_lod)
    ]
    if selected:
        return selected, intact_lod, False

    available_lods = sorted(
        {level for part in intact_parts if (level := part_lod(part.name)) is not None}
    )
    if available_lods:
        fallback_lod = available_lods[0]
        return (
            [part for part in intact_parts if part_lod(part.name) == fallback_lod],
            fallback_lod,
            True,
        )
    if intact_parts:
        return intact_parts, None, True
    raise GeometryError("intact filter removed every mesh part")


def decode_geometry_files(
    input_paths: Sequence[Path | str], intact_lod: int | None = 0
) -> tuple[list[DecodedGeometryFile], list[MeshPart]]:
    """Decode and merge one or more extracted .geometry files.

    The returned mesh-part list is ready for :func:`write_obj`. When several
    files are supplied, object names are namespaced by the input stem.
    """
    paths = [Path(path) for path in input_paths]
    if not paths:
        raise GeometryError("at least one .geometry input is required")

    results: list[DecodedGeometryFile] = []
    merged_parts: list[MeshPart] = []
    stem_uses: dict[str, int] = {}

    for path in paths:
        sections, all_parts = decode_geometry(path.read_bytes())
        chosen, selected_lod, fallback_used = select_intact_parts(
            all_parts, intact_lod
        )
        resolved_source = str(path.resolve())
        namespace = ""
        if len(paths) > 1:
            stem_uses[path.stem] = stem_uses.get(path.stem, 0) + 1
            occurrence = stem_uses[path.stem]
            namespace = path.stem if occurrence == 1 else f"{path.stem}_{occurrence}"

        selected_parts = [
            replace(
                part,
                name=f"{namespace}__{part.name}" if namespace else part.name,
                source=resolved_source,
            )
            for part in chosen
        ]
        results.append(
            DecodedGeometryFile(
                input_path=path,
                sections=sections,
                all_parts=all_parts,
                selected_parts=selected_parts,
                requested_lod=intact_lod,
                selected_lod=selected_lod,
                fallback_used=fallback_used,
            )
        )
        merged_parts.extend(selected_parts)

    return results, merged_parts


def _safe_obj_name(name: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in name
    )


def write_obj(parts: Iterable[MeshPart], output: BinaryIO) -> dict[str, int]:
    output.write(b"# World of Warships: Legends legacy geometry export\n")
    vertex_base = 1
    total_vertices = 0
    total_triangles = 0
    total_parts = 0

    for part in parts:
        safe_name = _safe_obj_name(part.name)
        output.write(f"o {safe_name}\n".encode("utf-8"))

        for vertex in part.vertices:
            output.write(
                ("v {:.9g} {:.9g} {:.9g}\n".format(*vertex.position)).encode("ascii")
            )
        for vertex in part.vertices:
            output.write(("vt {:.9g} {:.9g}\n".format(*vertex.uv)).encode("ascii"))
        for vertex in part.vertices:
            output.write(
                ("vn {:.9g} {:.9g} {:.9g}\n".format(*vertex.normal)).encode("ascii")
            )

        # Primitive groups only name ranges; all triangles are emitted exactly once.
        group_by_triangle: dict[int, int] = {}
        for group_index, group in enumerate(part.primitive_groups):
            first_triangle = group.start_index // 3
            for triangle_index in range(
                first_triangle, first_triangle + group.primitive_count
            ):
                group_by_triangle[triangle_index] = group_index

        active_group: int | None = None
        for triangle_index, triangle in enumerate(part.triangles):
            group = group_by_triangle.get(triangle_index, -1)
            if group != active_group:
                output.write(f"g {safe_name}_group_{group}\n".encode("utf-8"))
                active_group = group
            indices = [vertex_base + index for index in triangle]
            output.write(
                (
                    "f "
                    + " ".join(f"{index}/{index}/{index}" for index in indices)
                    + "\n"
                ).encode("ascii")
            )

        vertex_base += len(part.vertices)
        total_vertices += len(part.vertices)
        total_triangles += len(part.triangles)
        total_parts += 1

    return {
        "parts": total_parts,
        "vertices": total_vertices,
        "triangles": total_triangles,
    }


def _part_report(part: MeshPart) -> dict[str, object]:
    return {
        "name": part.name,
        "source": part.source,
        "vertex_format": part.vertex_format,
        "vertex_stride": part.vertex_stride,
        "index_format": part.index_format,
        "vertices": len(part.vertices),
        "triangles": len(part.triangles),
        "primitive_groups": [asdict(group) for group in part.primitive_groups],
    }


def build_report(
    decoded_files: Sequence[DecodedGeometryFile],
    output_path: Path,
    parts: Sequence[MeshPart],
    totals: dict[str, int],
) -> dict[str, object]:
    report: dict[str, object] = {
        "inputs": [
            {
                "input": str(item.input_path.resolve()),
                "requested_intact_lod": item.requested_lod,
                "selected_lod": item.selected_lod,
                "fallback_used": item.fallback_used,
                "all_part_count": len(item.all_parts),
                "selected_part_count": len(item.selected_parts),
                "excluded_parts": [
                    part.name
                    for part in item.all_parts
                    if part.name
                    not in {
                        selected.name.split("__", 1)[-1]
                        for selected in item.selected_parts
                    }
                ],
                "sections": [asdict(section) for section in item.sections],
            }
            for item in decoded_files
        ],
        "output_obj": str(output_path.resolve()),
        "totals": totals,
        "parts": [_part_report(part) for part in parts],
    }
    if len(decoded_files) == 1:
        report["input"] = str(decoded_files[0].input_path.resolve())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decode and merge legacy sectioned WoWS/WoT .geometry files to OBJ. "
            "The final positional path is the OBJ output."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="one or more extracted .geometry inputs",
    )
    parser.add_argument("output", type=Path, help="merged OBJ output")
    parser.add_argument(
        "--intact-lod",
        type=int,
        default=0,
        metavar="N",
        help=(
            "select intact LOD N (default: 0); joint patches/exterior crack "
            "surfaces stay, damage cracks/dead parts are excluded"
        ),
    )
    parser.add_argument(
        "--all-parts",
        action="store_true",
        help="disable intact/damage/LOD filtering",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON report path (defaults to <output>.json)",
    )
    args = parser.parse_args()

    requested_lod = None if args.all_parts else args.intact_lod
    decoded_files, parts = decode_geometry_files(args.inputs, requested_lod)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as output:
        totals = write_obj(parts, output)

    report_path = args.report or args.output.with_suffix(args.output.suffix + ".json")
    report = build_report(decoded_files, args.output, parts, totals)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"decoded {len(decoded_files)} file(s), {len(parts)} selected part(s), "
        f"{totals['vertices']} vertices, {totals['triangles']} triangles"
    )
    for item in decoded_files:
        if item.fallback_used:
            print(
                f"LOD fallback: {item.input_path.name}: requested "
                f"{item.requested_lod}, selected {item.selected_lod}"
            )
    print(f"OBJ: {args.output}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
