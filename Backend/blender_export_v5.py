from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--armor-input", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--editable-glb", type=Path)
    parser.add_argument("--fbx", type=Path)
    parser.add_argument("--armor-glb", type=Path)
    parser.add_argument("--model-report", type=Path)
    parser.add_argument("--texture-max-size", type=int, default=0)
    parser.add_argument("--texture-library", type=Path)
    parser.add_argument("--formats", default="obj,glb")
    return parser.parse_args(argv)


SUPPORTED_FORMATS = {"obj", "glb", "fbx"}


def parse_formats(value: str) -> set[str]:
    formats = {
        item.strip().casefold()
        for item in value.split(",")
        if item.strip()
    } or {"obj", "glb"}
    unsupported = formats - SUPPORTED_FORMATS
    if unsupported:
        raise ValueError(
            "지원하지 않는 출력 형식: " + ", ".join(sorted(unsupported))
        )
    return formats


def progress(stage: str, percent: int, message: str) -> None:
    print("[PROGRESS] " + json.dumps(
        {"stage": stage, "percent": percent, "message": message},
        ensure_ascii=False,
    ), flush=True)


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_.()\[\] -]+", "_", value).strip(" ._")
    return (cleaned or fallback)[:120]


def unique_object_names(objects) -> None:
    used: set[str] = set()
    for index, obj in enumerate(sorted(objects, key=lambda item: item.name)):
        base = safe_name(obj.name, f"Part_{index:03d}")
        name = base
        serial = 2
        while name.casefold() in used:
            name = f"{base[:110]}_{serial:02d}"
            serial += 1
        used.add(name.casefold())
        obj.name = name
        if obj.data:
            obj.data.name = name + "_Mesh"


def classify_part(name: str) -> str:
    folded = name.casefold()
    if folded.startswith("hull_") or re.search(r"(?:^|[^a-z])[a-z]sc\d{3}", folded) or "shipmat_pbs_hull" in folded:
        return "hull"
    rules = (
        ("missile_launcher", ("vertical_launch", "guided_missile", "missile_launcher", "hp_agr", "vls")),
        ("secondary", ("hp_ags", "hp_bgs", "secondary_artillery", "secondary", "secgun", "casemate")),
        ("anti_air", ("hp_aga", "hp_bga", "antiair", "anti_air", "air_defense", "aa_", "aagun", "machinegun")),
        ("torpedo", ("hp_agt", "hp_bgt", "torpedo", "ttube", "torp")),
        ("radar_sensor", ("hp_ad", "hp_bd", "hp_ars", "radar", "director", "rangefinder", "sensor")),
        ("main_gun", ("hp_agm", "hp_bgm", "main_gun", "main_artillery", "main_battery", "turret", "shipmat_pbs_gun")),
        ("deck_superstructure", ("deck", "superstructure", "deckhouse", "bridge")),
        ("aircraft", ("aircraft", "plane", "catapult")),
        ("decoration", ("flag", "rope", "wire", "anchor", "decor")),
    )
    for category, tokens in rules:
        if any(token in folded for token in tokens):
            return category
    return "other"


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
    if not shared.exists():
        temporary = shared.with_suffix(f".{os.getpid()}.part")
        shutil.copy2(target, temporary)
        try:
            os.replace(temporary, shared)
        finally:
            temporary.unlink(missing_ok=True)
    # Keep user-visible output independent from the shared cache.
    return False


def resize_images(images, max_size: int) -> int:
    resized = 0
    for image in images:
        if image is None or image.size[0] <= 0 or image.size[1] <= 0:
            continue
        if max_size > 0 and max(image.size[0], image.size[1]) > max_size:
            scale = max_size / max(image.size[0], image.size[1])
            image.scale(
                max(1, int(round(image.size[0] * scale))),
                max(1, int(round(image.size[1] * scale))),
            )
            resized += 1
    return resized


def export_images(images, texture_dir: Path, max_size: int, texture_library: Path | None):
    texture_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    used: set[str] = set()
    resized = resize_images(images, max_size)
    linked = 0
    for index, image in enumerate(images):
        if image is None or image.size[0] <= 0 or image.size[1] <= 0:
            continue
        base = safe_name(Path(image.name).stem, f"Texture_{index:03d}")
        name = base + ".png"
        serial = 2
        while name.casefold() in used:
            name = f"{base}_{serial:02d}.png"
            serial += 1
        used.add(name.casefold())
        target = texture_dir / name
        try:
            image.filepath_raw = str(target)
            image.file_format = "PNG"
            image.save()
            linked += int(publish_shared_texture(target, texture_library))
            image.filepath = str(target)
            written.append(str(target))
        except Exception as exc:
            print(f"[WARN] 텍스처 저장 건너뜀: {image.name}: {exc}", flush=True)
    return written, resized, linked


