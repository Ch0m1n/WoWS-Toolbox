from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
import ship_materials as materials


class MaterialCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.game = self.root / "game"
        self.index = self.game / "bin/123/idx/ships.idx"
        self.index.parent.mkdir(parents=True)
        self.index.write_bytes(b"index")
        self.backend = self.root / "backend"
        self.backend.mkdir()
        (self.backend / "ship_material_probe.exe").write_bytes(b"probe-v1")
        self.model = "content/gameplay/test/ship/cruiser/model/model.model"

    def run_scan(self, status="indexed", model=None):
        model = model or self.model
        rows = [{"ShipCode": "PFSB511", "ModelPath": model}]
        payload = {"schema": "ship-material-probe/v1", "models": {model: {
            "status": status,
            "indexed_materials": ["shared/exact-material.mfm"] if status == "indexed" else [],
            "materials_checked": 2,
            "errors": ["missing material"] if status == "unknown" else [],
        }}}
        with mock.patch.object(materials, "extract_entry", return_value=b"assets") as extract, \
             mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")) as run:
            materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root / "cache", backend_root=self.backend)
        return rows[0], extract.call_count, run.call_count

    def test_actual_evidence_cached_and_new_index_invalidates(self):
        row, extracted, scanned = self.run_scan()
        self.assertEqual((row["ShipMaterialStatus"], extracted, scanned), ("indexed", 1, 1))
        self.assertEqual(row["ShipMaterialEvidence"], ["shared/exact-material.mfm"])
        self.assertEqual(self.run_scan()[1:], (0, 0))
        self.index.write_bytes(b"different-index")
        self.assertEqual(self.run_scan()[1:], (1, 1))
        self.assertEqual(list((self.root / "cache").rglob("assets.bin")), [])

    def test_changed_probe_or_selected_model_invalidates(self):
        self.run_scan()
        (self.backend / "ship_material_probe.exe").write_bytes(b"probe-v2")
        self.assertEqual(self.run_scan()[1:], (1, 1))
        self.assertEqual(self.run_scan(model=self.model.replace("model.model", "alternate.model"))[1:], (1, 1))

    def test_code_alone_does_not_set_status_and_unknown_retries(self):
        row, _, _ = self.run_scan(status="legacy")
        self.assertEqual(row["ShipMaterialStatus"], "legacy")
        self.index.write_bytes(b"unknown-index")
        self.assertEqual(self.run_scan(status="unknown")[0]["ShipMaterialStatus"], "unknown")
        self.assertEqual(self.run_scan(status="unknown")[1:], (1, 1))

    def test_selective_refresh_merges_and_failure_preserves_other_models(self):
        other = self.model.replace('model.model', 'other.model')
        models = [self.model, other]
        def inspect(payload, selected=None, error=None):
            rows = [{"ModelPath": model} for model in models]
            with mock.patch.object(materials, "find_entry", return_value=object()), \
                 mock.patch.object(materials, "extract_entry", return_value=b"assets"), \
                 mock.patch.object(materials.subprocess, "run", side_effect=error,
                     return_value=subprocess.CompletedProcess([], 0, json.dumps({"schema":"ship-material-probe/v1", "models":payload}), "")):
                materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root/'cache', backend_root=self.backend, refresh=True, refresh_models=selected)
            return rows
        indexed = {"status":"indexed", "indexed_materials":["exact.mfm"], "materials_checked":2, "visuals_checked":3, "inspection_scope":"selected_visual", "errors":[]}
        legacy = {"status":"legacy", "materials_checked":2, "errors":[]}
        inspect({model:indexed for model in models})
        rows = inspect({self.model:legacy}, {self.model})
        self.assertEqual([r['ShipMaterialStatus'] for r in rows], ['legacy','indexed'])
        rows = inspect({}, {self.model}, RuntimeError('probe failed'))
        self.assertEqual([r['ShipMaterialStatus'] for r in rows], ['unknown','indexed'])
        self.assertEqual(rows[1]['ShipMaterialVisualCount'], 3)
        self.assertEqual(rows[1]['ShipMaterialReason'], '')

    def test_probe_missing_preserves_rows_as_unknown(self):
        (self.backend / "ship_material_probe.exe").unlink()
        row, extracted, scanned = self.run_scan()
        self.assertEqual((row["ShipMaterialStatus"], extracted, scanned), ("unknown", 0, 0))
        self.assertEqual(row["ShipMaterialCatalogVersion"], 2)
        self.assertEqual(row["ShipMaterialDiagnostic"], "helper_missing")

    def test_decode_runtime_error_preserves_catalog_and_cleans_temp(self):
        rows = [{"ModelPath": self.model}]
        with mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials, "extract_entry", side_effect=RuntimeError("Oodle chunk failed")):
            materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root / "cache", backend_root=self.backend)
        self.assertEqual(rows[0]["ShipMaterialStatus"], "unknown")
        self.assertEqual(rows[0]["ShipMaterialDiagnostic"], "assets_failed")
        self.assertEqual(list((self.root / "cache").rglob("material-probe-*")), [])

    def test_indexed_partial_errors_remain_visible(self):
        rows = [{"ModelPath": self.model}]
        payload = {"schema": "ship-material-probe/v1", "models": {self.model: {
            "status": "indexed", "materials_checked": 2, "visuals_checked": 3,
            "indexed_materials": ["exact.mfm"], "errors": ["missing material"]}}}
        with mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials, "extract_entry", return_value=b"assets"), \
             mock.patch.object(materials.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")):
            materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root / "cache", backend_root=self.backend)
        self.assertEqual(rows[0]["ShipMaterialStatus"], "indexed")
        self.assertEqual(rows[0]["ShipMaterialDiagnostic"], "partial_inspection")
        self.assertEqual(rows[0]["ShipMaterialErrors"], ["missing material"])

    def test_game_params_success_survives_optional_assets_failure(self):
        import test_catalog
        def annotate(rows, game, build, entries, **kwargs):
            materials.annotate_ship_materials(rows, self.game, 123, entries,
                                             cache_root=self.root / "cache", backend_root=self.backend)
        with mock.patch.object(test_catalog.CATALOG, "annotate_ship_materials", side_effect=annotate), \
             mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials, "extract_entry", side_effect=RuntimeError("Oodle failed")):
            rows = test_catalog.CatalogTranslationTests().catalog_with_translation("시험함", "korabli")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ShipMaterialStatus"], "unknown")
        self.assertEqual(rows[0]["ShipMaterialDiagnostic"], "assets_failed")

    def test_partial_or_unsubstantiated_response_rejected(self):
        with self.assertRaises(ValueError):
            materials._validate_results({}, [self.model])
        with self.assertRaises(ValueError):
            materials._validate_results({self.model: {"status": "indexed", "indexed_materials": []}}, [self.model])

    def test_explicit_refresh_retries_cached_model(self):
        self.run_scan()
        rows = [{"ModelPath": self.model}]
        payload = {"schema": "ship-material-probe/v1", "models": {self.model: {
            "status": "legacy", "materials_checked": 3, "indexed_materials": [], "errors": []}}}
        with mock.patch.object(materials, "extract_entry", return_value=b"assets"), \
             mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")) as run:
            materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root / "cache", backend_root=self.backend, refresh=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(rows[0]["ShipMaterialStatus"], "legacy")

    def test_timeout_keeps_catalog_usable(self):
        rows = [{"ModelPath": self.model}]
        with mock.patch.object(materials, "extract_entry", return_value=b"assets"), \
             mock.patch.object(materials, "find_entry", return_value=object()), \
             mock.patch.object(materials.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 120)):
            materials.annotate_ship_materials(rows, self.game, 123, {}, cache_root=self.root / "cache", backend_root=self.backend)
        self.assertEqual(rows[0]["ShipMaterialStatus"], "unknown")
        self.assertEqual(list((self.root / "cache").rglob("material-probe-*")), [])


if __name__ == "__main__":
    unittest.main()
