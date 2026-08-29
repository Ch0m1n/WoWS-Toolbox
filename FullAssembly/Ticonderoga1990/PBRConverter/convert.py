#!/usr/bin/env python3
"""Convert extracted WoWS: Legends geometry render sets to a PBR GLB.

The geometry decoder is deliberately imported from ../geometry_decoder.  This
tool never reads game packages itself and never writes into a game directory.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderSet:
    geometry: Path
    geometry_label: str
    vertices_section: str
    indices_section: str
    part_name: str
    object_name: str
    group_name: str
    material_mfm_path: str
    material_name: str
    texture_root: Path
    texture_maps: dict[str, str] | None
    material_fx_path: str | None
    material_properties: list[dict]
    rigid_node_world_matrix: tuple[float, ...] | None = None
    rigid_node_name: str | None = None
    uv_scale: tuple[float, float] = (1.0, 1.0)
    camouflage_mask: str | None = None
    camouflage_palette: tuple[str, ...] | None = None
    camouflage_blend: float = 0.0


MAP_SUFFIXES = {"a": "_a.dds", "n": "_n.dds", "mg": "_mg.dds", "ao": "_ao.dds"}
LOD_RE = re.compile(r"_lod(\d+)", re.IGNORECASE)
DAMAGE_RE = re.compile(r"(?:crack|patch|dead)", re.IGNORECASE)
IDENTITY_COLUMN_MAJOR = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)
MODEL_TO_COMPONENT_BASIS = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


def multiply_column_major(
    left: tuple[float, ...], right: tuple[float, ...]
) -> tuple[float, ...]:
    result = [0.0] * 16
    for column in range(4):
        for row in range(4):
            result[column * 4 + row] = sum(
                left[k * 4 + row] * right[column * 4 + k]
                for k in range(4)
            )
    return tuple(result)


def component_space_matrix(matrix: tuple[float, ...] | None) -> tuple[float, ...]:
    if matrix is None:
        return IDENTITY_COLUMN_MAJOR
    return multiply_column_major(
        multiply_column_major(MODEL_TO_COMPONENT_BASIS, matrix),
        MODEL_TO_COMPONENT_BASIS,
    )


def transform_point(
    matrix: tuple[float, ...], value: tuple[float, ...]
) -> tuple[float, float, float]:
    x, y, z = float(value[0]), float(value[1]), float(value[2])
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def determinant3(matrix: tuple[float, ...]) -> float:
    a, b, c = matrix[0], matrix[4], matrix[8]
    d, e, f = matrix[1], matrix[5], matrix[9]
    g, h, i = matrix[2], matrix[6], matrix[10]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def transform_normal(
    matrix: tuple[float, ...], value: tuple[float, ...]
) -> tuple[float, float, float]:
    a, b, c = matrix[0], matrix[4], matrix[8]
    d, e, f = matrix[1], matrix[5], matrix[9]
    g, h, i = matrix[2], matrix[6], matrix[10]
    det = determinant3(matrix)
    x, y, z = float(value[0]), float(value[1]), float(value[2])
    if abs(det) < 1e-12:
        nx, ny, nz = (
            a * x + b * y + c * z,
            d * x + e * y + f * z,
            g * x + h * y + i * z,
        )
    else:
        nx = (
            (e * i - f * h) * x
            + (f * g - d * i) * y
            + (d * h - e * g) * z
        ) / det
        ny = (
            (c * h - b * i) * x
            + (a * i - c * g) * y
            + (b * g - a * h) * z
        ) / det
        nz = (
            (b * f - c * e) * x
            + (c * d - a * f) * y
            + (a * e - b * d) * z
        ) / det
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / length, ny / length, nz / length


@functools.lru_cache(maxsize=1)
def normal_square_lookup() -> list[int]:
    """Quantize signed WoWS normal X/Y components to squared length."""

    return [
        min(255, round(((value / 127.5 - 1.0) ** 2) * 255.0))
        for value in range(256)
    ]


@functools.lru_cache(maxsize=1)
def normal_sqrt_lookup() -> list[int]:
    """Map remaining squared length to conventional positive tangent Z."""

    return [
        round((0.5 + 0.5 * math.sqrt(value / 255.0)) * 255.0)
        for value in range(256)
    ]


def reconstruct_tangent_normal(image):
    """Preserve signed X/Y in R/G and reconstruct +Z with Pillow C loops."""

    from PIL import Image, ImageChops

    rgba = image.convert("RGBA")
    red, green, _blue, alpha = rgba.split()
    x_squared = red.point(normal_square_lookup())
    y_squared = green.point(normal_square_lookup())
    remaining = ImageChops.invert(ImageChops.add(x_squared, y_squared))
    z_axis = remaining.point(normal_sqrt_lookup())
    return Image.merge("RGBA", (red, green, z_axis, alpha))



def default_decoder_root() -> Path:
    """Locate the decoder in either the research or packaged layout."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "geometry_decoder",
        here.parents[2] / "BlenderExtractor" / "geometry_decoder",
    ]
    for candidate in candidates:
        if (candidate / "decode_geometry.py").is_file():
            return candidate
    return candidates[-1]


