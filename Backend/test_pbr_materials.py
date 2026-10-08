from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from PIL import Image

import pbr_materials as PBR


class PbrMaterialsTests(unittest.TestCase):
    def test_bc7prep_supported_decoder_output_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Hull_n.dd0"
            header = bytearray(196)
            header[:4] = b"DDS "
            header[84:88] = b"DX10"
            header[148:152] = b"\xbc\x07\x00\x00"
            source.write_bytes(header)
            def decode(command):
                Image.new("RGBA", (16, 16), (128, 128, 0, 255)).save(command[-1])
            with mock.patch.object(PBR, "_run", side_effect=decode):
                outputs, _ = PBR._convert_to_cache(source, root / "cache", "/Hull_n.dd0", "normal", 0, Path("exporter.exe"))
            with Image.open(outputs["normal"]) as image:
                self.assertEqual(image.size, (16, 16))
                self.assertEqual(image.getpixel((0, 0)), (128, 128, 255, 255))

    def test_bc7prep_never_reaches_pillow_when_decoder_rejects_it(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Hull_n.dd0"
            header = bytearray(196)
            header[:4] = b"DDS "
            header[84:88] = b"DX10"
            header[148:152] = b"\xbc\x07\x00\x00"
            source.write_bytes(header)
            Image.new("RGBA", (8, 8), (128, 128, 0, 255)).save(source.with_suffix(".dds"), format="DDS")
            with mock.patch.object(PBR, "_run", side_effect=subprocess.CalledProcessError(1, "decode", output="unsupported layout")):
                outputs, _ = PBR._convert_to_cache(source, root / "cache", "/Hull_n.dd0", "normal", 0, Path("exporter.exe"))
            with Image.open(outputs["normal"]) as image:
                self.assertEqual(image.size, (8, 8))
                self.assertEqual(image.getpixel((0, 0)), (128, 128, 255, 255))
            self.assertTrue(list((root / "cache" / "maps").glob("*.decode_v6.json")))

    def test_bc7prep_without_decoder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Hull_n.dd0"
            header = bytearray(196)
            header[:4] = b"DDS "
            header[84:88] = b"DX10"
            header[148:152] = b"\xbc\x07\x00\x00"
            source.write_bytes(header)
            with self.assertRaisesRegex(RuntimeError, "requires the compatible exporter"):
                PBR._convert_to_cache(source, root / "cache", "/Hull_n.dd0", "normal", 0)

    def test_parses_asset_index_and_builds_dd0_first_candidates(self) -> None:
        output = "\n".join(
            [
                "(A) /content/gameplay/test/ship/textures/Test_Hull.mfm 123 bytes",
                "(A) /content/gameplay/test/gun/textures/Test_Gun_skinned.mfm 456 bytes",
                "(I) /content/not-an-asset.mfm 10 bytes",
            ]
        )
        paths = PBR.parse_mfm_index(output)
        self.assertEqual(len(paths), 2)
        aliases = PBR.mfm_alias_index(paths)
        self.assertIn("test_gun", aliases)
        candidates = PBR.candidate_sets(paths[0], "Test_Hull")
        self.assertTrue(candidates[0]["normal"][0].endswith("Test_Hull_n.dd0"))
        self.assertTrue(candidates[0]["normal"][1].endswith("Test_Hull_n.dds"))
        self.assertTrue(candidates[0]["normal"][2].endswith("Test_Hull_alpha_n.dd0"))
        self.assertTrue(candidates[0]["normal"][3].endswith("Test_Hull_alpha_n.dds"))

    def test_negative_cache_requires_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "availability.json"
            logical = "/content/test/textures/Paint_n.dd0"
            path.write_text(
                __import__("json").dumps(
                    {"schema": "wows-toolbox-pbr-materials/v2", "unavailable": [logical]}
                ),
                encoding="utf-8",
            )
            self.assertEqual(PBR.load_unavailable_paths(path), set())
            path.write_text(
                __import__("json").dumps({"schema": PBR.SCHEMA, "unavailable": [logical]}),
                encoding="utf-8",
            )
            self.assertEqual(PBR.load_unavailable_paths(path), {logical})

    def test_normal_and_metallic_gloss_channel_conversion(self) -> None:
        normal = Image.new("RGBA", (1, 1), (128, 128, 0, 255))
        reconstructed = PBR.reconstruct_tangent_normal(normal)
        self.assertGreater(reconstructed.getpixel((0, 0))[2], 250)

        mg = Image.new("RGBA", (1, 1), (64, 192, 7, 255))
        source, roughness, metalness = PBR.split_metallic_gloss(mg)
        specular = PBR.specular_from_metallic_gloss(mg)
        self.assertEqual(source.getpixel((0, 0)), (64, 192, 7, 255))
        self.assertEqual(specular.mode, "L")
        self.assertEqual(roughness.mode, "L")
        self.assertEqual(metalness.mode, "L")
        self.assertEqual(specular.getpixel((0, 0)), 64)
        self.assertEqual(roughness.getpixel((0, 0)), 191)
        self.assertEqual(metalness.getpixel((0, 0)), 192)

    def test_cached_pbr_channels_skip_package_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source = root / "source.png"
            Image.new("RGBA", (2, 2), (80, 10, 220, 255)).save(source)
            logical = {
                "normal": "/content/test/textures/Paint_n.dd0",
                "metallic_gloss": "/content/test/textures/Paint_mg.dd0",
                "ao": "/content/test/textures/Paint_ao.dd0",
            }
            for channel, source_path in logical.items():
                PBR._convert_to_cache(source, cache, source_path, channel, 0)
            (cache / "availability.json").write_text(
                __import__("json").dumps({"schema": PBR.SCHEMA, "unavailable": list(logical.values())}),
                encoding="utf-8",
            )
            document = {
                "materials": [{"name": "Paint", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
                "textures": [{"source": 0}],
                "images": [{"name": "Paint"}],
            }
            with mock.patch.object(
                PBR, "load_mfm_paths", return_value=(["/content/test/textures/Paint.mfm"], True)
            ), mock.patch.object(PBR, "_extract_paths", side_effect=AssertionError("cache miss")):
                contract = PBR.prepare_pbr_materials(
                    document,
                    exporter=root / "wowsunpack.exe",
                    game_dir=root / "game",
                    texture_dir=root / "textures",
                    work_dir=root / "work",
                    cache_root=cache,
                )
            self.assertEqual(contract["cache"]["extraction_calls"], 0)
            self.assertEqual(contract["coverage"]["pbr_materials"], 1)
            self.assertEqual(contract["naming"], "readable-role-suffix")
            self.assertEqual(
                set(contract["texture_files"]),
                {
                    "textures/Paint_ao.png",
                    "textures/Paint_metalness.png",
                    "textures/Paint_normal.png",
                    "textures/Paint_roughness.png",
                    "textures/Paint_specular.png",
                },
            )
            self.assertTrue(
                all(not __import__("re").search(r"_[0-9a-f]{8}_", name) for name in contract["texture_files"])
            )
    def test_resolved_cached_alias_skips_unselected_alias_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source = root / "source.png"
            Image.new("RGBA", (2, 2), (80, 10, 220, 255)).save(source)
            logical = {
                "normal": "/content/test/textures/Paint_n.dd0",
                "metallic_gloss": "/content/test/textures/Paint_mg.dd0",
                "ao": "/content/test/textures/Paint_ao.dd0",
            }
            for channel, source_path in logical.items():
                PBR._convert_to_cache(source, cache, source_path, channel, 0)
            document = {
                "materials": [
                    {
                        "name": "Paint",
                        "pbrMetallicRoughness": {
                            "baseColorTexture": {"index": 0}
                        },
                    }
                ],
                "textures": [{"source": 0}],
                "images": [{"name": "Paint"}],
            }
            aliases = [
                "/content/test/textures/Paint.mfm",
                "/content/test/textures/Paint_skinned.mfm",
            ]

            with mock.patch.object(
                PBR, "load_mfm_paths", return_value=(aliases, True)
            ), mock.patch.object(
                PBR,
                "_extract_paths",
                side_effect=AssertionError("unselected alias was re-extracted"),
            ):
                contract = PBR.prepare_pbr_materials(
                    document,
                    exporter=root / "wowsunpack.exe",
                    game_dir=root / "game",
                    texture_dir=root / "textures",
                    work_dir=root / "work",
                    cache_root=cache,
                )

            self.assertEqual(contract["cache"]["extraction_calls"], 0)
            self.assertEqual(contract["cache"]["resolved_materials_reused"], 1)
            self.assertEqual(contract["coverage"]["channels"], 5)

    def test_hash_named_image_resolves_pbr_by_material_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source = root / "source.png"
            Image.new("RGBA", (2, 2), (80, 10, 220, 255)).save(source)
            logical = {
                "normal": "/content/test/textures/Paint_n.dd0",
                "metallic_gloss": "/content/test/textures/Paint_mg.dd0",
                "ao": "/content/test/textures/Paint_ao.dd0",
            }
            for channel, source_path in logical.items():
                PBR._convert_to_cache(source, cache, source_path, channel, 0)
            document = {
                "materials": [
                    {
                        "name": "Paint",
                        "pbrMetallicRoughness": {
                            "baseColorTexture": {"index": 0}
                        },
                    }
                ],
                "textures": [{"source": 0}],
                "images": [
                    {"name": "0123456789abcdef0123456789abcdef"}
                ],
            }

            with mock.patch.object(
                PBR,
                "load_mfm_paths",
                return_value=(["/content/test/textures/Paint.mfm"], True),
            ), mock.patch.object(
                PBR, "_extract_paths", side_effect=AssertionError("cache miss")
            ):
                contract = PBR.prepare_pbr_materials(
                    document, exporter=root / "wowsunpack.exe",
                    game_dir=root / "game", texture_dir=root / "textures",
                    work_dir=root / "work", cache_root=cache,
                )
            self.assertEqual(contract["coverage"]["pbr_materials"], 1)
            self.assertEqual(contract["materials"][0]["base_image"], document["images"][0]["name"])

    def test_parallel_cache_writers_have_unique_temporary_paths(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "shared.png"
            image = Image.new("L", (2, 2), 73)
            barrier = Barrier(4)
            paths = []
            original_save = Image.Image.save
            def save(instance, path, **kwargs):
                paths.append(str(path))
                barrier.wait(timeout=5)
                return original_save(instance, path, **kwargs)
            with mock.patch.object(Image.Image, "save", new=save):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda _: PBR._save_cached_png(image, target), range(4)))
            self.assertEqual(len(set(paths)), 4)
            with Image.open(target) as cached:
                self.assertEqual(cached.getpixel((0, 0)), 73)
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_locked_valid_winner_is_reused_without_losing_pbr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "shared.png"
            Image.new("L", (2, 2), 73).save(target)
            with mock.patch.object(PBR.os, "replace", side_effect=PermissionError("reader holds winner")):
                PBR._save_cached_png(Image.new("L", (2, 2), 73), target)
            with Image.open(target) as cached:
                self.assertEqual(cached.getpixel((0, 0)), 73)
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_temporarily_locked_winner_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "shared.png"
            Image.new("L", (2, 2), 73).save(target)
            original_open = Image.open
            calls = 0
            def open_winner(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("publication still in progress")
                return original_open(path, *args, **kwargs)
            with mock.patch.object(PBR.os, "replace", side_effect=PermissionError("locked")), \
                    mock.patch.object(PBR.Image, "open", side_effect=open_winner):
                PBR._save_cached_png(Image.new("L", (2, 2), 73), target)
            self.assertEqual(calls, 2)
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_cached_output_contract_keeps_source_mg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "paint_mg.png"
            Image.new("RGBA", (2, 2), (80, 10, 220, 255)).save(source)
            outputs, resized = PBR._convert_to_cache(
                source, root / "cache", "/content/paint_mg.dd0", "metallic_gloss", 0
            )
            self.assertFalse(resized)
            self.assertEqual(set(outputs), {"metallic_gloss", "specular", "roughness", "metalness"})
            self.assertTrue(all(path.is_file() for path in outputs.values()))
            for role in ("specular", "roughness", "metalness"):
                with Image.open(outputs[role]) as cached:
                    self.assertEqual(cached.mode, "L")

    def test_publish_reuses_identical_role_content_across_readable_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("L", (2, 2), 73).save(first)
            second.write_bytes(first.read_bytes())
            allocations: dict[str, str] = {}
            names: set[str] = set()

            published_first = PBR._publish(
                first, root / "textures", "Hull", "roughness", allocations, names
            )
            published_second = PBR._publish(
                second, root / "textures", "Hull_wire", "roughness", allocations, names
            )

            self.assertEqual(published_first, published_second)
            self.assertEqual(len(list((root / "textures").glob("*.png"))), 1)

    def test_ao_uses_the_channel_with_real_payload(self) -> None:
        image = Image.new("RGBA", (4, 1))
        image.putdata([(0, 10, 0, 255), (0, 80, 0, 255), (0, 160, 0, 255), (0, 240, 0, 255)])
        ao, channel = PBR.extract_ambient_occlusion(image)
        self.assertEqual(channel, "G")
        self.assertEqual(ao.mode, "L")
        self.assertEqual([ao.getpixel((x, 0)) for x in range(4)], [10, 80, 160, 240])


if __name__ == "__main__":
    unittest.main()
