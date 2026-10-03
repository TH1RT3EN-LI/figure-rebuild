"""Real PDF masks are context, never ordinary get_drawings page objects."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from figure_rebuild.pdf_paint_context import (
    PdfPaintContextError, inspect_pdf_paint_context, paint_context_record,
)

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def object_ref(doc, value, content=None):
    ref = doc.get_new_xref()
    doc.update_object(ref, value)
    if content is not None:
        doc.update_stream(ref, content.encode('ascii'))
    return ref


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency missing')
class PdfPaintContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def save(self, doc, name='source.pdf'):
        path = Path(self.tmp.name) / name
        doc.save(path)
        self.addCleanup(doc.close)
        return path

    def mask_fixture(self, *, luminosity=False, partial=False, repeated=False,
                     nested=False, content_text=False):
        doc = fitz.open()
        sheet = doc.new_page(width=200, height=140)
        half = object_ref(doc, '<< /Type /ExtGState /ca 0.5 /CA 0.5 >>')
        mask = ('1 1 1 rg 20 30 40 80 re f 0 0 0 rg 60 30 40 80 re f' if luminosity
                else '0 0 0 rg 20 30 80 80 re f')
        if partial:
            mask = 'q /Half gs ' + mask + ' Q'
        resource = f'<< /ExtGState << /Half {half} 0 R >> >>'
        if nested:
            inner = object_ref(doc, '<< /Type /XObject /Subtype /Form /BBox [0 0 200 140] '
                               '/Group << /S /Transparency /CS /DeviceRGB /I true >> '
                               '/Resources << >> >>', '0 0 0 rg 25 35 40 40 re f')
            inner_gs = object_ref(doc, f'<< /SMask << /S /Alpha /G {inner} 0 R >> >>')
            resource = f'<< /ExtGState << /Inner {inner_gs} 0 R >> >>'
            mask = 'q /Inner gs ' + mask + ' Q'
        form = object_ref(doc, '<< /Type /XObject /Subtype /Form /BBox [0 0 200 140] '
                          '/Group << /S /Transparency /CS /DeviceRGB /I true >> '
                          '/Resources ' + resource + ' >>', mask)
        subtype = 'Luminosity' if luminosity else 'Alpha'
        gs = object_ref(doc, f'<< /Type /ExtGState /SMask << /S /{subtype} /G {form} 0 R '
                        '/BC [0 0 0] >> >>')
        font = object_ref(doc, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
        doc.xref_set_key(sheet.xref, 'Resources', f'<< /ExtGState << /Mask {gs} 0 R >> '
                         f'/Font << /F {font} 0 R >> >>')
        content = '1 1 1 rg 0 0 200 140 re f'
        if repeated:
            content += ' 0 0 1 rg 0 0 200 140 re f'
        if content_text:
            content = 'BT /F 12 Tf 30 80 Td (masked) Tj ET'
        stream = object_ref(doc, '<< >>', 'q /Mask gs ' + content +
                            ' Q 0 1 0 rg 130 30 40 40 re f')
        sheet.set_contents(stream)
        return doc, sheet, self.save(doc)

    def test_alpha_black_definition_is_never_a_page_fill(self):
        doc, sheet, path = self.mask_fixture()
        # Independent source rendering is white inside the alpha mask, not
        # the black get_drawings fill used only to define that mask.
        self.assertEqual(sheet.get_pixmap(alpha=True).pixel(40, 60), (255, 255, 255, 255))
        drawings = sheet.get_drawings()
        self.assertEqual(drawings[0]['fill'], (0., 0., 0.))
        report = inspect_pdf_paint_context(path, expected_bboxlog=sheet.get_bboxlog())
        records = [paint_context_record(report, d['seqno'], expected_kind='fill-path')
                   for d in drawings]
        self.assertEqual([r['role'] for r in records],
                         ['mask_definition', 'active_mask_unsupported', 'normal'])
        allowed_drawings = [d for d, r in zip(drawings, records) if r['page_object_allowed']]
        self.assertEqual([d['fill'] for d in allowed_drawings], [(0., 1., 0.)])
        self.assertTrue(records[1]['unresolved'])
        self.assertFalse(records[1]['page_object_allowed'])
        mask, = report['masks']
        self.assertEqual((mask['begin_paint_seqno'], mask['end_definition_paint_seqno'],
                          mask['end_use_paint_seqno']), (0, 1, 2))
        self.assertEqual(report['mask_reductions'], [])
        self.assertEqual(report['source_pdf_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_luminosity_and_partial_alpha_remain_unresolved(self):
        for kwargs in ({'luminosity': True}, {'partial': True}):
            with self.subTest(**kwargs):
                doc, sheet, path = self.mask_fixture(**kwargs)
                report = inspect_pdf_paint_context(path)
                masked = [r for r in report['paints'] if r['role'] == 'active_mask_unsupported']
                self.assertTrue(masked)
                self.assertTrue(all(r['unresolved'] and not r['page_object_allowed'] for r in masked))
                self.assertEqual(report['paints'][-1]['role'], 'normal')
                if kwargs.get('luminosity'):
                    self.assertEqual(sheet.get_pixmap(alpha=True).pixel(80, 60)[3], 0)
                else:
                    self.assertLessEqual(abs(sheet.get_pixmap(alpha=True).pixel(40, 60)[3] - 127.5), 3)
                path.unlink()

    def test_repeated_resource_is_distinct_mask_occurrences(self):
        _, _, path = self.mask_fixture(repeated=True)
        r = inspect_pdf_paint_context(path)
        self.assertEqual([p['role'] for p in r['paints']],
                         ['mask_definition', 'active_mask_unsupported', 'mask_definition',
                          'active_mask_unsupported', 'normal'])
        self.assertEqual([p['active_mask_ids'] for p in r['paints']], [[], [1], [], [2], []])

    def test_nested_definition_and_active_lifetimes(self):
        _, _, path = self.mask_fixture(nested=True)
        r = inspect_pdf_paint_context(path)
        self.assertEqual([p['role'] for p in r['paints']],
                         ['mask_definition', 'mask_definition', 'active_mask_unsupported', 'normal'])
        self.assertEqual(r['paints'][0]['mask_definition_ids'], [1, 2])
        self.assertEqual(r['paints'][1]['mask_definition_ids'], [1])
        self.assertEqual(r['paints'][1]['active_mask_ids'], [2])
        self.assertEqual(r['paints'][2]['active_mask_ids'], [1])
        self.assertEqual(r['paints'][-1]['active_mask_ids'], [])

    def test_masked_text_is_not_misclassified_as_normal(self):
        _, sheet, path = self.mask_fixture(content_text=True)
        r = inspect_pdf_paint_context(path)
        trace, = sheet.get_texttrace()
        item = paint_context_record(r, trace['seqno'], expected_kind='fill-text')
        self.assertEqual(item['role'], 'active_mask_unsupported')
        self.assertFalse(item['page_object_allowed'])

    def test_normal_fill_stroke_text_and_image_keep_original_sequences(self):
        from PIL import Image
        doc = fitz.open()
        sheet = doc.new_page(width=200, height=140)
        sheet.draw_rect(fitz.Rect(10, 10, 40, 40), fill=(1, 0, 0), color=(0, 0, 1))
        sheet.insert_text((10, 60), 'plain')
        stream = io.BytesIO()
        Image.new('RGB', (4, 4), 'green').save(stream, format='PNG')
        sheet.insert_image(fitz.Rect(80, 30, 100, 50), stream=stream.getvalue())
        path = self.save(doc)
        r = inspect_pdf_paint_context(path)
        self.assertEqual([(x['kind'], tuple(x['bbox_pdf_pt'])) for x in r['paints']], sheet.get_bboxlog())
        self.assertEqual([x['source_seqno'] for x in r['paints']], list(range(4)))
        self.assertTrue(all(x['role'] == 'normal' and x['page_object_allowed'] for x in r['paints']))
        self.assertEqual(r['masks'], [])

    def test_image_soft_mask_is_explicit_not_silently_unmasked(self):
        from PIL import Image
        doc = fitz.open()
        sheet = doc.new_page(width=40, height=40)
        stream = io.BytesIO()
        Image.new('RGBA', (4, 4), (255, 0, 0, 128)).save(stream, format='PNG')
        sheet.insert_image(sheet.rect, stream=stream.getvalue())
        r = inspect_pdf_paint_context(self.save(doc))
        image, = r['paints']
        self.assertEqual(image['role'], 'active_mask_unsupported')
        self.assertTrue(image['active_image_mask_clip_ids'])
        self.assertFalse(image['page_object_allowed'])

    def test_full_identity_rejects_missing_reordered_and_modified_bbox(self):
        _, sheet, path = self.mask_fixture()
        original = sheet.get_bboxlog()
        variants = [original[1:], list(reversed(original)),
                    [(original[0][0], (0, 0, 1, 1))] + original[1:]]
        for expected in variants:
            with self.assertRaisesRegex(PdfPaintContextError, 'complete source page'):
                inspect_pdf_paint_context(path, expected_bboxlog=expected)
        r = inspect_pdf_paint_context(path)
        with self.assertRaisesRegex(PdfPaintContextError, 'kind differs'):
            paint_context_record(r, 0, expected_kind='fill-image')
        with self.assertRaises(PdfPaintContextError):
            paint_context_record(r, -1, expected_kind='fill-path')

    def test_budgets_and_missing_native_api_fail_closed(self):
        _, _, path = self.mask_fixture()
        for kwargs in ({'max_paints': 1}, {'max_context_events': 1},
                       {'max_context_depth': 1}, {'max_pdf_bytes': 1}):
            with self.subTest(**kwargs), self.assertRaisesRegex(PdfPaintContextError, 'budget'):
                inspect_pdf_paint_context(path, **kwargs)
        with mock.patch.object(fitz, 'jm_bbox_fill_path', None):
            with self.assertRaises(PdfPaintContextError):
                inspect_pdf_paint_context(path)

    def test_native_callback_mismatch_never_returns_verified_context(self):
        _, _, path = self.mask_fixture()
        original = fitz.jm_bbox_fill_path

        def shifted(device, *args):
            original(device, *args)
            kind, bounds = device.result[-1]
            device.result[-1] = (kind, (bounds[0] + 1, *bounds[1:]))

        # get_bboxlog's built-in device retains its original callback. Only
        # our replay is perturbed: this must invalidate the complete identity.
        with mock.patch.object(fitz, 'jm_bbox_fill_path', shifted):
            with self.assertRaisesRegex(PdfPaintContextError, 'sequence differs'):
                inspect_pdf_paint_context(path)

    def test_context_callback_exception_aborts_explicitly(self):
        _, _, path = self.mask_fixture(luminosity=True)
        with mock.patch.object(fitz.mupdf, 'll_fz_colorspace_name',
                               side_effect=RuntimeError('injected context failure')):
            with self.assertRaisesRegex(PdfPaintContextError,
                                        'Native begin_(mask|group) callback failed: injected'):
                inspect_pdf_paint_context(path)

    def test_ordinary_clip_is_retained_without_inventing_a_mask(self):
        doc = fitz.open()
        sheet = doc.new_page(width=100, height=100)
        stream = object_ref(doc, '<< >>',
                            'q 20 20 50 50 re W n 1 0 0 rg 0 0 100 100 re f Q')
        sheet.set_contents(stream)
        report = inspect_pdf_paint_context(self.save(doc))
        paint, = report['paints']
        self.assertEqual(paint['role'], 'normal')
        self.assertEqual(paint['active_mask_ids'], [])
        self.assertEqual(len(paint['clip_ids']), 1)
        self.assertEqual(report['clips'][0]['kind'], 'clip_path')
        self.assertEqual(report['clips'][0]['end_paint_seqno'], 1)

    def test_invalid_page_rotation_and_inputs(self):
        doc, sheet, path = self.mask_fixture()
        for value in (0, -1, True, 1.5):
            with self.assertRaises(PdfPaintContextError):
                inspect_pdf_paint_context(path, page=value)
        with self.assertRaisesRegex(PdfPaintContextError, 'outside'):
            inspect_pdf_paint_context(path, page=2)
        sheet.set_rotation(90)
        rotated = Path(self.tmp.name) / 'rotated.pdf'
        doc.save(rotated)
        with self.assertRaisesRegex(PdfPaintContextError, 'unrotated'):
            inspect_pdf_paint_context(rotated)


class PdfPaintContextLazyImportTests(unittest.TestCase):
    def test_import_does_not_load_optional_native_runtime(self):
        completed = subprocess.run([sys.executable, '-c',
                                   "import sys; import figure_rebuild.pdf_paint_context; "
                                   "assert 'pymupdf' not in sys.modules"],
                                  capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
