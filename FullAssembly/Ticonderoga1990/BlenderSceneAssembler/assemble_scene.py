#!/usr/bin/env python3
"""Assemble hull and mount GLBs into a Blender scene.

Run with Blender 3.5+:

    blender -b --python assemble_scene.py -- \
      --plan assembly_plan.json \
      --output assembled.blend \
      --glb assembled.glb \
      --validation validation.json

The plan stores transforms as 16-element, column-major game-node matrices.  The
legacy ModelUber node space is Y-up with +Z toward the bow, while decoded hull
geometry uses +Y toward the bow. Placements therefore use B * M_game * B^-1
where B maps (x, y, z) to (-x, z, y).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import bpy
from mathutils import Matrix, Vector


GAME_NODE_TO_BLENDER_BASIS = Matrix(
    (
        (-1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)


def _safe_name(value: object, fallback: str = "unnamed") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.")
    return (text or fallback)[:80]


def _canonical_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _resolve_path(value: str, plan_dir: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = plan_dir / path
    return Path(os.path.abspath(str(path)))


def _matrix_from_column_major(values: Sequence[object]) -> Matrix:
    if len(values) != 16:
        raise ValueError(f"matrix must contain 16 values, got {len(values)}")
    v = [float(item) for item in values]
    return Matrix(
        (
            (v[0], v[4], v[8], v[12]),
            (v[1], v[5], v[9], v[13]),
            (v[2], v[6], v[10], v[14]),
            (v[3], v[7], v[11], v[15]),
        )
    )


def game_node_matrix_to_blender(values: Sequence[object]) -> Matrix:
    """Convert a ModelUber game-node matrix to Blender world space."""
    game_matrix = _matrix_from_column_major(values)
    basis = GAME_NODE_TO_BLENDER_BASIS
    return basis @ game_matrix @ basis.inverted()


def _matrix_to_rows(matrix: Matrix) -> List[List[float]]:
    return [[float(matrix[row][column]) for column in range(4)] for row in range(4)]


def _new_collection(name: str, parent: Optional[bpy.types.Collection]) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    if parent is not None:
        parent.children.link(collection)
    return collection


def _remove_new_import_collections(
    collections_before: Set[bpy.types.Collection],
    protected: Iterable[bpy.types.Collection],
) -> None:
    protected_set = set(protected)
    for collection in list(bpy.data.collections):
        if collection in collections_before or collection in protected_set:
            continue
        try:
            bpy.data.collections.remove(collection)
        except RuntimeError:
            # A collection can still be referenced by an imported scene.  It is
            # harmless after all imported objects have been re-homed.
            pass


def _import_glb_into_collection(
    path: Path,
    target: bpy.types.Collection,
) -> List[bpy.types.Object]:
    objects_before = set(bpy.data.objects)
    collections_before = set(bpy.data.collections)

    result = bpy.ops.import_scene.gltf(filepath=str(path))
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender glTF importer did not finish for {path}")

    imported = [obj for obj in bpy.data.objects if obj not in objects_before]
    if not imported:
        raise RuntimeError(f"GLB imported no objects: {path}")

    for obj in imported:
        # Re-linking does not change local or world transforms.
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        target.objects.link(obj)

    _remove_new_import_collections(collections_before, (target,))
    return imported


def _material_and_image_summary() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    materials = sorted(
        {material.name for material in bpy.data.materials if material.users > 0}
    )
    images = sorted({image.name for image in bpy.data.images if image.users > 0})
    return (
        {"count": len(materials), "names": materials},
        {"count": len(images), "names": images},
    )


def _scene_bounds() -> Tuple[Optional[Dict[str, List[float]]], int]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    minimum: Optional[Vector] = None
    maximum: Optional[Vector] = None
    mesh_occurrences = 0

    # object_instances includes ordinary scene objects and collection-instance
    # descendants. Its matrix_world is already the final evaluated matrix. Use
    # actual vertices instead of transformed local AABBs: a rotated local AABB
    # can overestimate the scene by millimetres and falsely reject a valid OBJ.
    for occurrence in depsgraph.object_instances:
        obj = occurrence.object
        if obj is None or obj.type != "MESH" or not obj.data.vertices:
            continue
        mesh_occurrences += 1
        matrix = occurrence.matrix_world
        for vertex in obj.data.vertices:
            point = matrix @ vertex.co
            if minimum is None:
                minimum = point.copy()
                maximum = point.copy()
            else:
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], point[axis])
                    maximum[axis] = max(maximum[axis], point[axis])

    if minimum is None or maximum is None:
        return None, mesh_occurrences

    size = maximum - minimum
    return (
        {
            "min": [float(value) for value in minimum],
            "max": [float(value) for value in maximum],
            "size": [float(value) for value in size],
        },
        mesh_occurrences,
    )


def _get_or_create_child_collection(
    cache: Dict[Tuple[str, str], bpy.types.Collection],
    parent: bpy.types.Collection,
    cache_scope: str,
    display_name: str,
) -> bpy.types.Collection:
    key = (cache_scope, display_name)
    if key not in cache:
        cache[key] = _new_collection(_safe_name(display_name), parent)
    return cache[key]


def _bounds_from_object(obj: bpy.types.Object) -> Optional[Dict[str, List[float]]]:
    if obj.type != "MESH" or not obj.data.vertices:
        return None
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    minimum = [min(point[axis] for point in points) for axis in range(3)]
    maximum = [max(point[axis] for point in points) for axis in range(3)]
    return {
        "min": [float(value) for value in minimum],
        "max": [float(value) for value in maximum],
        "size": [float(maximum[axis] - minimum[axis]) for axis in range(3)],
    }


def _bounds_from_objects(
    objects: Sequence[bpy.types.Object],
) -> Optional[Dict[str, List[float]]]:
    bounds = [_bounds_from_object(obj) for obj in objects]
    valid = [item for item in bounds if item is not None]
    if not valid:
        return None
    minimum = [min(item["min"][axis] for item in valid) for axis in range(3)]
    maximum = [max(item["max"][axis] for item in valid) for axis in range(3)]
    return {
        "min": minimum,
        "max": maximum,
        "size": [maximum[axis] - minimum[axis] for axis in range(3)],
    }


def _bounds_max_delta(
    left: Optional[Dict[str, List[float]]],
    right: Optional[Dict[str, List[float]]],
) -> Optional[float]:
    if left is None or right is None:
        return None
    values = []
    for key in ("min", "max", "size"):
        if len(left.get(key, [])) != 3 or len(right.get(key, [])) != 3:
            return None
        values.extend(
            abs(float(left[key][axis]) - float(right[key][axis]))
            for axis in range(3)
        )
    return max(values, default=0.0)


def _compact_used_material_slots(joined: bpy.types.Object) -> Dict[str, Any]:
    """Keep exactly one slot for every material used by a joined polygon."""
    original_slots = list(joined.data.materials)
    polygon_materials: List[bpy.types.Material] = []
    for polygon in joined.data.polygons:
        if polygon.material_index >= len(original_slots):
            raise RuntimeError("joined polygon material index is out of range")
        material = original_slots[polygon.material_index]
        if material is None:
            raise RuntimeError("visible joined polygon has no material")
        polygon_materials.append(material)

    unique_materials: List[bpy.types.Material] = []
    slot_by_pointer: Dict[int, int] = {}
    for material in polygon_materials:
        pointer = material.as_pointer()
        if pointer not in slot_by_pointer:
            slot_by_pointer[pointer] = len(unique_materials)
            unique_materials.append(material)

    joined.data.materials.clear()
    for material in unique_materials:
        joined.data.materials.append(material)
    for polygon, material in zip(joined.data.polygons, polygon_materials):
        polygon.material_index = slot_by_pointer[material.as_pointer()]

    return {
        "count": len(unique_materials),
        "names": [material.name for material in unique_materials],
    }


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _source_png_payload(
    image: bpy.types.Image,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Return unchanged PNG bytes when Blender already owns a PNG payload."""
    if not bool(getattr(image, "is_dirty", False)):
        packed = getattr(image, "packed_file", None)
        if packed is not None:
            try:
                data = bytes(packed.data)
            except (AttributeError, BufferError, TypeError, ValueError):
                data = b""
            if data.startswith(_PNG_SIGNATURE):
                return data, "packed PNG passthrough"

        raw_path = str(getattr(image, "filepath", "") or "").strip()
        if raw_path:
            try:
                source = Path(bpy.path.abspath(raw_path))
                if source.is_file() and source.suffix.casefold() == ".png":
                    data = source.read_bytes()
                    if data.startswith(_PNG_SIGNATURE):
                        return data, "source PNG passthrough"
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
    return None, None


