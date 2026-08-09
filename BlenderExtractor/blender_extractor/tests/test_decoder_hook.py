from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from legends_assets.core import UnsafePathError
from legends_assets.decoder_hook import run_decoder_hook


class DecoderHookTests(unittest.TestCase):
    def test_dry_run_does_not_require_decoder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_decoder_hook(
                root / "missing.geometry",
                root / "out",
                execute=False,
            )
            self.assertEqual(result["status"], "dry-run")

    def test_optional_decoder_output_is_bounded_and_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.geometry"
            source.write_bytes(b"legacy geometry")
            module = types.ModuleType("fixture_geometry_decoder")

            def decode_geometry(_source: Path, output_dir: Path):
                target = output_dir / "sample.obj"
                target.write_text("o sample\nv 0 0 0\n", encoding="utf-8")
                return [target]

            module.decode_geometry = decode_geometry
            sys.modules[module.__name__] = module
            try:
                result = run_decoder_hook(
                    source,
                    root / "out",
                    module_name=module.__name__,
                    execute=True,
                )
            finally:
                sys.modules.pop(module.__name__, None)
            self.assertEqual(result["status"], "decoded-and-validated")
            self.assertEqual(len(result["outputs"]), 1)

    def test_decoder_cannot_return_file_outside_output_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.geometry"
            source.write_bytes(b"legacy geometry")
            module = types.ModuleType("escaping_geometry_decoder")

            def decode_geometry(_source: Path, _output_dir: Path):
                target = root / "escape.obj"
                target.write_text("o bad\n", encoding="utf-8")
                return [target]

            module.decode_geometry = decode_geometry
            sys.modules[module.__name__] = module
            try:
                with self.assertRaises(UnsafePathError):
                    run_decoder_hook(
                        source,
                        root / "out",
                        module_name=module.__name__,
                        execute=True,
                    )
            finally:
                sys.modules.pop(module.__name__, None)


if __name__ == "__main__":
    unittest.main()
