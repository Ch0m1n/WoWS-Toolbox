from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blitz_extract import (
    _include_mesh,
    _priority_obb_names,
    _safe_archive_target,
    _world_matrices,
)


class BlitzExtractionPureTests(unittest.TestCase):
    def test_lod_filter_keeps_requested_and_unsuffixed_meshes(self) -> None:
        self.assertTrue(_include_mesh("US_BB_Iowa_1943_LOD0", 0))
        self.assertFalse(_include_mesh("US_BB_Iowa_1943_LOD1", 0))
        self.assertTrue(_include_mesh("RadarDish", 0))
        self.assertFalse(_include_mesh("Hull_COL", 0))

    def test_world_matrix_composes_parent_translation(self) -> None:
        identity = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        parent = [row[:] for row in identity]
        parent[0][3] = 3.0
        child = [row[:] for row in identity]
        child[1][3] = 5.0

        worlds = _world_matrices(
            {
                1: {"parent_id": 0, "local": parent},
                2: {"parent_id": 1, "local": child},
            }
        )

        self.assertEqual(worlds[2][0][3], 3.0)
        self.assertEqual(worlds[2][1][3], 5.0)

    def test_obb_priority_targets_common_and_ship_bundles(self) -> None:
        names = [
            "assets/bundle/ui/unrelated.ab",
            "assets/bundle/artist/germany/gun.ab",
            "assets/bundle/artist/germany/misc.ab",
            "assets/bundle/artist/animation.ab",
            "assets/bundle/shaders.ab",
            "assets/bundle/germany/ship/battleship/model/ge_bb_mecklenburg_1945.ab",
        ]

        selected = _priority_obb_names(names, "ge_bb_mecklenburg_1945")

        self.assertEqual(selected, names[1:])
    def test_obb_priority_uses_blitz_panamerica_artist_folder(self) -> None:
        names = [
            "assets/bundle/artist/panamerica/gun.ab",
            "assets/bundle/artist/panamerica/misc.ab",
            "assets/bundle/artist/panasia/gun.ab",
        ]

        selected = _priority_obb_names(names, "pa_bb_example_1945")

        self.assertEqual(selected, names[:2])


    def test_obb_target_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                _safe_archive_target(root, "../escape.ab")

    def test_obb_target_preserves_safe_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = _safe_archive_target(root, "assets/bundle/shaders.ab")
            self.assertEqual(target, root / "assets" / "bundle" / "shaders.ab")


if __name__ == "__main__":
    unittest.main()
