from __future__ import annotations

"""Convert the PC/Korabli exporter GLB to editable OBJ without Blender."""

import argparse
from io import BytesIO
import hashlib
import json
import math
import os
import re
import shutil
import struct
from pathlib import Path
from typing import Iterable, Iterator

from PIL import Image


COMPONENTS = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
TYPE_WIDTHS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}
IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class NativeGlbError(RuntimeError):
    pass


def language() -> str:
    return "en" if os.environ.get("WOWS_TOOLBOX_LANGUAGE", "").casefold() == "en" else "ko"


def text(ko: str, en: str) -> str:
    return en if language() == "en" else ko


def progress(stage: str, percent: int, ko: str, en: str) -> None:
    print(
        "[PROGRESS] "
        + json.dumps(
            {"stage": stage, "percent": percent, "message": text(ko, en)},
            ensure_ascii=False,
        ),
        flush=True,
    )


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_.()\[\] -]+", "_", value).strip(" ._")
    return (cleaned or fallback)[:120]


def unique_names(values: Iterable[str], fallback: str) -> list[str]:
    result: list[str] = []
    used: set[str] = set()
    for index, value in enumerate(values):
        base = safe_name(value, f"{fallback}_{index:03d}")
        candidate = base
        serial = 2
        while candidate.casefold() in used:
            candidate = f"{base[:110]}_{serial:02d}"
            serial += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def load_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise NativeGlbError(text("GLB 헤더가 올바르지 않아요", "Invalid GLB header"))
    version, declared = struct.unpack_from("<II", data, 4)
    if version != 2 or declared != len(data):
        raise NativeGlbError(text("GLB 버전 또는 길이가 올바르지 않아요", "Invalid GLB version or length"))
    offset = 12
    document: dict | None = None
    binary = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        payload = data[offset : offset + length]
        offset += length
        if kind == b"JSON":
            document = json.loads(payload.rstrip(b" \0").decode("utf-8"))
        elif kind == b"BIN\0":
            binary = payload
    if not isinstance(document, dict) or not binary:
        raise NativeGlbError(text("GLB JSON/BIN 청크가 없어요", "GLB JSON/BIN chunk is missing"))
    return document, binary


def _normalize(value: int | float, component_type: int) -> float:
    if component_type == 5120:
        return max(float(value) / 127.0, -1.0)
    if component_type == 5121:
        return float(value) / 255.0
    if component_type == 5122:
        return max(float(value) / 32767.0, -1.0)
    if component_type == 5123:
        return float(value) / 65535.0
    return float(value)


def accessor_values(document: dict, binary: bytes, index: int) -> list[tuple]:
    try:
        accessor = document["accessors"][index]
        view = document["bufferViews"][accessor["bufferView"]]
        component_type = int(accessor["componentType"])
        component_format, component_size = COMPONENTS[component_type]
        width = TYPE_WIDTHS[accessor["type"]]
        count = int(accessor["count"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise NativeGlbError(f"Invalid accessor {index}: {exc}") from exc
    if accessor.get("sparse"):
        raise NativeGlbError(f"Sparse accessor is not supported: {index}")
    packed_size = component_size * width
    stride = int(view.get("byteStride", packed_size))
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    unpack = struct.Struct("<" + component_format * width).unpack_from
    normalized = bool(accessor.get("normalized"))
    result: list[tuple] = []
    for row in range(count):
        values = unpack(binary, start + row * stride)
        if normalized:
            values = tuple(_normalize(value, component_type) for value in values)
        result.append(values)
    return result


def multiply(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    result = [0.0] * 16
    for column in range(4):
        for row in range(4):
            result[column * 4 + row] = sum(
                a[k * 4 + row] * b[column * 4 + k] for k in range(4)
            )
    return tuple(result)


def trs_matrix(node: dict) -> tuple[float, ...]:
    if "matrix" in node:
        values = tuple(float(value) for value in node["matrix"])
        if len(values) != 16:
            raise NativeGlbError("Node matrix must contain 16 values")
        return values
    tx, ty, tz = (list(node.get("translation", (0.0, 0.0, 0.0))) + [0.0] * 3)[:3]
    sx, sy, sz = (list(node.get("scale", (1.0, 1.0, 1.0))) + [1.0] * 3)[:3]
    x, y, z, w = (list(node.get("rotation", (0.0, 0.0, 0.0, 1.0))) + [0.0, 0.0, 0.0, 1.0])[:4]
    length = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / length, y / length, z / length, w / length
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz)) * sx,
        (2 * (xy + wz)) * sx,
        (2 * (xz - wy)) * sx,
        0.0,
        (2 * (xy - wz)) * sy,
        (1 - 2 * (xx + zz)) * sy,
        (2 * (yz + wx)) * sy,
        0.0,
        (2 * (xz + wy)) * sz,
        (2 * (yz - wx)) * sz,
        (1 - 2 * (xx + yy)) * sz,
        0.0,
        float(tx), float(ty), float(tz), 1.0,
    )


