from __future__ import annotations

import json
from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import native_glb_export as native


class NativeGlbTextureNameTests(unittest.TestCase):
    @staticmethod
    def png_bytes(color: tuple[int, int, int, int]) -> bytes:
        payload = BytesIO()
        Image.new("RGBA", (2, 2), color).save(payload, format="PNG")
        return payload.getvalue()

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

    def test_export_textures_reuses_identical_pixels_and_drops_opaque_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.png_bytes((20, 40, 60, 255))
            document = {
                "images": [
                    {"name": "Hull", "bufferView": 0},
                    {"name": "Hull_wire", "bufferView": 1},
                ],
                "bufferViews": [
                    {"byteOffset": 0, "byteLength": len(payload)},
                    {"byteOffset": len(payload), "byteLength": len(payload)},
                ],
            }

            mapping, paths, resized, linked = native.export_textures(
                document,
                payload + payload,
                root / "ship.glb",
                root / "textures",
                0,
                None,
            )

            self.assertEqual(mapping[0], mapping[1])
            self.assertEqual(len(paths), 1)
            self.assertEqual(resized, 0)
            self.assertEqual(linked, 0)
            with Image.open(paths[0]) as image:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((0, 0)), (20, 40, 60))

    def test_export_textures_reuses_decoded_pixel_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.png_bytes((10, 30, 50, 255))
            document = {
                "images": [{"name": "Hull", "bufferView": 0}],
                "bufferViews": [
                    {"byteOffset": 0, "byteLength": len(payload)}
                ],
            }
            library = root / "library"

            first = native.export_textures(
                document,
                payload,
                root / "ship.glb",
                root / "first",
                0,
                library,
            )
            second = native.export_textures(
                document,
                payload,
                root / "ship.glb",
                root / "second",
                0,
                library,
            )

            self.assertEqual(first[3], 0)
            self.assertEqual(second[3], 1)
            self.assertEqual(
                Path(first[1][0]).read_bytes(),
                Path(second[1][0]).read_bytes(),
            )
            shared = list((library / "decoded-pixels-v1").rglob("*.png"))
            self.assertEqual(len(shared), 1)

if __name__ == "__main__":
    unittest.main()
