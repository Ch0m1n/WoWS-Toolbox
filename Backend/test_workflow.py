from pathlib import Path
import errno
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow
import workbench
from output_provenance import finalize_output


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'game'
        index = self.game / 'bin/123/idx/test.idx'
        index.parent.mkdir(parents=True)
        index.write_bytes(b'index')
        package = self.game / 'res_packages/test.pkg'
        package.parent.mkdir()
        package.write_bytes(b'package')
        for name in ('wowsunpack.exe', 'native_glb_export.py'):
            path = self.root / 'Backend' / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'component')
        self.request = {'common': {'toolbox_root': str(self.root), 'output_root': str(self.root/'output'),
                                   'state_root': str(self.root/'state'), 'formats': 'obj', 'lod': 1, 'texture_max_size': 1024},
                        'items': [{'source': 'pc', 'game_dir': str(self.game), 'display_name': 'Test', 'ship_index': 'PTEST'}]}

    def plan(self):
        return workflow.make_plan(self.request)

    def extractor(self, args, output):
        output.mkdir()
        obj = output/'test.obj'
        obj.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        result = {'output_dir': str(output), 'obj': str(obj), 'quality_contract': {'model_lod': args.lod}}
        return finalize_output(args, result)

    def test_preview_creates_no_output_and_never_calls_exporter(self):
        self.request['items'].append(dict(self.request['items'][0]))
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('preview invoked exporter')):
            plan, args = self.plan()
        self.assertTrue(plan['ok'])
        self.assertNotEqual(plan['items'][0]['output'], plan['items'][1]['output'])
        self.assertFalse((self.root/'output').exists())
        self.assertFalse((self.root/'state').exists())

    def test_source_and_existing_content_changes_invalidate_plan(self):
        first, args = self.plan()
        (self.game/'bin/123/idx/test.idx').write_bytes(b'updated-index')
        second, _ = self.plan()
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.request['common']['output_policy']='replace'
        _, args = self.plan()
        output=args[0].planned_output;output.mkdir(parents=True)
        file=output/'edited.txt';file.write_text('old')
        before,_=self.plan();file.write_text('changed');after,_=self.plan()
        self.assertNotEqual(before['fingerprint'],after['fingerprint'])

    def test_legends_requested_policy_is_not_hidden(self):
        self.request['items'][0].update(source='legends', ship_key='TEST', run_slug='TEST')
        with mock.patch('batch_extract.compatibility', return_value={'ok':True,'build':999,'message':''}), \
             mock.patch('batch_extract.item_contract', return_value={'ok':True}), \
             mock.patch.object(workflow,'source_identity', return_value={'source':'legends'}):
            plan,_=self.plan()
        self.assertEqual(plan['items'][0]['requested']['lod'],1)
        self.assertEqual(plan['items'][0]['applied']['lod'],0)
        self.assertEqual(plan['items'][0]['applied']['texture_max_size'],0)
        self.assertIsNone(plan['items'][0]['build'])

    def test_failed_staging_preserves_existing_edit(self):
        self.request['common']['output_policy']='replace'
        _,args=self.plan();output=args[0].planned_output;output.mkdir(parents=True)
        (output/'edited.txt').write_text('preserve')
        _,args=self.plan()
        def failed(staged, target):
            target.mkdir();(target/'partial.obj').write_text('partial')
            raise RuntimeError('export failed')
        with self.assertRaises(RuntimeError):
            workflow.run_staged(args[0],output,failed)
        self.assertEqual((output/'edited.txt').read_text(),'preserve')

    def test_replacement_retains_backup_and_failed_publish_rolls_back(self):
        self.request['common']['output_policy']='replace'
        _,args=self.plan();output=args[0].planned_output;output.mkdir(parents=True)
        (output/'edited.txt').write_text('old')
        _,args=self.plan()
        result=workflow.run_staged(args[0],output,self.extractor)
        self.assertEqual((Path(result['previous_output_backup'])/'edited.txt').read_text(),'old')
        self.assertTrue((output/'test.obj').is_file())
        _,args=self.plan()
        real_replace=workflow.os.replace
        def replace(source,target):
            if Path(target)==output and '.staging' in str(source):
                raise PermissionError('simulated lock')
            return real_replace(source,target)
        with mock.patch.object(workflow.os,'replace',side_effect=replace), self.assertRaises(PermissionError):
            workflow.run_staged(args[0],output,self.extractor)
        self.assertTrue((output/'test.obj').is_file())

    def test_edit_during_staging_blocks_publish(self):
        self.request['common']['output_policy']='replace'
        _,args=self.plan();output=args[0].planned_output;output.mkdir(parents=True)
        edited=output/'edited.txt';edited.write_text('before')
        _,args=self.plan()
        def concurrent_edit(staged,target):
            result=self.extractor(staged,target)
            edited.write_text('user changed this while exporting')
            return result
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            workflow.run_staged(args[0],output,concurrent_edit)
        self.assertEqual(edited.read_text(),'user changed this while exporting')
        self.assertFalse((output/'test.obj').exists())

    def test_skip_only_verifies_same_request_and_unchanged_files(self):
        _,args=self.plan();output=args[0].planned_output
        workflow.run_staged(args[0],output,self.extractor)
        self.request['common']['output_policy']='skip_verified'
        plan,args=self.plan()
        self.assertEqual(plan['items'][0]['action'],'skip_verified')
        with mock.patch.object(subprocess,'run',side_effect=AssertionError('skip exported')):
            result=workflow.run_staged(args[0],output,self.extractor)
        self.assertTrue(result['skipped_verified'])
        (output/'test.obj').write_text('changed')
        plan,_=self.plan()
        self.assertEqual(plan['items'][0]['action'],'new')
        self.assertNotEqual(plan['items'][0]['output'],str(output))

    def test_journal_owner_and_interruption_are_not_overwritten(self):
        plan,_=self.plan();state=self.root/'state'
        journal=workflow.Journal(state,plan)
        journal.update(0,'running')
        with self.assertRaises(FileExistsError):workflow.Journal(state,plan)
        with self.assertRaises(RuntimeError):workbench.recovery(state,self.root/'output',acknowledge=True)
        journal.close()
        with self.assertRaises(RuntimeError):workflow.Journal(state,plan)
        review=workbench.recovery(state,self.root/'output',acknowledge=True)
        self.assertTrue(review['requires_review'])
        self.assertEqual(len(list(state.glob('batch-journal-reviewed-*.json'))),1)
        next_journal=workflow.Journal(state,plan);next_journal.close()

    def test_retry_guidance_does_not_loop_or_resume_partial_files(self):
        for error,code in ((PermissionError('locked'),'file_locked_or_denied'),
                           (OSError(errno.ENOSPC,'full'),'insufficient_disk'),
                           (ValueError('unsupported format'),'unsupported_request')):
            guide=workflow.retry_guidance(error)
            self.assertEqual(guide['code'],code)
            self.assertFalse(guide['automatic_retry']);self.assertFalse(guide['resume_partial_files'])

    def test_retry_plan_reports_changed_quality(self):
        plan,_=self.plan()
        failure={'source':'pc','ship_index':'PTEST','ship_key':'','model_path':'',
                 'request_identity':plan['items'][0]['request_identity'],'requested':plan['items'][0]['requested']}
        self.request['common']['lod']=0
        new,_=workflow.make_plan(self.request,failures=[failure])
        self.assertTrue(new['items'][0]['retry']['settings_changed'])

    def test_snapshots_pin_metadata_and_do_not_erase_good_scan(self):
        rows=[{'GameParamsKey':'TEST','ModelPath':'test.model','ShipMaterialStatus':'indexed'}]
        first=workbench.save_snapshot(rows,self.root,'korabli')
        workbench.pin_snapshot(first,True)
        for i in range(12):
            workbench.save_snapshot([dict(rows[0],ShipMaterialStatus='unknown',Build=i)],self.root,'korabli')
        self.assertTrue(first.is_file())
        latest=workbench.save_snapshot([dict(rows[0],ShipMaterialStatus='unknown')],self.root,'korabli')
        diff=workbench.compare_snapshots(first,latest)
        self.assertEqual(diff['changed'][0]['material_change_certainty'],'unverified')
        self.assertEqual(diff['added'],[])

    def test_diagnostics_are_allowlisted_not_free_form_logs(self):
        state=self.root/'state';state.mkdir()
        (state/'last-batch-report.json').write_text(json.dumps({'success_count':1,'message':'SECRET /private/path','traceback':'TOKEN'}))
        data=workbench.diagnostics(self.root,state)
        self.assertNotIn('SECRET',json.dumps(data));self.assertNotIn('TOKEN',json.dumps(data))


if __name__=='__main__':unittest.main()
