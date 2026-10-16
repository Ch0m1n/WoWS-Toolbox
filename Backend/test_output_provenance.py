from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import output_provenance as provenance


class OutputProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows CI TEMP can use an 8.3 alias (RUNNER~1). Match the canonical
        # output root used by production without weakening linked-path checks.
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / 'ship'
        self.output.mkdir()
        self.obj = self.output / 'ship.obj'
        self.obj.write_text('mtllib ship.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        (self.output / 'ship.mtl').write_text('newmtl hull\nmap_Kd paint with spaces.png\n# wows_map_Pr rough.png\n')
        (self.output / 'paint with spaces.png').write_bytes(b'paint')
        (self.output / 'rough.png').write_bytes(b'rough')
        self.args = SimpleNamespace(output_root=self.root, toolbox_root=self.root, source='korabli',
                                    ship_index='PTEST001', formats='obj', lod=0, texture_max_size=0,
                                    camouflage='permanent', camouflage_color_scheme='primary')

    def result(self):
        return {'output_dir': str(self.output), 'obj': str(self.obj), 'build': 123,
                'quality_contract': {'model_lod': 0, 'texture_max_size': 0},
                'camouflage': 'permanent', 'camouflage_color_scheme': 'primary'}

    def test_portable_manifest_and_offline_recheck(self):
        result = provenance.finalize_output(self.args, self.result())
        path = Path(result['provenance_manifest'])
        data = json.loads(path.read_text())
        self.assertEqual(data['source']['build'], 123)
        self.assertEqual(data['applied']['camouflage_color_scheme'], 'primary')
        self.assertEqual(len(data['artifacts']), 4)
        self.assertNotIn(str(self.root), path.read_text())
        self.assertIsNone(data['armor_available'])
        self.assertEqual(provenance.revalidate(path)['status'], 'complete')
        (self.output / 'rough.png').write_bytes(b'modified')
        checked = provenance.revalidate(path)
        self.assertEqual(checked['status'], 'failed')
        self.assertIn('artifact_changed', [i['code'] for i in checked['issues']])

    def test_missing_texture_fails_but_preserves_outputs_and_diagnostics(self):
        (self.output / 'rough.png').unlink()
        with self.assertRaises(RuntimeError):
            provenance.finalize_output(self.args, self.result())
        self.assertTrue(self.obj.is_file())
        data = json.loads((self.output / provenance.MANIFEST_NAME).read_text())
        self.assertEqual(data['validation']['status'], 'failed')
        self.assertIn('missing_or_empty_texture', [i['code'] for i in data['validation']['issues']])

    def test_external_reference_never_opened(self):
        external = self.root / 'private.png'
        external.write_bytes(b'private')
        (self.output / 'ship.mtl').write_text('map_Kd ../private.png\n')
        opened = []
        real_digest = provenance.digest
        def tracked(path):
            opened.append(path)
            return real_digest(path)
        with mock.patch.object(provenance, 'digest', side_effect=tracked):
            validation = provenance.inspect_outputs(self.output, self.result())
        self.assertEqual(validation['status'], 'failed')
        self.assertNotIn(external, opened)

    def test_optional_armor_failure_is_warning_and_not_zero_thickness(self):
        result = self.result()
        result['armor_export_error'] = 'unsupported'
        final = provenance.finalize_output(self.args, result)
        self.assertEqual(final['output_validation']['status'], 'warning')
        self.assertEqual(provenance.revalidate(Path(final['provenance_manifest']))['status'], 'warning')

    def test_reparse_point_is_rejected_before_reading_texture(self):
        texture = self.output / 'paint with spaces.png'
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            if path == texture:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with mock.patch.object(Path, 'stat', stat):
            checked = provenance.inspect_outputs(self.output, self.result())
        self.assertEqual(checked['status'], 'failed')
        self.assertNotIn('paint with spaces.png', [a['path'] for a in checked['artifacts']])

    def test_unknown_applied_options_remain_unknown(self):
        result = self.result()
        result.pop('quality_contract')
        result.pop('build')
        self.args.source = 'legends'
        final = provenance.finalize_output(self.args, result)
        data = json.loads(Path(final['provenance_manifest']).read_text())
        self.assertIsNone(data['source']['build'])
        self.assertIsNone(data['applied']['lod'])

    def test_all_source_adapters_and_batch_entry_point(self):
        import batch_extract as batch
        import threading
        for source, extractor in (('pc', 'extract_pc_family'), ('korabli', 'extract_pc_family'),
                                  ('legends', 'extract_legends'), ('blitz', 'extract_blitz')):
            self.args.source = source
            self.args.prefetch_event = threading.Event()
            result = self.result()
            if source == 'blitz':
                result['obj_file'] = result.pop('obj')
            with mock.patch.object(batch, extractor, return_value=result):
                final = batch.run_one(self.args, self.output)
            self.assertEqual(final['output_validation']['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
