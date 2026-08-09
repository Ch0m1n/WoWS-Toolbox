#!/usr/bin/env python3
"""Blender-side synthetic end-to-end test for assemble_scene.py."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
sys.dont_write_bytecode = True


import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assemble_scene  # noqa: E402


def _args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _export_cube(path: Path, name: str, scale: tuple[float, float, float]) -> None:
    _clear()
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(f"{name}_Material")
    material.diffuse_color = (0.25, 0.55, 0.85, 1.0)
    material.use_nodes = True
    image = bpy.data.images.new(f"{name}_PackedTexture", width=2, height=2)
    image.pixels = [
        0.25, 0.55, 0.85, 1.0,
        0.35, 0.65, 0.95, 1.0,
        0.45, 0.75, 0.55, 1.0,
        0.55, 0.85, 0.65, 1.0,
    ]
    texture_path = path.with_name(f"{name}_source_texture.png")
    image.filepath_raw = str(texture_path)
    image.file_format = "PNG"
    image.save()
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    principled = next(
        node
        for node in material.node_tree.nodes
        if node.type == "BSDF_PRINCIPLED"
    )
    material.node_tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    obj.data.materials.append(material)
    result = bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=False,
        export_yup=True,
    )
    assert "FINISHED" in result


def _column_major(matrix: Matrix) -> list[float]:
    return [float(matrix[row][column]) for column in range(4) for row in range(4)]


def main() -> int:
    output_dir = Path(_args()[0] if _args() else HERE / "_self_test_output")
    output_dir = Path(os.path.abspath(str(output_dir)))
    output_dir.mkdir(parents=True, exist_ok=True)

    hull_glb = output_dir / "synthetic_hull.glb"
    mount_glb = output_dir / "synthetic_mount.glb"
    _export_cube(hull_glb, "SyntheticHull", (1.0, 2.0, 0.5))
    _export_cube(mount_glb, "SyntheticMount", (0.25, 0.5, 0.125))

    translation = Matrix.Translation((2.0, 3.0, 4.0))
    rotation_y_90 = Matrix.Rotation(math.radians(90.0), 4, "Y")
    combined = Matrix.Translation((-2.0, 1.5, -1.0)) @ rotation_y_90
    mirrored = (
        Matrix.Translation((0.5, -1.0, 2.0))
        @ Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
    )
    identity = Matrix.Identity(4)

    plan = {
        "hull_glbs": [hull_glb.name],
        "mounts": [
            {
                "hardpoint": "HP_TRANSLATION",
                "category": "Guns",
                "model_glb": mount_glb.name,
                "matrix": _column_major(translation),
            },
            {
                "hardpoint": "HP_ROTATION",
                "category": "Guns",
                "model_glb": mount_glb.name,
                "matrix": _column_major(combined),
            },
            {
                "hardpoint": "HP_IDENTITY",
                "category": "Radar",
                "model_glb": mount_glb.name,
                "matrix": _column_major(identity),
            },
            {
                "hardpoint": "HP_MIRRORED",
                "category": "Guns",
                "model_glb": mount_glb.name,
                "matrix": _column_major(mirrored),
            },
            {
                "hardpoint": "HP_HIDDEN_RUNTIME",
                "category": "RuntimeOverlay",
                "model_glb": mount_glb.name,
                "matrix": _column_major(identity),
                "visible": False,
            },
        ],
        "output_blend": "synthetic_assembled.blend",
        "output_glb": "synthetic_assembled.glb",
        "output_combined_obj": "Ticonderoga1990_Combined.obj",
        "validation_json": "synthetic_validation.json",
    }
    plan_path = output_dir / "assembly_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    validation = assemble_scene.assemble(plan_path)
    assert validation["ok"], validation
    assert validation["mounts"]["requested"] == 5
    assert validation["mounts"]["actual_instances"] == 5
    assert validation["mounts"]["unique_models"] == 1
    assert validation["mounts"]["default_visible"] == 4
    assert validation["mounts"]["default_hidden"] == 1
    assert validation["hulls"]["imported"] == 1
    assert validation["scene"]["bounds"] is not None
    assert validation["scene"]["evaluated_mesh_occurrences"] == 5
    combined_obj = validation["combined_obj"]
    assert combined_obj["ok"], combined_obj
    assert combined_obj["source_visible_mesh_objects"] == 5
    assert combined_obj["unified_mesh_objects"] == 1
    assert combined_obj["vertices"] > 0 and combined_obj["faces"] > 0
    assert combined_obj["uv_records"] > 0 and combined_obj["normal_records"] > 0
    assert combined_obj["material_slots"] > 0 and combined_obj["usemtl"]
    assert len(combined_obj["object_names"]) == 1
    assert combined_obj["object_names"][0].startswith("Ticonderoga1990_Combined")
    assert combined_obj["mtllib"] == "Ticonderoga1990_Combined.mtl"
    assert combined_obj["map_references"]
    assert all(
        item.startswith("textures/")
        and item.endswith(".png")
        and "\\" not in item
        for item in combined_obj["map_references"]
    )
    assert combined_obj["missing_map_references"] == []
    assert combined_obj["absolute_map_references"] == []
    assert combined_obj["hidden_names_present_in_obj_or_mtl"] == []
    assert combined_obj["bounds_max_delta"] <= 1e-4
    assert combined_obj["obj_bounds_max_delta"] <= 1e-4
    assert combined_obj["clean_reimport"]["mesh_objects"] == 1
    assert combined_obj["clean_reimport"]["uv_layers"] > 0
    assert combined_obj["clean_reimport"]["materials"] > 0
    assert combined_obj["original_blend_preservation"]["ok"] is True

    instances = sorted(
        (
            obj
            for obj in bpy.data.objects
            if obj.instance_type == "COLLECTION" and obj.instance_collection
        ),
        key=lambda obj: int(obj["plan_index"]),
    )
    assert len(instances) == 5
    assert len({obj.instance_collection.as_pointer() for obj in instances}) == 1
    assert instances[4]["visible"] is False
    assert instances[4].hide_viewport is True
    assert instances[4].hide_render is True

    # ModelUber node translation [2,3,4] becomes [-2,-4,3] in Blender.
    expected_translation = Vector((-2.0, -4.0, 3.0))
    assert (instances[0].matrix_world.translation - expected_translation).length < 1e-6

    # A +Y game-node rotation maps through the left-handed-to-Blender basis.
    # +X rotates to -Z in node space, which becomes -Y in Blender.
    converted_x = instances[1].matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))
    assert (converted_x - Vector((0.0, -1.0, 0.0))).length < 1e-6

    coordinate_translation = list(instances[0].matrix_world.translation)
    converted_x_values = list(converted_x)
    shared_template_collection = instances[0].instance_collection.name

    blend_path = output_dir / "synthetic_assembled.blend"
    glb_path = output_dir / "synthetic_assembled.glb"
    validation_path = output_dir / "synthetic_validation.json"
    obj_path = output_dir / "Ticonderoga1990_Combined.obj"
    mtl_path = output_dir / "Ticonderoga1990_Combined.mtl"
    texture_dir = output_dir / "textures"
    assert blend_path.is_file() and blend_path.stat().st_size > 0
    assert glb_path.is_file() and glb_path.stat().st_size > 0
    assert validation_path.is_file() and validation_path.stat().st_size > 0
    assert obj_path.is_file() and obj_path.stat().st_size > 0
    assert mtl_path.is_file() and mtl_path.stat().st_size > 0
    assert texture_dir.is_dir() and list(texture_dir.glob("*.png"))

    # Hidden runtime overlays must survive in the editable .blend.
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    saved_instances = sorted(
        (
            obj
            for obj in bpy.data.objects
            if obj.instance_type == "COLLECTION" and "plan_index" in obj
        ),
        key=lambda obj: int(obj["plan_index"]),
    )
    assert len(saved_instances) == 5
    assert saved_instances[4]["visible"] is False
    assert saved_instances[4].hide_viewport is True
    assert saved_instances[4].hide_render is True

    # use_visible/use_renderable must exclude that hidden overlay from GLB.
    _clear()
    import_result = bpy.ops.import_scene.gltf(filepath=str(glb_path))
    assert "FINISHED" in import_result
    exported_mesh_objects = [
        obj for obj in bpy.context.scene.objects if obj.type == "MESH"
    ]
    assert len(exported_mesh_objects) == 5

    editable_obj_path = output_dir / "synthetic_editable.obj"
    editable_validation = assemble_scene.assemble(
        plan_path,
        output_obj=editable_obj_path,
        validation_path=output_dir / "synthetic_editable_validation.json",
        obj_only=True,
        editable_objects=True,
    )
    assert editable_validation["ok"], editable_validation
    editable_contract = editable_validation["combined_obj"]
    assert editable_contract["ok"], editable_contract
    assert editable_contract["checks"]["mirrored_winding_corrected"]
    editable_winding = editable_contract["mirrored_winding_correction"]
    assert editable_winding["requested_objects"]
    assert editable_winding["matched_objects"] == editable_winding["requested_objects"]
    assert editable_winding["missing_objects"] == []
    assert editable_winding["faces_reversed"] > 0
    assert editable_winding["normals_reversed"] > 0

    winding_fixture = output_dir / "mirrored_winding_fixture.obj"
    winding_fixture.write_text(
        "o Normal\n"
        "vn 0.0 1.0 0.0\n"
        "f 1//1 2//1 3//1\n"
        "o Mirrored\n"
        "vn 0.0 -1.0 0.0\n"
        "f 4//2 5//2 6//2\n",
        encoding="utf-8",
    )
    winding_report = assemble_scene._repair_obj_mirrored_winding(
        winding_fixture, {"Mirrored"}
    )
    winding_text = winding_fixture.read_text(encoding="utf-8")
    assert "f 1//1 2//1 3//1" in winding_text
    assert "vn 0.0 1.0 0.0\nf 6//2 5//2 4//2" in winding_text
    assert winding_report["faces_reversed"] == 1
    assert winding_report["normals_reversed"] == 1

    result = {
        "ok": True,
        "coordinate_translation": coordinate_translation,
        "converted_x_after_game_node_y_rotation": converted_x_values,
        "shared_template_collection": shared_template_collection,
        "default_visible": validation["mounts"]["default_visible"],
        "default_hidden": validation["mounts"]["default_hidden"],
        "hidden_overlay_preserved_in_blend": True,
        "exported_glb_mesh_objects": len(exported_mesh_objects),
        "bounds": validation["scene"]["bounds"],
        "files": {
            "blend": str(blend_path),
            "glb": str(glb_path),
            "validation": str(validation_path),
            "combined_obj": str(obj_path),
            "combined_mtl": str(mtl_path),
            "combined_textures": str(texture_dir),
        },
        "combined_obj": combined_obj,
        "mirrored_winding_fixture": winding_report,
        "editable_mirrored_winding": editable_winding,
    }
    result_path = output_dir / "self_test_result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
