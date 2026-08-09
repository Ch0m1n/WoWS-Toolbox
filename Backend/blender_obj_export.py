from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def arguments() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--armor-input", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv)


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


def export_images(images, texture_dir: Path) -> list[str]:
    texture_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    used: set[str] = set()
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
            image.filepath = str(target)
            written.append(str(target))
        except Exception as exc:
            print(f"[WARN] 텍스처 저장 건너뜀: {image.name}: {exc}", flush=True)
    return written


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
    rgb = tuple(max(0.0, min(1.0, float(value))) for value in color[:3])
    candidates = (rgb, tuple(value ** (1.0 / 2.2) for value in rgb))
    best = (float("inf"), 0)
    for candidate in candidates:
        for bucket in ARMOR_BUCKETS:
            target = tuple(value / 255.0 for value in bucket["rgb"])
            distance = sum((candidate[index] - target[index]) ** 2 for index in range(3))
            best = min(best, (distance, int(bucket["id"])))
    return best[1]


def triangle_color(mesh, triangle, color_attribute) -> tuple[float, float, float, float]:
    if color_attribute is not None:
        try:
            if color_attribute.domain == "CORNER":
                return tuple(color_attribute.data[triangle.loops[0]].color)
            return tuple(color_attribute.data[triangle.vertices[0]].color)
        except (AttributeError, IndexError, TypeError):
            pass
    try:
        material = mesh.materials[triangle.material_index]
        return tuple(material.diffuse_color)
    except (AttributeError, IndexError, TypeError):
        return (110 / 255, 209 / 255, 176 / 255, 1.0)


def export_armor_sidecar(objects, target: Path) -> dict:
    grouped: dict[tuple[str, int], list[float]] = {}
    source_objects: list[str] = []
    for obj in objects:
        zone = armor_zone(obj)
        if not zone:
            continue
        source_objects.append(obj.name)
        mesh = obj.data
        mesh.calc_loop_triangles()
        color_attribute = None
        if getattr(mesh, "color_attributes", None):
            color_attribute = mesh.color_attributes.active_color
            if color_attribute is None and len(mesh.color_attributes):
                color_attribute = mesh.color_attributes[0]
        matrix = obj.matrix_world
        for triangle in mesh.loop_triangles:
            bucket = closest_armor_bucket(triangle_color(mesh, triangle, color_attribute))
            positions = grouped.setdefault((zone, bucket), [])
            for vertex_index in triangle.vertices:
                point: Vector = matrix @ mesh.vertices[vertex_index].co
                positions.extend((round(point.x, 6), round(point.y, 6), round(point.z, 6)))
    if not grouped:
        return {"available": False, "path": None, "groups": 0, "triangles": 0}
    groups = [
        {
            "zone": zone,
            "bucket": bucket,
            "triangle_count": len(positions) // 9,
            "positions": positions,
        }
        for (zone, bucket), positions in sorted(grouped.items())
    ]
    payload = {
        "schema": "wows-toolbox-armor-viewer/v1",
        "coordinate_system": "Blender Z-up; same source space as exported OBJ",
        "buckets": [
            {key: value for key, value in bucket.items() if key != "rgb"}
            for bucket in ARMOR_BUCKETS
        ],
        "zones": sorted({group["zone"] for group in groups}),
        "groups": groups,
        "source_objects": source_objects,
        "triangle_count": sum(group["triangle_count"] for group in groups),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {
        "available": True,
        "path": str(target),
        "groups": len(groups),
        "triangles": payload["triangle_count"],
        "zones": payload["zones"],
    }

def main() -> None:
    args = arguments()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("[PROGRESS] {\"stage\":\"obj\",\"percent\":12,\"message\":\"GLB를 Blender로 불러오는 중\"}", flush=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input))

    model_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not model_meshes:
        raise RuntimeError("GLB에서 메시 오브젝트를 찾지 못했어요")
    armor_meshes = [obj for obj in model_meshes if armor_zone(obj)]
    meshes = [obj for obj in model_meshes if obj not in armor_meshes]
    if args.armor_input and args.armor_input.is_file():
        existing = set(bpy.context.scene.objects)
        print("[PROGRESS] {\"stage\":\"armor\",\"percent\":42,\"message\":\"장갑 메시를 분리하는 중\"}", flush=True)
        bpy.ops.import_scene.gltf(filepath=str(args.armor_input))
        imported = [
            obj
            for obj in bpy.context.scene.objects
            if obj not in existing and obj.type == "MESH"
        ]
        armor_meshes.extend(obj for obj in imported if armor_zone(obj))
    if not meshes:
        raise RuntimeError("GLB에서 일반 함선 메시를 찾지 못했어요")
    armor_path = args.output.with_suffix(".armor.json")
    armor_report = export_armor_sidecar(armor_meshes, armor_path)
    unique_object_names(meshes)

    texture_dir = args.output.parent / "textures"
    textures = export_images(bpy.data.images, texture_dir)
    print(
        f"[PROGRESS] {{\"stage\":\"obj\",\"percent\":64,\"message\":\"편집 가능한 파트 {len(meshes)}개를 OBJ로 쓰는 중\"}}",
        flush=True,
    )

    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]

    if hasattr(bpy.ops.export_scene, "obj"):
        bpy.ops.export_scene.obj(
            filepath=str(args.output),
            use_selection=True,
            use_materials=True,
            use_normals=True,
            use_uvs=True,
            use_blen_objects=True,
            group_by_object=True,
            keep_vertex_order=True,
            path_mode="RELATIVE",
            axis_forward="-Y",
            axis_up="Z",
        )
    else:
        bpy.ops.wm.obj_export(
            filepath=str(args.output),
            export_selected_objects=True,
            export_materials=True,
            export_normals=True,
            export_uv=True,
            forward_axis="NEGATIVE_Y",
            up_axis="Z",
            path_mode="RELATIVE",
        )

    object_names = [obj.name for obj in meshes]
    report = {
        "ok": args.output.is_file() and args.output.stat().st_size > 0,
        "obj": str(args.output),
        "mtl": str(args.output.with_suffix(".mtl")),
        "textures": textures,
        "object_count": len(meshes),
        "object_names": object_names,
        "blend_created": False,
        "armor": armor_report,
        "axis_forward": "-Y",
        "axis_up": "Z",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[PROGRESS] {{\"stage\":\"obj\",\"percent\":100,\"message\":\"OBJ 파트 {len(meshes)}개 저장 완료\"}}",
        flush=True,
    )


if __name__ == "__main__":
    main()
