"""Preflight validation and frozen build inputs, without authoring a PPT."""
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from PIL import Image

from figure_rebuild import cli

class BuildInputChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.job=Path(self.tmp.name)/'job';self.job.mkdir()
        Image.new('RGB',(200,100),'white').save(self.job/'original.png')
        self.m={'schema_version':1,'id':'input-check','revision':1,'source':{'path':'original.png','sha256':cli.digest(self.job/'original.png'),'kind':'user_original','width':200,'height':100},'canvas':{'width':200,'height':100},'recognition':{'provider':'calling_host','status':'reviewed'},'objects':[{'id':'line','kind':'path','commands':[{'moveTo':{'x':10,'y':10}},{'lineTo':{'x':50,'y':50}}],'style':{'stroke':'#000000','stroke_width':2}}]}
        self.m['recognition'].update(reviewed_revision=1, reviewed_digest=cli.content_digest(self.m))
        self.manifest=self.job/'manifest.json';cli.save(self.manifest,self.m)
        from argparse import Namespace
        self.args=Namespace(manifest=str(self.manifest),output=None,base=None,base_sha256=None,slide_id=None,placement=None,replace_id=None,marker_already_started=True)
        self.rt={'node':sys.executable,'python':sys.executable,'node_modules':'/tmp','presentation_skill':'/tmp'}
    def tearDown(self):self.tmp.cleanup()
    def test_base_only_options_fail_before_run_and_authoring(self):
        self.args.placement=[0,0,100,100]
        with patch.object(cli,'runtime',return_value=self.rt),patch.object(cli.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'placement requires'):cli.build(self.args)
            run.assert_not_called();self.assertFalse((self.job/'build').exists())
    def test_build_uses_validated_snapshot_not_later_manifest_edits(self):
        def on_run(*args,**kwargs):
            config=json.loads((self.job/'build/run-001/build-config.json').read_text())
            changed=dict(self.m,revision=2);cli.save(self.manifest,changed)
            snapshot=Path(config['manifest'])
            self.assertNotEqual(snapshot,self.manifest)
            self.assertEqual(json.loads(snapshot.read_text())['revision'],1)
            self.assertEqual(Path(config['job']),self.job.resolve())
            frozen=Path(config['asset_root'])/'original.png'
            self.assertEqual(cli.digest(frozen),self.m['source']['sha256'])
            (self.job/'original.png').write_bytes(b'changed after reservation')
            self.assertEqual(cli.digest(frozen),self.m['source']['sha256'])
        with patch.object(cli,'runtime',return_value=self.rt),patch.object(cli.subprocess,'run',side_effect=on_run),redirect_stdout(io.StringIO()):cli.build(self.args)
    def test_failed_input_does_not_create_run(self):
        self.m['recognition']['status']='needs_review';cli.save(self.manifest,self.m)
        with patch.object(cli.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'review'):cli.build(self.args)
            run.assert_not_called();self.assertFalse((self.job/'build').exists())

    def test_modified_review_fails_before_runtime_or_run(self):
        self.m['objects'][0]['commands'][1]['lineTo']['x']=51;cli.save(self.manifest,self.m)
        with patch.object(cli,'runtime') as rt,patch.object(cli.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'stale'):cli.build(self.args)
            rt.assert_not_called();run.assert_not_called();self.assertFalse((self.job/'build').exists())

    def test_review_preserves_provenance_and_binds_the_exact_content(self):
        from argparse import Namespace
        self.m['recognition']['notes']='This diagram is approximate, not experimental data.'
        cli.save(self.manifest,self.m)
        with redirect_stdout(io.StringIO()):cli.review(Namespace(manifest=str(self.manifest),note='All arrows checked.'))
        reviewed=json.loads(self.manifest.read_text())
        self.assertEqual(reviewed['recognition']['notes'],self.m['recognition']['notes'])
        self.assertEqual(reviewed['recognition']['review_note'],'All arrows checked.')
        self.assertEqual(cli.verify_review(reviewed),cli.content_digest(reviewed))

    def build_without_authoring(self):
        with patch.object(cli,'runtime',return_value=self.rt),patch.object(cli.subprocess,'run'),redirect_stdout(io.StringIO()):
            cli.build(self.args)

    def test_same_revision_rebuild_allowed_only_for_identical_content(self):
        self.build_without_authoring()
        self.args.output=str(self.job/'exports/repeated.pptx')
        self.build_without_authoring()
        self.assertTrue((self.job/'build/run-002/manifest-snapshot.json').exists())
        self.m['objects'][0]['commands'][1]['lineTo']['x']=52
        self.m['recognition']['reviewed_digest']=cli.content_digest(self.m)
        cli.save(self.manifest,self.m)
        with patch.object(cli,'runtime',return_value=self.rt),patch.object(cli.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'increment revision'):cli.build(self.args)
            run.assert_not_called();self.assertFalse((self.job/'build/run-003').exists())

    def test_changed_content_with_incremented_revision_can_build(self):
        self.build_without_authoring()
        self.m['revision']=2;self.m['objects'][0]['commands'][1]['lineTo']['x']=52
        self.m['recognition'].update(reviewed_revision=2,reviewed_digest=cli.content_digest(self.m))
        cli.save(self.manifest,self.m)
        self.build_without_authoring()
        saved=json.loads((self.job/'build/run-002/manifest-snapshot.json').read_text())
        self.assertEqual(saved['revision'],2)

    def test_atomic_json_failure_leaves_previous_document(self):
        original=self.manifest.read_bytes()
        with patch.object(cli.os,'replace',side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError,'disk error'):cli.save(self.manifest,{'replacement':True})
        self.assertEqual(self.manifest.read_bytes(),original)
        self.assertFalse(list(self.job.glob('.manifest.json-*.tmp')))

    def test_all_raster_assets_are_frozen_without_modifying_the_originals(self):
        Image.new('RGB',(20,10),'navy').save(self.job/'photo.png')
        original=(self.job/'original.png').read_bytes();photo=(self.job/'photo.png').read_bytes()
        self.m['objects'].append({'id':'photo','kind':'image','path':'photo.png','sha256':cli.digest(self.job/'photo.png'),'editable':False,'box':{'x':10,'y':10,'width':20,'height':10}})
        self.m['recognition']['reviewed_digest']=cli.content_digest(self.m);cli.save(self.manifest,self.m)
        self.build_without_authoring()
        root=self.job/'build/run-001/assets'
        self.assertEqual((root/'original.png').read_bytes(),original)
        self.assertEqual((root/'photo.png').read_bytes(),photo)
        self.assertEqual((self.job/'original.png').read_bytes(),original)
        self.assertEqual((self.job/'photo.png').read_bytes(),photo)
        receipt=json.loads((self.job/'build/run-001/asset-snapshot.json').read_text())
        self.assertEqual(len(receipt['assets']),2)

    def test_mutation_during_asset_copy_fails_before_authoring(self):
        def changed_copy(original,target):Path(target).write_bytes(b'incomplete copy')
        with patch.object(cli,'runtime',return_value=self.rt),patch.object(cli.shutil,'copy2',side_effect=changed_copy),patch.object(cli.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'during snapshot'):cli.build(self.args)
            run.assert_not_called();self.assertFalse((self.job/'build/run-001/manifest-snapshot.json').exists())


class RuntimeChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.font=self.root/'test-font.ttf';self.font.write_bytes(b'test font bytes; table parsing belongs to doctor')
        self.modules=self.root/'node_modules';self.modules.mkdir()
        self.adapter=self.root/'presentations';self.adapter.mkdir()
        self.profile=self.root/'font-profile.json'
        self.profile.write_text(json.dumps({'fonts':{'family':'Test Family','regular':{'path':'test-font.ttf','face_index':0},'bold':{'path':'test-font.ttf','face_index':0}}}))
        self.config=self.root/'external-config/runtime.json'
        from argparse import Namespace
        self.args=Namespace(node=sys.executable,python=sys.executable,node_modules=str(self.modules),presentation_skill=str(self.adapter),font_profile=str(self.profile))

    def configure(self):
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True),redirect_stdout(io.StringIO()):
            cli.configure(self.args)
        return json.loads(self.config.read_text())

    def test_external_configuration_and_relative_font_paths(self):
        data=self.configure()
        self.assertEqual(data['fonts']['regular']['path'],str(self.font.resolve()))
        self.assertEqual(data['fonts']['bold']['sha256'],cli.digest(self.font))
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True),patch.object(cli,'preflight') as check:
            loaded=cli.runtime()
            check.assert_called_once_with(loaded)

    def test_virtualenv_executable_symlink_path_is_not_dereferenced(self):
        invoked=self.root/'.venv/bin/python';invoked.parent.mkdir(parents=True)
        try:invoked.symlink_to(sys.executable)
        except OSError as exc:self.skipTest('Executable symlinks unavailable: '+str(exc))
        self.args.python=str(invoked);self.args.node=str(invoked)
        data=self.configure()
        self.assertEqual(data['python'],str(invoked.absolute()))
        self.assertEqual(data['node'],str(invoked.absolute()))
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True),patch.object(cli,'preflight'):
            loaded=cli.runtime()
        self.assertEqual(loaded['python'],str(invoked.absolute()))
        self.assertNotEqual(loaded['python'],str(invoked.resolve()))

    def test_font_hash_and_face_type_are_checked_without_external_dependencies(self):
        for face in ({'path':'test-font.ttf','face_index':True},{'path':'test-font.ttf','face_index':0,'sha256':'0'*64},None):
            with self.subTest(face=face),self.assertRaises(ValueError):
                cli.font_profile({'family':'Test Family','regular':face,'bold':{'path':str(self.font),'face_index':0}},self.root)

    def test_uppercase_declared_hash_is_verified_and_normalized(self):
        checksum=cli.digest(self.font)
        fonts=cli.font_profile({'family':'Test Family','regular':{'path':str(self.font),'face_index':0,'sha256':checksum.upper()},'bold':{'path':str(self.font),'face_index':0}},self.root)
        self.assertEqual(fonts['regular']['sha256'],checksum)

    def test_non_executable_or_wrong_directory_fails_before_config_is_saved(self):
        self.args.node=str(self.font)
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True):
            with self.assertRaisesRegex(ValueError,'executable'):cli.configure(self.args)
        self.assertFalse(self.config.exists())

    def test_doctor_reports_dependency_failure_with_nonzero_status(self):
        self.configure()
        out=io.StringIO()
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True),patch.object(cli,'preflight',side_effect=ValueError('fontTools unavailable')),redirect_stdout(out):
            status=cli.doctor(None)
        self.assertEqual(status,1)
        self.assertIn('fontTools',json.loads(out.getvalue())['errors'][0])

    def test_malformed_runtime_json_returns_a_clear_value_error(self):
        self.config.parent.mkdir();self.config.write_text('[]')
        with patch.dict(cli.os.environ,{'FIGURE_REBUILD_CONFIG':str(self.config)},clear=True):
            with self.assertRaisesRegex(ValueError,'JSON record'):cli.runtime()

if __name__=='__main__':unittest.main()
