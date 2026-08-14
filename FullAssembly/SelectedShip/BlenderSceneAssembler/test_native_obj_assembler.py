from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import native_obj_assembler as native


class NativeObjAssemblerTests(unittest.TestCase):
    def test_hull_mount_transform_materials_and_hidden_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = "content/gameplay/test/ship.model"
            component = root / "component"
            component.mkdir()
            texture = root / "paint.png"
            texture.write_bytes(b"synthetic-png-fixture")
            obj = component / "ship.obj"
            obj.write_text(
                "\n".join(
                    [
                        "mtllib ship.mtl",
                        "o Mesh",
                        "v 0 0 0",
                        "v 1 0 0",
                        "v 0 1 0",
                        "vt 0 0",
                        "vt 1 0",
                        "vt 0 1",
                        "vn 0 0 1",
                        "vn 0 0 1",
                        "vn 0 0 1",
                        "g Mesh_group",
                        "usemtl Paint",
                        "f 1/1/1 2/2/2 3/3/3",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            material_manifest = component / "ship.blender-input.json"
            material_manifest.write_text(
                json.dumps(
                    {
                        "objects": [
                            {"object_name": "Mesh", "material_name": "Paint"}
                        ],
                        "materials": [
                            {
                                "name": "Paint",
                                "maps": {"a": str(texture)},
                                "properties": [],
                                "fx_path": "ship.fx",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mapping = root / "mapping.json"
            translated = list(native.IDENTITY_COLUMN_MAJOR)
            translated[12:15] = [10.0, 2.0, 3.0]
            mapping.write_text(
                json.dumps(
                    {
                        "hull_parts": [
                            {"role": "mesh", "path": model_path}
                        ],
                        "combat_mounts": [
                            {
                                "model_path": model_path,
                                "hardpoint": "HP_MAIN",
                                "category": "MainGun",
                                "matrix": translated,
                            }
                        ],
                        "misc_instances": [],
                        "runtime_action_overlays": [
                            {
                                "model_path": model_path,
                                "parent_hardpoint": "HP_MAIN",
                                "instance_name": "HIDDEN_OVERLAY",
                                "matrix": list(native.IDENTITY_COLUMN_MAJOR),
                                "visibility_condition": "overlay",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            summary = root / "summary.json"
            summary.write_text(
                json.dumps(
                    {
                        "source_mapping_sha256": native.sha256(mapping),
                        "strict_validation": {"accepted": True},
                        "results": [
                            {
                                "status": "OK",
                                "model_path": model_path,
                                "output_key": "ship",
                                "output_obj": str(obj),
                                "material_manifest": str(material_manifest),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "scene" / "ship.obj"
            validation = root / "scene" / "ship.validation.json"
            result = native.assemble(
                mapping, summary, output, validation, "harbor_dock"
            )

            self.assertTrue(result["ok"])
            combined = result["combined_obj"]
            self.assertEqual(combined["editable_mesh_objects"], 2)
            self.assertEqual(len(combined["excluded_hidden_mounts"]), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("v -10 2 -3", text)
            self.assertIn("f 4/4/4 5/5/5 6/6/6", text)
            self.assertNotIn("HIDDEN_OVERLAY__Mesh", text)
            mtl = output.with_suffix(".mtl").read_text(encoding="utf-8")
            self.assertIn("map_Kd textures/", mtl)
            sidecar = json.loads(
                output.with_suffix(".model.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(sidecar["parts"]), 2)
            self.assertEqual(sidecar["parts"][1]["pivot"], [-10.0, 2.0, -3.0])
            self.assertEqual(sidecar["obj_axis_forward"], "-Z")
            self.assertEqual(sidecar["obj_axis_up"], "Y")
            self.assertEqual(sidecar["pivot_space"], "obj")


if __name__ == "__main__":
    unittest.main()
