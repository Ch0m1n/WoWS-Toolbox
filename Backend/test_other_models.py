import copy
import json
from pathlib import Path
import sys
import tempfile
import struct
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import other_models as models


class OtherModelTests(unittest.TestCase):
    def test_only_installed_model_categories_and_highest_lod_are_classified(self):
        cases = {'aircraft/fighter/A/A.geometry': 'aircraft',
                 'projectile/torpedo/T/T.geometry': 'torpedo',
                 'projectile/artillery/S/S.geometry': 'shell',
                 'projectile/rocket/R/R.geometry': 'missile',
                 'ship/destroyer/S/S.geometry': None,
                 'projectile/torpedo/T/T_lod1.geometry': None,
                 'aircraft/fighter/A/A.dds': None}
        for suffix, expected in cases.items():
            self.assertEqual(models.kind_of('content/gameplay/usa/'+suffix), expected)

    def test_traversal_and_windows_drive_paths_are_rejected(self):
        for path in ('../secret', 'content/gameplay/../../secret', 'C:/private.geometry', 'content/gameplay/a/C:secret.geometry'):
            with self.assertRaises(ValueError):
                models.logical_path(path)

    def test_matching_torpedo_color_ignores_numeric_separators_and_prefers_dd0(self):
        root = 'content/gameplay/usa/projectile/torpedo/'
        model = root+'APT011_Sea_Torpedo_Mk_11/APT011_Sea_Torpedo_Mk_11.geometry'
        dds = root+'textures/APT011_Sea_Torpedo_Mk11_a.dds'
        dd0 = dds[:-4]+'.dd0'
        self.assertEqual(models.choose_albedo(model, [dds, dd0]), dd0)
        self.assertIsNone(models.choose_albedo(model, [root+'textures/APT012_Sea_Torpedo_Mk11_a.dds']))

    def document(self):
        return {'materials': [{'name':'AIRCRAFT', 'pbrMetallicRoughness':{'baseColorTexture':{'index':0}}},
                              {'name':'PROPELLERMAT_propeller', 'pbrMetallicRoughness':{'baseColorTexture':{'index':1}}}],
                'meshes':[{'primitives':[{'material':0},{'material':1}]}], 'nodes':[{'mesh':0}],
                'textures':[{'source':0},{'source':1}], 'images':[{'name':'paint'},{'name':'blur'}]}

    def test_rotation_disc_is_removed_with_its_unused_texture(self):
        doc=self.document()
        self.assertEqual(models.filter_document(doc, 'aircraft', False), ['PROPELLERMAT_propeller'])
        self.assertEqual(len(doc['meshes'][0]['primitives']),1)
        self.assertEqual(doc['images'],[{'name':'paint'}])
        self.assertEqual(len(doc['textures']),1)
        self.assertEqual(len(doc['materials']),1)
        original=self.document()
        models.filter_document(original,'aircraft',True)
        self.assertEqual(len(original['meshes'][0]['primitives']),2)

    def test_shell_body_retained_and_head_and_friction_effects_excluded(self):
        self.assertFalse(models.is_effect('SHELLBODY_1','shell','shaders/std_effects/trails/shell_tracer_body.fx'))
        self.assertTrue(models.is_effect('HEAD_3','shell'))
        self.assertTrue(models.is_effect('FRICTION_2','shell'))
        self.assertTrue(models.is_effect('SHELLTAIL_CPA001_2','shell'))
        self.assertFalse(models.is_effect('HEAD_3','torpedo'))
        self.assertFalse(models.is_effect('PROPELLER_BLADES','aircraft'))

    def test_legends_aircraft_property_flags_are_not_a_property_count(self):
        blob=bytearray(0x260)
        struct.pack_into('<II6Q',blob,0,1,1,0x38,0x50,0x60,0x100,0x108,0x120)
        struct.pack_into('<Q',blob,0x78,111)
        struct.pack_into('<I',blob,0x80,1)
        struct.pack_into('<q',blob,0x88,0xa0-0x60)
        struct.pack_into('<QIII',blob,0xa0,222,333,444,555)
        struct.pack_into('<Q',blob,0x100,222);struct.pack_into('<I',blob,0x108,333)
        struct.pack_into('<Q',blob,0x120,777)
        struct.pack_into('<I',blob,0x12c,0x20001)
        struct.pack_into('<q',blob,0x138,0x230-0x120)
        struct.pack_into('<q',blob,0x140,0x238-0x120)
        struct.pack_into('<q',blob,0x160,0x240-0x120)
        struct.pack_into('<I',blob,0x230,888);blob[0x238]=3
        struct.pack_into('<Q',blob,0x240,666)
        assets=mock.Mock()
        assets.resolve_resource.side_effect=lambda i:{111:'content/gameplay/usa/aircraft/A.primitives',222:'content/gameplay/usa/aircraft/textures/A.mfm',666:'content/gameplay/usa/aircraft/textures/A_a.dds',777:'shaders/PBS.fx'}[i]
        assets.get_string.side_effect=lambda i:{333:'AIRCRAFT',444:'A.vertices',555:'A.indices',888:'diffuseMap'}[i]
        record=models.legends_visual_record(blob,assets)
        self.assertEqual(record['material_prototypes'][0]['properties'][0]['value']['path'],'content/gameplay/usa/aircraft/textures/A_a.dds')
        self.assertEqual(record['visual_prototypes'][0]['derived_geometry_path'],'content/gameplay/usa/aircraft/A.geometry')
        struct.pack_into('<Q',blob,0x18,len(blob)+100)
        with self.assertRaisesRegex(ValueError,'pointer outside'):
            models.legends_visual_record(blob,assets)

    def test_empty_effect_only_models_fail_and_can_be_retried_with_effects(self):
        doc=self.document(); doc['meshes'][0]['primitives']=[{'material':1}]
        with self.assertRaisesRegex(ValueError,'effects included'):
            models.filter_document(copy.deepcopy(doc),'aircraft',False)
        models.filter_document(doc,'aircraft',True)
        self.assertEqual(doc['materials'][0]['name'],'PROPELLERMAT_propeller')
        self.assertEqual(doc['meshes'][0]['primitives'][0]['material'],0)

    def test_cache_identity_changes_with_install_and_index_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            game=root/'game'; idx=game/'bin/123/idx/a.idx'; idx.parent.mkdir(parents=True);idx.write_bytes(b'index')
            _,first=models.source_identity('pc',game)
            idx.write_bytes(b'new index')
            _,second=models.source_identity('pc',game)
            self.assertNotEqual(first,second)

    def test_game_output_directory_is_rejected_before_any_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';game.mkdir(); request=root/'request.json'
            request.write_text(json.dumps({'source':'pc','game_dir':str(game),'output_root':str(game/'output'),'paths':[],'cache_root':str(root/'cache')}))
            with mock.patch.object(models,'Installation') as install:
                with self.assertRaisesRegex(ValueError,'outside the game'):
                    models.export_request(request)
                install.assert_not_called()

    def test_unresolved_visual_never_exports_raw_mixed_lod_as_a_model(self):
        install=mock.Mock();install.game=Path('game')
        with mock.patch.object(models,'run',return_value='No matching .visual found; exporting raw geometry.'), mock.patch.object(models.native,'load_glb') as load:
            with self.assertRaisesRegex(ValueError,'raw geometry with mixed LODs'):
                models.pc_model(install,'content/gameplay/usa/aircraft/A/A.geometry','aircraft',Path('out'),Path('work'),Path('cache'),False)
            load.assert_not_called()

    def test_missing_model_does_not_abort_next_selection_and_has_no_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';game.mkdir(); request=root/'request.json'
            path='content/gameplay/usa/projectile/torpedo/A/A.geometry'
            request.write_text(json.dumps({'source':'pc','game_dir':str(game),'output_root':str(root/'out'),'paths':[path],'cache_root':str(root/'cache')}))
            instance=mock.Mock();instance.game=game;instance.entries={};instance.token='test';instance.build=1
            with mock.patch.object(models,'Installation') as install:
                install.return_value.__enter__.return_value=instance
                result=models.export_request(request)
            self.assertFalse(result['ok']);self.assertIsNone(result['obj'])
            self.assertEqual(len(result['failures']),1);self.assertEqual(result['successes'],[])


if __name__=='__main__':
    unittest.main()
