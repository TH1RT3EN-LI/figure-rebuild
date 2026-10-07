"""Actual native source fixtures and bounded public API replay controls."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from figure_rebuild.pdf_source_replay import (PdfSourceDescriptor,SourceReplayPolicy,
    ReplayLimits,SourceReplayError,replay_pdf_source)
try:
    import pymupdf as fitz
except ImportError:
    fitz=None


def source_fixture(root,*,masked=False,luminosity=False):
    doc=fitz.open();page=doc.new_page(width=120,height=80)
    if not masked:
        page.insert_text((12,28),'Ab',fontsize=12,color=(0,0,0))
        if __package__:
            from .test_pptx_fidelity import png
        else:
            from test_pptx_fidelity import png
        page.insert_image(fitz.Rect(60,20,80,40),stream=png())
    else:
        def obj(text,stream=None):
            x=doc.get_new_xref();doc.update_object(x,text)
            if stream:doc.update_stream(x,stream.encode())
            return x
        form=obj('<< /Type /XObject /Subtype /Form /BBox [0 0 120 80] /Group << /S /Transparency /CS /DeviceRGB /I true >> /Resources << >> >>','0 0 0 rg 10 10 60 40 re f')
        gs=obj(f'<< /Type /ExtGState /SMask << /S /'+('Luminosity' if luminosity else 'Alpha')+f' /G {form} 0 R >> >>')
        doc.xref_set_key(page.xref,'Resources',f'<< /ExtGState << /M {gs} 0 R >> >>')
        page.set_contents(obj('<< >>','q /M gs 1 1 1 rg 0 0 120 80 re f Q'))
    path=Path(root)/('source-'+str(masked)+'-'+str(luminosity)+'.pdf');doc.save(path);doc.close()
    return PdfSourceDescriptor(path,hashlib.sha256(path.read_bytes()).hexdigest(),1,(0,0,120,80))


class ReplayPublicApiTests(unittest.TestCase):
    def test_imports_do_not_load_optional_source_library(self):
        command=[sys.executable,'-c',"import sys; import figure_rebuild.source_fidelity; assert 'pymupdf' not in sys.modules; assert 'fitz' not in sys.modules"]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_invalid_policy_budget_and_coordinates(self):
        source=PdfSourceDescriptor('absent.pdf','0'*64,1,(0,0,10,10))
        with tempfile.TemporaryDirectory() as t:
            cases=[({'limits':replace(ReplayLimits(),max_commands=True)}),({'limits':replace(ReplayLimits(),timeout_seconds=float('nan'))}),({'policy':False})]
            for kwargs in cases:
                with self.subTest(kwargs=kwargs),self.assertRaises(SourceReplayError):replay_pdf_source(source,evidence_dir=Path(t)/'bad',**kwargs)
            with self.assertRaises(SourceReplayError):replay_pdf_source(replace(source,region=(0,0,10.123,10)),evidence_dir=Path(t)/'bad')

    def test_extreme_finite_and_integer_inputs_have_validation_error_contract(self):
        source=PdfSourceDescriptor('absent.pdf','0'*64,1,(0,0,10,10))
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'bad'
            cases=[(replace(source,region=(0,0,10**400,10)),{}),
                   (replace(source,scale=10**400),{}),
                   (replace(source,region=(0,0,1e308,1e308)),{}),
                   (source,{'limits':replace(ReplayLimits(),timeout_seconds=10**400)}),
                   (source,{'policy':replace(SourceReplayPolicy(),max_clip_overhang=10**400)})]
            for request,kwargs in cases:
                with self.subTest(request=request,kwargs=kwargs),self.assertRaises(SourceReplayError):
                    replay_pdf_source(request,evidence_dir=out,**kwargs)
                self.assertFalse(out.exists())


@unittest.skipIf(fitz is None,'optional PyMuPDF unavailable')
class ActualSourceReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.source=source_fixture(self.root)
    def run_replay(self,name='evidence',**kwargs):return replay_pdf_source(self.source,evidence_dir=self.root/name,**kwargs)
    def test_actual_glyphs_and_native_image_replayed(self):
        r=self.run_replay();self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE',r)
        self.assertEqual(r['source_accounting']['unresolved'],0);self.assertEqual(r['target_objects'],3)
        self.assertFalse(r['native_to_expanded_glyph_bijection_claimed']);self.assertEqual(r['semantic_recognition'],'NOT_PROVIDED')
        self.assertTrue((self.root/'evidence/source.pdf').exists())
    def test_original_path_is_not_reopened_by_worker(self):
        original=subprocess.run
        def change_after_snapshot(*args,**kwargs):
            Path(self.source.pdf_path).write_bytes(b'changed original after one read')
            return original(*args,**kwargs)
        with mock.patch('figure_rebuild.pdf_source_replay.subprocess.run',side_effect=change_after_snapshot):r=self.run_replay()
        self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE',r)
        self.assertEqual(r['source_pdf_sha256'],self.source.pdf_sha256)
    def test_source_digest_mismatch_is_fail(self):
        r=replay_pdf_source(replace(self.source,pdf_sha256='0'*64),evidence_dir=self.root/'wrong')
        self.assertEqual(r['status'],'FAIL');self.assertFalse((self.root/'wrong/worker-result.json').exists())
    def test_real_alpha_and_luminosity_masks_unresolved(self):
        for lum in (False,True):
            source=source_fixture(self.root,masked=True,luminosity=lum)
            r=replay_pdf_source(source,evidence_dir=self.root/str(lum))
            self.assertEqual(r['status'],'UNRESOLVED',r)
            self.assertIn('general_native_soft_mask_not_supported',[v['code'] for v in r['unresolved']])
            self.assertFalse((self.root/str(lum)/'expected-scene.json').exists())
    def test_actual_command_and_image_budgets(self):
        for name,limit in [('commands',replace(ReplayLimits(),max_commands=1)),('pixels',replace(ReplayLimits(),max_image_pixels=1))]:
            r=self.run_replay(name,limits=limit);self.assertEqual(r['status'],'UNRESOLVED',r)
    def test_explicit_unimplemented_postprocessing_is_unresolved(self):
        r=self.run_replay(policy=replace(SourceReplayPolicy(),transformations=('fill-winding-normalization-v1',)))
        self.assertEqual(r['status'],'UNRESOLVED');self.assertEqual(r['unresolved'][0]['code'],'unimplemented_explicit_transformation_proof')
    def test_unknown_worker_module_never_returns_verified(self):
        with mock.patch('figure_rebuild.pdf_source_replay.subprocess.run',return_value=subprocess.CompletedProcess(['worker'],1)):
            r=self.run_replay()
        self.assertEqual(r['status'],'UNRESOLVED');self.assertEqual(r['unresolved'][0]['code'],'source_worker_failed')
    def test_fresh_reference_is_from_pristine_document(self):
        reference=self.root/'reference.png'
        with fitz.open(self.source.pdf_path) as doc:
            doc[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(reference)
        source=replace(self.source,reference_png_path=reference,reference_png_sha256=hashlib.sha256(reference.read_bytes()).hexdigest())
        r=replay_pdf_source(source,evidence_dir=self.root/'reference-bound')
        self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE',r)
        self.assertEqual(r['reference_render_basis'],'separate_pristine_document_first_render')
    def test_worker_timeout_cannot_emit_success(self):
        with mock.patch('figure_rebuild.pdf_source_replay.subprocess.run',side_effect=subprocess.TimeoutExpired(['worker'],.1)):
            r=self.run_replay()
        self.assertEqual(r['status'],'UNRESOLVED');self.assertEqual(r['unresolved'][0]['code'],'source_replay_timeout')

if __name__=='__main__':unittest.main()