ARMOR_BUCKETS = (
    {"id": 0, "label": "≤ 14 mm", "min_mm": 0, "max_mm": 14, "color": "#6ED1B0", "rgb": (110, 209, 176)},
    {"id": 1, "label": "15–16 mm", "min_mm": 15, "max_mm": 16, "color": "#95D27F", "rgb": (149, 210, 127)},
    {"id": 2, "label": "17–24 mm", "min_mm": 17, "max_mm": 24, "color": "#AAC966", "rgb": (170, 201, 102)},
    {"id": 3, "label": "25–26 mm", "min_mm": 25, "max_mm": 26, "color": "#C0C150", "rgb": (192, 193, 80)},
    {"id": 4, "label": "27–28 mm", "min_mm": 27, "max_mm": 28, "color": "#E2C33E", "rgb": (226, 195, 62)},
    {"id": 5, "label": "29–33 mm", "min_mm": 29, "max_mm": 33, "color": "#E1AB36", "rgb": (225, 171, 54)},
    {"id": 6, "label": "34–75 mm", "min_mm": 34, "max_mm": 75, "color": "#E39031", "rgb": (227, 144, 49)},
    {"id": 7, "label": "76–160 mm", "min_mm": 76, "max_mm": 160, "color": "#E67331", "rgb": (230, 115, 49)},
    {"id": 8, "label": "161–399 mm", "min_mm": 161, "max_mm": 399, "color": "#DC4E30", "rgb": (220, 78, 48)},
    {"id": 9, "label": "≥ 400 mm", "min_mm": 400, "max_mm": 999, "color": "#B92F30", "rgb": (185, 47, 48)},
)


def armor_zone(obj) -> str:
    current = obj
    while current is not None:
        name = str(current.name)
        folded = name.casefold()
        if folded.startswith(("armor", "armour")):
            zone = re.sub(r"^(?:armor|armour)[_ .-]*", "", name, flags=re.IGNORECASE)
            zone = re.sub(r"[_. -]+\d+$", "", zone).strip("_. -")
            return zone or "Hull"
        current = current.parent
    return ""


def closest_armor_bucket(color) -> int:
    rgb = tuple(max(0, min(255, round(component * 255))) for component in color[:3])
    distances = [
        sum((rgb[index] - bucket["rgb"][index]) ** 2 for index in range(3))
        for bucket in ARMOR_BUCKETS
    ]
    return int(min(range(len(distances)), key=distances.__getitem__))


def triangle_color(mesh, triangle, color_attribute):
    if color_attribute is None:
        return (0.43, 0.82, 0.69, 1.0)
    data = color_attribute.data
    if color_attribute.domain == "CORNER":
        values = [data[index].color for index in triangle.loops]
    elif color_attribute.domain == "POINT":
        values = [data[index].color for index in triangle.vertices]
    else:
        return tuple(data[triangle.index].color)
    count = max(1, len(values))
    return tuple(sum(value[channel] for value in values) / count for channel in range(4))


