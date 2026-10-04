"""Explicit stroke sampling must preserve original clips and source identity."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from figure_rebuild.pdf_stroke_sampling import sample_pdf_stroke, PdfStrokeSamplingError
from figure_rebuild.pdf_paint_context import inspect_pdf_paint_context
try:
    import pymupdf
except ImportError:
    pymupdf = None

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class StrokeArgumentTests(unittest.TestCase):

    def test_invalid_declared_coordinates_and_budgets_fail_before_pdf_read(self):
        base = dict(source_pdf_sha256='a' * 64, page=1, native_sequence=0, region=[0, 0, 100, 100])
        for changes in [dict(source_pdf_sha256='unknown'), dict(page=True), dict(native_sequence=True), dict(page=0), dict(native_sequence=-1), dict(scale=True), dict(scale=float('nan')), dict(scale=65), dict(scale=10**1000), dict(scale=-(10**1000)), dict(region=[0,0,10**1000,100]), dict(region=[-(10**1000),0,100,100]), dict(region=[0, 0, 0, 100]), dict(region=[0, 0, 100]), dict(region=[0, False, 100, 100]), dict(region=[0, 0, 100, float('inf')]), dict(region=[0.1, 0, 100, 100]), dict(native_sampling_scale=True), dict(native_sampling_scale=16), dict(region=[0, 0, 10000, 10000])]:
            with self.subTest(changes=changes), patch.object(Path, 'is_file', side_effect=AssertionError('invalid input accessed the filesystem')), self.assertRaises(PdfStrokeSamplingError):
                sample_pdf_stroke('/file-must-not-be-read.pdf', **{**base, **changes})

    def test_storage_failure_is_a_controlled_sampling_error(self):
        with patch.object(Path, 'is_file', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(PdfStrokeSamplingError, 'could not be read'):
                sample_pdf_stroke('/input.pdf', source_pdf_sha256='a' * 64, page=1, native_sequence=0, region=[0, 0, 100, 100])

@unittest.skipIf(pymupdf is None or pymupdf.VersionFitz != '1.28.2', 'verified optional MuPDF 1.28.2 provider unavailable')
class NativeStrokeSamplingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        pymupdf.TOOLS.mupdf_warnings(reset=True)

    def pdf(self, name, extra=True, dashed=True, masked=False):
        path = self.root / name
        with pymupdf.open() as doc:
            page = doc.new_page(width=100, height=100)
            contents = doc.get_new_xref()
            doc.update_object(contents, '<<>>')
            if masked:
                mask = doc.get_new_xref()
                doc.update_object(mask, '<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources <<>> /Group << /S /Transparency /CS /DeviceGray >> >>')
                doc.update_stream(mask, b'1 g 0 0 100 100 re f')
                gs = doc.get_new_xref()
                doc.update_object(gs, f'<< /Type /ExtGState /SMask << /S /Luminosity /G {mask} 0 R >> >>')
                doc.xref_set_key(page.xref, 'Resources', f'<< /ExtGState << /GS {gs} 0 R >> >>')
            target = b'q 15 15 60 60 re W n 0 G 4 w ' + (b'[7 4] 2 d ' if dashed else b'') + (b'/GS gs ' if masked else b'') + b'10 50 m 10 90 90 90 90 50 c 90 10 10 10 10 50 c h S Q'
            before = b'1 0 0 rg 0 0 100 100 re f ' if extra else b''
            after = b'0 0 1 RG 5 w 0 0 m 100 100 l S q 5 5 4 4 re W n 0 1 0 rg 0 0 100 100 re f Q' if extra else b''
            doc.update_stream(contents, b' '.join((before, target, after)))
            page.set_contents(contents)
            doc.save(path)
        with pymupdf.open(path) as document:
            document[0].get_bboxlog()
        self.assertEqual(pymupdf.TOOLS.mupdf_warnings(reset=True), '')
        return path

    def sample(self, path, **kwargs):
        context = inspect_pdf_paint_context(path)
        selected = next((p for p in context['paints'] if p['kind'] == 'stroke-path'))
        return sample_pdf_stroke(path, source_pdf_sha256=sha(path), page=1, native_sequence=selected['source_seqno'], region=[0, 0, 100, 100], scale=1, **kwargs)

    def test_real_curved_dash_matches_isolated_original_and_suppresses_other_paints(self):
        source = self.pdf('source.pdf')
        isolated = self.pdf('isolated.pdf', extra=False)
        before = sha(source)
        result = self.sample(source)
        actual = Image.open(BytesIO(result['asset_bytes'])).convert('RGBA')
        with pymupdf.open(isolated) as doc:
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(8, 8), alpha=True)
            expected = Image.open(BytesIO(pix.tobytes('png'))).convert('RGBA')
        self.assertEqual(actual.tobytes(), expected.tobytes())
        self.assertEqual(actual.getpixel((50, 50))[3], 0)
        self.assertEqual(sha(source), before)
        pr = result['provenance']
        self.assertEqual(pr['stroke_paints_forwarded'], 1)
        self.assertEqual(pr['independent_other_paints_forwarded'], 0)
        self.assertEqual(pr['native_stroke']['dash_len'], 2)
        self.assertEqual(result['source_kind'], 'sampled_original_native_stroke')
        self.assertFalse(result['editable'])
        self.assertTrue(pr['complete_roi_storage'])
        self.assertEqual(pymupdf.TOOLS.mupdf_warnings(reset=True), '')

    def test_native_sampling_four_retains_real_clip_and_exact_forwarding_identity(self):
        source = self.pdf('scale4.pdf', dashed=False)
        result = self.sample(source, native_sampling_scale=4)
        image = Image.open(BytesIO(result['asset_bytes']))
        self.assertEqual(image.size, (400, 400))
        self.assertEqual(result['box'], dict(x=0, y=0, width=100, height=100))
        self.assertEqual(result['provenance']['original_clip_records'][0]['kind'], 'clip_path')
        self.assertTrue(result['provenance']['full_native_identity_verified'])

    def test_wrong_source_kind_sequence_digest_and_page_remain_rejected(self):
        source = self.pdf('identity.pdf')
        for kwargs in [dict(source_pdf_sha256='a' * 64), dict(native_sequence=0), dict(native_sequence=1000), dict(page=2)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(PdfStrokeSamplingError):
                sample_pdf_stroke(source, **{**dict(source_pdf_sha256=sha(source), page=1, native_sequence=1, region=[0, 0, 100, 100]), **kwargs})

    def test_masked_native_stroke_not_emitted_unmasked(self):
        source = self.pdf('masked.pdf', masked=True)
        with self.assertRaisesRegex(PdfStrokeSamplingError, 'ordinary native stroke'):
            self.sample(source)

    def test_changed_context_clip_or_source_binding_is_rejected(self):
        source = self.pdf('context.pdf')
        real = inspect_pdf_paint_context(source)
        for field in ('matrix', 'source_sha'):
            context = deepcopy(real)
            if field == 'matrix':
                context['clips'][0]['matrix'][4] += 1
            else:
                context['source_pdf_sha256'] = 'a' * 64
            with self.subTest(field=field), patch('figure_rebuild.pdf_stroke_sampling.inspect_pdf_paint_context', return_value=context), self.assertRaises(PdfStrokeSamplingError):
                self.sample(source)

    def test_unverified_provider_rejected_without_changing_source(self):
        source = self.pdf('provider.pdf')
        before = sha(source)
        with patch.object(pymupdf, 'VersionFitz', 'unverified'), self.assertRaisesRegex(PdfStrokeSamplingError, 'not been verified'):
            self.sample(source)
        self.assertEqual(sha(source), before)