def safe_name(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )
    return cleaned or "unnamed"


def blender_id_name(value: str, max_bytes: int = 63) -> str:
    """Return a deterministic Blender-safe ID name without silent truncation."""
    cleaned = safe_name(value)
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned
    suffix = "__" + hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:10]
    byte_budget = max_bytes - len(suffix.encode("ascii"))
    prefix = ""
    for character in cleaned:
        candidate = prefix + character
        if len(candidate.encode("utf-8")) > byte_budget:
            break
        prefix = candidate
    return (prefix or "id") + suffix


def resolve_path(base: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_decoder(decoder_root: Path):
    decoder_file = decoder_root / "decode_geometry.py"
    if not decoder_file.is_file():
        raise ConversionError(f"geometry decoder not found: {decoder_file}")
    sys.path.insert(0, str(decoder_root))
    try:
        import decode_geometry as decoder  # type: ignore
    finally:
        sys.path.pop(0)
    return decoder


def load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConversionError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ConversionError("manifest root must be a JSON object")
    return payload


def parse_render_sets(payload: dict, manifest_path: Path) -> list[RenderSet]:
    base = manifest_path.parent
    models = payload.get("models", payload.get("entries"))
    if not isinstance(models, list) or not models:
        raise ConversionError("manifest must contain a non-empty models array")

    default_texture_root = payload.get("texture_root")
    output: list[RenderSet] = []
    used_objects: set[str] = set()
    for model_index, model in enumerate(models):
        if not isinstance(model, dict):
            raise ConversionError(f"models[{model_index}] must be an object")
        if "geometry" not in model:
            raise ConversionError(f"models[{model_index}] has no geometry")
        geometry = resolve_path(base, model["geometry"])
        if not geometry.is_file():
            raise ConversionError(f"geometry file not found: {geometry}")
        geometry_label = str(model.get("name") or geometry.stem)
        texture_root_value = model.get("texture_root", default_texture_root)
        if texture_root_value is None:
            raise ConversionError(f"{geometry_label}: texture_root is required")
        texture_root = resolve_path(base, texture_root_value)
        if not texture_root.is_dir():
            raise ConversionError(f"texture root not found: {texture_root}")

        render_sets = model.get("render_sets")
        if not isinstance(render_sets, list) or not render_sets:
            raise ConversionError(f"{geometry_label}: render_sets must be non-empty")
        for render_index, render_set in enumerate(render_sets):
            if not isinstance(render_set, dict):
                raise ConversionError(
                    f"{geometry_label}.render_sets[{render_index}] must be an object"
                )
            required = (
                "vertices_section",
                "indices_section",
                "material_mfm_path",
                "material_name",
            )
            missing = [key for key in required if not render_set.get(key)]
            if missing:
                raise ConversionError(
                    f"{geometry_label}.render_sets[{render_index}] missing {missing}"
                )
            vertices = str(render_set["vertices_section"])
            indices = str(render_set["indices_section"])
            if not vertices.endswith(".vertices"):
                raise ConversionError(f"vertex section must end in .vertices: {vertices}")
            if not indices.endswith(".indices"):
                raise ConversionError(f"index section must end in .indices: {indices}")
            part_name = vertices[: -len(".vertices")]
            if indices[: -len(".indices")] != part_name:
                raise ConversionError(
                    f"render set section stems differ: {vertices!r} vs {indices!r}"
                )
            lod_match = LOD_RE.search(part_name)
            if lod_match and int(lod_match.group(1)) != 0:
                raise ConversionError(f"non-LOD0 render set requested: {part_name}")
            if DAMAGE_RE.search(part_name):
                raise ConversionError(f"damage render set is not intact: {part_name}")

            texture_maps_value = render_set.get("texture_maps")
            texture_maps: dict[str, str] | None = None
            if texture_maps_value is not None:
                if not isinstance(texture_maps_value, dict):
                    raise ConversionError(
                        f"{geometry_label}.render_sets[{render_index}].texture_maps "
                        "must be an object"
                    )
                allowed_channels = {"a", "n", "mg", "ao", "detail"}
                invalid_channels = sorted(set(texture_maps_value) - allowed_channels)
                if invalid_channels:
                    raise ConversionError(
                        f"unsupported explicit texture channels: {invalid_channels}"
                    )
                texture_maps = {}
                for channel, logical_path in texture_maps_value.items():
                    if not isinstance(logical_path, str) or not logical_path:
                        raise ConversionError(
                            f"texture_maps.{channel} must be a non-empty path string"
                        )
                    logical = Path(logical_path.replace("\\", "/"))
                    if logical.is_absolute() or ".." in logical.parts:
                        raise ConversionError(
                            f"unsafe logical texture path: {logical_path}"
                        )
                    texture_maps[channel] = logical.as_posix()

            material_fx_path_value = render_set.get("material_fx_path")
            material_fx_path = (
                str(material_fx_path_value) if material_fx_path_value else None
            )
            material_properties_value = render_set.get("material_properties", [])
            if not isinstance(material_properties_value, list) or not all(
                isinstance(item, dict) for item in material_properties_value
            ):
                raise ConversionError("material_properties must be an array of objects")

            rigid_matrix_value = render_set.get("rigid_node_world_matrix")
            rigid_matrix: tuple[float, ...] | None = None
            if rigid_matrix_value is not None:
                if (
                    not isinstance(rigid_matrix_value, (list, tuple))
                    or len(rigid_matrix_value) != 16
                ):
                    raise ConversionError(
                        "rigid_node_world_matrix must contain 16 column-major values"
                    )
                try:
                    rigid_matrix = tuple(
                        float(value) for value in rigid_matrix_value
                    )
                except (TypeError, ValueError) as error:
                    raise ConversionError(
                        "rigid_node_world_matrix values must be numeric"
                    ) from error
                if not all(math.isfinite(value) for value in rigid_matrix):
                    raise ConversionError("rigid_node_world_matrix must be finite")
            rigid_node_name_value = render_set.get("rigid_node_name")
            rigid_node_name = (
                str(rigid_node_name_value) if rigid_node_name_value else None
            )

            uv_scale_value = render_set.get("uv_scale", [1.0, 1.0])
            if (
                not isinstance(uv_scale_value, (list, tuple))
                or len(uv_scale_value) != 2
            ):
                raise ConversionError("uv_scale must contain two values")
            try:
                uv_scale = tuple(float(value) for value in uv_scale_value)
            except (TypeError, ValueError) as error:
                raise ConversionError("uv_scale values must be numeric") from error
            if not all(math.isfinite(value) and value > 0.0 for value in uv_scale):
                raise ConversionError("uv_scale values must be finite and positive")

            camouflage_mask_value = render_set.get("camouflage_mask")
            camouflage_mask = None
            if camouflage_mask_value is not None:
                if not isinstance(camouflage_mask_value, str) or not camouflage_mask_value:
                    raise ConversionError("camouflage_mask must be a path string")
                logical_mask = Path(camouflage_mask_value.replace("\\", "/"))
                if logical_mask.is_absolute() or ".." in logical_mask.parts:
                    raise ConversionError("unsafe camouflage_mask path")
                camouflage_mask = logical_mask.as_posix()
            palette_value = render_set.get("camouflage_palette")
            camouflage_palette = None
            if palette_value is not None:
                if (
                    not isinstance(palette_value, list)
                    or len(palette_value) != 4
                    or not all(
                        isinstance(value, str)
                        and re.fullmatch(r"#[0-9A-Fa-f]{6}", value)
                        for value in palette_value
                    )
                ):
                    raise ConversionError(
                        "camouflage_palette must contain four #RRGGBB colours"
                    )
                camouflage_palette = tuple(palette_value)
            camouflage_blend = float(render_set.get("camouflage_blend", 0.0))
            if not math.isfinite(camouflage_blend) or not 0.0 <= camouflage_blend <= 1.0:
                raise ConversionError("camouflage_blend must be between 0 and 1")
            if (camouflage_mask is None) != (camouflage_palette is None):
                raise ConversionError(
                    "camouflage_mask and camouflage_palette must be supplied together"
                )

            object_name = blender_id_name(f"{geometry_label}__{part_name}")
            if object_name in used_objects:
                raise ConversionError(f"duplicate render-set object: {object_name}")
            used_objects.add(object_name)
            output.append(
                RenderSet(
                    geometry=geometry,
                    geometry_label=geometry_label,
                    vertices_section=vertices,
                    indices_section=indices,
                    part_name=part_name,
                    object_name=object_name,
                    group_name=blender_id_name(
                        f"{geometry_label}__{part_name}__render_set_{render_index:03d}"
                    ),
                    material_mfm_path=str(render_set["material_mfm_path"]),
                    material_name=str(render_set["material_name"]),
                    texture_root=texture_root,
                    texture_maps=texture_maps,
                    material_fx_path=material_fx_path,
                    material_properties=material_properties_value,
                    rigid_node_world_matrix=rigid_matrix,
                    rigid_node_name=rigid_node_name,
                    uv_scale=(uv_scale[0], uv_scale[1]),
                    camouflage_mask=camouflage_mask,
                    camouflage_palette=camouflage_palette,
                    camouflage_blend=camouflage_blend,
                )
            )
    return output


def find_texture(texture_root: Path, material_mfm_path: str, suffix: str) -> Path | None:
    stem = Path(material_mfm_path.replace("\\", "/")).stem
    stems = [stem]
    for tag in ("_skinned",):
        if stem.casefold().endswith(tag):
            stems.append(stem[: -len(tag)])
    wanted = {(candidate + suffix).casefold() for candidate in stems}
    direct_candidates = [
        candidate
        for candidate_stem in stems
        for candidate in (
            texture_root / (candidate_stem + suffix),
            texture_root / "textures" / (candidate_stem + suffix),
        )
    ]
    for candidate in direct_candidates:
        if candidate.is_file():
            return candidate.resolve()

    matches = [
        path.resolve()
        for path in texture_root.rglob("*")
        if path.is_file() and path.name.casefold() in wanted
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    mfm_parent_parts = {
        part.casefold()
        for part in Path(material_mfm_path.replace("\\", "/")).parent.parts
        if part not in (".", "..")
    }
    matches.sort(
        key=lambda path: (
            -sum(part.casefold() in mfm_parent_parts for part in path.parent.parts),
            len(path.parts),
            str(path).casefold(),
        )
    )
    return matches[0]


def resolve_materials(render_sets: Sequence[RenderSet]) -> tuple[list[dict], list[dict]]:
    materials: list[dict] = []
    missing_maps: list[dict] = []
    by_key: dict[tuple[str, ...], dict] = {}
    for render_set in render_sets:
        explicit_key = json.dumps(render_set.texture_maps, sort_keys=True)
        camouflage_key = json.dumps(
            {
                "mask": render_set.camouflage_mask,
                "palette": render_set.camouflage_palette,
                "blend": render_set.camouflage_blend,
            },
            sort_keys=True,
        )
        key = (
            render_set.material_name,
            render_set.material_mfm_path,
            str(render_set.texture_root),
            explicit_key,
            render_set.material_fx_path or "",
            json.dumps(render_set.material_properties, sort_keys=True),
            camouflage_key,
        )
        if key not in by_key:
            maps: dict[str, str | None] = {}
            if render_set.texture_maps is not None:
                for channel, logical_path in render_set.texture_maps.items():
                    texture = (render_set.texture_root / logical_path).resolve()
                    root = render_set.texture_root.resolve()
                    try:
                        texture.relative_to(root)
                    except ValueError as error:
                        raise ConversionError(
                            f"texture path leaves texture root: {logical_path}"
                        ) from error
                    maps[channel] = str(texture) if texture.is_file() else None
                    if not texture.is_file():
                        missing_maps.append(
                            {
                                "material_name": render_set.material_name,
                                "material_mfm_path": render_set.material_mfm_path,
                                "map": channel,
                                "logical_path": logical_path,
                                "texture_root": str(render_set.texture_root),
                                "source_declared": True,
                            }
                        )
                resolution = "explicit material-prototype texture properties"
            else:
                for channel, suffix in MAP_SUFFIXES.items():
                    texture = find_texture(
                        render_set.texture_root, render_set.material_mfm_path, suffix
                    )
                    maps[channel] = str(texture) if texture else None
                    if texture is None:
                        missing_maps.append(
                            {
                                "material_name": render_set.material_name,
                                "material_mfm_path": render_set.material_mfm_path,
                                "map": channel,
                                "expected_filename": (
                                    Path(render_set.material_mfm_path).stem + suffix
                                ),
                                "texture_root": str(render_set.texture_root),
                                "source_declared": False,
                            }
                        )
                resolution = "MFM-stem heuristic fallback"
            camouflage = None
            if render_set.camouflage_mask is not None:
                mask = (render_set.texture_root / render_set.camouflage_mask).resolve()
                root = render_set.texture_root.resolve()
                try:
                    mask.relative_to(root)
                except ValueError as error:
                    raise ConversionError(
                        f"camouflage mask leaves texture root: {render_set.camouflage_mask}"
                    ) from error
                camouflage = {
                    "mask": str(mask) if mask.is_file() else None,
                    "palette": list(render_set.camouflage_palette or ()),
                    "blend": render_set.camouflage_blend,
                }
                if not mask.is_file():
                    missing_maps.append(
                        {
                            "material_name": render_set.material_name,
                            "map": "camouflage_mask",
                            "logical_path": render_set.camouflage_mask,
                            "texture_root": str(render_set.texture_root),
                            "source_declared": True,
                        }
                    )
            material = {
                "id": f"material_{len(materials):03d}",
                "name": render_set.material_name,
                "mfm_path": render_set.material_mfm_path,
                "maps": maps,
                "source_maps": dict(maps),
                "source_declared_channels": sorted(maps),
                "map_resolution": resolution,
                "fx_path": render_set.material_fx_path,
                "properties": render_set.material_properties,
                "camouflage": camouflage,
                "mg_semantics": (
                    "unknown; retained as a Non-Color image and split-channel nodes, "
                    "not connected to Metallic or Roughness"
                ),
            }
            by_key[key] = material
            materials.append(material)
    for render_set in render_sets:
        key = (
            render_set.material_name,
            render_set.material_mfm_path,
            str(render_set.texture_root),
            json.dumps(render_set.texture_maps, sort_keys=True),
            render_set.material_fx_path or "",
            json.dumps(render_set.material_properties, sort_keys=True),
            json.dumps(
                {
                    "mask": render_set.camouflage_mask,
                    "palette": render_set.camouflage_palette,
                    "blend": render_set.camouflage_blend,
                },
                sort_keys=True,
            ),
        )
        object_material = by_key[key]
        object_material.setdefault("objects", []).append(render_set.object_name)
    return materials, missing_maps


TEXTURE_TRANSCODE_REVISION = "shared-png-rg-normal-z-v1"


def _shared_texture_target(
    shared_texture_dir: Path,
    source: Path,
    channel: str,
    variant: str = "",
) -> Path:
    stat = source.stat()
    identity = json.dumps(
        {
            "revision": TEXTURE_TRANSCODE_REVISION,
            "channel": channel,
            "source": os.path.normcase(str(source.resolve())),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "variant": variant,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()
    return shared_texture_dir / f"{key}.png"


def _save_png_atomic(image, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        image.save(temporary, format="PNG", optimize=False)
        for attempt in range(20):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                # Parallel model workers can finish the same content-addressed
                # texture at the same moment. A destination that now exists is
                # an identical, completely published PNG from the other worker.
                if target.is_file() and target.stat().st_size > 0:
                    return
                if attempt == 19:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def apply_palette_camouflage(base_image, mask_image, palette, blend: float):
    """Bake a four-colour RGB camouflage mask into a diffuse image."""

    from PIL import Image, ImageChops, ImageOps

    base = base_image.convert("RGBA")
    mask = mask_image.convert("RGB")
    if mask.size != base.size:
        mask = mask.resize(base.size, resample=Image.Resampling.BILINEAR)
    red, green, blue = mask.split()
    black = ImageOps.invert(ImageChops.lighter(red, ImageChops.lighter(green, blue)))

    def colour(value: str) -> tuple[int, int, int]:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))

    colours = [colour(value) for value in palette]
    colour_map = Image.new("RGB", base.size, colours[3])
    colour_map.paste(colours[0], mask=red)
    colour_map.paste(colours[1], mask=green)
    colour_map.paste(colours[2], mask=blue)
    colour_map.paste(colours[3], mask=black)

    luminance = ImageOps.grayscale(base.convert("RGB"))
    scaled_channels = []
    for channel in colour_map.split():
        doubled = channel.point(lambda value: min(255, value * 2))
        scaled_channels.append(ImageChops.multiply(luminance, doubled))
    coloured = Image.merge("RGB", tuple(scaled_channels))
    mixed = Image.blend(base.convert("RGB"), coloured, blend)
    mixed.putalpha(base.getchannel("A"))
    return mixed


def prepare_blender_textures(
    materials: Sequence[dict],
    output_dir: Path,
    shared_texture_dir: Path | None = None,
) -> None:
    """Transcode each unique DDS once and reuse the PNG across ship parts."""
    try:
        from PIL import Image
    except ImportError as error:
        raise ConversionError(
            "Pillow is required to transcode Legends DDS maps for Blender 3.5"
        ) from error

    texture_output = (
        shared_texture_dir.resolve()
        if shared_texture_dir is not None
        else output_dir / "_textures"
    )
    texture_output.mkdir(parents=True, exist_ok=True)
    for material in materials:
        blender_maps: dict[str, str | None] = {}
        camouflage = material.get("camouflage")
        for channel, source_value in material["source_maps"].items():
            if source_value is None:
                blender_maps[channel] = None
                continue
            source = Path(source_value)
            variant = ""
            mask_source = None
            if channel == "a" and isinstance(camouflage, dict):
                mask_value = camouflage.get("mask")
                if isinstance(mask_value, str) and mask_value:
                    mask_source = Path(mask_value)
                    if mask_source.is_file():
                        mask_stat = mask_source.stat()
                        variant = json.dumps(
                            {
                                "camouflage_mask": str(mask_source.resolve()),
                                "mask_bytes": mask_stat.st_size,
                                "mask_mtime_ns": mask_stat.st_mtime_ns,
                                "palette": camouflage.get("palette"),
                                "blend": camouflage.get("blend"),
                            },
                            sort_keys=True,
                        )
            target = (
                _shared_texture_target(texture_output, source, channel, variant)
                if shared_texture_dir is not None
                else texture_output / f"{material['id']}_{channel}.png"
            )
            if not target.is_file():
                try:
                    with Image.open(source) as image:
                        image.load()
                        if image.mode not in ("RGB", "RGBA", "L", "LA"):
                            image = image.convert("RGBA")
                        if channel == "n":
                            image = reconstruct_tangent_normal(image)
                        elif mask_source is not None and mask_source.is_file():
                            with Image.open(mask_source) as mask_image:
                                mask_image.load()
                                image = apply_palette_camouflage(
                                    image,
                                    mask_image,
                                    camouflage["palette"],
                                    float(camouflage["blend"]),
                                )
                        _save_png_atomic(image, target)
                except (OSError, ValueError) as error:
                    raise ConversionError(
                        f"cannot transcode {channel} texture {source}: {error}"
                    ) from error
            blender_maps[channel] = str(target.resolve())
        material["maps"] = blender_maps
        material["texture_transcode"] = (
            "DDS source -> shared PNG; RG normal +Z reconstructed; "
            "active RGB camouflage masks baked into diffuse maps"
        )



def write_obj(
    mapped_parts: Sequence[tuple[RenderSet, object]], obj_path: Path, mtl_path: Path
) -> dict[str, int]:
    """Write one object/group/material per exact render-set join."""
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    total_vertices = 0
    total_triangles = 0
    vertex_base = 1
    with obj_path.open("wb") as output:
        output.write(b"# WoWS: Legends exact render-set LOD0 export\n")
        output.write(f"mtllib {mtl_path.name}\n".encode("utf-8"))
        for render_set, part in mapped_parts:
            rigid_matrix = component_space_matrix(
                render_set.rigid_node_world_matrix
            )
            mirrored = determinant3(rigid_matrix) < 0.0
            output.write(f"o {render_set.object_name}\n".encode("utf-8"))
            output.write(
                f"# vertices_section {render_set.vertices_section}\n".encode("utf-8")
            )
            output.write(
                f"# indices_section {render_set.indices_section}\n".encode("utf-8")
            )
            output.write(f"g {render_set.group_name}\n".encode("utf-8"))
            output.write(f"usemtl {safe_name(render_set.material_name)}\n".encode("utf-8"))
            for vertex in part.vertices:
                output.write(
                    ("v {:.9g} {:.9g} {:.9g}\n".format(
                        *transform_point(rigid_matrix, vertex.position)
                    )).encode("ascii")
                )
            for vertex in part.vertices:
                output.write(
                    ("vt {:.9g} {:.9g}\n".format(vertex.uv[0] * render_set.uv_scale[0], vertex.uv[1] * render_set.uv_scale[1])).encode("ascii")
                )
            for vertex in part.vertices:
                output.write(
                    ("vn {:.9g} {:.9g} {:.9g}\n".format(
                        *transform_normal(rigid_matrix, vertex.normal)
                    )).encode("ascii")
                )
            for triangle in part.triangles:
                if mirrored:
                    triangle = (triangle[0], triangle[2], triangle[1])
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

    with mtl_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("# Material names are replaced by Blender PBR nodes later.\n")
        for material_name in sorted(
            {safe_name(render_set.material_name) for render_set, _ in mapped_parts}
        ):
            output.write(f"newmtl {material_name}\nKd 0.8 0.8 0.8\n\n")
    return {
        "parts": len(mapped_parts),
        "vertices": total_vertices,
        "triangles": total_triangles,
    }


def map_parts(decoder, render_sets: Sequence[RenderSet]):
    grouped: dict[Path, list[RenderSet]] = {}
    for item in render_sets:
        grouped.setdefault(item.geometry, []).append(item)

    mapped: list[tuple[RenderSet, object]] = []
    joins: list[dict] = []
    errors: list[str] = []
    for geometry, requests in grouped.items():
        sections, parts = decoder.decode_geometry(geometry.read_bytes())
        section_names = {section.name for section in sections}
        parts_by_name = {part.name: part for part in parts}
        for request in requests:
            missing_sections = [
                name
                for name in (request.vertices_section, request.indices_section)
                if name not in section_names
            ]
            part = parts_by_name.get(request.part_name)
            status = "matched"
            if missing_sections or part is None:
                status = "missing"
                errors.append(
                    f"{geometry.name}:{request.part_name}: "
                    f"missing_sections={missing_sections}, part_found={part is not None}"
                )
            else:
                mapped.append((request, part))
            joins.append(
                {
                    "geometry": str(geometry),
                    "vertices_section": request.vertices_section,
                    "indices_section": request.indices_section,
                    "part_name": request.part_name,
                    "object_name": request.object_name,
                    "group_name": request.group_name,
                    "material_name": request.material_name,
                    "status": status,
                }
            )
    if errors:
        raise ConversionError("render-set join failed: " + "; ".join(errors))
    if len(mapped) != len(render_sets):
        raise ConversionError(
            f"render-set acceptance failed: {len(mapped)}/{len(render_sets)} joined"
        )
    return mapped, joins


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--decoder-root",
        type=Path,
        default=default_decoder_root(),
    )
    parser.add_argument(
        "--blender",
        type=Path,
        default=Path(r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe"),
    )
    parser.add_argument("--shared-texture-dir", type=Path)
    parser.add_argument("--skip-blender", action="store_true")
    parser.add_argument("--no-blend", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = args.manifest.resolve()
    output_dir = args.output_dir.resolve()
    payload = load_manifest(manifest_path)
    name = safe_name(args.name or payload.get("name") or manifest_path.stem)
    decoder = load_decoder(args.decoder_root.resolve())
    render_sets = parse_render_sets(payload, manifest_path)
    mapped_parts, joins = map_parts(decoder, render_sets)
    materials, missing_maps = resolve_materials(render_sets)

    obj_path = output_dir / f"{name}.obj"
    mtl_path = output_dir / f"{name}.mtl"
    blender_input = output_dir / f"{name}.blender-input.json"
    validation_path = output_dir / f"{name}.validation.json"
    glb_path = output_dir / f"{name}.glb"
    blend_path = output_dir / f"{name}.blend"
    totals = write_obj(mapped_parts, obj_path, mtl_path)
    prepare_blender_textures(
        materials,
        output_dir,
        args.shared_texture_dir.resolve()
        if args.shared_texture_dir is not None
        else None,
    )

    blender_payload = {
        "schema": 1,
        "name": name,
        "obj": str(obj_path),
        "glb": str(glb_path),
        "blend": str(blend_path),
        "save_blend": not args.no_blend,
        "validation": str(validation_path),
        "objects": [
            {
                **asdict(render_set),
                "geometry": str(render_set.geometry),
                "texture_root": str(render_set.texture_root),
            }
            for render_set in render_sets
        ],
        "materials": materials,
        "pre_blender": {
            "totals": totals,
            "render_set_count": len(render_sets),
            "matched_render_sets": len(mapped_parts),
            "missing_render_sets": 0,
            "joins": joins,
            "missing_maps": missing_maps,
            "obj_sha256": sha256(obj_path),
        },
    }
    write_json(blender_input, blender_payload)

    if not args.skip_blender:
        blender = args.blender.resolve()
        if not blender.is_file():
            raise ConversionError(f"Blender executable not found: {blender}")
        blender_script = Path(__file__).with_name("blender_pbr.py").resolve()
        command = [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(blender_script),
            "--",
            str(blender_input),
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        (output_dir / f"{name}.blender.log").write_text(
            result.stdout + "\n--- STDERR ---\n" + result.stderr,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise ConversionError(
                f"Blender failed with exit code {result.returncode}; "
                f"see {output_dir / (name + '.blender.log')}"
            )
        expected_missing = (
            not validation_path.is_file()
            or not glb_path.is_file()
            or (not args.no_blend and not blend_path.is_file())
        )
        if expected_missing:
            raise ConversionError("Blender returned success but expected outputs are missing")
    else:
        validation = {
            **blender_payload["pre_blender"],
            "status": "OBJ_ONLY",
            "blender_skipped": True,
        }
        write_json(validation_path, validation)

    print(
        json.dumps(
            {
                "status": "OK",
                "obj": str(obj_path),
                "blend": (
                    str(blend_path)
                    if not args.skip_blender and not args.no_blend else None
                ),
                "glb": str(glb_path) if not args.skip_blender else None,
                "validation": str(validation_path),
                "render_sets": len(render_sets),
                "missing_render_sets": 0,
                "missing_maps": len(missing_maps),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConversionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from None
