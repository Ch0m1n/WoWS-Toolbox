from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "blender_addon" / "wows_legends_importer" / "__init__.py"
BATCH = ROOT / "blender_batch_import.py"


def dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and dotted_name(node.func) == name
    ]


def literal_keywords(call: ast.Call, names: set[str]) -> dict[str, object]:
    return {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in call.keywords
        if keyword.arg in names
    }


class BlenderBridgeSourceTests(unittest.TestCase):
    def test_addon_declares_blender_35(self):
        tree = ast.parse(ADDON.read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "bl_info"
                for target in node.targets
            )
        )
        self.assertEqual(ast.literal_eval(assignment.value)["blender"], (3, 5, 0))

    def test_decoded_obj_is_imported_as_z_up(self):
        for path in (ADDON, BATCH):
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modern = calls_named(tree, "bpy.ops.wm.obj_import")
                legacy = calls_named(tree, "bpy.ops.import_scene.obj")
                self.assertEqual(len(modern), 1)
                self.assertEqual(len(legacy), 1)
                modern_keywords = literal_keywords(
                    modern[0], {"forward_axis", "up_axis"}
                )
                legacy_keywords = literal_keywords(
                    legacy[0], {"axis_forward", "axis_up"}
                )
                self.assertEqual(modern_keywords["forward_axis"], "NEGATIVE_Y")
                self.assertEqual(modern_keywords["up_axis"], "Z")
                self.assertEqual(legacy_keywords["axis_forward"], "-Y")
                self.assertEqual(legacy_keywords["axis_up"], "Z")


if __name__ == "__main__":
    unittest.main()
