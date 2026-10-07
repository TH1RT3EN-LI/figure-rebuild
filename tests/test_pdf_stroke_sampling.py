"""Explicit stroke sampling must preserve original clips and source identity."""
from copy import deepcopy
from collections import Counter
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageCms
from figure_rebuild.pdf_stroke_sampling import sample_pdf_stroke, PdfStrokeSamplingError
from figure_rebuild.pdf_paint_context import inspect_pdf_paint_context
try:
    import pymupdf
except ImportError:
    pymupdf = None

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class StrokeArgumentTests(unittest.TestCase):

    def test_rgb_group_opt_in_requires_boolean_before_reading_source(self):
        for value in (1, 0, 'yes', None, [], {}):
            with self.subTest(value=value), patch.object(Path, 'is_file', side_effect=AssertionError('invalid flag accessed source')), self.assertRaisesRegex(PdfStrokeSamplingError, 'boolean'):
                sample_pdf_stroke('unread.pdf', source_pdf_sha256='a' * 64, page=1,
                                  native_sequence=0, region=[0, 0, 100, 100],
                                  allow_native_rgb_group_sampling=value)

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

    def group_pdf(self, name, *, mixed=True, colorspace='DeviceRGB', nested=False,
                  knockout=False, alpha=False, blend=False):
        path = self.root / name
        with pymupdf.open() as doc:
            page = doc.new_page(width=100, height=100)
            resources = '<<>>'
            if alpha or blend:
                gs = doc.get_new_xref()
                doc.update_object(gs, '<< /Type /ExtGState ' +
                                  ('/CA 0.5 ' if alpha else '') +
                                  ('/BM /Multiply ' if blend else '') + '>>')
                resources = f'<< /ExtGState << /GS {gs} 0 R >> >>'
            cs = '/' + colorspace
            if colorspace == 'ICC':
                profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
                icc = doc.get_new_xref()
                doc.update_object(icc, '<< /N 3 /Alternate /DeviceRGB >>')
                doc.update_stream(icc, profile)
                cs = f'[/ICCBased {icc} 0 R]'
            form = doc.get_new_xref()
            group = '/Group << /S /Transparency /CS ' + cs + ' /I true /K ' + ('true' if knockout else 'false') + ' >>'
            doc.update_object(form, f'<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources {resources} {group} >>')
            target = b'q 15 15 60 60 re W n 0 G 4 w [7 4] 2 d ' + (b'/GS gs ' if alpha or blend else b'') + b'10 50 m 10 90 90 90 90 50 c 90 10 10 10 10 50 c h S Q'
            independent = b'1 0 0 rg 0 0 100 100 re f ' if mixed else b''
            after = b'0 0 1 RG 5 w 0 0 m 100 100 l S' if mixed else b''
            doc.update_stream(form, b' '.join((independent, target, after)))
            if nested:
                outer = doc.get_new_xref()
                doc.update_object(outer, f'<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /XObject << /Inner {form} 0 R >> >> {group} >>')
                doc.update_stream(outer, b'/Inner Do')
                form = outer
            doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB >>')
            doc.xref_set_key(page.xref, 'Resources', f'<< /XObject << /Figure {form} 0 R >> >>')
            contents = doc.get_new_xref()
            doc.update_object(contents, '<<>>')
            doc.update_stream(contents, b'q /Figure Do Q')
            page.set_contents(contents)
            doc.save(path)
        self.assertEqual(pymupdf.TOOLS.mupdf_warnings(reset=True), '')
        return path

    def test_strict_group_defaults_reject_isolated_child(self):
        for cs in ('DeviceRGB', 'ICC'):
            path = self.group_pdf('strict-' + cs + '.pdf', colorspace=cs)
            for kwargs in ({}, dict(allow_native_rgb_group_sampling=False)):
                with self.subTest(colorspace=cs, kwargs=kwargs), self.assertRaisesRegex(PdfStrokeSamplingError, 'group'):
                    self.sample(path, **kwargs)

    def test_explicit_rgb_group_stroke_matches_original_isolated_render(self):
        for cs in ('DeviceRGB', 'ICC'):
            with self.subTest(colorspace=cs):
                path = self.group_pdf('mixed-' + cs + '.pdf', colorspace=cs)
                reference = self.group_pdf('reference-' + cs + '.pdf', mixed=False, colorspace=cs)
                before = sha(path)
                row = self.sample(path, allow_native_rgb_group_sampling=True)
                with pymupdf.open(reference) as doc:
                    expected = doc[0].get_pixmap(matrix=pymupdf.Matrix(8, 8), alpha=True)
                actual = Image.open(BytesIO(row['asset_bytes'])).convert('RGBA')
                self.assertEqual(actual.tobytes(), expected.samples)
                self.assertEqual(sha(path), before)
                self.assertEqual(actual.getpixel((50, 50))[3], 0)
                receipt = row['provenance']
                self.assertTrue(receipt['original_active_group_identity_verified'])
                self.assertTrue(receipt['sampled_group_extension_used'])
                self.assertTrue(receipt['shared_group_split_unverified'])
                self.assertTrue(receipt['required_full_figure_visual_review'])
                self.assertIsNone(receipt['rgb_alpha_error_bound'])
                self.assertFalse(receipt['exact_group_decomposition_claimed'])
                self.assertEqual(receipt['stroke_paints_forwarded'], 1)
                self.assertEqual(receipt['independent_other_paints_forwarded'], 0)
                self.assertTrue(receipt['native_default_rgb_at_paint']['actual_device_rgb_identity'])
                self.assertEqual(len(receipt['original_group_records']), 2)
                with pymupdf.open(path) as doc:
                    counts = Counter(kind for kind, _ in doc[0].get_bboxlog())
                for group in receipt['groups']:
                    self.assertEqual(group['source_paint_counts'], dict(counts))
                    self.assertEqual(group['independent_paint_count'], 2)
                    self.assertFalse(group['colorspace_aliased_or_replaced'])
                self.assertEqual(pymupdf.TOOLS.mupdf_warnings(reset=True), '')

    def test_group_extension_retains_color_depth_and_compositing_rejections(self):
        for options in (dict(colorspace='DeviceCMYK'), dict(nested=True),
                        dict(knockout=True), dict(alpha=True), dict(blend=True)):
            path = self.group_pdf('unsupported-' + str(len(list(self.root.iterdir()))) + '.pdf', **options)
            with self.subTest(options=options), self.assertRaises(PdfStrokeSamplingError):
                self.sample(path, allow_native_rgb_group_sampling=True)

    def test_missing_group_uncertainty_or_changed_identity_is_rejected(self):
        path = self.group_pdf('receipts.pdf')
        from figure_rebuild.pdf_image_render import _render_native_pdf_paint
        for field in ('required_full_figure_visual_review', 'rgb_alpha_error_bound', 'group_identity'):
            def altered(*args, **kwargs):
                row = _render_native_pdf_paint(*args, **kwargs)
                if field == 'group_identity':
                    row['receipt']['groups'][1]['end_paint_seqno_exclusive'] += 1
                else:
                    row['receipt'].pop(field)
                return row
            with self.subTest(field=field), patch('figure_rebuild.pdf_stroke_sampling._render_native_pdf_paint', side_effect=altered), self.assertRaises(PdfStrokeSamplingError):
                self.sample(path, allow_native_rgb_group_sampling=True)

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
