from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import native_glb_export as native


class NativeGlbTextureNameTests(unittest.TestCase):
    def test_hash_image_names_fall_back_to_material_names(self) -> None:
        document = {
            "images": [
                {"name": "0123456789abcdef0123456789abcdef"},
                {"name": "Hull_a"},
            ],
            "textures": [{"source": 0}, {"source": 1}],
            "materials": [
                {
                    "name": "Main Gun Paint",
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
                },
                {
                    "name": "Hull Paint",
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}},
                },
            ],
        }

        self.assertEqual(
            native.readable_image_names(document),
            ["Main Gun Paint_albedo", "Hull_albedo"],
        )

    def test_texture_manifest_keeps_hash_as_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            texture = root / "textures" / "Main Gun Paint_albedo.png"
            texture.parent.mkdir()
            Image.new("RGBA", (1, 1), (1, 2, 3, 255)).save(texture)
            document = {
                "images": [{"name": "0123456789abcdef0123456789abcdef"}]
            }

            manifest_path = native.write_texture_manifest(
                document,
                root,
                {0: "textures/Main Gun Paint_albedo.png"},
                None,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["naming"], "readable-role-suffix")
            self.assertEqual(
                manifest["textures"][0]["output"],
                "textures/Main Gun Paint_albedo.png",
            )
            self.assertEqual(
                manifest["textures"][0]["source"],
                "0123456789abcdef0123456789abcdef",
            )
            self.assertRegex(manifest["textures"][0]["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()