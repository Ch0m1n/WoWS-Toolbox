#!/usr/bin/env python3
"""Blender 3.5 background worker for convert.py."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import addon_utils
import bpy
from mathutils import Vector


def arguments() -> list[str]:
    if "--" not in sys.argv:
        raise SystemExit("expected blender arguments after --")
    return sys.argv[sys.argv.index("--") + 1 :]


def enable_addon(name: str) -> None:
    loaded, enabled = addon_utils.check(name)
    if not loaded or not enabled:
        addon_utils.enable(name, default_set=False, persistent=False)


def import_obj(path: Path) -> None:
    enable_addon("io_scene_obj")
    bpy.ops.import_scene.obj(filepath=str(path), axis_forward="-Y", axis_up="Z")


def set_colorspace(image, value: str) -> None:
    try:
        image.colorspace_settings.name = value
    except (TypeError, AttributeError):
        pass


def load_map(path: str | None, colorspace: str):
    if not path:
        return None
    image = bpy.data.images.load(path, check_existing=True)
    set_colorspace(image, colorspace)
    return image


def image_node(nodes, name: str, label: str, image, location: tuple[int, int]):
    node = nodes.new("ShaderNodeTexImage")
    node.name = name
    node.label = label
    node.image = image
    node.location = location
    return node


def is_grid_alpha_fx(fx_name: str) -> bool:
    return fx_name in {"grid_alpha.fx", "grid_alpha_skinned.fx"}


def finite_float(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def build_material(spec: dict):
    material = bpy.data.materials.get(spec["name"])
    if material is None:
        material = bpy.data.materials.new(spec["name"])
    material.use_nodes = True
    material["wows_mfm_path"] = spec["mfm_path"]
    material["wows_fx_path"] = spec.get("fx_path") or ""
    material["wows_properties_json"] = json.dumps(
        spec.get("properties", []), sort_keys=True
    )
    material["wows_mg_semantics"] = spec["mg_semantics"]
    material["wows_mg_connected"] = False

    property_values = {
        item.get("name"): item.get("value")
        for item in spec.get("properties", [])
        if isinstance(item, dict)
    }
    fx_name = Path(spec.get("fx_path") or "").name.casefold()
    double_sided = bool(property_values.get("doubleSided", False))
    material.use_backface_culling = not double_sided
    material["wows_double_sided"] = double_sided
    alpha_mode = "OPAQUE"
    alpha_threshold = None
    if fx_name == "lightonly_alpha_flat.fx":
        alpha_mode = "BLEND"
    elif is_grid_alpha_fx(fx_name):
        alpha_mode = "CLIP"
        alpha_reference = finite_float(property_values.get("alphaReference", 50), 50.0)
        alpha_threshold = max(0.0, min(255.0, alpha_reference)) / 255.0
    elif fx_name == "wire_material.fx":
        alpha_mode = "HASHED"

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (720, 80)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled BSDF"
    principled.location = (430, 80)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    maps = spec["maps"]
    albedo_image = load_map(maps.get("a"), "sRGB")
    ao_image = load_map(maps.get("ao"), "Non-Color")
    normal_image = load_map(maps.get("n"), "Non-Color")
    mg_image = load_map(maps.get("mg"), "Non-Color")
    detail_image = load_map(maps.get("detail"), "Non-Color")

    albedo = None
    if albedo_image is not None:
        albedo = image_node(
            nodes, f"{spec['name']}_Albedo", "_a / sRGB", albedo_image, (-650, 250)
        )
    if ao_image is not None:
        ao = image_node(
            nodes, f"{spec['name']}_AO", "_ao / Non-Color", ao_image, (-650, 0)
        )
        if albedo is not None:
            multiply = nodes.new("ShaderNodeMixRGB")
            multiply.name = f"{spec['name']}_Albedo_x_AO"
            multiply.label = "Base Color × AO"
            multiply.blend_type = "MULTIPLY"
            multiply.inputs["Fac"].default_value = 1.0
            multiply.location = (-150, 200)
            links.new(albedo.outputs["Color"], multiply.inputs[1])
            links.new(ao.outputs["Color"], multiply.inputs[2])
            links.new(multiply.outputs["Color"], principled.inputs["Base Color"])
        else:
            links.new(ao.outputs["Color"], principled.inputs["Base Color"])
    elif albedo is not None:
        links.new(albedo.outputs["Color"], principled.inputs["Base Color"])

    alpha_connected = False
    if albedo is not None and alpha_mode != "OPAQUE":
        links.new(albedo.outputs["Alpha"], principled.inputs["Alpha"])
        material.blend_method = alpha_mode
        if alpha_threshold is not None:
            material.alpha_threshold = alpha_threshold
        if alpha_mode == "BLEND":
            material.use_screen_refraction = True
        alpha_connected = True
    material["wows_alpha_mode"] = alpha_mode
    material["wows_alpha_from_albedo"] = alpha_connected
    material["wows_alpha_threshold"] = (
        alpha_threshold if alpha_threshold is not None else -1.0
    )

    if normal_image is not None:
        normal_texture = image_node(
            nodes,
            f"{spec['name']}_NormalTexture",
            "_n / Non-Color",
            normal_image,
            (-650, -250),
        )
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = f"{spec['name']}_NormalMap"
        normal_map.location = (-150, -220)
        links.new(normal_texture.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    if mg_image is not None:
        mg = image_node(
            nodes,
            f"{spec['name']}_MG_Unresolved",
            "_mg / semantics unresolved; intentionally not shader-connected",
            mg_image,
            (-650, -500),
        )
        separate = nodes.new("ShaderNodeSeparateRGB")
        separate.name = f"{spec['name']}_MG_Channels_Unresolved"
        separate.label = "MG channels (unresolved)"
        separate.location = (-320, -500)
        links.new(mg.outputs["Color"], separate.inputs["Image"])

    if detail_image is not None:
        detail = image_node(
            nodes,
            f"{spec['name']}_Detail_Unresolved",
            "detailMap / retained; influence contract not reconstructed",
            detail_image,
            (-650, -720),
        )
        material["wows_detail_connected"] = False
        material["wows_detail_image"] = detail.image.name

    return material


def material_report(material, spec: dict) -> dict:
    return {
        "name": material.name,
        "mfm_path": spec["mfm_path"],
        "fx_path": spec.get("fx_path"),
        "source_property_names": [
            item.get("name") for item in spec.get("properties", [])
        ],
        "double_sided": bool(material.get("wows_double_sided", False)),
        "backface_culling": bool(material.use_backface_culling),
        "alpha_mode": material.get("wows_alpha_mode", "OPAQUE"),
        "alpha_from_albedo": bool(material.get("wows_alpha_from_albedo", False)),
        "alpha_threshold": (
            None
            if float(material.get("wows_alpha_threshold", -1.0)) < 0.0
            else float(material.get("wows_alpha_threshold"))
        ),
        "mg_connected": bool(material.get("wows_mg_connected", False)),
        "detail_connected": bool(material.get("wows_detail_connected", False)),
        "detail_retained": bool(material.get("wows_detail_image", "")),
    }


def process_input(input_path: Path) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    import_obj(Path(payload["obj"]))
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("OBJ import produced no mesh objects")

    object_specs = {item["object_name"]: item for item in payload["objects"]}
    imported_names = {obj.name for obj in mesh_objects}
    expected_names = set(object_specs)
    missing_objects = sorted(expected_names - imported_names)
    unexpected_objects = sorted(imported_names - expected_names)
    if missing_objects or unexpected_objects:
        raise RuntimeError(
            f"OBJ render-set object mismatch: missing={missing_objects}, "
            f"unexpected={unexpected_objects}"
        )

    materials_by_name = {
        spec["name"]: build_material(spec) for spec in payload["materials"]
    }
    material_reports = [
        material_report(materials_by_name[spec["name"]], spec)
        for spec in payload["materials"]
    ]
    material_policy_errors = []
    for report in material_reports:
        fx_name = Path(report["fx_path"] or "").name.casefold()
        if is_grid_alpha_fx(fx_name):
            expected_mode = "CLIP"
        else:
            expected_mode = {
                "lightonly_alpha_flat.fx": "BLEND",
                "wire_material.fx": "HASHED",
            }.get(fx_name, "OPAQUE")
        if report["alpha_mode"] != expected_mode:
            material_policy_errors.append(
                f"{report['name']}: alpha {report['alpha_mode']} != {expected_mode}"
            )
        if expected_mode != "OPAQUE" and not report["alpha_from_albedo"]:
            material_policy_errors.append(
                f"{report['name']}: alpha source is not albedo Alpha"
            )
        if is_grid_alpha_fx(fx_name) and abs(
            float(report["alpha_threshold"] or 0.0) - 50.0 / 255.0
        ) > 1e-7:
            material_policy_errors.append(
                f"{report['name']}: grid alpha threshold mismatch"
            )
        if report["double_sided"] == report["backface_culling"]:
            material_policy_errors.append(
                f"{report['name']}: double-sided/culling policy mismatch"
            )
    if material_policy_errors:
        raise RuntimeError("material policy failed: " + "; ".join(material_policy_errors))

    object_reports = []
    for obj in sorted(mesh_objects, key=lambda item: item.name):
        spec = object_specs[obj.name]
        material = materials_by_name[spec["material_name"]]
        obj.data.materials.clear()
        obj.data.materials.append(material)
        obj["wows_geometry"] = spec["geometry"]
        obj["wows_vertices_section"] = spec["vertices_section"]
        obj["wows_indices_section"] = spec["indices_section"]
        obj["wows_render_group"] = spec["group_name"]
        obj["wows_material_mfm_path"] = spec["material_mfm_path"]
        changed = bool(obj.data.validate(verbose=False, clean_customdata=False))
        obj.data.update()
        object_reports.append(
            {
                "name": obj.name,
                "vertices_section": spec["vertices_section"],
                "indices_section": spec["indices_section"],
                "render_group": spec["group_name"],
                "material": material.name,
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
                "uv_layers": [layer.name for layer in obj.data.uv_layers],
                "mesh_validate_changed": changed,
            }
        )

    corners = [
        obj.matrix_world @ Vector(corner) for obj in mesh_objects for corner in obj.bound_box
    ]
    bounds_min = [min(point[axis] for point in corners) for axis in range(3)]
    bounds_max = [max(point[axis] for point in corners) for axis in range(3)]

    save_blend = bool(payload.get("save_blend", True))
    blend_path = Path(payload["blend"]) if save_blend else None
    if blend_path is not None:
        bpy.ops.file.pack_all()
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    enable_addon("io_scene_gltf2")
    glb_path = Path(payload["glb"])
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(glb_path),
        export_format="GLB",
        export_materials="EXPORT",
        export_texcoords=True,
        export_normals=True,
    )

    pre = payload["pre_blender"]
    report = {
        "status": "OK",
        "blender_version": bpy.app.version_string,
        "render_set_count": pre["render_set_count"],
        "matched_render_sets": pre["matched_render_sets"],
        "missing_render_sets": 0,
        "unexpected_objects": [],
        "missing_maps": pre["missing_maps"],
        "joins": pre["joins"],
        "objects": object_reports,
        "materials": payload["materials"],
        "material_policy": material_reports,
        "totals": {
            "objects": len(mesh_objects),
            "vertices": sum(len(obj.data.vertices) for obj in mesh_objects),
            "polygons": sum(len(obj.data.polygons) for obj in mesh_objects),
        },
        "scene_bounds": {
            "minimum": [float(value) for value in bounds_min],
            "maximum": [float(value) for value in bounds_max],
            "dimensions": [
                float(bounds_max[index] - bounds_min[index]) for index in range(3)
            ],
        },
        "outputs": {
            "obj": payload["obj"],
            "blend": str(blend_path) if blend_path is not None else None,
            "blend_bytes": blend_path.stat().st_size if blend_path is not None else 0,
            "glb": str(glb_path),
            "glb_bytes": glb_path.stat().st_size,
        },
        "acceptance": {
            "render_set_to_geometry_part_missing": 0,
            "expected_objects": len(expected_names),
            "imported_objects": len(imported_names),
            "material_policy_errors": material_policy_errors,
            "material_policy_passed": not material_policy_errors,
            "passed": True,
        },
    }
    validation_path = Path(payload["validation"])
    validation_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> int:
    inputs = [Path(value).resolve() for value in arguments()]
    if not inputs:
        raise SystemExit(
            "usage: blender_pbr.py -- blender-input.json [blender-input.json ...]"
        )
    failures: list[str] = []
    for index, input_path in enumerate(inputs, start=1):
        try:
            process_input(input_path)
        except Exception as exc:
            failures.append(f"{input_path}: {exc}")
            print(f"[BLENDER_BATCH_ERROR] {input_path}: {exc}", flush=True)
        print(
            f"[BLENDER_BATCH] {index}/{len(inputs)} {input_path}",
            flush=True,
        )
    if failures:
        raise RuntimeError("; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
