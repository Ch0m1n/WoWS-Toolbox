from __future__ import annotations

import unittest

from PIL import Image

import convert


class CamouflageRenderingTests(unittest.TestCase):
    def test_rgb_mask_bakes_four_palette_regions(self) -> None:
        base = Image.new("RGBA", (4, 1), (160, 160, 160, 255))
        mask = Image.new("RGB", (4, 1))
        mask.putdata(
            [
                (255, 0, 0),
                (0, 255, 0),
                (0, 0, 255),
                (0, 0, 0),
            ]
        )

        baked = convert.apply_palette_camouflage(
            base,
            mask,
            ["#804040", "#408040", "#404080", "#808080"],
            1.0,
        )

        pixels = list(baked.convert("RGB").getdata())
        self.assertGreater(pixels[0][0], pixels[0][1])
        self.assertGreater(pixels[1][1], pixels[1][0])
        self.assertGreater(pixels[2][2], pixels[2][0])
        self.assertEqual(pixels[3][0], pixels[3][1])
        self.assertEqual(pixels[3][1], pixels[3][2])


if __name__ == "__main__":
    unittest.main()
