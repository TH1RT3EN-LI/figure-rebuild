"""Actual PDF RGB group sampling: explicit approximation and strict boundaries."""
from collections import Counter
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageCms

from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError
from figure_rebuild.pdf_image_render import render_native_pdf_image
from figure_rebuild.pdf_image_native import PdfImageNativeError

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency missing')
class NativeRgbGroupSamplingTests(unittest.TestCase):
    def fixture(self, *, mixed=True, repeated=False, colorspace='ICC', isolated=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        doc = fitz.open()
        self.addCleanup(doc.close)
        sheet = doc.new_page(width=120, height=100)
        if mixed:
            sheet.draw_rect(fitz.Rect(1, 1, 119, 99), fill=(0, 0, 1), color=None)
            sheet.draw_line((2, 2), (118, 98), color=(0, 1, 1))
            sheet.insert_text((3, 94), 'independent text')
        for alpha in ((64, 192) if repeated else (64,)):
            im = Image.new('RGBA', (16, 16), (255, 0, 0, alpha))
            stream = io.BytesIO()
            im.save(stream, format='PNG')
            sheet.insert_image(fitz.Rect(20, 10, 80, 70), stream=stream.getvalue(), keep_proportion=False)
        if mixed:
            sheet.draw_rect(fitz.Rect(35, 25, 40, 30), fill=(0, 0, 0), color=None)
        resources = doc.xref_get_key(sheet.xref, 'Resources')[1]
        contents = b'\n'.join(doc.xref_stream(x) for x in sheet.get_contents())
        image_streams = [doc.xref_stream(x) for x in sheet.get_contents()
                         if b'/fzImg' in doc.xref_stream(x)]
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        icc = doc.get_new_xref()
        doc.update_object(icc, '<< /N 3 /Alternate /DeviceRGB >>')
        doc.update_stream(icc, profile)
        cs = f'[/ICCBased {icc} 0 R]' if colorspace == 'ICC' else colorspace
        form = doc.get_new_xref()
        doc.update_object(form, '<< /Type /XObject /Subtype /Form /BBox [0 0 120 100] '
                          '/Resources '+resources+' /Group << /S /Transparency /CS '+cs+
                          ' /I '+('true' if isolated else 'false')+' /K false >> >>')
        doc.update_stream(form, contents)
        if not hasattr(self, 'image_streams'):
            self.image_streams = {}
        self.image_streams[id(doc)] = image_streams
        doc.xref_set_key(sheet.xref, 'Resources', f'<< /XObject << /Figure {form} 0 R >> >>')
        doc.xref_set_key(sheet.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB >>')
        stream = doc.get_new_xref()
        doc.update_object(stream, '<< >>')
        doc.update_stream(stream, b'q /Figure Do Q')
        sheet.set_contents(stream)
        path = Path(tmp.name)/'source.pdf'
        doc.save(path)
        return doc, sheet, form, path, profile

    def changed(self, doc, path):
        result = path.with_name('changed.pdf')
        doc.save(result)
        return result

    def extract(self, path, **kwargs):
        return extract_pdf_images(path, native_occurrence_rendering=True,
                                  allow_native_rgb_group_sampling=True, **kwargs)

    def isolated_reference(self, doc, sheet, form, index=0):
        # Fixture resource operators are known at creation, not matched by
        # xref alias, bbox, or expected pixel color. Preserve the same ICC Form.
        doc.update_stream(form, self.image_streams[id(doc)][index])
        pixmap = sheet.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True)
        return Image.open(io.BytesIO(pixmap.tobytes('png'))).convert('RGBA').crop((160, 80, 640, 560))

    def test_flag_is_strict_boolean_and_requires_native_rendering(self):
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'boolean'):
                extract_pdf_images('unused.pdf', native_occurrence_rendering=True,
                                   allow_native_rgb_group_sampling=value)
        with self.assertRaisesRegex(ValueError, 'requires native_occurrence_rendering'):
            extract_pdf_images('unused.pdf', allow_native_rgb_group_sampling=True)

    def test_strict_defaults_still_reject_isolated_icc_child(self):
        _, _, _, path, _ = self.fixture()
        for kwargs in ({}, {'native_occurrence_rendering': True},
                       {'native_occurrence_rendering': True, 'allow_native_rgb_group_sampling': False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UnsupportedPdfImageError):
                extract_pdf_images(path, **kwargs)

    def test_real_icc_mixed_group_keeps_profile_context_full_ledger_and_review_flags(self):
        doc, sheet, form, path, profile = self.fixture()
        original = fitz.mupdf.ll_fz_begin_group
        seen = []
        def forward(device, area, cs, isolated, knockout, blend, alpha):
            if cs:
                digest = bytearray(16)
                fitz.mupdf.ll_fz_colorspace_digest(cs, fitz.mupdf.python_mutable_buffer_data(digest))
                seen.append(digest.hex())
            return original(device, area, cs, isolated, knockout, blend, alpha)
        with mock.patch.object(fitz.mupdf, 'll_fz_begin_group', side_effect=forward):
            record, = self.extract(path)
        receipt = record['provenance']['native_image']
        root, group = receipt['groups']
        self.assertTrue(root['full_page_root'])
        self.assertTrue(group['isolated'])
        self.assertTrue(group['sampled_group_extension_used'])
        native_cs = group['native_colorspace']
        self.assertEqual(native_cs['components'], 3)
        self.assertTrue(native_cs['actual_rgb_type'])
        self.assertFalse(native_cs['actual_device_rgb_identity'])
        self.assertEqual(native_cs['native_profile_md5'], hashlib.md5(profile).hexdigest())
        self.assertIn(native_cs['native_profile_md5'], seen)
        self.assertFalse(group['colorspace_aliased_or_replaced'])
        start, end = group['begin_paint_seqno'], group['end_paint_seqno_exclusive']
        self.assertEqual(group['source_paint_counts'], dict(Counter(k for k, *_ in sheet.get_bboxlog()[start:end])))
        self.assertEqual(set(group['source_paint_counts']), {'fill-image', 'fill-text', 'fill-path', 'stroke-path'})
        self.assertEqual(group['independent_paint_count'], end-start-1)
        self.assertTrue(receipt['required_full_figure_visual_review'])
        self.assertTrue(receipt['shared_group_split_unverified'])
        self.assertIsNone(receipt['rgb_alpha_error_bound'])
        self.assertFalse(receipt['exact_group_decomposition_claimed'])
        self.assertTrue(receipt['native_default_rgb_at_paint']['actual_device_rgb_identity'])
        self.assertEqual(receipt['source_pdf_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        image = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        # The later black rectangle and earlier text/background do not enter
        # this asset; only the original red image and its actual soft mask do.
        reference = self.isolated_reference(doc, sheet, form)
        self.assertEqual(image.tobytes(), reference.tobytes())

    def test_original_icc_image_only_matches_independent_native_page(self):
        _, sheet, _, path, _ = self.fixture(mixed=False)
        record, = self.extract(path)
        actual = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        source = sheet.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True)
        expected = Image.open(io.BytesIO(source.tobytes('png'))).convert('RGBA').crop((160, 80, 640, 560))
        self.assertEqual(actual.size, expected.size)
        self.assertEqual(actual.tobytes(), expected.tobytes())
        receipt = record['provenance']['native_image']
        self.assertFalse(receipt['shared_group_split_unverified'])
        self.assertTrue(receipt['required_full_figure_visual_review'])
        self.assertIsNone(receipt['rgb_alpha_error_bound'])

    def test_same_rgb_different_actual_smask_and_order_remain_bound(self):
        doc, sheet, form, path, _ = self.fixture(mixed=False, repeated=True)
        first, second = self.extract(path)
        a, b = (r['provenance']['native_image'] for r in (first, second))
        self.assertEqual(a['native_digest'], b['native_digest'])
        self.assertNotEqual(a['attached_mask']['native_digest'], b['attached_mask']['native_digest'])
        self.assertLess(first['paint_seqno'], second['paint_seqno'])
        for index, record in enumerate((first, second)):
            reference = self.isolated_reference(doc, sheet, form, index)
            self.assertEqual(Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA').tobytes(), reference.tobytes())
        self.assertEqual(a['groups'][-1]['independent_paint_counts'], {'fill-image': 1})

    def test_three_component_lab_and_cmyk_are_not_rgb(self):
        for cs in ('[/Lab << /WhitePoint [.9642 1 .8249] >>]', '/DeviceCMYK'):
            with self.subTest(cs=cs):
                _, _, _, path, _ = self.fixture(colorspace=cs)
                with self.assertRaisesRegex(UnsupportedPdfImageError, 'group'):
                    self.extract(path)

    def test_isolated_device_rgb_child_is_explicitly_sampled(self):
        _, _, _, path, _ = self.fixture(colorspace='/DeviceRGB')
        with self.assertRaises(UnsupportedPdfImageError):
            extract_pdf_images(path, native_occurrence_rendering=True)
        record, = self.extract(path)
        self.assertTrue(record['provenance']['native_image']['sampled_group_extension_used'])

    def test_knockout_group_alpha_and_blend_remain_rejected_including_outside_roi(self):
        for effect in ('knockout', '/ca .5', '/BM /Multiply'):
            with self.subTest(effect=effect):
                doc, sheet, form, path, _ = self.fixture()
                if effect == 'knockout':
                    doc.xref_set_key(form, 'Group/K', 'true')
                else:
                    doc.xref_set_key(sheet.xref, 'Resources/ExtGState', '<< /Bad << '+effect+' >> >>')
                    doc.update_stream(sheet.get_contents()[0], b'q /Bad gs /Figure Do Q')
                changed = self.changed(doc, path)
                for region in (None, [200, 200, 201, 201]):
                    with self.subTest(region=region), self.assertRaises(UnsupportedPdfImageError):
                        self.extract(changed, region=region)

    def test_second_child_depth_and_non_neutral_root_fail_closed(self):
        for case in ('depth', 'root_icc'):
            with self.subTest(case=case):
                doc, sheet, form, path, _ = self.fixture()
                if case == 'depth':
                    outer = doc.get_new_xref()
                    doc.update_object(outer, '<< /Type /XObject /Subtype /Form /BBox [0 0 120 100] '
                                      '/Resources << /XObject << /Inner '+str(form)+' 0 R >> >> '
                                      '/Group << /S /Transparency /CS /DeviceRGB /I false >> >>')
                    doc.update_stream(outer, b'/Inner Do')
                    doc.xref_set_key(sheet.xref, 'Resources/XObject/Figure', f'{outer} 0 R')
                else:
                    doc.xref_set_key(sheet.xref, 'Group/CS', doc.xref_get_key(form, 'Group/CS')[1])
                with self.assertRaisesRegex(UnsupportedPdfImageError, 'group'):
                    self.extract(self.changed(doc, path))

    def test_non_group_inner_form_cannot_change_default_rgb_after_group_entry(self):
        for sampling in (False, True):
            with self.subTest(sampling=sampling):
                doc, _, form, path, _ = self.fixture(mixed=False)
                source_icc = doc.xref_get_key(form, 'Group/CS')[1]
                if not sampling:
                    # This group is supported by the original strict route.
                    doc.xref_set_key(form, 'Group/CS', '/DeviceRGB')
                    doc.xref_set_key(form, 'Group/I', 'false')
                resources = int(doc.xref_get_key(form, 'Resources')[1].split()[0])
                inner_resources = doc.get_new_xref()
                doc.update_object(inner_resources, doc.xref_object(resources))
                doc.xref_set_key(inner_resources, 'ColorSpace', '<< /DefaultRGB '+source_icc+' >>')
                inner = doc.get_new_xref()
                doc.update_object(inner, '<< /Type /XObject /Subtype /Form /BBox [0 0 120 100] '
                                  '/Resources '+str(inner_resources)+' 0 R >>')
                doc.update_stream(inner, doc.xref_stream(form))
                doc.xref_set_key(form, 'Resources', '<< /XObject << /Inner '+str(inner)+' 0 R >> >>')
                doc.update_stream(form, b'/Inner Do')
                changed = self.changed(doc, path)
                options = dict(native_occurrence_rendering=True, allow_native_rgb_group_sampling=sampling)
                for region in (None, [200, 200, 201, 201]):
                    with self.subTest(region=region), self.assertRaisesRegex(UnsupportedPdfImageError, 'non-DeviceRGB default'):
                        extract_pdf_images(changed, region=region, **options)
                doc.xref_set_key(inner_resources, 'ColorSpace', '<< /DefaultRGB /DeviceRGB >>')
                positive = path.with_name('positive-control.pdf')
                doc.save(positive)
                record, = extract_pdf_images(positive, **options)
                receipt = record['provenance']['native_image']
                self.assertTrue(receipt['default_rgb_is_device_rgb_at_paint'])
                if sampling:
                    self.assertTrue(receipt['native_default_rgb_at_paint']['actual_device_rgb_identity'])

    def test_profile_introspection_failure_and_absent_api_return_no_asset(self):
        _, _, _, path, _ = self.fixture()
        with mock.patch.object(fitz.mupdf, 'll_fz_colorspace_digest', side_effect=RuntimeError('profile probe failed')):
            with self.assertRaisesRegex(UnsupportedPdfImageError, 'profile probe failed'):
                self.extract(path)
        original = fitz.mupdf.ll_fz_colorspace_is_rgb
        del fitz.mupdf.ll_fz_colorspace_is_rgb
        try:
            with self.assertRaisesRegex(UnsupportedPdfImageError, 'lacks required'):
                self.extract(path)
        finally:
            fitz.mupdf.ll_fz_colorspace_is_rgb = original

    def test_public_low_level_boolean_and_tampered_paint_identity(self):
        _, sheet, _, _, _ = self.fixture()
        bbox = sheet.get_bboxlog()
        seq = next(i for i, row in enumerate(bbox) if row[0] == 'fill-image')
        arguments = dict(source_transform=(1, 0, 0, 1, 0, 0), source_bounds=[20, 10, 80, 70],
                         user_clip_pdf=[0, 0, 120, 100])
        with self.assertRaises(ValueError):
            render_native_pdf_image(sheet, bbox, seq, allow_native_rgb_group_sampling=1, **arguments)
        altered = list(bbox)
        kind, box = altered[seq]
        altered[seq] = (kind, (box[0]+1, *box[1:]))
        with self.assertRaisesRegex(PdfImageNativeError, 'sequence differs'):
            render_native_pdf_image(sheet, altered, seq, allow_native_rgb_group_sampling=True, **arguments)


if __name__ == '__main__':
    unittest.main()