def _prepare_obj_material_textures(
    mesh_objects: Sequence[bpy.types.Object],
    texture_dir: Path,
    export_scene: bpy.types.Scene,
) -> List[Dict[str, Any]]:
    texture_dir.mkdir(parents=True, exist_ok=True)
    for stale in texture_dir.glob("*.png"):
        stale.unlink()

    image_cache: Dict[int, Tuple[bpy.types.Image, Path, str]] = {}
    digest_cache: Dict[str, Tuple[bpy.types.Image, Path]] = {}
    material_cache: Dict[int, bpy.types.Material] = {}
    texture_records: List[Dict[str, Any]] = []
    next_material = 0
    for mesh_object in mesh_objects:
        for material_index, material in enumerate(list(mesh_object.data.materials)):
            if material is None:
                continue
            material_key = material.as_pointer()
            cloned = material_cache.get(material_key)
            if cloned is None:
                cloned = material.copy()
                cloned.name = (
                    f"OBJ_{next_material:03d}_{_safe_name(material.name)}"
                )
                next_material += 1
                if cloned.use_nodes and cloned.node_tree is not None:
                    for node in cloned.node_tree.nodes:
                        if node.type != "TEX_IMAGE" or node.image is None:
                            continue
                        original_image = node.image
                        image_key = original_image.as_pointer()
                        cached = image_cache.get(image_key)
                        if cached is None:
                            serial = len(image_cache)
                            image_stem = _safe_name(
                                Path(original_image.name).stem,
                                f"image_{serial:03d}",
                            )
                            png_payload, materialization = _source_png_payload(
                                original_image
                            )
                            candidate = texture_dir / f".__candidate_{serial:03d}.png"
                            if png_payload is None:
                                original_image.save_render(
                                    filepath=str(candidate), scene=export_scene
                                )
                                if (
                                    not candidate.is_file()
                                    or candidate.stat().st_size <= 0
                                ):
                                    raise RuntimeError(
                                        "failed to materialize OBJ texture: "
                                        f"{original_image.name}"
                                    )
                                png_payload = candidate.read_bytes()
                                materialization = "Blender Image.save_render"
                            digest = hashlib.sha256(png_payload).hexdigest()
                            duplicate = digest in digest_cache
                            if duplicate:
                                exported_image, destination = digest_cache[digest]
                                candidate.unlink(missing_ok=True)
                            else:
                                destination = texture_dir / (
                                    f"tex_{digest[:16]}_{image_stem}.png"
                                )
                                if candidate.is_file():
                                    candidate.replace(destination)
                                else:
                                    destination.write_bytes(png_payload)
                                exported_image = bpy.data.images.load(
                                    str(destination), check_existing=False
                                )
                                exported_image.name = (
                                    f"OBJTEX_{digest[:12]}_{image_stem}"
                                )
                                digest_cache[digest] = (
                                    exported_image,
                                    destination,
                                )
                            image_cache[image_key] = (
                                exported_image,
                                destination,
                                digest,
                            )
                            image_cache.setdefault(
                                exported_image.as_pointer(), image_cache[image_key]
                            )
                            cached = image_cache[image_key]
                            texture_records.append(
                                {
                                    "source_image": original_image.name,
                                    "file": str(destination),
                                    "relative": (
                                        f"textures/{destination.name}"
                                    ),
                                    "bytes": destination.stat().st_size,
                                    "sha256": digest,
                                    "deduplicated": duplicate,
                                    "packed_source": (
                                        original_image.packed_file is not None
                                    ),
                                    "source_colorspace": (
                                        original_image.colorspace_settings.name
                                    ),
                                    "png_materialization": materialization,
                                }
                            )
                        node.image = cached[0]
                material_cache[material_key] = cloned
            mesh_object.data.materials[material_index] = cloned
    return texture_records


