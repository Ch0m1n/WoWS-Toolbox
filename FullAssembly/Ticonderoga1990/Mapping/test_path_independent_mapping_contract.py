from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "ticon_mapping_core", HERE / "build_ticonderoga_assembly.py"
)
assert SPEC is not None and SPEC.loader is not None
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


class PathIndependentMappingContractTests(unittest.TestCase):
    def test_source_record_depends_on_content_not_workspace_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first" / "GameParams.data"
            second = root / "second" / "GameParams.data"
            first.parent.mkdir()
            second.parent.mkdir()
            content = b"same verified source content"
            first.write_bytes(content)
            second.write_bytes(content)

            first_record = core.source_file_record("GameParams.data", first)
            second_record = core.source_file_record("GameParams.data", second)
            self.assertEqual(first_record, second_record)
            self.assertEqual(
                first_record["logical_source"], "content/GameParams.data"
            )
            self.assertNotIn("workspace_copy", first_record)
            self.assertNotIn(str(root), repr(first_record))

    def test_source_label_cannot_smuggle_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            source.write_bytes(b"x")
            with self.assertRaises(ValueError):
                core.source_file_record("../source.bin", source)


if __name__ == "__main__":
    unittest.main()