def transform_point(matrix: tuple[float, ...], value: tuple) -> tuple[float, float, float]:
    x, y, z = (float(value[0]), float(value[1]), float(value[2]))
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


def transform_normal(matrix: tuple[float, ...], value: tuple) -> tuple[float, float, float]:
    a, b, c = matrix[0], matrix[4], matrix[8]
    d, e, f = matrix[1], matrix[5], matrix[9]
    g, h, i = matrix[2], matrix[6], matrix[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    x, y, z = float(value[0]), float(value[1]), float(value[2])
    if abs(det) < 1e-12:
        nx = a * x + b * y + c * z
        ny = d * x + e * y + f * z
        nz = g * x + h * y + i * z
    else:
        # inverse(A)^T * normal
        nx = ((e * i - f * h) * x + (f * g - d * i) * y + (d * h - e * g) * z) / det
        ny = ((c * h - b * i) * x + (a * i - c * g) * y + (b * g - a * h) * z) / det
        nz = ((b * f - c * e) * x + (c * d - a * f) * y + (a * e - b * d) * z) / det
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / length, ny / length, nz / length


def scene_meshes(document: dict) -> list[tuple[int, dict, tuple[float, ...], str | None]]:
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    if not scenes:
        roots = list(range(len(nodes)))
    else:
        scene_index = int(document.get("scene", 0))
        roots = scenes[scene_index].get("nodes", [])
    result: list[tuple[int, dict, tuple[float, ...], str | None]] = []
    active: set[int] = set()

    def walk(index: int, parent: tuple[float, ...], parent_name: str | None) -> None:
        if index in active:
            raise NativeGlbError(f"Node cycle detected at {index}")
        active.add(index)
        node = nodes[index]
        world = multiply(parent, trs_matrix(node))
        name = str(node.get("name") or f"Node_{index:03d}")
        if "mesh" in node:
            result.append((index, node, world, parent_name))
        for child in node.get("children", []):
            walk(int(child), world, name)
        active.remove(index)

    for root in roots:
        walk(int(root), IDENTITY, None)
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_shared_texture(target: Path, library: Path | None) -> bool:
    if library is None:
        return False
    digest = sha256(target)
    shared = library / digest[:2] / f"{digest}.png"
    shared.parent.mkdir(parents=True, exist_ok=True)
    temporary = shared.with_suffix(f".{os.getpid()}.{id(target)}.part")
    if not shared.is_file() or shared.stat().st_size <= 0:
        shutil.copy2(target, temporary)
        try:
            try:
                os.replace(temporary, shared)
            except PermissionError:
                if not shared.is_file() or shared.stat().st_size <= 0:
                    raise
        finally:
            temporary.unlink(missing_ok=True)
    # The exported file must remain independent. Hard-linking it to the
    # shared cache would let an editor mutate the cache and other exports.
    return False


def image_payload(document: dict, binary: bytes, image: dict, source: Path) -> bytes:
    if "bufferView" in image:
        view = document["bufferViews"][int(image["bufferView"])]
        start = int(view.get("byteOffset", 0))
        return binary[start : start + int(view["byteLength"])]
    uri = str(image.get("uri", ""))
    if uri.startswith("data:"):
        import base64
        return base64.b64decode(uri.split(",", 1)[1])
    if uri:
        return (source.parent / uri).read_bytes()
    raise NativeGlbError("Image has neither bufferView nor URI")


def export_textures(
    document: dict,
    binary: bytes,
    source: Path,
    texture_dir: Path,
    max_size: int,
    library: Path | None,
) -> tuple[dict[int, str], list[str], int, int]:
    images = document.get("images", [])
    names = unique_names(
        [str(image.get("name") or f"Texture_{index:03d}") for index, image in enumerate(images)],
        "Texture",
    )
    texture_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[int, str] = {}
    paths: list[str] = []
    resized = 0
    linked = 0
    for index, image in enumerate(images):
        payload = image_payload(document, binary, image, source)
        target = texture_dir / f"{names[index]}.png"
        with Image.open(BytesIO(payload)) as opened:
            converted = opened.convert("RGBA") if opened.mode not in {"RGB", "RGBA"} else opened.copy()
        if max_size > 0 and max(converted.size) > max_size:
            scale = max_size / max(converted.size)
            converted = converted.resize(
                (max(1, round(converted.width * scale)), max(1, round(converted.height * scale))),
                Image.Resampling.LANCZOS,
            )
            resized += 1
        converted.save(target, format="PNG", optimize=True)
        linked += int(publish_shared_texture(target, library))
        mapping[index] = f"textures/{target.name}"
        paths.append(str(target))
    return mapping, paths, resized, linked


def classify_part(name: str) -> str:
    folded = name.casefold()
    if (
        not folded.startswith("hp_")
        and any(token in folded for token in ("_bow", "_midback", "_midfront", "_stern", "hull"))
    ):
        return "hull"
    rules = (
        ("missile_launcher", ("vertical_launch", "guided_missile", "missile_launcher", "vls")),
        ("secondary", ("_ags_", "_bgs_", "secondary", "secgun", "casemate", "_jgs_", "_ags")),
        ("anti_air", ("_aga_", "_bga_", "antiair", "anti_air", "aagun", "machinegun", "_jga_")),
        ("torpedo", ("_agt_", "_bgt_", "torpedo", "ttube", "torp")),
        ("radar_sensor", ("_ad_", "_bd_", "_ars_", "_jrs_", "radar", "director", "rangefinder", "sensor")),
        ("main_gun", ("_agm_", "_bgm_", "_jgm_", "main_gun", "main_artillery", "turret")),
        ("deck_superstructure", ("deck", "superstructure", "deckhouse", "bridge")),
        ("aircraft", ("aircraft", "plane", "catapult")),
        ("decoration", ("flag", "rope", "wire", "anchor", "decor")),
    )
    for category, tokens in rules:
        if any(token in folded for token in tokens):
            return category
    return "other"


def material_texture_index(document: dict, material: dict) -> int | None:
    info = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if not isinstance(info, dict) or "index" not in info:
        return None
    texture = document.get("textures", [])[int(info["index"])]
    if "source" in texture:
        return int(texture["source"])
    basis = texture.get("extensions", {}).get("KHR_texture_basisu", {})
    return int(basis["source"]) if "source" in basis else None


def write_mtl(document: dict, target: Path, image_paths: dict[int, str]) -> list[str]:
    materials = document.get("materials", [])
    names = unique_names(
        [str(item.get("name") or f"Material_{index:03d}") for index, item in enumerate(materials)],
        "Material",
    )
    with target.open("w", encoding="utf-8", newline="\n") as output:
        output.write("# WoWS Toolbox native GLB material library\n")
        for index, material in enumerate(materials):
            pbr = material.get("pbrMetallicRoughness", {})
            factor = list(pbr.get("baseColorFactor", (1.0, 1.0, 1.0, 1.0))) + [1.0] * 4
            output.write(f"\nnewmtl {names[index]}\n")
            output.write(f"Kd {factor[0]:.6g} {factor[1]:.6g} {factor[2]:.6g}\n")
            output.write("Ka 0 0 0\nKs 0.02 0.02 0.02\nNs 16\nillum 2\n")
            if factor[3] < 0.999:
                output.write(f"d {factor[3]:.6g}\n")
            image_index = material_texture_index(document, material)
            if image_index is not None and image_index in image_paths:
                output.write(f"map_Kd {image_paths[image_index]}\n")
                if str(material.get("alphaMode", "OPAQUE")) != "OPAQUE":
                    output.write(f"map_d {image_paths[image_index]}\n")
    return names


def triangles(indices: list[int], mode: int) -> Iterator[tuple[int, int, int]]:
    if mode == 4:
        for offset in range(0, len(indices) - 2, 3):
            yield indices[offset], indices[offset + 1], indices[offset + 2]
    elif mode == 5:
        for offset in range(len(indices) - 2):
            if offset % 2:
                yield indices[offset + 1], indices[offset], indices[offset + 2]
            else:
                yield indices[offset], indices[offset + 1], indices[offset + 2]
    elif mode == 6 and len(indices) >= 3:
        for offset in range(1, len(indices) - 1):
            yield indices[0], indices[offset], indices[offset + 1]
    else:
        raise NativeGlbError(f"Unsupported primitive mode: {mode}")


def face_token(vertex: int, uv: int | None, normal: int | None) -> str:
    if uv is not None and normal is not None:
        return f"{vertex}/{uv}/{normal}"
    if uv is not None:
        return f"{vertex}/{uv}"
    if normal is not None:
        return f"{vertex}//{normal}"
    return str(vertex)


def export_obj(
    document: dict,
    binary: bytes,
    target: Path,
    material_names: list[str],
) -> tuple[list[dict], int, int]:
    scene_entries = scene_meshes(document)
    object_names = unique_names(
        [str(node.get("name") or document["meshes"][int(node["mesh"])].get("name", "")) for _, node, _, _ in scene_entries],
        "Part",
    )
    vertex_offset = uv_offset = normal_offset = 0
    objects: list[dict] = []
    triangle_total = 0
    with target.open("w", encoding="utf-8", newline="\n", buffering=1024 * 1024) as output:
        output.write("# WoWS Toolbox Blender-free PC/Korabli OBJ\n")
        output.write(f"mtllib {target.with_suffix('.mtl').name}\n")
        for entry_index, ((_, node, world, parent_name), object_name) in enumerate(zip(scene_entries, object_names)):
            mesh = document["meshes"][int(node["mesh"])]
            output.write(f"\no {object_name}\n")
            object_vertices = 0
            object_triangles = 0
            for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
                attributes = primitive.get("attributes", {})
                if "POSITION" not in attributes:
                    continue
                positions = accessor_values(document, binary, int(attributes["POSITION"]))
                normals = accessor_values(document, binary, int(attributes["NORMAL"])) if "NORMAL" in attributes else []
                uvs = accessor_values(document, binary, int(attributes["TEXCOORD_0"])) if "TEXCOORD_0" in attributes else []
                if "indices" in primitive:
                    indices = [int(row[0]) for row in accessor_values(document, binary, int(primitive["indices"]))]
                else:
                    indices = list(range(len(positions)))
                output.write(f"g {object_name}_P{primitive_index:02d}\n")
                material_index = primitive.get("material")
                if material_index is not None and int(material_index) < len(material_names):
                    output.write(f"usemtl {material_names[int(material_index)]}\n")
                for value in positions:
                    x, y, z = transform_point(world, value)
                    output.write(f"v {x:.8g} {y:.8g} {z:.8g}\n")
                for value in uvs:
                    # Blender's glTF->OBJ path flips V to preserve the source image orientation.
                    output.write(f"vt {float(value[0]):.8g} {1.0 - float(value[1]):.8g}\n")
                for value in normals:
                    x, y, z = transform_normal(world, value)
                    output.write(f"vn {x:.8g} {y:.8g} {z:.8g}\n")
                reverse = determinant3(world) < 0.0
                for a, b, c in triangles(indices, int(primitive.get("mode", 4))):
                    if reverse:
                        b, c = c, b
                    tokens = []
                    for item in (a, b, c):
                        tokens.append(
                            face_token(
                                vertex_offset + item + 1,
                                uv_offset + item + 1 if uvs else None,
                                normal_offset + item + 1 if normals else None,
                            )
                        )
                    output.write("f " + " ".join(tokens) + "\n")
                    object_triangles += 1
                vertex_offset += len(positions)
                uv_offset += len(uvs)
                normal_offset += len(normals)
                object_vertices += len(positions)
            if object_vertices:
                category = classify_part(object_name)
                objects.append(
                    {
                        "name": object_name,
                        "category": category,
                        "parent": parent_name,
                        "pivot": [round(world[12], 6), round(world[13], 6), round(world[14], 6)],
                        "vertices": object_vertices,
                        "polygons": object_triangles,
                    }
                )
                triangle_total += object_triangles
    return objects, vertex_offset, triangle_total


def build(args: argparse.Namespace) -> dict:
    formats = {part.strip().casefold() for part in args.formats.split(",") if part.strip()} or {"obj"}
    unsupported = formats - {"obj", "glb"}
    if unsupported:
        raise NativeGlbError(
            text(
                "WoWS Toolbox는 Blender 없이 OBJ/원본 GLB만 지원해요: ",
                "WoWS Toolbox supports OBJ/raw GLB without Blender only: ",
            )
            + ", ".join(sorted(unsupported))
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    document, binary = load_glb(args.input)
    progress("obj", 12, "원본 GLB 구조를 읽는 중", "Reading the source GLB structure")
    image_paths: dict[int, str] = {}
    textures: list[str] = []
    resized = linked = 0
    if "obj" in formats:
        progress("texture", 40, "텍스처를 PNG로 준비하는 중", "Preparing textures as PNG files")
        image_paths, textures, resized, linked = export_textures(
            document,
            binary,
            args.input,
            args.output.parent / "textures",
            max(0, args.texture_max_size),
            args.texture_library,
        )
    material_names = write_mtl(document, args.output.with_suffix(".mtl"), image_paths)
    objects: list[dict] = []
    vertices = triangle_count = 0
    if "obj" in formats:
        progress("obj", 68, "파트별 OBJ를 쓰는 중", "Writing the part-based OBJ")
        objects, vertices, triangle_count = export_obj(document, binary, args.output, material_names)
    editable_glb = args.output.with_suffix(".editable.glb")
    if "glb" in formats:
        progress("glb", 88, "원본 계층 GLB를 복사하는 중", "Copying the original hierarchical GLB")
        shutil.copy2(args.input, editable_glb)
    categories: dict[str, int] = {}
    for item in objects:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    weapon_keys = ("main_gun", "secondary", "anti_air", "torpedo", "missile_launcher", "radar_sensor")
    weapon_counts = {key: categories.get(key, 0) for key in weapon_keys}
    hierarchy = {
        "schema": "wows-toolbox-model/v1",
        "coordinate_space": "gltf-rh-y-up",
        "obj_axis_forward": "-Z",
        "obj_axis_up": "Y",
        "categories": categories,
        "objects": objects,
        "weapon_counts": weapon_counts,
    }
    model_report = args.output.with_suffix(".model.json")
    model_report.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")
    obj_ok = "obj" not in formats or (args.output.is_file() and args.output.stat().st_size > 0)
    glb_ok = "glb" not in formats or (editable_glb.is_file() and editable_glb.stat().st_size > 0)
    verification = {
        "passed": obj_ok and glb_ok and ("obj" not in formats or bool(objects)),
        "editable_parts": len(objects),
        "hull_parts": categories.get("hull", 0),
        "weapon_and_sensor_parts": sum(weapon_counts.values()),
        "warnings": [],
    }
    if categories.get("hull", 0) <= 0:
        verification["warnings"].append(text("선체 파트를 분류하지 못했어요", "No hull part was classified"))
    report = {
        "ok": verification["passed"],
        "engine": "native_python_glb_obj/v1",
        "native_no_blender": True,
        "formats": sorted(formats),
        "obj": str(args.output) if "obj" in formats and obj_ok else None,
        "mtl": str(args.output.with_suffix(".mtl")) if "obj" in formats else None,
        "editable_glb": str(editable_glb) if "glb" in formats and glb_ok else None,
        "model_report": str(model_report),
        "textures": textures,
        "texture_max_size": args.texture_max_size,
        "textures_resized": resized,
        "textures_shared": linked,
        "object_count": len(objects),
        "object_names": [item["name"] for item in objects],
        "vertex_count": vertices,
        "triangle_count": triangle_count,
        "categories": categories,
        "weapon_counts": weapon_counts,
        "verification": verification,
        "blend_created": False,
        "armor": {"available": False, "path": None, "groups": 0, "triangles": 0},
        "axis_forward": "-Z",
        "axis_up": "Y",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(
        "complete",
        100,
        f"편집 파트 {len(objects)}개 · OBJ 저장 완료",
        f"Saved OBJ with {len(objects)} editable parts",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Native GLB to OBJ exporter")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--formats", default="obj")
    parser.add_argument("--texture-max-size", type=int, default=0)
    parser.add_argument("--texture-library", type=Path)
    args = parser.parse_args()
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    args.report = args.report.resolve()
    if args.texture_library:
        args.texture_library = args.texture_library.resolve()
    build(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("[ERROR] " + str(exc), flush=True)
        raise SystemExit(1) from None
