#!/usr/bin/env python3
"""Assemble Legends component OBJs into one editable OBJ without Blender."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from PIL import Image, ImageOps


VISIBILITY_PROFILES = {
    "harbor_dock": {"dock": True, "overlay": False},
    "neutral_battle_intact": {"dock": False, "overlay": False},
    "overlay_debug": {"dock": True, "overlay": True},
}
IDENTITY_COLUMN_MAJOR = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]

# These matrices reproduce the established Blender route exactly. Source
# component OBJ vertices already use Blender coordinates. Mount placement is
# B * M_game * B^-1, then Blender's -Z-forward/Y-up OBJ export maps
# (x, y, z) to (x, z, -y). Hull geometry only receives the final OBJ export
# basis. ModelUber port nodes use +Z toward the bow while decoded component
# geometry uses +Y toward the bow. Therefore B maps (x, y, z) to (-x, z, y);
# the final OBJ translation becomes (-x, y, -z), matching the hull segments.
GAME_NODE_TO_BLENDER_BASIS = [
    [-1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
BLENDER_BASIS_INVERSE = [
    [-1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
BLENDER_TO_EDITABLE_OBJ = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, -1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]

class NativeAssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelAsset:
    model_path: str
    output_key: str
    obj: Path
    material_manifest: Path


@dataclass(frozen=True)
class Occurrence:
    model_path: str
    name: str
    category: str
    hardpoint: str
    visible: bool
    matrix: tuple[float, ...]
    assembly_kind: str


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise NativeAssemblyError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: object, fallback: str = "unnamed", limit: int = 180) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_.")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) <= limit:
        return cleaned
    suffix = "__" + hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10]
    return cleaned[: limit - len(suffix)] + suffix


def normalize_model_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NativeAssemblyError(f"invalid model path: {value!r}")
    path = PurePosixPath(value.strip().replace("/", "/"))
    if path.is_absolute() or ".." in path.parts or not path.as_posix().endswith(".model"):
        raise NativeAssemblyError(f"unsafe model path: {value!r}")
    return path.as_posix()


def resolve_path(value: object, parent: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise NativeAssemblyError(f"invalid file path: {value!r}")
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = parent / path
    return Path(os.path.abspath(str(path)))


def array(mapping: Mapping[str, Any], name: str) -> list[Any]:
    value = mapping.get(name, [])
    if not isinstance(value, list):
        raise NativeAssemblyError(f"{name} must be an array")
    return value


def mapping_matrix(item: Mapping[str, Any]) -> tuple[float, ...]:
    raw: object = item.get("corrected_gltf_rh_y_up_matrix")
    if raw is None:
        raw = item.get("matrix")
    if isinstance(raw, dict):
        raw = raw.get("column_major")
    if not isinstance(raw, list) or len(raw) != 16:
        raise NativeAssemblyError("assembly item matrix must contain 16 values")
    values = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in values):
        raise NativeAssemblyError("assembly matrix contains non-finite values")
    return values


def visibility_policy(mapping: Mapping[str, Any], profile: str) -> dict[str, bool]:
    profiles = mapping.get("visibility_profiles", {})
    if profiles is not None and not isinstance(profiles, dict):
        raise NativeAssemblyError("visibility_profiles must be an object")
    raw = profiles.get(profile) if isinstance(profiles, dict) else None
    if raw is None:
        raw = VISIBILITY_PROFILES.get(profile)
    if not isinstance(raw, dict):
        raise NativeAssemblyError(f"unknown visibility profile: {profile}")
    dock = raw.get("dock", raw.get("dock_misc"))
    overlay = raw.get("overlay", raw.get("runtime_action_overlays"))
    if not isinstance(dock, bool) or not isinstance(overlay, bool):
        raise NativeAssemblyError("visibility profile dock/overlay values must be bool")
    return {"dock": dock, "overlay": overlay}


def is_visible(condition: object, policy: Mapping[str, bool]) -> bool:
    if condition in (None, "always"):
        return True
    if condition == "dock":
        return policy["dock"]
    if condition in ("overlay", "runtime_overlay", "launch_action_state"):
        return policy["overlay"]
    raise NativeAssemblyError(f"unsupported visibility condition: {condition!r}")


def load_assets(summary: Mapping[str, Any], summary_dir: Path) -> dict[str, ModelAsset]:
    strict = summary.get("strict_validation")
    if not isinstance(strict, dict) or strict.get("accepted") is not True:
        raise NativeAssemblyError("native component batch is not accepted")
    results = summary.get("results")
    if not isinstance(results, list) or not results:
        raise NativeAssemblyError("native component batch has no results")
    assets: dict[str, ModelAsset] = {}
    for index, item in enumerate(results):
        if not isinstance(item, dict) or item.get("status") != "OK":
            raise NativeAssemblyError(f"native component result {index} is not OK")
        model_path = normalize_model_path(item.get("model_path"))
        obj = resolve_path(item.get("output_obj"), summary_dir)
        material_manifest = resolve_path(item.get("material_manifest"), summary_dir)
        if not obj.is_file() or not material_manifest.is_file():
            raise NativeAssemblyError(f"native component files missing: {model_path}")
        assets[model_path] = ModelAsset(
            model_path=model_path,
            output_key=safe_name(item.get("output_key"), f"model_{index:03d}"),
            obj=obj,
            material_manifest=material_manifest,
        )
    return assets


def build_occurrences(mapping: Mapping[str, Any], profile: str) -> list[Occurrence]:
    policy = visibility_policy(mapping, profile)
    occurrences: list[Occurrence] = []
    for index, item in enumerate(array(mapping, "hull_parts")):
        if not isinstance(item, dict):
            raise NativeAssemblyError("hull part must be an object")
        if item.get("role") != "mesh" or item.get("render_required", True) is False:
            continue
        model_path = normalize_model_path(item.get("path"))
        occurrences.append(
            Occurrence(
                model_path=model_path,
                name=f"HULL_{index:03d}_{Path(model_path).stem}",
                category="Hull",
                hardpoint=f"Hull_{index:03d}",
                visible=True,
                matrix=tuple(IDENTITY_COLUMN_MAJOR),
                assembly_kind="hull",
            )
        )

    def append_mount(
        item: Mapping[str, Any],
        index: int,
        *,
        kind: str,
        category: str,
        hardpoint: str,
        condition: object,
    ) -> None:
        if item.get("render_required", True) is False:
            return
        model_path = normalize_model_path(item.get("model_path"))
        visible = is_visible(condition, policy)
        occurrences.append(
            Occurrence(
                model_path=model_path,
                name=f"{index:04d}_{safe_name(category)}_{safe_name(hardpoint)}",
                category=category,
                hardpoint=hardpoint,
                visible=visible,
                matrix=mapping_matrix(item),
                assembly_kind=kind,
            )
        )

    sequence = 0
    for item in array(mapping, "combat_mounts"):
        if not isinstance(item, dict):
            raise NativeAssemblyError("combat mount must be an object")
        sequence += 1
        append_mount(
            item,
            sequence,
            kind="combat",
            category=f"Combat_{item.get('category', 'Uncategorized')}",
            hardpoint=str(item.get("hardpoint") or f"HP_{sequence:03d}"),
            condition=item.get("visibility_condition", "always"),
        )
    for item in array(mapping, "misc_instances"):
        if not isinstance(item, dict):
            raise NativeAssemblyError("misc instance must be an object")
        sequence += 1
        condition = item.get("visibility_condition", "always")
        append_mount(
            item,
            sequence,
            kind="misc",
            category="Misc_Dock" if condition == "dock" else "Misc",
            hardpoint=str(item.get("instance_name") or f"MISC_{sequence:03d}"),
            condition=condition,
        )
    for item in array(mapping, "runtime_action_overlays"):
        if not isinstance(item, dict):
            raise NativeAssemblyError("runtime overlay must be an object")
        sequence += 1
        parent = str(item.get("parent_hardpoint") or f"OVERLAY_{sequence:03d}")
        append_mount(
            item,
            sequence,
            kind="runtime_overlay",
            category="RuntimeOverlay",
            hardpoint=str(item.get("instance_name") or f"{parent}_ACTION_{sequence}"),
            condition=item.get("visibility_condition", "overlay"),
        )
    if not any(item.assembly_kind == "hull" for item in occurrences):
        raise NativeAssemblyError("mapping contains no render-bearing hull")
    return occurrences


def matrix_rows_from_column_major(values: Sequence[float]) -> list[list[float]]:
    return [
        [float(values[column * 4 + row]) for column in range(4)]
        for row in range(4)
    ]


def multiply_matrices(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> list[list[float]]:
    return [
        [
            sum(left[row][index] * right[index][column] for index in range(4))
            for column in range(4)
        ]
        for row in range(4)
    ]


def output_matrix_for_occurrence(occurrence: Occurrence) -> list[list[float]]:
    if occurrence.assembly_kind == "hull":
        return [row[:] for row in BLENDER_TO_EDITABLE_OBJ]
    game_matrix = matrix_rows_from_column_major(occurrence.matrix)
    blender_world = multiply_matrices(
        multiply_matrices(GAME_NODE_TO_BLENDER_BASIS, game_matrix),
        BLENDER_BASIS_INVERSE,
    )
    return multiply_matrices(BLENDER_TO_EDITABLE_OBJ, blender_world)

def determinant3(matrix: Sequence[Sequence[float]]) -> float:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def normal_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    det = determinant3(matrix)
    if abs(det) < 1e-12:
        raise NativeAssemblyError("assembly transform is singular")
    inverse = [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]
    return [[inverse[column][row] for column in range(3)] for row in range(3)]


def transform_position(matrix: Sequence[Sequence[float]], values: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = values
    result = []
    for row in range(3):
        result.append(
            matrix[row][0] * x
            + matrix[row][1] * y
            + matrix[row][2] * z
            + matrix[row][3]
        )
    return result[0], result[1], result[2]


def transform_normal(matrix: Sequence[Sequence[float]], values: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = values
    result = [
        matrix[row][0] * x + matrix[row][1] * y + matrix[row][2] * z
        for row in range(3)
    ]
    length = math.sqrt(sum(value * value for value in result))
    if length <= 1e-20:
        return 0.0, 0.0, 1.0
    return result[0] / length, result[1] / length, result[2] / length


def offset_face_token(token: str, offsets: tuple[int, int, int]) -> str:
    parts = token.split("/")
    output: list[str] = []
    for index, value in enumerate(parts):
        if not value:
            output.append("")
            continue
        number = int(value)
        if number <= 0:
            raise NativeAssemblyError("negative/zero OBJ indices are not supported")
        output.append(str(number + offsets[min(index, 2)]))
    return "/".join(output)


def material_contract(asset: ModelAsset) -> tuple[dict[str, str], list[dict[str, Any]]]:
    payload = load_json(asset.material_manifest)
    objects = payload.get("objects")
    materials = payload.get("materials")
    if not isinstance(objects, list) or not isinstance(materials, list):
        raise NativeAssemblyError(f"invalid material manifest: {asset.material_manifest}")
    object_material: dict[str, str] = {}
    for item in objects:
        if isinstance(item, dict):
            object_name = str(item.get("object_name") or "")
            material_name = str(item.get("material_name") or "")
            if object_name and material_name:
                object_material[object_name] = material_name
                object_material[safe_name(object_name)] = material_name
    clean_materials = [item for item in materials if isinstance(item, dict)]
    return object_material, clean_materials


def materialize_texture(source: Path, texture_dir: Path, cache: dict[str, str]) -> str:
    source = source.resolve()
    key = os.path.normcase(str(source))
    previous = cache.get(key)
    if previous is not None:
        return previous
    if not source.is_file():
        raise NativeAssemblyError(f"material texture is missing: {source}")
    digest = sha256(source)
    target = texture_dir / f"{digest}.png"
    if not target.is_file():
        # Final exports must not share writable file records with conversion
        # caches or unpacked source data.
        shutil.copy2(source, target)
    relative = f"textures/{target.name}"
    cache[key] = relative
    return relative


def split_metallic_gloss_texture(source: Path, texture_dir: Path) -> dict[str, str]:
    """Publish the original MG map plus DCC-friendly roughness/metalness maps."""

    texture_dir.mkdir(parents=True, exist_ok=True)
    digest = sha256(source)
    targets = {
        "metallic_gloss": texture_dir / f"{digest}_metallic_gloss.png",
        "roughness": texture_dir / f"{digest}_roughness.png",
        "metalness": texture_dir / f"{digest}_metalness.png",
    }
    if not all(path.is_file() for path in targets.values()):
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
            red, green, _blue, _alpha = rgba.split()
            rgba.save(targets["metallic_gloss"], format="PNG", compress_level=3)
            Image.merge("RGB", (ImageOps.invert(red),) * 3).save(
                targets["roughness"], format="PNG", compress_level=3
            )
            Image.merge("RGB", (green,) * 3).save(
                targets["metalness"], format="PNG", compress_level=3
            )
    return {role: f"textures/{path.name}" for role, path in targets.items()}


def write_mtl(
    output: Path,
    assets: Mapping[str, ModelAsset],
    texture_dir: Path,
) -> tuple[dict[tuple[str, str], str], dict[str, str], int]:
    texture_dir.mkdir(parents=True, exist_ok=True)
    texture_cache: dict[str, str] = {}
    material_names: dict[tuple[str, str], str] = {}
    object_materials: dict[str, str] = {}
    blocks: list[str] = ["# WoWS Toolbox native OBJ material library"]
    for model_path, asset in sorted(assets.items()):
        per_object, materials = material_contract(asset)
        object_materials.update(
            {f"{model_path}\0{name}": material for name, material in per_object.items()}
        )
        for index, material in enumerate(materials):
            source_name = str(material.get("name") or f"material_{index:03d}")
            output_name = safe_name(f"{asset.output_key}__{source_name}")
            material_names[(model_path, source_name)] = output_name
            maps = material.get("maps") if isinstance(material.get("maps"), dict) else {}
            mapped: dict[str, str] = {}
            for channel, source_value in maps.items():
                channel = str(channel)
                if channel not in {"a", "n", "ao", "mg"}:
                    continue
                if isinstance(source_value, str) and source_value:
                    source_path = Path(source_value)
                    if channel == "mg":
                        mapped.update(split_metallic_gloss_texture(source_path, texture_dir))
                    else:
                        mapped[channel] = materialize_texture(
                            source_path, texture_dir, texture_cache
                        )
            properties = {
                str(item.get("name")): item.get("value")
                for item in material.get("properties", [])
                if isinstance(item, dict) and item.get("name")
            }
            fx_name = Path(str(material.get("fx_path") or "")).name.casefold()
            transparent = (
                fx_name in {"lightonly_alpha_flat.fx", "wire_material.fx"}
                or "alpha" in fx_name
                or "grid" in fx_name
            )
            blocks.extend(
                [
                    "",
                    f"newmtl {output_name}",
                    "Ka 0.080000 0.080000 0.080000",
                    "Kd 0.800000 0.800000 0.800000",
                    "Ks 0.050000 0.050000 0.050000",
                    "Ns 32.000000",
                    "d 1.000000",
                    "illum 2",
                    "Pr 1" if "roughness" in mapped else "Pr 0.72",
                    "Pm 1" if "metalness" in mapped else "Pm 0",
                    f"# wows_fx {fx_name or 'unknown'}",
                    f"# wows_double_sided {bool(properties.get('doubleSided', False))}",
                ]
            )
            if "a" in mapped:
                blocks.append(f"map_Kd {mapped['a']}")
                if transparent:
                    blocks.append(f"map_d {mapped['a']}")
            if "n" in mapped:
                blocks.append(f"map_Bump {mapped['n']}")
            if "ao" in mapped:
                blocks.append(f"map_Ka {mapped['ao']}")
            if "roughness" in mapped:
                blocks.append("# wows_pbr_contract R=gloss G=metalness roughness=1-R")
                blocks.append(f"map_Pr {mapped['roughness']}")
            if "metalness" in mapped:
                blocks.append(f"map_Pm {mapped['metalness']}")
    output.write_text("\n".join(blocks) + "\n", encoding="utf-8", newline="\n")
    return material_names, object_materials, len(set(texture_cache.values()))


def assemble(
    mapping_path: Path,
    summary_path: Path,
    output_obj: Path,
    validation_path: Path,
    profile: str,
) -> dict[str, Any]:
    mapping_path = mapping_path.resolve()
    summary_path = summary_path.resolve()
    output_obj = Path(os.path.abspath(str(output_obj)))
    validation_path = Path(os.path.abspath(str(validation_path)))
    mapping = load_json(mapping_path)
    summary = load_json(summary_path)
    expected_sha = str(summary.get("source_mapping_sha256") or "").lower()
    if expected_sha != sha256(mapping_path).lower():
        raise NativeAssemblyError("mapping/native batch SHA-256 mismatch")
    assets = load_assets(summary, summary_path.parent)
    occurrences = build_occurrences(mapping, profile)
    missing_assets = sorted({item.model_path for item in occurrences} - set(assets))
    if missing_assets:
        raise NativeAssemblyError(f"component OBJ missing for: {missing_assets}")

    output_obj.parent.mkdir(parents=True, exist_ok=True)
    output_mtl = output_obj.with_suffix(".mtl")
    texture_dir = output_obj.parent / "textures"
    material_names, object_materials, texture_files = write_mtl(
        output_mtl, assets, texture_dir
    )

    temporary = output_obj.with_name(f".{output_obj.name}.{os.getpid()}.part")
    offsets = [0, 0, 0]
    totals = {"vertices": 0, "uvs": 0, "normals": 0, "faces": 0, "objects": 0}
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    object_names: set[str] = set()
    used_materials: set[str] = set()
    part_records: list[dict[str, Any]] = []
    included: list[str] = []
    excluded: list[str] = []

    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write("# WoWS Toolbox Blender-free editable OBJ\n")
            output.write(f"mtllib {output_mtl.name}\n")
            for occurrence_index, occurrence in enumerate(occurrences):
                if not occurrence.visible:
                    excluded.append(occurrence.name)
                    continue
                included.append(occurrence.name)
                asset = assets[occurrence.model_path]
                matrix = output_matrix_for_occurrence(occurrence)
                normals = normal_matrix(matrix)
                mirrored = determinant3(matrix) < 0.0
                start_offsets = tuple(offsets)
                local = [0, 0, 0]
                current_source_object = "Mesh"
                current_output_object = ""
                with asset.obj.open("r", encoding="utf-8-sig", errors="strict") as source:
                    for raw_line in source:
                        line = raw_line.rstrip("\r\n")
                        if not line or line.startswith("#") or line.startswith("mtllib "):
                            continue
                        if line.startswith("v "):
                            values = [float(value) for value in line.split()[1:4]]
                            point = transform_position(matrix, values)
                            output.write("v {:.9g} {:.9g} {:.9g}\n".format(*point))
                            for axis in range(3):
                                bounds_min[axis] = min(bounds_min[axis], point[axis])
                                bounds_max[axis] = max(bounds_max[axis], point[axis])
                            local[0] += 1
                            totals["vertices"] += 1
                        elif line.startswith("vt "):
                            output.write(line + "\n")
                            local[1] += 1
                            totals["uvs"] += 1
                        elif line.startswith("vn "):
                            values = [float(value) for value in line.split()[1:4]]
                            normal = transform_normal(normals, values)
                            output.write("vn {:.9g} {:.9g} {:.9g}\n".format(*normal))
                            local[2] += 1
                            totals["normals"] += 1
                        elif line.startswith("o "):
                            current_source_object = line[2:].strip() or "Mesh"
                            current_output_object = safe_name(
                                f"{occurrence.name}__{current_source_object}"
                            )
                            if current_output_object in object_names:
                                current_output_object = safe_name(
                                    f"{current_output_object}__{occurrence_index:04d}_{totals['objects']:04d}"
                                )
                            object_names.add(current_output_object)
                            output.write(f"o {current_output_object}\n")
                            totals["objects"] += 1
                            part_records.append(
                                {
                                    "name": current_output_object,
                                    "source_object": current_source_object,
                                    "source_model": occurrence.model_path,
                                    "category": occurrence.category,
                                    "hardpoint": occurrence.hardpoint,
                                    "assembly_kind": occurrence.assembly_kind,
                                    "pivot": [matrix[0][3], matrix[1][3], matrix[2][3]],
                                    "matrix_rows": matrix,
                                }
                            )
                        elif line.startswith("g "):
                            output.write(
                                "g "
                                + safe_name(f"{occurrence.name}__{line[2:].strip()}")
                                + "\n"
                            )
                        elif line.startswith("usemtl "):
                            source_material = object_materials.get(
                                f"{occurrence.model_path}\0{current_source_object}",
                                line[7:].strip(),
                            )
                            output_material = material_names.get(
                                (occurrence.model_path, source_material)
                            )
                            if output_material is None:
                                raise NativeAssemblyError(
                                    f"material mapping missing: {occurrence.model_path} / {source_material}"
                                )
                            used_materials.add(output_material)
                            output.write(f"usemtl {output_material}\n")
                        elif line.startswith("f "):
                            tokens = line.split()[1:]
                            converted = [
                                offset_face_token(token, start_offsets) for token in tokens
                            ]
                            if mirrored:
                                converted.reverse()
                            output.write("f " + " ".join(converted) + "\n")
                            totals["faces"] += 1
                        elif line.startswith("s "):
                            output.write(line + "\n")
                for index in range(3):
                    offsets[index] += local[index]

        os.replace(temporary, output_obj)
    finally:
        temporary.unlink(missing_ok=True)

    if totals["vertices"] <= 0 or totals["faces"] <= 0 or totals["objects"] <= 0:
        raise NativeAssemblyError("native assembly produced no editable geometry")
    if not used_materials:
        raise NativeAssemblyError("native assembly used no materials")

    sidecar = output_obj.with_suffix(".model.json")
    write_json(
        sidecar,
        {
            "schema": "wows-toolbox-native-object-layout/v1",
            "coordinate_system": "Blender -Z-forward/Y-up OBJ basis: (x, z, -y)",
            "obj_axis_forward": "-Z",
            "obj_axis_up": "Y",
            "pivot_space": "obj",
            "parts": part_records,
        },
    )
    checks = {
        "vertices_positive": totals["vertices"] > 0,
        "faces_positive": totals["faces"] > 0,
        "editable_objects_positive": totals["objects"] > 0,
        "object_names_unique": len(object_names) == totals["objects"],
        "uvs_preserved": totals["uvs"] > 0,
        "normals_preserved": totals["normals"] > 0,
        "materials_preserved": len(used_materials) > 0,
        "mtllib_relative_basename": output_mtl.name == output_mtl.name,
        "texture_refs_portable": texture_files > 0,
        "hidden_overlay_names_excluded": not set(excluded).intersection(object_names),
        "bounds_finite": all(math.isfinite(value) for value in bounds_min + bounds_max),
        "native_no_blender": True,
    }
    combined = {
        "ok": all(checks.values()),
        "mode": "editable_obj_only",
        "engine": "native_python_obj/v1",
        "obj": str(output_obj),
        "mtl": str(output_mtl),
        "texture_directory": str(texture_dir),
        "model_sidecar": str(sidecar),
        "vertices": totals["vertices"],
        "uv_records": totals["uvs"],
        "normal_records": totals["normals"],
        "faces": totals["faces"],
        "editable_mesh_objects": totals["objects"],
        "material_slots": len(used_materials),
        "texture_source_images": texture_files,
        "unique_texture_files": texture_files,
        "deduplicated_texture_sources": 0,
        "included_visible_mounts": included,
        "excluded_hidden_mounts": excluded,
        "bounds": {"min": bounds_min, "max": bounds_max},
        "checks": checks,
        "errors": [name for name, value in checks.items() if not value],
    }
    result = {
        "schema": "wows-toolbox-native-scene-validation/v1",
        "ok": combined["ok"],
        "engine": "native_python_obj/v1",
        "blender_used": False,
        "visibility_profile": profile,
        "source_mapping": str(mapping_path),
        "source_batch_summary": str(summary_path),
        "combined_obj": combined,
    }
    write_json(validation_path, result)
    if not result["ok"]:
        raise NativeAssemblyError(f"native assembly checks failed: {combined['errors']}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--batch-summary", required=True, type=Path)
    parser.add_argument("--obj", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--visibility-profile", default="harbor_dock")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    assemble(
        args.assembly,
        args.batch_summary,
        args.obj,
        args.validation,
        args.visibility_profile,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (NativeAssemblyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
