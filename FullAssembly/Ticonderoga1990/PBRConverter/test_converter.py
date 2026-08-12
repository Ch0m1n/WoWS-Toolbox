from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

import convert


class ConverterTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(convert.safe_name("A/B Shape"), "A_B_Shape")

    def test_blender_id_name_is_bounded_and_collision_resistant(self):
        first = convert.blender_id_name("A" * 80 + "_first")
        second = convert.blender_id_name("A" * 80 + "_second")
        self.assertLessEqual(len(first.encode("utf-8")), 63)
        self.assertLessEqual(len(second.encode("utf-8")), 63)
        self.assertNotEqual(first, second)
        self.assertEqual(first, convert.blender_id_name("A" * 80 + "_first"))

    def test_find_texture_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture = root / "nested" / "TURRET_A.DDS"
            texture.parent.mkdir()
            texture.write_bytes(b"DDS ")
            self.assertEqual(
                convert.find_texture(root, "wherever/Turret.mfm", "_a.dds"),
                texture.resolve(),
            )

    def test_find_texture_skinned_mfm_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture = root / "nested" / "Turret_n.dds"
            texture.parent.mkdir()
            texture.write_bytes(b"DDS ")
            self.assertEqual(
                convert.find_texture(root, "wherever/Turret_skinned.mfm", "_n.dds"),
                texture.resolve(),
            )

    def test_explicit_maps_only_report_declared_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render_set = convert.RenderSet(
                geometry=root / "unused.geometry",
                geometry_label="Example",
                vertices_section="Shape.vertices",
                indices_section="Shape.indices",
                part_name="Shape",
                object_name="Example__Shape",
                group_name="Example__Shape__render_set_000",
                material_mfm_path="content/Example.mfm",
                material_name="ExampleMaterial",
                texture_root=root,
                texture_maps={"a": "content/Example_a.dds"},
                material_fx_path="shaders/example.fx",
                material_properties=[],
            )
            materials, missing = convert.resolve_materials([render_set])
            self.assertEqual([item["map"] for item in missing], ["a"])
            self.assertTrue(missing[0]["source_declared"])
            self.assertEqual(materials[0]["source_declared_channels"], ["a"])

    def test_blender_worker_covers_grid_alpha_variants_and_default(self):
        source = Path(__file__).with_name("blender_pbr.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"grid_alpha_skinned.fx"', source)
        self.assertIn('finite_float(property_values.get("alphaReference", 50), 50.0)', source)
        self.assertIn('max(0.0, min(255.0, alpha_reference))', source)

    def test_rg_normal_reconstructs_positive_z_and_preserves_xy_alpha(self):
        from PIL import Image

        source = Image.new("RGBA", (2, 1))
        source.putdata([(128, 128, 0, 17), (255, 128, 255, 231)])
        converted = convert.reconstruct_tangent_normal(source)
        flat, tangent = list(converted.getdata())

        self.assertEqual((128, 128, 255, 17), flat)
        self.assertEqual((255, 128, 128, 231), tangent)
        self.assertIs(
            convert.normal_square_lookup(), convert.normal_square_lookup()
        )
        self.assertIs(convert.normal_sqrt_lookup(), convert.normal_sqrt_lookup())

    def test_shared_texture_is_transcoded_once_across_model_outputs(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGBA", (2, 2), (64, 96, 128, 255)).save(source)
            shared = root / "shared"
            first_materials = [
                {"id": "material_000", "source_maps": {"a": str(source)}}
            ]
            second_materials = [
                {"id": "material_999", "source_maps": {"a": str(source)}}
            ]

            convert.prepare_blender_textures(
                first_materials, root / "first", shared
            )
            convert.prepare_blender_textures(
                second_materials, root / "second", shared
            )

            self.assertEqual(
                first_materials[0]["maps"]["a"],
                second_materials[0]["maps"]["a"],
            )
            self.assertEqual(1, len(list(shared.glob("*.png"))))

    def test_atomic_png_accepts_an_identical_parallel_winner(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "shared.png"
            image = Image.new("RGBA", (2, 2), (12, 34, 56, 255))

            def publish_then_report_windows_race(source, destination):
                destination = Path(destination)
                destination.write_bytes(Path(source).read_bytes())
                raise PermissionError("simulated parallel Windows destination race")

            with mock.patch.object(
                convert.os,
                "replace",
                side_effect=publish_then_report_windows_race,
            ):
                convert._save_png_atomic(image, target)

            self.assertGreater(target.stat().st_size, 0)
            with Image.open(target) as loaded:
                loaded.verify()
            self.assertEqual([], list(root.glob("*.part")))

    def test_manifest_rejects_mismatched_section_pair(self):
        root = Path(__file__).resolve().parent
        payload = {
            "models": [
                {
                    "geometry": str(root / "missing.geometry"),
                    "texture_root": str(root),
                    "render_sets": [
                        {
                            "vertices_section": "TurretShape.vertices",
                            "indices_section": "Other.indices",
                            "material_mfm_path": "Turret.mfm",
                            "material_name": "Turret",
                        }
                    ],
                }
            ]
        }
        with self.assertRaises(convert.ConversionError):
            convert.parse_render_sets(payload, root / "dummy.json")


if __name__ == "__main__":
    unittest.main()
