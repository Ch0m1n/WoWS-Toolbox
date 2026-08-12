from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace


BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import native_glb_export as NATIVE  # noqa: E402
from runtime_i18n import translate_line  # noqa: E402


_png_stream = BytesIO()
NATIVE.Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(_png_stream, format="PNG")
PNG = _png_stream.getvalue()


def synthetic_glb(path: Path, *, duplicate_hull_primitive: bool = False) -> None:
    binary = bytearray()
    views = []

    def append(payload: bytes, target: int | None = None) -> int:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(payload)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    positions = append(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0), 34962)
    normals = append(struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1), 34962)
    uvs = append(struct.pack("<6f", 0, 0, 1, 0, 0, 1), 34962)
    indices = append(struct.pack("<3H", 0, 1, 2), 34963)
    image = append(PNG)
    accessors = [
        {"bufferView": positions, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": normals, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": uvs, "componentType": 5126, "count": 3, "type": "VEC2"},
        {"bufferView": indices, "componentType": 5123, "count": 3, "type": "SCALAR"},
    ]
    primitive = {
        "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
        "indices": 3,
        "material": 0,
    }
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [
            {"name": "ASC001_Test_Bow", "mesh": 0, "translation": [2, 3, 4]},
            {"name": "HP_AGM_1 (TestGun)", "mesh": 1, "scale": [-1, 1, 1]},
        ],
        "meshes": [
            {"name": "Hull", "primitives": [primitive, primitive] if duplicate_hull_primitive else [primitive]},
            {"name": "Gun", "primitives": [primitive]},
        ],
        "materials": [
            {
                "name": "SHIPMAT_PBS_Hull",
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
            }
        ],
        "textures": [{"source": 0}],
        "images": [{"name": "TestTexture", "bufferView": image, "mimeType": "image/png"}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    binary.extend(b"\0" * ((4 - len(binary) % 4) % 4))
    total = 12 + 8 + len(encoded) + 8 + len(binary)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


class NativeGlbExportTests(unittest.TestCase):
    def test_exports_separate_parts_without_blender(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "ship.glb"
            synthetic_glb(source)
            args = SimpleNamespace(
                input=source,
                output=root / "ship.obj",
                report=root / "export.json",
                formats="obj",
                texture_max_size=1024,
                texture_library=root / "shared",
                pbr_exporter=None,
                pbr_game_dir=None,
                pbr_cache=None,
            )
            report = NATIVE.build(args)
            self.assertTrue(report["ok"])
            self.assertTrue(report["native_no_blender"])
            self.assertEqual(report["object_count"], 2)
            self.assertEqual(report["categories"]["hull"], 1)
            self.assertEqual(report["categories"]["main_gun"], 1)
            obj = args.output.read_text(encoding="utf-8")
            self.assertIn("o ASC001_Test_Bow", obj)
            self.assertIn("o HP_AGM_1 (TestGun)", obj)
            self.assertIn("v 2 3 4", obj)
            self.assertIn("vt 0 1", obj)
            self.assertTrue(args.output.with_suffix(".mtl").is_file())
            texture = root / "textures" / "TestTexture.png"
            self.assertTrue(texture.is_file())
            shared = next((root / "shared").rglob("*.png"))
            cached_bytes = shared.read_bytes()
            texture.write_bytes(b"user-edited")
            self.assertEqual(shared.read_bytes(), cached_bytes)

    def test_mtl_emits_pbr_channel_contract(self) -> None:
        document = {
            "materials": [{"name": "Paint", "pbrMetallicRoughness": {}}],
            "textures": [],
            "images": [],
        }
        contract = {
            "materials": [{"maps": {
                "normal": "textures/paint_normal.png",
                "roughness": "textures/paint_roughness.png",
                "metalness": "textures/paint_metalness.png",
                "ao": "textures/paint_ao.png",
            }}]
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "ship.mtl"
            NATIVE.write_mtl(document, target, {}, contract)
            result = target.read_text(encoding="utf-8")
            self.assertIn("norm textures/paint_normal.png", result)
            self.assertIn("map_Pr textures/paint_roughness.png", result)
            self.assertIn("map_Pm textures/paint_metalness.png", result)
            self.assertIn("map_Ka textures/paint_ao.png", result)

    def test_english_runtime_lines_contain_no_hangul(self) -> None:
        old = os.environ.get("WOWS_TOOLBOX_LANGUAGE")
        os.environ["WOWS_TOOLBOX_LANGUAGE"] = "en"
        try:
            samples = (
                '[PROGRESS] {"message":"코라블리 GameParams를 읽는 중"}',
                "[WARN] 장갑 메시 추출을 건너뛰어요: exit 2",
                "[PROGRESS] 함선/장비 데이터 20,811개를 변환하는 중",
                "[PROGRESS] 필요한 리소스 74개를 추출하는 중",
            )
            for sample in samples:
                with self.subTest(sample=sample):
                    translated = translate_line(sample)
                    self.assertIsNone(__import__("re").search(r"[가-힣]", translated))
        finally:
            if old is None:
                os.environ.pop("WOWS_TOOLBOX_LANGUAGE", None)
            else:
                os.environ["WOWS_TOOLBOX_LANGUAGE"] = old


    def test_skips_duplicate_primitive_references_within_one_part(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "duplicate.glb"
            synthetic_glb(source, duplicate_hull_primitive=True)
            args = SimpleNamespace(
                input=source,
                output=root / "duplicate.obj",
                report=root / "export.json",
                formats="obj",
                texture_max_size=0,
                texture_library=None,
                pbr_exporter=None,
                pbr_game_dir=None,
                pbr_cache=None,
            )
            report = NATIVE.build(args)
            obj = args.output.read_text(encoding="utf-8")
            self.assertEqual(report["object_count"], 2)
            self.assertEqual(obj.count("f "), 2)

    def test_catapult_hardpoint_is_classified_as_aircraft(self) -> None:
        self.assertEqual(
            NATIVE.classify_part("HP_AC_1 (AC001_Catapult_1)"),
            "aircraft",
        )

if __name__ == "__main__":
    unittest.main()