def export_armor_sidecar(objects, target: Path) -> dict:
    groups_by_key: dict[tuple[str, int], dict] = {}
    source_objects: list[str] = []
    for obj in objects:
        zone = armor_zone(obj) or "Hull"
        mesh = obj.data
        mesh.calc_loop_triangles()
        color_attribute = mesh.color_attributes.active_color if hasattr(mesh, "color_attributes") else None
        source_objects.append(obj.name)
        for triangle in mesh.loop_triangles:
            bucket_id = closest_armor_bucket(triangle_color(mesh, triangle, color_attribute))
            group = groups_by_key.setdefault(
                (zone, bucket_id),
                {"zone": zone, "bucket": bucket_id, "positions": [], "triangle_count": 0},
            )
            for vertex_index in triangle.vertices:
                vertex = obj.matrix_world @ mesh.vertices[vertex_index].co
                group["positions"].extend((
                    round(float(vertex.x), 5),
                    round(float(vertex.y), 5),
                    round(float(vertex.z), 5),
                ))
            group["triangle_count"] += 1
    groups = sorted(groups_by_key.values(), key=lambda item: (item["zone"], item["bucket"]))
    if not groups:
        return {"available": False, "path": None, "groups": 0, "triangles": 0, "zones": []}
    payload = {
        "schema": "wows-toolbox-armor-viewer/v2",
        "buckets": [
            {key: value for key, value in bucket.items() if key != "rgb"}
            for bucket in ARMOR_BUCKETS
        ],
        "zones": sorted({group["zone"] for group in groups}),
        "groups": groups,
        "source_objects": source_objects,
        "triangle_count": sum(group["triangle_count"] for group in groups),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {
        "available": True,
        "path": str(target),
        "groups": len(groups),
        "triangles": payload["triangle_count"],
        "zones": payload["zones"],
    }


def gather_with_ancestors(objects) -> list:
    gathered = set(objects)
    for obj in list(objects):
        current = obj.parent
        while current is not None:
            gathered.add(current)
            current = current.parent
    return list(gathered)


def select_objects(objects) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    bpy.context.view_layer.objects.active = meshes[0] if meshes else objects[0]


def export_editable_glb(objects, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    select_objects(gather_with_ancestors(objects))
    bpy.ops.export_scene.gltf(
        filepath=str(target),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
    )
    return target.is_file() and target.stat().st_size > 0


def export_fbx(objects, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    select_objects(gather_with_ancestors(objects))
    bpy.ops.export_scene.fbx(
        filepath=str(target),
        use_selection=True,
        axis_forward="-Y",
        axis_up="Z",
        apply_unit_scale=True,
        bake_space_transform=False,
        path_mode="RELATIVE",
    )
    return target.is_file() and target.stat().st_size > 0


def hierarchy_report(meshes) -> dict:
    categories: dict[str, int] = {}
    objects: list[dict] = []
    for obj in sorted(meshes, key=lambda item: item.name.casefold()):
        category = classify_part(obj.name)
        categories[category] = categories.get(category, 0) + 1
        pivot = obj.matrix_world.translation
        obj["wows_category"] = category
        objects.append({
            "name": obj.name,
            "category": category,
            "parent": obj.parent.name if obj.parent else None,
            "children": len(obj.children),
            "pivot": [round(float(pivot.x), 6), round(float(pivot.y), 6), round(float(pivot.z), 6)],
            "location": [round(float(v), 6) for v in obj.location],
            "rotation_euler": [round(float(v), 6) for v in obj.rotation_euler],
            "scale": [round(float(v), 6) for v in obj.scale],
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
        })
    return {
        "schema": "wows-toolbox-model/v1",
        "coordinate_space": "blender-z-up",
        "obj_axis_forward": "-Z",
        "obj_axis_up": "Y",
        "categories": categories,
        "objects": objects,
        "weapon_counts": {
            key: categories.get(key, 0)
            for key in ("main_gun", "secondary", "anti_air", "torpedo", "missile_launcher", "radar_sensor")
        },
    }


def export_obj(meshes, target: Path) -> bool:
    select_objects(meshes)
    if hasattr(bpy.ops.export_scene, "obj"):
        bpy.ops.export_scene.obj(
            filepath=str(target),
            use_selection=True,
            use_materials=True,
            use_normals=True,
            use_uvs=True,
            use_blen_objects=True,
            group_by_object=True,
            keep_vertex_order=True,
            path_mode="RELATIVE",
            axis_forward="-Z",
            axis_up="Y",
        )
    else:
        bpy.ops.wm.obj_export(
            filepath=str(target),
            export_selected_objects=True,
            export_materials=True,
            export_normals=True,
            export_uv=True,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
            path_mode="RELATIVE",
        )
    return target.is_file() and target.stat().st_size > 0


def main() -> None:
    args = arguments()
    formats = parse_formats(args.formats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    progress("obj", 8, "원본 GLB와 계층을 Blender로 불러오는 중")
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    model_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not model_meshes:
        raise RuntimeError("GLB에서 메시 오브젝트를 찾지 못했어요")
    armor_meshes = [obj for obj in model_meshes if armor_zone(obj)]
    meshes = [obj for obj in model_meshes if obj not in armor_meshes]
    if args.armor_input and args.armor_input.is_file():
        existing = set(bpy.context.scene.objects)
        progress("armor", 24, "장갑 메시와 구역을 분리하는 중")
        bpy.ops.import_scene.gltf(filepath=str(args.armor_input))
        imported = [
            obj for obj in bpy.context.scene.objects
            if obj not in existing and obj.type == "MESH"
        ]
        armor_meshes.extend(obj for obj in imported if armor_zone(obj))
    if not meshes:
        raise RuntimeError("GLB에서 일반 함선 메시를 찾지 못했어요")

    unique_object_names(meshes)
    hierarchy = hierarchy_report(meshes)
    model_report = args.model_report or args.output.with_suffix(".model.json")
    model_report.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")
    armor_path = args.output.with_suffix(".armor.json")
    armor_report = export_armor_sidecar(armor_meshes, armor_path)

    textures: list[str] = []
    resized_textures = 0
    linked_textures = 0
    if formats.intersection({"obj", "fbx"}):
        progress("texture", 40, "텍스처를 출력 프로필에 맞춰 정리하는 중")
        textures, resized_textures, linked_textures = export_images(
            bpy.data.images,
            args.output.parent / "textures",
            max(0, args.texture_max_size),
            args.texture_library,
        )
    else:
        resized_textures = resize_images(bpy.data.images, max(0, args.texture_max_size))
        progress("texture", 40, "GLB 내부에 텍스처를 포함해 외부 PNG 생성을 건너뛰는 중")
    editable_glb = args.editable_glb or args.output.with_suffix(".editable.glb")
    fbx_path = args.fbx or args.output.with_suffix(".fbx")
    armor_glb_path = args.armor_glb or args.output.with_suffix(".armor.glb")
    glb_ok = fbx_ok = armor_glb_ok = obj_ok = False

    if "glb" in formats:
        progress("glb", 55, "계층과 파트 원점이 보존된 편집용 GLB를 쓰는 중")
        glb_ok = export_editable_glb(meshes, editable_glb)
        if armor_meshes:
            armor_glb_ok = export_editable_glb(armor_meshes, armor_glb_path)
    if "fbx" in formats:
        progress("fbx", 68, "계층형 FBX를 쓰는 중")
        fbx_ok = export_fbx(meshes, fbx_path)
    if "obj" in formats:
        progress("obj", 78, f"편집 가능한 파트 {len(meshes)}개를 OBJ로 쓰는 중")
        obj_ok = export_obj(meshes, args.output)

    requested_ok = (
        ("obj" not in formats or obj_ok)
        and ("glb" not in formats or glb_ok)
        and ("fbx" not in formats or fbx_ok)
    )
    verification_warnings = []
    if hierarchy["categories"].get("hull", 0) <= 0:
        verification_warnings.append("선체로 분류된 파트가 없어요")
    weapon_total = sum(hierarchy["weapon_counts"].values())
    if weapon_total <= 0:
        verification_warnings.append("무장·센서 파트가 감지되지 않았어요")
    verification = {
        "passed": requested_ok and len(meshes) > 0 and hierarchy["categories"].get("hull", 0) > 0,
        "editable_parts": len(meshes),
        "hull_parts": hierarchy["categories"].get("hull", 0),
        "weapon_and_sensor_parts": weapon_total,
        "warnings": verification_warnings,
    }
    report = {
        "ok": verification["passed"],
        "formats": sorted(formats),
        "obj": str(args.output) if obj_ok else None,
        "mtl": str(args.output.with_suffix(".mtl")) if obj_ok and args.output.with_suffix(".mtl").is_file() else None,
        "editable_glb": str(editable_glb) if glb_ok else None,
        "fbx": str(fbx_path) if fbx_ok else None,
        "armor_glb": str(armor_glb_path) if armor_glb_ok else None,
        "model_report": str(model_report),
        "textures": textures,
        "texture_max_size": args.texture_max_size,
        "textures_resized": resized_textures,
        "textures_shared": linked_textures,
        "object_count": len(meshes),
        "object_names": [obj.name for obj in meshes],
        "categories": hierarchy["categories"],
        "weapon_counts": hierarchy["weapon_counts"],
        "verification": verification,
        "blend_created": False,
        "armor": armor_report,
        "axis_forward": "-Z",
        "axis_up": "Y",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress("complete", 100, f"편집 파트 {len(meshes)}개 · {','.join(sorted(formats)).upper()} 저장 완료")


if __name__ == "__main__":
    main()
