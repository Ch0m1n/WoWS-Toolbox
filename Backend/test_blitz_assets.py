from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blitz_assets import blitz_catalog, resolve_blitz_layout


class BlitzLayoutTests(unittest.TestCase):
    def make_workspace(self, root: Path) -> Path:
        body = root / "full_bundle" / "prefab" / "ship" / "body"
        body.mkdir(parents=True)
        (root / "downloads").mkdir()
        (root / "downloads" / "main.123.net.wargaming.wows.blitz.obb").write_bytes(b"obb")
        return body

    def test_workspace_resolves_bundle_and_obb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = self.make_workspace(root)
            (body / "us_bb_iowa_1943.ab").write_bytes(b"body")

            layout = resolve_blitz_layout(root, require_obb=True)

            self.assertEqual(layout.bundle_root, (root / "full_bundle").resolve())
            self.assertEqual(
                layout.obb_path,
                (root / "downloads" / "main.123.net.wargaming.wows.blitz.obb").resolve(),
            )

    def test_fallback_catalog_groups_paint_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = self.make_workspace(root)
            (body / "ge_bb_mecklenburg_1945.ab").write_bytes(b"base")
            (body / "ge_bb_mecklenburg_1945_paint_01.ab").write_bytes(b"gold")
            (body / "ge_bb_mecklenburg_1945_paint_02.ab").write_bytes(b"alternate")

            rows = blitz_catalog(root, "ko")

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["ShipResource"], "ge_bb_mecklenburg_1945")
            self.assertEqual(row["Nation"], "독일")
            self.assertEqual(row["ShipClass"], "전함")
            self.assertEqual(
                [choice["Id"] for choice in row["Camouflages"]],
                ["default", "paint_01", "paint_02"],
            )
            self.assertEqual(
                row["ModelPath"],
                "prefab/ship/body/ge_bb_mecklenburg_1945.ab",
            )

    def test_direct_bundle_root_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = root / "prefab" / "ship" / "body"
            body.mkdir(parents=True)
            (body / "jp_dd_umikaze_1911.ab").write_bytes(b"body")

            layout = resolve_blitz_layout(root)

            self.assertEqual(layout.bundle_root, root.resolve())
            self.assertIsNone(layout.obb_path)


if __name__ == "__main__":
    unittest.main()
