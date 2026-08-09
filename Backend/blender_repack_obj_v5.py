from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import blender_export_v5 as export_core  # noqa: E402


def arguments() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-obj", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--formats", default="obj,glb")
    parser.add_argument("--texture-max-size", type=int, default=0)
    parser.add_argument("--texture-library", type=Path)
    return parser.parse_args(argv)


def import_obj(path: Path) -> None:
    if hasattr(bpy.ops.import_scene, "obj"):
        bpy.ops.import_scene.obj(
            filepath=str(path),
            use_split_objects=True,
            use_split_groups=True,
            use_image_search=True,
            axis_forward="-Z",
            axis_up="Y",
        )
    else:
        bpy.ops.wm.obj_import(
            filepath=str(path),
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )


def main() -> None:
    args = arguments()
    formats = export_core.parse_formats(args.formats)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    export_core.progress("repack", 84, "Legends OBJ 파트와 재질을 다시 읽는 중")
    import_obj(args.input_obj)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("Legends OBJ에서 메시 오브젝트를 찾지 못했어요")

    export_core.unique_object_names(meshes)
    hierarchy = export_core.hierarchy_report(meshes)
    model_report = args.input_obj.with_suffix(".model.json")
    model_report.write_text(
        json.dumps(hierarchy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    textures: list[str] = []
    resized = 0
    shared = 0
    if formats.intersection({"obj", "fbx"}):
        textures, resized, shared = export_core.export_images(
            bpy.data.images,
            args.input_obj.parent / "textures",
            max(0, args.texture_max_size),
            args.texture_library,
        )
    else:
        resized = export_core.resize_images(
            bpy.data.images,
            max(0, args.texture_max_size),
        )
        export_core.progress(
            "texture",
            88,
            "GLB 내부에 텍스처를 포함해 외부 PNG 생성을 건너뛰는 중",
        )
    editable_glb = args.input_obj.with_suffix(".editable.glb")
    fbx_path = args.input_obj.with_suffix(".fbx")
    glb_ok = fbx_ok = False
    if "glb" in formats:
        export_core.progress("glb", 90, "Legends 파트별 편집형 GLB를 쓰는 중")
        glb_ok = export_core.export_editable_glb(meshes, editable_glb)
    if "fbx" in formats:
        export_core.progress("fbx", 94, "Legends 파트별 FBX를 쓰는 중")
        fbx_ok = export_core.export_fbx(meshes, fbx_path)

    obj_ok = args.input_obj.is_file() and args.input_obj.stat().st_size > 0
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
        "obj": str(args.input_obj) if obj_ok and "obj" in formats else None,
        "mtl": str(args.input_obj.with_suffix(".mtl"))
        if obj_ok and "obj" in formats and args.input_obj.with_suffix(".mtl").is_file()
        else None,
        "editable_glb": str(editable_glb) if glb_ok else None,
        "fbx": str(fbx_path) if fbx_ok else None,
        "armor_glb": None,
        "model_report": str(model_report),
        "textures": textures,
        "texture_max_size": args.texture_max_size,
        "textures_resized": resized,
        "textures_shared": shared,
        "object_count": len(meshes),
        "object_names": [obj.name for obj in meshes],
        "categories": hierarchy["categories"],
        "weapon_counts": hierarchy["weapon_counts"],
        "verification": verification,
        "blend_created": False,
        "armor": {
            "available": False,
            "reason": "Legends 설치본에는 PC/Korabli와 같은 장갑 GLB 계약이 확인되지 않았어요",
        },
        "pivot_quality": "assembled-object-origin",
        "axis_forward": "-Z",
        "axis_up": "Y",
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    export_core.progress("complete", 100, f"Legends 편집 파트 {len(meshes)}개 출력 완료")


if __name__ == "__main__":
    main()
