"""Whole source -> submitted scene -> actual PPT; all expectations come from source."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from figure_rebuild.source_fidelity import (PdfSourceDescriptor,SourceReplayPolicy,
    ReplayLimits,audit_source_fidelity,compare_replayed_scene,replay_pdf_source)
if __package__:
    from .test_pdf_source_replay import fitz, source_fixture
    from .test_pptx_fidelity import make_ppt
else:
    from test_pdf_source_replay import fitz, source_fixture
    from test_pptx_fidelity import make_ppt


class NoSourceTests(unittest.TestCase):
    def test_no_source_does_not_claim_empty_pass(self):
        with tempfile.TemporaryDirectory() as t:
            r=audit_source_fidelity(None,manifest_path='absent',resolved_scene_path='absent',asset_root='absent',pptx_path='absent',evidence_dir=Path(t)/'evidence')
            self.assertEqual(r['status'],'NOT_PROVIDED');self.assertEqual(r['semantic_recognition'],'NOT_PROVIDED')


@unittest.skipIf(fitz is None,'optional PyMuPDF unavailable')
class SourceFidelityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=source_fixture(self.root);self.assets=self.root/'independent-replay'
        r=replay_pdf_source(self.source,evidence_dir=self.assets);self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE',r)
        self.expected=json.loads((self.assets/'expected-scene.json').read_text())
        self.manifest={'canvas':self.expected['canvas'],'source':{'path':'fresh-source.png','sha256':self.expected['source_sha256']},'objects':self.expected['objects']}
        self.mp=self.root/'manifest.json';self.rp=self.root/'resolved.json';self.mp.write_text(json.dumps(self.manifest));self.rp.write_text(json.dumps(self.manifest))
        self.ppt=self.root/'actual.pptx';make_ppt(self.ppt,self.manifest,self.assets)
    def audit(self,name='report',**kwargs):
        return audit_source_fidelity(self.source,manifest_path=self.mp,resolved_scene_path=self.rp,asset_root=self.assets,pptx_path=self.ppt,evidence_dir=self.root/name,**kwargs)
    def test_all_four_layers_pass_without_semantic_promotion(self):
        r=self.audit();self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE',r)
        self.assertEqual(len(r['layers']),4);self.assertTrue(all(v['status']=='VERIFIED_IN_DECLARED_SCOPE' for v in r['layers'].values()))
        self.assertEqual(r['semantic_recognition'],'NOT_PROVIDED');self.assertEqual(r['visual_acceptance'],'NOT_EVALUATED')
    def test_three_real_geometry_mutations_fail_and_bool_cannot_override(self):
        for name in ('omit','glyph','image'):
            m=copy.deepcopy(self.manifest);glyph=next(o for o in m['objects'] if o['kind']=='path')
            if name=='omit':m['objects'].remove(glyph)
            elif name=='glyph':
                for cmd in glyph['commands']:
                    for values in cmd.values():
                        for key in values:
                            if key in ('x','x1','x2'):values[key]+=.125
            else:next(o for o in m['objects'] if o['kind']=='image')['box']['x']+=.25
            m['source_complete']=True;m['source_fidelity']={'status':'PASS'}
            self.mp.write_text(json.dumps(m));self.rp.write_text(json.dumps(m))
            result=self.audit(name)
            self.assertEqual(result['status'],'FAIL',result)
            self.assertEqual(result['layers']['manifest']['status'],'FAIL')
            self.assertEqual(result['layers']['resolved_scene']['status'],'FAIL')
            # Original PPT still matches independent source replay, not tampered input.
            self.assertEqual(result['layers']['actual_pptx']['status'],'VERIFIED_IN_DECLARED_SCOPE')
    def test_unimplemented_declared_transform_does_not_become_mismatch_pass(self):
        r=self.audit(policy=SourceReplayPolicy(transformations=('new-winding-proof',)))
        self.assertEqual(r['status'],'UNRESOLVED');self.assertNotIn('actual_pptx',r['layers'])
    def test_forged_image_hash_and_source_png_are_detected(self):
        image=next(o for o in self.manifest['objects'] if o['kind']=='image')
        (self.assets/image['path']).write_bytes(b'wrong actual asset')
        r=compare_replayed_scene(self.expected,self.manifest,asset_root=self.assets)
        self.assertEqual(r['status'],'FAIL');self.assertIn('source_bitmap_bytes_mismatch',[v['code'] for v in r['failures']])
    def test_strict_json_types_reject_bool_as_numeric_one(self):
        m=copy.deepcopy(self.manifest);next(o for o in m['objects'] if o['kind']=='path')['style']['opacity']=True
        r=compare_replayed_scene(self.expected,m,asset_root=self.assets)
        self.assertEqual(r['status'],'FAIL');self.assertIn('style',r['failures'][0]['fields'])
    def test_final_report_write_failure_never_returns_verified(self):
        from figure_rebuild import source_fidelity as module
        real_save=module._save
        def fail_report(path,data):
            if Path(path).name=='source-fidelity.json':raise OSError('injected evidence publication failure')
            return real_save(path,data)
        with mock.patch.object(module,'_save',side_effect=fail_report),self.assertRaises(OSError):self.audit('write-failed')
        self.assertFalse((self.root/'write-failed/source-fidelity.json').exists())
    def test_evidence_never_overwritten(self):
        self.audit()
        with self.assertRaises(FileExistsError):self.audit()


@unittest.skipUnless(os.environ.get('FIGURE_REBUILD_SGL_REPLAY_FIXTURE'),'external real SGL fixture not requested')
class RealSglFidelityRegression(unittest.TestCase):
    def test_actual_sgl_and_three_mutations(self):
        f=json.loads(Path(os.environ['FIGURE_REBUILD_SGL_REPLAY_FIXTURE']).read_text())
        source=PdfSourceDescriptor(**f['source']);original=json.loads(Path(f['manifest_path']).read_text())
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for label in ('positive','omit_glyph','glyph_shift','bitmap_shift'):
                m=copy.deepcopy(original);g=next(o for o in m['objects'] if o['kind']=='path')
                if label=='omit_glyph':m['objects'].remove(g)
                elif label=='glyph_shift':
                    for cmd in g['commands']:
                        for v in cmd.values():
                            for k in v:
                                if k in ('x','x1','x2'):v[k]+=.125
                elif label=='bitmap_shift':next(o for o in m['objects'] if o['kind']=='image')['box']['x']+=.25
                mp=root/(label+'.json');mp.write_text(json.dumps(m))
                r=audit_source_fidelity(source,manifest_path=mp,resolved_scene_path=mp,asset_root=f['asset_root'],pptx_path=f['pptx_path'],evidence_dir=root/label)
                self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE' if label=='positive' else 'FAIL',r)
                self.assertEqual(r['layers']['actual_pptx']['status'],'VERIFIED_IN_DECLARED_SCOPE')

if __name__=='__main__':unittest.main()
