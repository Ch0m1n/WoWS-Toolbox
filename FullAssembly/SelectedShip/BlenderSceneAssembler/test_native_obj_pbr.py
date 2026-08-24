from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

import native_obj_assembler as native


class NativeObjPbrTests(unittest.TestCase):
    def test_metallic_gloss_is_retained_and_split_for_obj_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "paint_mg.png"
            Image.new("RGBA", (2, 2), (64, 192, 7, 255)).save(source)

            maps = native.split_metallic_gloss_texture(source, root / "textures")

            self.assertEqual(set(maps), {"metallic_gloss", "specular", "roughness", "metalness"})
            self.assertEqual(
                maps,
                {
                    "metallic_gloss": "textures/paint_metallic_gloss.png",
                    "specular": "textures/paint_specular.png",
                    "roughness": "textures/paint_roughness.png",
                    "metalness": "textures/paint_metalness.png",
                },
            )
            roughness = Image.open(root / maps["roughness"])
            metalness = Image.open(root / maps["metalness"])
            self.assertEqual(roughness.getpixel((0, 0)), (191, 191, 191))
            self.assertEqual(metalness.getpixel((0, 0)), (192, 192, 192))

    def test_material_hint_and_collision_suffix_stay_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "one" / "0123456789abcdef0123456789abcdef.png"
            second = root / "two" / "fedcba9876543210fedcba9876543210.png"
            first.parent.mkdir()
            second.parent.mkdir()
            Image.new("RGBA", (1, 1), (10, 20, 30, 255)).save(first)
            Image.new("RGBA", (1, 1), (30, 20, 10, 255)).save(second)
            cache: dict[str, str] = {}
            used: set[str] = set()

            first_name = native.materialize_texture(
                first,
                root / "textures",
                cache,
                stem_hint="Main Gun Paint",
                used_names=used,
            )
            second_name = native.materialize_texture(
                second,
                root / "textures",
                cache,
                stem_hint="Main Gun Paint",
                used_names=used,
            )

            self.assertEqual(first_name, "textures/Main_Gun_Paint_albedo.png")
            self.assertEqual(second_name, "textures/Main_Gun_Paint_albedo_02.png")


if __name__ == "__main__":
    unittest.main()
