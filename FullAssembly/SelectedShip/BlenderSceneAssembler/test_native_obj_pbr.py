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

            self.assertEqual(set(maps), {"metallic_gloss", "roughness", "metalness"})
            roughness = Image.open(root / maps["roughness"])
            metalness = Image.open(root / maps["metalness"])
            self.assertEqual(roughness.getpixel((0, 0)), (191, 191, 191))
            self.assertEqual(metalness.getpixel((0, 0)), (192, 192, 192))


if __name__ == "__main__":
    unittest.main()