def _export_obj_selected(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(bpy.ops.export_scene, "obj"):
        result = bpy.ops.export_scene.obj(
            filepath=str(path),
            use_selection=True,
            use_mesh_modifiers=True,
            use_edges=True,
            use_normals=True,
            use_uvs=True,
            use_materials=True,
            use_triangles=False,
            use_nurbs=False,
            use_vertex_groups=False,
            use_blen_objects=True,
            group_by_object=False,
            group_by_material=False,
            keep_vertex_order=True,
            path_mode="RELATIVE",
            axis_forward="-Z",
            axis_up="Y",
        )
    else:
        result = bpy.ops.wm.obj_export(
            filepath=str(path),
            export_selected_objects=True,
            export_materials=True,
            export_uv=True,
            export_normals=True,
            path_mode="RELATIVE",
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
    if "FINISHED" not in result:
        raise RuntimeError("Blender OBJ exporter did not finish")


def _normalize_obj_mtllib(obj_path: Path, mtl_path: Path) -> Dict[str, Any]:
    text = obj_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    mtllib_indices = [
        index for index, line in enumerate(lines) if line.strip().startswith("mtllib ")
    ]
    if len(mtllib_indices) != 1:
        raise RuntimeError(
            f"OBJ must contain exactly one mtllib declaration, got {len(mtllib_indices)}"
        )
    lines[mtllib_indices[0]] = f"mtllib {mtl_path.name}"
    obj_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "mtllib": mtl_path.name,
        "absolute": Path(mtl_path.name).is_absolute(),
    }


def _normalize_and_validate_mtl(
    mtl_path: Path,
    texture_dir: Path,
) -> Dict[str, Any]:
    if not mtl_path.is_file():
        raise RuntimeError(f"OBJ MTL was not created: {mtl_path}")
    available = {
        path.name: path for path in texture_dir.glob("*.png") if path.is_file()
    }
    if len({name.casefold() for name in available}) != len(available):
        raise RuntimeError("case-insensitive OBJ texture filename collision")

    output_lines: List[str] = []
    references: List[str] = []
    material_names: List[str] = []
    for raw_line in mtl_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            output_lines.append(raw_line)
            continue
        tokens = shlex.split(stripped, posix=False)
        if not tokens:
            output_lines.append(raw_line)
            continue
        keyword = tokens[0]
        if keyword == "newmtl" and len(tokens) >= 2:
            material_names.append(" ".join(tokens[1:]).strip('"'))
        if keyword.casefold().startswith("map_"):
            if len(tokens) < 2:
                raise RuntimeError(f"invalid MTL texture declaration: {raw_line}")
            raw_reference = tokens[-1].strip('"').replace("\\", "/")
            basename = raw_reference.rsplit("/", 1)[-1]
            if basename not in available:
                raise RuntimeError(
                    f"MTL texture reference does not resolve to generated PNG: "
                    f"{raw_reference}"
                )
            relative = f"textures/{basename}"
            if (
                relative.startswith("/")
                or re.match(r"^[A-Za-z]:", relative)
                or ".." in relative.split("/")
                or not relative.endswith(".png")
            ):
                raise RuntimeError(f"non-portable MTL texture path: {relative}")
            tokens[-1] = relative
            references.append(relative)
            output_lines.append(" ".join(tokens))
        else:
            output_lines.append(raw_line)
    mtl_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    referenced_names = {reference.split("/", 1)[1] for reference in references}
    for path in texture_dir.glob("*.png"):
        if path.name not in referenced_names:
            path.unlink()
    missing = [
        reference
        for reference in references
        if not (mtl_path.parent / Path(reference.replace("/", os.sep))).is_file()
    ]
    absolute = [
        reference
        for reference in references
        if reference.startswith("/") or re.match(r"^[A-Za-z]:", reference)
    ]
    if not references:
        raise RuntimeError("MTL contains no map_* texture references")
    if missing or absolute:
        raise RuntimeError(
            f"MTL portability failure: missing={missing}, absolute={absolute}"
        )
    return {
        "material_names": material_names,
        "material_count": len(material_names),
        "map_references": references,
        "unique_map_references": sorted(set(references)),
        "missing_map_references": missing,
        "absolute_map_references": absolute,
        "texture_files": sorted(
            f"textures/{path.name}" for path in texture_dir.glob("*.png")
        ),
    }


def _negate_obj_number(token: str) -> str:
    try:
        if float(token) == 0.0:
            return token.lstrip("-")
    except ValueError:
        return token
    if token.startswith("-"):
        return token[1:]
    if token.startswith("+"):
        return "-" + token[1:]
    return "-" + token


def _repair_obj_mirrored_winding(
    obj_path: Path,
    mirrored_object_names: Iterable[str],
) -> Dict[str, Any]:
    """Reverse faces and normals baked through a negative-determinant mount.

    Blender's legacy OBJ exporter bakes the reflected vertex positions but keeps
    the original face order. Two-sided Blender viewports hide that mismatch,
    while normal front-face renderers expose the inner shell. Repair only the
    explicitly mirrored realized objects and atomically replace the new OBJ.
    """
    requested = {str(name) for name in mirrored_object_names if str(name)}
    report: Dict[str, Any] = {
        "requested_objects": sorted(requested),
        "matched_objects": [],
        "missing_objects": [],
        "faces_reversed": 0,
        "normals_reversed": 0,
    }
    if not requested:
        return report

    temporary = obj_path.with_suffix(obj_path.suffix + ".winding.tmp")
    matched: Set[str] = set()
    requested_by_length = sorted(requested, key=len, reverse=True)
    active = False
    try:
        with obj_path.open("r", encoding="utf-8", errors="replace") as source, \
                temporary.open("w", encoding="utf-8", newline="\n") as target:
            for raw_line in source:
                line = raw_line.rstrip("\r\n")
                if line.startswith("o "):
                    object_name = line[2:].strip()
                    matched_name = next(
                        (
                            name
                            for name in requested_by_length
                            if object_name == name
                            or object_name.startswith(name + "_")
                        ),
                        None,
                    )
                    active = matched_name is not None
                    if matched_name is not None:
                        matched.add(matched_name)
                elif active and line.startswith("vn "):
                    tokens = line.split()
                    if len(tokens) >= 4:
                        tokens[1:4] = [
                            _negate_obj_number(value) for value in tokens[1:4]
                        ]
                        line = " ".join(tokens)
                        report["normals_reversed"] += 1
                elif active and line.startswith("f "):
                    tokens = line.split()
                    if len(tokens) >= 4:
                        line = "f " + " ".join(reversed(tokens[1:]))
                        report["faces_reversed"] += 1
                target.write(line + "\n")

        missing = requested - matched
        report["matched_objects"] = sorted(matched)
        report["missing_objects"] = sorted(missing)
        if missing:
            raise RuntimeError(
                "mirrored OBJ winding targets were not exported: "
                + ", ".join(sorted(missing))
            )
        if report["faces_reversed"] <= 0 or report["normals_reversed"] <= 0:
            raise RuntimeError("mirrored OBJ winding repair changed no faces or normals")
        os.replace(temporary, obj_path)
        return report
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_obj_contract(obj_path: Path) -> Dict[str, Any]:
    text = obj_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    values = {
        "vertices": sum(line.startswith("v ") for line in lines),
        "uvs": sum(line.startswith("vt ") for line in lines),
        "normals": sum(line.startswith("vn ") for line in lines),
        "faces": sum(line.startswith("f ") for line in lines),
        "object_names": [line[2:].strip() for line in lines if line.startswith("o ")],
        "group_names": [line[2:].strip() for line in lines if line.startswith("g ")],
        "usemtl": [line[7:].strip() for line in lines if line.startswith("usemtl ")],
        "text": text,
    }
    coordinates = []
    for line in lines:
        if not line.startswith("v "):
            continue
        parts = line.split()
        if len(parts) >= 4:
            coordinates.append(tuple(float(value) for value in parts[1:4]))
    if coordinates:
        minimum = [min(point[axis] for point in coordinates) for axis in range(3)]
        maximum = [max(point[axis] for point in coordinates) for axis in range(3)]
        values["bounds"] = {
            "min": minimum,
            "max": maximum,
            "size": [maximum[axis] - minimum[axis] for axis in range(3)],
        }
    else:
        values["bounds"] = None
    return values


def _obj_export_bounds_to_blender(
    bounds: Optional[Dict[str, List[float]]],
) -> Optional[Dict[str, List[float]]]:
    """Convert raw OBJ -Z-forward/Y-up bounds back to Blender Z-up bounds."""
    if not isinstance(bounds, dict):
        return None
    minimum = bounds.get("min")
    maximum = bounds.get("max")
    if not isinstance(minimum, list) or not isinstance(maximum, list):
        return None
    if len(minimum) != 3 or len(maximum) != 3:
        return None
    converted_min = [minimum[0], -maximum[2], minimum[1]]
    converted_max = [maximum[0], -minimum[2], maximum[1]]
    return {
        "min": converted_min,
        "max": converted_max,
        "size": [
            converted_max[axis] - converted_min[axis] for axis in range(3)
        ],
    }


def _import_obj_clean(path: Path) -> List[bpy.types.Object]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if hasattr(bpy.ops.import_scene, "obj"):
        result = bpy.ops.import_scene.obj(
            filepath=str(path),
            use_split_objects=True,
            use_split_groups=False,
            axis_forward="-Z",
            axis_up="Y",
        )
    else:
        result = bpy.ops.wm.obj_import(
            filepath=str(path),
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
    if "FINISHED" not in result:
        raise RuntimeError("clean OBJ reimport did not finish")
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def _export_visible_combined_obj(
    output_obj: Path,
    output_blend: Path,
    expected_bounds: Optional[Dict[str, List[float]]],
    expected_mesh_occurrences: int,
    instance_records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    output_obj = Path(os.path.abspath(str(output_obj)))
    output_mtl = output_obj.with_suffix(".mtl")
    texture_dir = output_obj.parent / "textures"
    hidden_names = [
        str(record["object"])
        for record in instance_records
        if not record.get("visible", True)
    ]
    visible_names = [
        str(record["object"])
        for record in instance_records
        if record.get("visible", True)
    ]
    result: Dict[str, Any] = {
        "ok": False,
        "obj": str(output_obj),
        "mtl": str(output_mtl),
        "texture_directory": str(texture_dir),
        "included_visible_mounts": visible_names,
        "excluded_hidden_mounts": hidden_names,
        "limitations": [
            "OBJ/MTL preserves UVs, normals, material assignments, and referenced PNGs, "
            "but it cannot represent the complete Principled/PBR node graph.",
            "Metallic/roughness packing, AO/detail mixing, transparency modes, and other "
            "shader-specific semantics are approximated by Blender's MTL exporter.",
            "Packed Blender images are materialized as PNG through Image.save_render; "
            "color-space conversion and precision loss can occur, especially for normal "
            "or packed data maps. Use the BLEND/GLB for the authoritative PBR result.",
        ],
    }
    errors: List[str] = []
    try:
        output_obj.parent.mkdir(parents=True, exist_ok=True)
        for stale in (output_obj, output_mtl):
            if stale.exists():
                stale.unlink()

        bpy.ops.scene.new(type="FULL_COPY")
        export_scene = bpy.context.scene
        export_scene.name = "__COMBINED_VISIBLE_OBJ_EXPORT__"
        instance_roots = [
            obj
            for obj in list(export_scene.objects)
            if obj.instance_type == "COLLECTION" and obj.instance_collection is not None
        ]
        visible_roots = [
            obj
            for obj in instance_roots
            if bool(obj.get("visible", not obj.hide_render))
            and not obj.hide_render
            and not obj.hide_viewport
        ]
        bpy.ops.object.select_all(action="DESELECT")
        for obj in visible_roots:
            obj.hide_set(False)
            obj.select_set(True)
        if visible_roots:
            bpy.context.view_layer.objects.active = visible_roots[0]
            made_real = bpy.ops.object.duplicates_make_real(
                use_base_parent=False, use_hierarchy=True
            )
            if "FINISHED" not in made_real:
                raise RuntimeError("collection instance realization did not finish")
        for root in instance_roots:
            if root.name in bpy.data.objects:
                bpy.data.objects.remove(root, do_unlink=True)
        bpy.context.view_layer.update()

        mesh_objects = [
            obj
            for obj in export_scene.objects
            if obj.type == "MESH"
            and obj.visible_get()
            and not obj.hide_render
            and not obj.hide_viewport
        ]
        source_mesh_objects = len(mesh_objects)
        if source_mesh_objects != expected_mesh_occurrences:
            raise RuntimeError(
                "visible mesh realization count mismatch: "
                f"expected {expected_mesh_occurrences}, got {source_mesh_objects}"
            )
        if not mesh_objects:
            raise RuntimeError("visible-state realization produced no meshes")

        bpy.ops.object.select_all(action="DESELECT")
        for obj in mesh_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objects[0]
        joined_result = bpy.ops.object.join()
        if "FINISHED" not in joined_result:
            raise RuntimeError("visible mesh join did not finish")
        joined = bpy.context.view_layer.objects.active
        if joined is None or joined.type != "MESH":
            raise RuntimeError("joined visible result is not a mesh")
        combined_name = output_obj.stem
        joined.name = combined_name
        joined.data.name = f"{combined_name}_Mesh"
        joined.hide_set(False)
        joined.hide_viewport = False
        joined.hide_render = False
        material_contract = _compact_used_material_slots(joined)
        expected_material_count = int(material_contract["count"])
        if expected_material_count <= 0:
            raise RuntimeError("visible joined result has no used materials")
        bpy.context.view_layer.update()

        joined_bounds = _bounds_from_object(joined)
        bounds_delta = _bounds_max_delta(expected_bounds, joined_bounds)
        texture_records = _prepare_obj_material_textures(
            [joined], texture_dir, export_scene
        )
        bpy.ops.object.select_all(action="DESELECT")
        joined.select_set(True)
        bpy.context.view_layer.objects.active = joined
        _export_obj_selected(output_obj)
        mtllib = _normalize_obj_mtllib(output_obj, output_mtl)
        mtl = _normalize_and_validate_mtl(output_mtl, texture_dir)
        obj_contract = _parse_obj_contract(output_obj)
        hidden_in_text = [
            name
            for name in hidden_names
            if name in obj_contract["text"]
            or name in output_mtl.read_text(encoding="utf-8", errors="replace")
        ]
        raw_obj_bounds_delta = _bounds_max_delta(
            expected_bounds, obj_contract["bounds"]
        )
        material_slot_count = len(joined.data.materials)

        imported_meshes = _import_obj_clean(output_obj)
        clean_bounds = (
            _bounds_from_object(imported_meshes[0])
            if len(imported_meshes) == 1
            else None
        )
        clean_bounds_delta = _bounds_max_delta(expected_bounds, clean_bounds)
        # The legacy Blender OBJ exporter writes coordinates in the selected
        # OBJ axis basis. A clean Blender re-import applies the inverse basis,
        # so this is the portable comparison against the source/GLB scene.
        obj_bounds_delta = clean_bounds_delta
        clean_uv_layers = (
            len(imported_meshes[0].data.uv_layers)
            if len(imported_meshes) == 1
            else 0
        )
        clean_materials = (
            len(imported_meshes[0].data.materials)
            if len(imported_meshes) == 1
            else 0
        )

        conditions = {
            "vertices_positive": obj_contract["vertices"] > 0,
            "faces_positive": obj_contract["faces"] > 0,
            "one_obj_object": len(obj_contract["object_names"]) == 1
            and obj_contract["object_names"][0].startswith(combined_name),
            "uvs_preserved": obj_contract["uvs"] > 0 and clean_uv_layers > 0,
            "normals_preserved": obj_contract["normals"] > 0,
            "materials_preserved": material_slot_count
            == expected_material_count
            == len(set(obj_contract["usemtl"]))
            == len(set(mtl["material_names"]))
            == clean_materials,
            "mtllib_relative_basename": mtllib["mtllib"] == output_mtl.name
            and not mtllib["absolute"],
            "texture_refs_portable": bool(mtl["map_references"])
            and not mtl["missing_map_references"]
            and not mtl["absolute_map_references"]
            and all(
                reference.startswith("textures/")
                and reference.endswith(".png")
                and "\\" not in reference
                for reference in mtl["map_references"]
            ),
            "hidden_overlay_names_excluded": not hidden_in_text,
            "source_occurrences_exact": source_mesh_objects
            == expected_mesh_occurrences,
            "joined_bounds_match": bounds_delta is not None
            and bounds_delta <= 1e-4,
            "obj_bounds_match": obj_bounds_delta is not None
            and obj_bounds_delta <= 1e-4,
            "clean_reimport_one_mesh": len(imported_meshes) == 1,
            "clean_reimport_bounds_match": clean_bounds_delta is not None
            and clean_bounds_delta <= 1e-4,
        }
        errors.extend(
            name for name, accepted in conditions.items() if not accepted
        )
        result.update(
            {
                "source_visible_mesh_objects": source_mesh_objects,
                "unified_mesh_objects": 1,
                "vertices": obj_contract["vertices"],
                "uv_records": obj_contract["uvs"],
                "normal_records": obj_contract["normals"],
                "faces": obj_contract["faces"],
                "material_slots": material_slot_count,
                "expected_materials": expected_material_count,
                "expected_visible_material_names": material_contract["names"],
                "usemtl": sorted(set(obj_contract["usemtl"])),
                "object_names": obj_contract["object_names"],
                "group_names": obj_contract["group_names"],
                "mtllib": mtllib["mtllib"],
                "mtl_materials": mtl["material_names"],
                "map_references": mtl["map_references"],
                "texture_files": mtl["texture_files"],
                "generated_texture_records": texture_records,
                "missing_map_references": mtl["missing_map_references"],
                "absolute_map_references": mtl["absolute_map_references"],
                "hidden_names_present_in_obj_or_mtl": hidden_in_text,
                "expected_bounds": expected_bounds,
                "joined_bounds": joined_bounds,
                "obj_bounds": clean_bounds,
                "raw_obj_coordinate_bounds": obj_contract["bounds"],
                "bounds_max_delta": bounds_delta,
                "obj_bounds_max_delta": obj_bounds_delta,
                "raw_obj_coordinate_bounds_max_delta": raw_obj_bounds_delta,
                "clean_reimport": {
                    "mesh_objects": len(imported_meshes),
                    "bounds": clean_bounds,
                    "bounds_max_delta": clean_bounds_delta,
                    "uv_layers": clean_uv_layers,
                    "materials": clean_materials,
                },
                "checks": conditions,
                "errors": errors,
                "ok": not errors,
            }
        )
    except Exception as exc:
        errors.append(str(exc))
        result["error"] = str(exc)
        result["errors"] = errors
        result["traceback"] = traceback.format_exc()
        result["ok"] = False
    finally:
        try:
            bpy.ops.wm.open_mainfile(filepath=str(output_blend))
            saved_instances = [
                obj
                for obj in bpy.context.scene.objects
                if obj.instance_type == "COLLECTION" and "plan_index" in obj
            ]
            expected_states = {
                str(record["object"]): bool(record.get("visible", True))
                for record in instance_records
            }
            actual_states = {
                obj.name: {
                    "visible_property": bool(obj.get("visible", True)),
                    "hide_viewport": bool(obj.hide_viewport),
                    "hide_render": bool(obj.hide_render),
                }
                for obj in saved_instances
            }
            state_mismatches = []
            for name, expected_visible in expected_states.items():
                actual = actual_states.get(name)
                if actual is None:
                    state_mismatches.append({"name": name, "reason": "missing"})
                    continue
                expected_hidden = not expected_visible
                if (
                    actual["visible_property"] != expected_visible
                    or actual["hide_viewport"] != expected_hidden
                    or actual["hide_render"] != expected_hidden
                ):
                    state_mismatches.append(
                        {
                            "name": name,
                            "expected_visible": expected_visible,
                            "actual": actual,
                        }
                    )
            unexpected_names = sorted(set(actual_states) - set(expected_states))
            saved_hidden = sorted(
                name
                for name, state in actual_states.items()
                if not state["visible_property"]
                and state["hide_viewport"]
                and state["hide_render"]
            )
            preservation = {
                "mount_instances": len(saved_instances),
                "hidden_mount_instances": len(saved_hidden),
                "hidden_mount_names": saved_hidden,
                "expected_hidden_mount_names": sorted(hidden_names),
                "expected_mount_instances": len(instance_records),
                "expected_hidden_mount_instances": len(hidden_names),
                "state_mismatches": state_mismatches,
                "unexpected_instance_names": unexpected_names,
                "ok": set(actual_states) == set(expected_states)
                and saved_hidden == sorted(hidden_names)
                and not state_mismatches
                and not unexpected_names,
            }
            result["original_blend_preservation"] = preservation
            if not preservation["ok"]:
                errors.append("original_blend_mount_visibility_not_preserved")
                result["errors"] = errors
                result["ok"] = False
        except Exception as exc:
            errors.append(f"original BLEND reopen validation failed: {exc}")
            result["errors"] = errors
            result["ok"] = False
    return result


def _export_visible_editable_obj(
    output_obj: Path,
    expected_bounds: Optional[Dict[str, List[float]]],
    expected_mesh_occurrences: int,
    instance_records: Sequence[Dict[str, Any]],
    deep_validate_obj: bool = False,
) -> Dict[str, Any]:
    """Export one portable OBJ while preserving independently selectable parts."""
    output_obj = Path(os.path.abspath(str(output_obj)))
    output_mtl = output_obj.with_suffix(".mtl")
    texture_dir = output_obj.parent / "textures"
    hidden_names = [
        str(item["object"]) for item in instance_records
        if not item.get("visible", True)
    ]
    visible_names = [
        str(item["object"]) for item in instance_records
        if item.get("visible", True)
    ]
    result: Dict[str, Any] = {
        "ok": False,
        "mode": "editable_obj_only",
        "obj": str(output_obj),
        "mtl": str(output_mtl),
        "texture_directory": str(texture_dir),
        "included_visible_mounts": visible_names,
        "excluded_hidden_mounts": hidden_names,
        "limitations": [
            "OBJ/MTL keeps separate selectable mesh objects, UVs, normals, "
            "materials, and portable PNG texture references.",
            "OBJ cannot keep Blender collections, parenting, animations, pivots, "
            "or the complete Principled/PBR node graph.",
        ],
    }
    errors: List[str] = []
    total_started = time.perf_counter()
    try:
        output_obj.parent.mkdir(parents=True, exist_ok=True)
        for stale in (output_obj, output_mtl):
            if stale.exists():
                stale.unlink()

        export_scene = bpy.context.scene
        export_scene.name = "__EDITABLE_VISIBLE_OBJ_EXPORT__"
        roots = [
            obj for obj in list(export_scene.objects)
            if obj.instance_type == "COLLECTION"
            and obj.instance_collection is not None
        ]
        visible_roots = [
            obj for obj in roots
            if bool(obj.get("visible", not obj.hide_render))
            and not obj.hide_render
            and not obj.hide_viewport
        ]

        used_names: Set[str] = set()

        def unique_name(base: str) -> str:
            candidate = _safe_name(base)
            serial = 2
            while candidate.casefold() in used_names:
                suffix = f"_{serial:02d}"
                candidate = f"{_safe_name(base)[:80 - len(suffix)]}{suffix}"
                serial += 1
            used_names.add(candidate.casefold())
            return candidate

        hull_meshes = [
            obj for obj in export_scene.objects
            if obj.type == "MESH" and obj.visible_get() and not obj.hide_render
        ]
        for index, obj in enumerate(sorted(hull_meshes, key=lambda item: item.name)):
            obj.name = unique_name(
                f"Hull_{index:03d}_{_safe_name(obj.name, 'part')}"
            )
            obj.data.name = f"{obj.name}_Mesh"

        realized_records: List[Dict[str, Any]] = []
        for root in sorted(
            visible_roots, key=lambda item: int(item.get("plan_index", 0))
        ):
            collection = root.instance_collection
            mirrored = root.matrix_world.to_3x3().determinant() < -1e-8
            source_meshes = sorted(
                [obj for obj in collection.all_objects if obj.type == "MESH"],
                key=lambda item: item.name,
            )
            if not source_meshes:
                raise RuntimeError(f"instance produced no mesh: {root.name}")
            source = _safe_name(
                Path(str(root.get("source_glb", "mount"))).stem, "mount"
            )
            names = []
            for part_index, source_obj in enumerate(source_meshes):
                obj = source_obj.copy()
                obj.data = source_obj.data.copy()
                obj.animation_data_clear()
                obj.parent = None
                obj.matrix_world = root.matrix_world @ source_obj.matrix_world
                obj.hide_set(False)
                obj.hide_viewport = False
                obj.hide_render = False
                export_scene.collection.objects.link(obj)
                tail = "" if len(source_meshes) == 1 else (
                    f"_part_{part_index:02d}_{_safe_name(source_obj.name, 'mesh')}"
                )
                obj.name = unique_name(f"{root.name}_{source}{tail}")
                obj.data.name = f"{obj.name}_Mesh"
                names.append(obj.name)
            realized_records.append(
                {
                    "instance": root.name,
                    "source": source,
                    "objects": names,
                    "mirrored": mirrored,
                }
            )

        for root in roots:
            if root.name in bpy.data.objects:
                bpy.data.objects.remove(root, do_unlink=True)
        bpy.context.view_layer.update()

        meshes = sorted(
            [
                obj for obj in export_scene.objects
                if obj.type == "MESH"
                and obj.visible_get()
                and not obj.hide_render
                and not obj.hide_viewport
            ],
            key=lambda item: item.name,
        )
        if len(meshes) != expected_mesh_occurrences:
            raise RuntimeError(
                "visible mesh count mismatch: "
                f"expected {expected_mesh_occurrences}, got {len(meshes)}"
            )
        if not meshes:
            raise RuntimeError("editable export has no meshes")

        for obj in meshes:
            _compact_used_material_slots(obj)
        editable_bounds = _bounds_from_objects(meshes)
        bounds_delta = _bounds_max_delta(expected_bounds, editable_bounds)
        realization_seconds = time.perf_counter() - total_started
        texture_started = time.perf_counter()
        texture_records = _prepare_obj_material_textures(
            meshes, texture_dir, export_scene
        )
        texture_seconds = time.perf_counter() - texture_started

        obj_write_started = time.perf_counter()
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        _export_obj_selected(output_obj)
        mirrored_object_names = {
            name
            for record in realized_records
            if record["mirrored"]
            for name in record["objects"]
        }
        winding_correction = _repair_obj_mirrored_winding(
            output_obj, mirrored_object_names
        )
        mtllib = _normalize_obj_mtllib(output_obj, output_mtl)
        mtl = _normalize_and_validate_mtl(output_mtl, texture_dir)
        contract = _parse_obj_contract(output_obj)
        obj_write_seconds = time.perf_counter() - obj_write_started
        hidden_in_text = [
            name for name in hidden_names
            if name in contract["text"]
            or name in output_mtl.read_text(encoding="utf-8", errors="replace")
        ]

        reimport_started = time.perf_counter()
        if deep_validate_obj:
            imported = _import_obj_clean(output_obj)
            clean_bounds = _bounds_from_objects(imported)
            clean_reimport = {"performed": True, "mesh_objects": len(imported)}
        else:
            imported = []
            clean_bounds = _obj_export_bounds_to_blender(contract.get("bounds"))
            clean_reimport = {
                "performed": False,
                "mesh_objects": None,
                "reason": "fast contract and exported-bounds validation",
            }
        clean_delta = _bounds_max_delta(expected_bounds, clean_bounds)
        names = contract["object_names"]
        texture_files = list(texture_dir.glob("*.png"))
        texture_hashes = {
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in texture_files
        }
        checks = {
            "geometry_present": contract["vertices"] > 0
            and contract["faces"] > 0,
            "separate_objects_preserved": len(names) == len(meshes),
            "object_names_unique": len({name.casefold() for name in names})
            == len(names),
            "uvs_and_normals_present": contract["uvs"] > 0
            and contract["normals"] > 0,
            "materials_preserved": set(contract["usemtl"])
            == set(mtl["material_names"]),
            "portable_mtl_and_textures": mtllib["mtllib"] == output_mtl.name
            and not mtllib["absolute"]
            and bool(mtl["map_references"])
            and not mtl["missing_map_references"]
            and not mtl["absolute_map_references"],
            "textures_content_unique": len(texture_files)
            == len(texture_hashes),
            "hidden_mounts_excluded": not hidden_in_text,
            "mirrored_winding_corrected": (
                set(winding_correction["matched_objects"])
                == mirrored_object_names
                and not winding_correction["missing_objects"]
            ),
            "source_bounds_match": bounds_delta is not None
            and bounds_delta <= 1e-4,
            "clean_reimport_count": (
                len(imported) == len(meshes) if deep_validate_obj else True
            ),
            "clean_reimport_bounds_match": clean_delta is not None
            and clean_delta <= 1e-4,
        }
        errors.extend(name for name, passed in checks.items() if not passed)
        reimport_seconds = time.perf_counter() - reimport_started
        timings = {
            "realization": round(realization_seconds, 3),
            "textures": round(texture_seconds, 3),
            "obj_write": round(obj_write_seconds, 3),
            "reimport_validation": round(reimport_seconds, 3),
            "total": round(time.perf_counter() - total_started, 3),
        }
        result.update(
            {
                "source_visible_mesh_objects": len(meshes),
                "editable_mesh_objects": len(meshes),
                "unified_mesh_objects": len(meshes),
                "realized_mounts": realized_records,
                "mirrored_winding_correction": winding_correction,
                "vertices": contract["vertices"],
                "faces": contract["faces"],
                "uv_records": contract["uvs"],
                "normal_records": contract["normals"],
                "object_names": names,
                "group_names": contract["group_names"],
                "usemtl": sorted(set(contract["usemtl"])),
                "mtllib": mtllib["mtllib"],
                "mtl_materials": mtl["material_names"],
                "map_references": mtl["map_references"],
                "texture_files": mtl["texture_files"],
                "generated_texture_records": texture_records,
                "timings_seconds": timings,
                "texture_source_images": len(texture_records),
                "unique_texture_files": len(texture_files),
                "deduplicated_texture_sources": len(texture_records) - len(texture_files),
                "hidden_names_present_in_obj_or_mtl": hidden_in_text,
                "expected_bounds": expected_bounds,
                "editable_bounds": editable_bounds,
                "bounds_max_delta": bounds_delta,
                "obj_bounds": clean_bounds,
                "obj_bounds_max_delta": clean_delta,
                "clean_reimport": clean_reimport,
                "original_blend_preservation": {
                    "skipped": True,
                    "reason": "editable OBJ-only mode creates no BLEND",
                    "ok": True,
                },
                "checks": checks,
                "errors": errors,
                "ok": not errors,
            }
        )
    except Exception as exc:
        errors.append(str(exc))
        result["error"] = str(exc)
        result["errors"] = errors
        result["traceback"] = traceback.format_exc()
    return result


def _save_and_export(
    output_blend: Optional[Path],
    output_glb: Optional[Path],
) -> Optional[str]:
    if output_blend is not None:
        output_blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)

    if output_glb is None:
        return None

    output_glb.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(output_glb),
        export_format="GLB",
        use_selection=False,
        use_visible=True,
        use_renderable=True,
        export_apply=False,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
    )
    if "FINISHED" not in result:
        return "Blender glTF exporter did not finish"
    return None


def assemble(
    plan_path: Path,
    output_blend: Optional[Path] = None,
    output_glb: Optional[Path] = None,
    output_obj: Optional[Path] = None,
    validation_path: Optional[Path] = None,
    obj_only: bool = False,
    editable_objects: bool = False,
    deep_validate_obj: bool = False,
) -> Dict[str, Any]:
    plan_path = Path(os.path.abspath(str(plan_path)))
    plan_dir = plan_path.parent
    with plan_path.open("r", encoding="utf-8-sig") as handle:
        plan = json.load(handle)
    if not isinstance(plan, dict):
        raise ValueError("assembly plan root must be a JSON object")

    def plan_output(cli_value: Optional[Path], key: str, default: str) -> Path:
        if cli_value is not None:
            return Path(os.path.abspath(str(cli_value)))
        raw = plan.get(key, default)
        return _resolve_path(str(raw), plan_dir)

    if obj_only:
        output_blend = None
        output_glb = None
    else:
        output_blend = plan_output(output_blend, "output_blend", "assembled.blend")
        if output_glb is None and plan.get("output_glb"):
            output_glb = _resolve_path(str(plan["output_glb"]), plan_dir)
        elif output_glb is not None:
            output_glb = Path(os.path.abspath(str(output_glb)))
    if output_obj is None and plan.get("output_combined_obj"):
        output_obj = _resolve_path(str(plan["output_combined_obj"]), plan_dir)
    elif output_obj is not None:
        output_obj = Path(os.path.abspath(str(output_obj)))
    elif output_blend is not None:
        output_obj = output_blend.with_name(f"{output_blend.stem}_Combined.obj")
    else:
        output_obj = plan_dir / "assembled_Editable.obj"
    validation_path = plan_output(
        validation_path, "validation_json", "validation.json"
    )

    hull_values = plan.get("hull_glbs", [])
    mount_values = plan.get("mounts", [])
    if not isinstance(hull_values, list):
        raise ValueError("hull_glbs must be an array")
    if not isinstance(mount_values, list):
        raise ValueError("mounts must be an array")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0

    assembly_root = _new_collection("ASSEMBLY", None)
    scene.collection.children.link(assembly_root)
    hull_root = _new_collection("Hull", assembly_root)
    mounts_root = _new_collection("Mounts", assembly_root)

    missing_hulls: List[Dict[str, Any]] = []
    imported_hulls: List[Dict[str, Any]] = []
    protected_collections: List[bpy.types.Collection] = [
        assembly_root,
        hull_root,
        mounts_root,
    ]

    for index, raw_path in enumerate(hull_values):
        if not isinstance(raw_path, str) or not raw_path.strip():
            missing_hulls.append(
                {"index": index, "path": raw_path, "reason": "invalid hull path"}
            )
            continue
        hull_path = _resolve_path(raw_path, plan_dir)
        if not hull_path.is_file():
            missing_hulls.append(
                {"index": index, "path": str(hull_path), "reason": "file not found"}
            )
            continue

        stem = _safe_name(hull_path.stem, f"hull_{index:03d}")
        collection = _new_collection(f"{index:03d}_{stem}", hull_root)
        protected_collections.append(collection)
        try:
            objects = _import_glb_into_collection(hull_path, collection)
        except Exception as exc:
            bpy.data.collections.remove(collection)
            missing_hulls.append(
                {"index": index, "path": str(hull_path), "reason": str(exc)}
            )
            continue
        imported_hulls.append(
            {
                "index": index,
                "path": str(hull_path),
                "collection": collection.name,
                "object_count": len(objects),
            }
        )

    template_by_path: Dict[str, bpy.types.Collection] = {}
    template_object_count: Dict[str, int] = {}
    category_cache: Dict[Tuple[str, str], bpy.types.Collection] = {}
    hardpoint_cache: Dict[Tuple[str, str], bpy.types.Collection] = {}
    missing_mounts: List[Dict[str, Any]] = []
    instances: List[bpy.types.Object] = []
    instance_records: List[Dict[str, Any]] = []

    for index, mount in enumerate(mount_values):
        if not isinstance(mount, dict):
            missing_mounts.append(
                {"index": index, "reason": "mount entry must be an object"}
            )
            continue

        hardpoint_raw = mount.get("hardpoint", f"HP_{index:03d}")
        category_raw = mount.get("category", "Uncategorized")
        hardpoint = _safe_name(hardpoint_raw, f"HP_{index:03d}")
        category = _safe_name(category_raw, "Uncategorized")
        visible = mount.get("visible", True)
        if not isinstance(visible, bool):
            missing_mounts.append(
                {
                    "index": index,
                    "hardpoint": hardpoint,
                    "category": category,
                    "reason": "visible must be a boolean",
                    "visible": visible,
                }
            )
            continue

        raw_model = mount.get("model_glb")
        if not isinstance(raw_model, str) or not raw_model.strip():
            missing_mounts.append(
                {
                    "index": index,
                    "hardpoint": hardpoint,
                    "category": category,
                    "reason": "invalid model_glb path",
                }
            )
            continue
        model_path = _resolve_path(raw_model, plan_dir)
        if not model_path.is_file():
            missing_mounts.append(
                {
                    "index": index,
                    "hardpoint": hardpoint,
                    "category": category,
                    "path": str(model_path),
                    "reason": "file not found",
                }
            )
            continue
        try:
            matrix_world = game_node_matrix_to_blender(mount.get("matrix", []))
        except Exception as exc:
            missing_mounts.append(
                {
                    "index": index,
                    "hardpoint": hardpoint,
                    "category": category,
                    "path": str(model_path),
                    "reason": str(exc),
                }
            )
            continue

        canonical = _canonical_path(model_path)
        template = template_by_path.get(canonical)
        if template is None:
            digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
            template = _new_collection(
                f"TPL_{_safe_name(model_path.stem)}_{digest}", None
            )
            template["source_glb"] = str(model_path)
            try:
                imported = _import_glb_into_collection(model_path, template)
            except Exception as exc:
                bpy.data.collections.remove(template)
                missing_mounts.append(
                    {
                        "index": index,
                        "hardpoint": hardpoint,
                        "category": category,
                        "path": str(model_path),
                        "reason": str(exc),
                    }
                )
                continue
            template_by_path[canonical] = template
            template_object_count[canonical] = len(imported)

        category_collection = _get_or_create_child_collection(
            category_cache, mounts_root, "category", category
        )
        hardpoint_collection = _get_or_create_child_collection(
            hardpoint_cache,
            category_collection,
            f"hardpoint:{category}",
            hardpoint,
        )

        instance = bpy.data.objects.new(
            f"{index:04d}_{category}_{hardpoint}", object_data=None
        )
        hardpoint_collection.objects.link(instance)
        instance.instance_type = "COLLECTION"
        instance.instance_collection = template
        instance.empty_display_type = "ARROWS"
        instance.empty_display_size = 0.5
        instance.hide_viewport = not visible
        instance.hide_render = not visible
        instance.matrix_world = matrix_world
        instance["hardpoint"] = str(hardpoint_raw)
        instance["category"] = str(category_raw)
        instance["source_glb"] = str(model_path)
        instance["plan_index"] = index
        instance["visible"] = visible
        instances.append(instance)
        instance_records.append(
            {
                "index": index,
                "object": instance.name,
                "hardpoint": str(hardpoint_raw),
                "category": str(category_raw),
                "source_glb": str(model_path),
                "visible": visible,
                "hide_viewport": instance.hide_viewport,
                "hide_render": instance.hide_render,
                "template_collection": template.name,
                "blender_matrix_rows": _matrix_to_rows(matrix_world),
            }
        )

    # Force a dependency graph update before bounds and export.
    bpy.context.view_layer.update()
    bounds, mesh_occurrences = _scene_bounds()
    materials, images = _material_and_image_summary()

    template_records = []
    for canonical, template in sorted(
        template_by_path.items(), key=lambda item: item[1].name
    ):
        template_records.append(
            {
                "collection": template.name,
                "source_glb": template.get("source_glb", canonical),
                "object_count": template_object_count.get(canonical, 0),
            }
        )

    export_error = _save_and_export(output_blend, output_glb)
    if export_error is None:
        if editable_objects:
            combined_obj = _export_visible_editable_obj(
                output_obj,
                bounds,
                mesh_occurrences,
                instance_records,
                deep_validate_obj=deep_validate_obj,
            )
        else:
            if output_blend is None:
                raise RuntimeError(
                    "joined OBJ mode requires a saved BLEND output"
                )
            combined_obj = _export_visible_combined_obj(
                output_obj,
                output_blend,
                bounds,
                mesh_occurrences,
                instance_records,
            )
    else:
        combined_obj = {
            "ok": False,
            "obj": str(output_obj),
            "error": "combined OBJ skipped because GLB export failed",
        }

    validation: Dict[str, Any] = {
        "schema_version": 1,
        "plan": str(plan_path),
        "outputs": {
            "blend": str(output_blend) if output_blend is not None else None,
            "glb": str(output_glb) if output_glb is not None else None,
            "combined_obj": str(output_obj),
            "combined_mtl": str(output_obj.with_suffix(".mtl")),
            "combined_textures": str(output_obj.parent / "textures"),
            "validation": str(validation_path),
        },
        "coordinate_conversion": {
            "input": "ModelUber game-node left-handed, Y-up, column-major matrices",
            "output": "Blender right-handed, Z-up",
            "vector_mapping": "[x, y, z] -> [-x, -z, y]",
            "formula": "M_blender = B * M_game * inverse(B)",
            "basis_rows": _matrix_to_rows(GAME_NODE_TO_BLENDER_BASIS),
            "obj_export_axes": {"forward": "-Z", "up": "Y"},
        },
        "hulls": {
            "requested": len(hull_values),
            "imported": len(imported_hulls),
            "records": imported_hulls,
            "missing": missing_hulls,
        },
        "mounts": {
            "requested": len(mount_values),
            "actual_instances": len(instances),
            "default_visible": sum(
                1 for record in instance_records if record["visible"]
            ),
            "default_hidden": sum(
                1 for record in instance_records if not record["visible"]
            ),
            "missing": missing_mounts,
            "records": instance_records,
            "unique_models": len(template_by_path),
            "templates": template_records,
        },
        "scene": {
            "bounds": bounds,
            "evaluated_mesh_occurrences": mesh_occurrences,
            "materials": materials,
            "images": images,
        },
        "combined_obj": combined_obj,
        "export_error": export_error,
        "ok": not missing_hulls
        and not missing_mounts
        and export_error is None
        and combined_obj.get("ok") is True,
    }

    validation_path.parent.mkdir(parents=True, exist_ok=True)
    with validation_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(validation, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return validation


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Output .blend path")
    parser.add_argument("--glb", type=Path, help="Optional assembled .glb path")
    parser.add_argument(
        "--obj", type=Path, help="Visible-state OBJ output path"
    )
    parser.add_argument(
        "--validation", type=Path, help="Output validation JSON path"
    )
    parser.add_argument(
        "--obj-only",
        action="store_true",
        help="Skip final BLEND/GLB creation",
    )
    parser.add_argument(
        "--editable-objects",
        action="store_true",
        help="Keep every realized mesh as a separately selectable OBJ object",
    )
    parser.add_argument(
        "--deep-validate-obj",
        action="store_true",
        help="Re-import the final OBJ for release/self-test validation",
    )
    return parser.parse_args(argv)


def _script_args() -> List[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def main() -> int:
    args = _parse_args(_script_args())
    try:
        result = assemble(
            args.plan,
            output_blend=args.output,
            output_glb=args.glb,
            output_obj=args.obj,
            validation_path=args.validation,
            obj_only=args.obj_only,
            editable_objects=args.editable_objects,
            deep_validate_obj=args.deep_validate_obj,
        )
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
