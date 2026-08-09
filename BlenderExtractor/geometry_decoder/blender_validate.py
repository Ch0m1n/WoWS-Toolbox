"""Blender 3.5 background-mode OBJ validation and optional exports.

Usage:
  blender.exe --background --factory-startup --python blender_validate.py -- \
      input.obj report.json [output.glb] [output.blend] \
      [base_diffuse.dds] [deckhouse_diffuse.dds]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import addon_utils
import bpy
from mathutils import Vector


def script_arguments() -> list[str]:
    if "--" not in sys.argv:
        raise SystemExit("expected arguments after --")
    return sys.argv[sys.argv.index("--") + 1 :]


def enable_addon(name: str) -> None:
    loaded, enabled = addon_utils.check(name)
    if not loaded or not enabled:
        addon_utils.enable(name, default_set=False, persistent=False)


def import_obj(path: Path) -> None:
    # The decoded OBJ is already Z-up. Blender 3.5's OBJ default assumes Y-up,
    # so both axes must be explicit or the ship is imported standing upright.
    try:
        enable_addon("io_scene_obj")
        bpy.ops.import_scene.obj(
            filepath=str(path), axis_forward="-Y", axis_up="Z"
        )
    except (AttributeError, RuntimeError):
        bpy.ops.wm.obj_import(
            filepath=str(path), forward_axis="NEGATIVE_Y", up_axis="Z"
        )


def export_glb(path: Path) -> None:
    enable_addon("io_scene_gltf2")
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")


def save_blend(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)


def make_diffuse_material(name: str, path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"diffuse texture not found: {path}")
    image = bpy.data.images.load(str(path), check_existing=True)
    try:
        image.colorspace_settings.name = "sRGB"
    except TypeError:
        pass

    material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = nodes.get("Principled BSDF")
    if principled is None:
        raise RuntimeError("Principled BSDF node was not created")
    texture = nodes.new("ShaderNodeTexImage")
    texture.name = f"{name}_Diffuse"
    texture.label = path.name
    texture.image = image
    links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    return material, image


def assign_diffuse_materials(
    mesh_objects: list,
    base_diffuse: Path,
    deckhouse_diffuse: Path | None,
) -> dict[str, object]:
    base_material, base_image = make_diffuse_material(
        "Legends_Hull_Diffuse", base_diffuse
    )
    if deckhouse_diffuse is not None:
        deck_material, deck_image = make_diffuse_material(
            "Legends_DeckHouse_Diffuse", deckhouse_diffuse
        )
    else:
        deck_material, deck_image = base_material, base_image

    assignments: list[dict[str, str]] = []
    counts = {"base": 0, "deckhouse": 0}
    for obj in mesh_objects:
        use_deck = "deckhouse" in obj.name.casefold()
        material = deck_material if use_deck else base_material
        obj.data.materials.clear()
        obj.data.materials.append(material)
        bucket = "deckhouse" if use_deck else "base"
        counts[bucket] += 1
        assignments.append(
            {"object": obj.name, "material": material.name, "rule": bucket}
        )

    pack_result = bpy.ops.file.pack_all()
    return {
        "base_diffuse": str(base_diffuse),
        "deckhouse_diffuse": (
            str(deckhouse_diffuse) if deckhouse_diffuse is not None else None
        ),
        "base_image": base_image.name,
        "deckhouse_image": deck_image.name,
        "assignment_rule": "object name contains DeckHouse => deckhouse; otherwise base",
        "assignment_counts": counts,
        "assignments": assignments,
        "pack_all_result": sorted(pack_result),
    }


def main() -> int:
    args = script_arguments()
    if len(args) < 2 or len(args) > 6:
        raise SystemExit(
            "usage: input.obj report.json [output.glb] [output.blend] "
            "[base_diffuse.dds] [deckhouse_diffuse.dds]"
        )

    input_obj = Path(args[0]).resolve()
    report_json = Path(args[1]).resolve()
    output_glb = Path(args[2]).resolve() if len(args) >= 3 else None
    output_blend = Path(args[3]).resolve() if len(args) >= 4 else None
    base_diffuse = Path(args[4]).resolve() if len(args) >= 5 else None
    deckhouse_diffuse = Path(args[5]).resolve() if len(args) >= 6 else None

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    import_obj(input_obj)

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("OBJ import produced no mesh objects")

    report = {
        "blender_version": bpy.app.version_string,
        "input_obj": str(input_obj),
        "mesh_objects": [],
        "totals": {
            "objects": len(mesh_objects),
            "vertices": 0,
            "edges": 0,
            "polygons": 0,
            "loops": 0,
            "uv_layers": 0,
        },
    }

    for obj in mesh_objects:
        mesh = obj.data
        mesh_changed = bool(mesh.validate(verbose=False, clean_customdata=False))
        mesh.update()
        item = {
            "name": obj.name,
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "polygons": len(mesh.polygons),
            "loops": len(mesh.loops),
            "uv_layers": [layer.name for layer in mesh.uv_layers],
            "has_custom_normals": bool(mesh.has_custom_normals),
            "mesh_validate_changed": mesh_changed,
            "dimensions": [float(value) for value in obj.dimensions],
        }
        report["mesh_objects"].append(item)
        report["totals"]["vertices"] += item["vertices"]
        report["totals"]["edges"] += item["edges"]
        report["totals"]["polygons"] += item["polygons"]
        report["totals"]["loops"] += item["loops"]
        report["totals"]["uv_layers"] += len(item["uv_layers"])

    world_corners = [
        obj.matrix_world @ Vector(corner)
        for obj in mesh_objects
        for corner in obj.bound_box
    ]
    bounds_min = [min(point[axis] for point in world_corners) for axis in range(3)]
    bounds_max = [max(point[axis] for point in world_corners) for axis in range(3)]
    report["import_axes"] = {"forward": "-Y", "up": "Z"}
    report["scene_bounds"] = {
        "minimum": [float(value) for value in bounds_min],
        "maximum": [float(value) for value in bounds_max],
        "dimensions": [
            float(bounds_max[axis] - bounds_min[axis]) for axis in range(3)
        ],
    }

    if base_diffuse is not None:
        report["materials"] = assign_diffuse_materials(
            mesh_objects, base_diffuse, deckhouse_diffuse
        )

    if output_glb is not None:
        output_glb.parent.mkdir(parents=True, exist_ok=True)
        export_glb(output_glb)
        report["output_glb"] = str(output_glb)
        report["output_glb_bytes"] = output_glb.stat().st_size

    if output_blend is not None:
        save_blend(output_blend)
        report["output_blend"] = str(output_blend)
        report["output_blend_bytes"] = output_blend.stat().st_size

    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
