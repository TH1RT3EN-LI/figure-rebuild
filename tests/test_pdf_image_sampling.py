"""Native sampling policies retain exact source geometry and declared draw scale."""
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
class NativeSamplingScaleTests(unittest.TestCase):
    def fixture(self, *, alpha=False, affine=False, group=False, repeated=False):
        image = Image.new('RGBA' if alpha else 'RGB', (41, 37))
        image.putdata([((x*17+y*3) % 256, (x*7+y*11) % 256, (x*3+y*23) % 256)
                       + (((x*13+y*19) % 256,) if alpha else ())
                       for y in range(37) for x in range(41)])
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        with fitz.open() as doc:
            sheet = doc.new_page(width=60, height=60)
            xref = sheet.insert_image(fitz.Rect(7, 8, 44, 48), stream=stream.getvalue())
            doc.xref_set_key(xref, 'Interpolate', 'true')
            mask = doc.xref_get_key(xref, 'SMask')
            if mask[0] == 'xref':
                doc.xref_set_key(int(mask[1].split()[0]), 'Interpolate', 'true')
            resources = int(doc.xref_get_key(sheet.xref, 'Resources')[1].split()[0])
            doc.xref_set_key(resources, 'XObject', f'<< /Image {xref} 0 R >>')
            matrix = '31 6 -4 33 12 9' if affine else '31 0 0 33 10.125 9.25'
            # Independent original PDF cubic clip, including fractional edges.
            content = (f'q 12.25 12.125 m 12.25 40 34 46 40.75 40 c '
                       f'40.75 13.5 l h W n q {matrix} cm /Image Do Q Q').encode()
            if repeated:
                content += b' q 18 0 0 18 40 40 cm /Image Do Q'
            doc.update_stream(sheet.get_contents()[0], content)
            if group:
                profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
                icc = doc.get_new_xref()
                doc.update_object(icc, '<< /N 3 /Alternate /DeviceRGB >>')
                doc.update_stream(icc, profile)
                form = doc.get_new_xref()
                doc.update_object(form, f'<< /Type /XObject /Subtype /Form /BBox [0 0 60 60] '
                                  f'/Resources {resources} 0 R /Group << /S /Transparency '
                                  f'/CS [/ICCBased {icc} 0 R] /I true /K false >> >>')
                doc.update_stream(form, content)
                doc.xref_set_key(sheet.xref, 'Resources', f'<< /XObject << /Figure {form} 0 R >> >>')
                doc.xref_set_key(sheet.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB >>')
                doc.update_stream(sheet.get_contents()[0], b'q /Figure Do Q')
            data = doc.tobytes()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)/'source.pdf'
        path.write_bytes(data)
        return path, data

    def extract(self, path, **kwargs):
        return extract_pdf_images(path, native_occurrence_rendering=True,
                                  allow_affine_rasterization=True,
                                  source_transform=[[.5, 0, 0], [0, .5, 0]], **kwargs)

    def assert_pristine(self, row, original, scale):
        # Render original bytes directly in a fresh document: no extraction,
        # hashes, SVG export or reconstruction of image/mask/clip primitives.
        with fitz.open(stream=original, filetype='pdf') as doc:
            reference = Image.open(io.BytesIO(doc[0].get_pixmap(
                matrix=fitz.Matrix(.5*scale, .5*scale), alpha=True).tobytes('png'))).convert('RGBA')
        b = row['box']
        reference = reference.crop(tuple(int(v*scale) for v in
                                   (b['x'], b['y'], b['x']+b['width'], b['y']+b['height'])))
        actual = Image.open(io.BytesIO(row['asset_bytes'])).convert('RGBA')
        self.assertEqual(actual.size, reference.size)
        self.assertEqual(actual.tobytes(), reference.tobytes())
        receipt = row['provenance']['native_image']
        self.assertEqual(receipt['native_sampling_scale_requested'], scale)
        self.assertEqual(receipt['native_sampling_scale_effective'], scale)
        self.assertEqual(receipt['sampling_scale'], scale)
        self.assertEqual(receipt['sampling_pitch_source_px'], 1/scale)
        self.assertEqual(receipt['raster_size'], list(actual.size))
        self.assertEqual(receipt['pixel_metadata_capture_phase'],
                         'after_all_native_draw_devices_closed_and_png_encoded')

    def test_default_and_explicit_eight_keep_the_same_png_and_geometry(self):
        path, data = self.fixture(alpha=True, affine=True)
        default, = self.extract(path)
        explicit, = self.extract(path, native_sampling_scale=8)
        self.assertEqual(default, explicit)
        self.assert_pristine(default, data, 8)

    def test_four_matches_actual_pristine_rgb_soft_mask_and_affine_clip_draw(self):
        for alpha in (False, True):
            for affine in (False, True):
                with self.subTest(alpha=alpha, affine=affine):
                    path, data = self.fixture(alpha=alpha, affine=affine)
                    four, = self.extract(path, native_sampling_scale=4)
                    eight, = self.extract(path)
                    self.assert_pristine(four, data, 4)
                    for field in ('box', 'visible_frame', 'asset_source_box', 'crop', 'full_source_box'):
                        self.assertEqual(four[field], eight[field], field)
                    self.assertEqual(bool(four['provenance']['native_image']['attached_mask']), alpha)
                    self.assertEqual(four['asset_size'], [v//2 for v in eight['asset_size']])

    def test_four_reuses_exact_original_icc_group_and_attached_mask(self):
        path, data = self.fixture(alpha=True, affine=True, group=True)
        with self.assertRaises(UnsupportedPdfImageError):
            self.extract(path, native_sampling_scale=4)
        row, = self.extract(path, native_sampling_scale=4, allow_native_rgb_group_sampling=True)
        self.assert_pristine(row, data, 4)
        receipt = row['provenance']['native_image']
        self.assertTrue(receipt['sampled_group_extension_used'])
        self.assertTrue(receipt['required_full_figure_visual_review'])
        self.assertFalse(receipt['exact_group_decomposition_claimed'])
        self.assertIsNone(receipt['rgb_alpha_error_bound'])

    def test_four_keeps_repeated_resource_occurrences_independent(self):
        path, _ = self.fixture(alpha=True, repeated=True)
        together = self.extract(path, native_sampling_scale=4)
        self.assertEqual(len(together), 2)
        for index, row in enumerate(together):
            alone, = self.extract(path, native_sampling_scale=4, image_indices=[index])
            self.assertEqual(row['asset_bytes'], alone['asset_bytes'])
            self.assertEqual(row['box'], alone['box'])
            self.assertEqual(row['paint_seqno'], alone['paint_seqno'])

    def test_scales_reject_invalid_values_and_non_native_four(self):
        for value in (True, False, 4.0, 8.0, 0, 1, 2, 16, '4', None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'integer 4 or 8'):
                    extract_pdf_images('unused.pdf', native_occurrence_rendering=True,
                                       native_sampling_scale=value)
                with self.assertRaisesRegex(ValueError, 'integer 4 or 8'):
                    render_native_pdf_image(None, [], 0, source_transform=None,
                                            source_bounds=None, user_clip_pdf=None,
                                            native_sampling_scale=value)
        with self.assertRaisesRegex(ValueError, 'requires native_occurrence_rendering'):
            extract_pdf_images('unused.pdf', native_sampling_scale=4)

    def test_pixel_and_dimension_limits_use_effective_scale_before_allocation(self):
        path, _ = self.fixture()
        with fitz.open(path) as doc:
            sheet = doc[0]
            bbox = sheet.get_bboxlog()
            seq = next(i for i, (kind, _) in enumerate(bbox) if kind == 'fill-image')
            # 64M pixels and 32768 per dimension are inclusive. Avoid allocating
            # these large buffers: reaching the draw allocation proves admission.
            cases = ((4, [0, 0, 2000, 2000], True),
                     (8, [0, 0, 2000, 2000], False),
                     (4, [0, 0, 2001, 2000], False),
                     (4, [0, 0, 8192, 1], True),
                     (4, [0, 0, 8193, 1], False),
                     (8, [0, 0, 4096, 1], True),
                     (8, [0, 0, 4097, 1], False))
            for scale, bounds, admitted in cases:
                with self.subTest(scale=scale, bounds=bounds):
                    with mock.patch.object(fitz.mupdf, 'fz_new_pixmap_with_bbox',
                                           side_effect=RuntimeError('allocation boundary reached')) as allocate:
                        message = 'allocation boundary reached' if admitted else f'{scale}x sampling budget'
                        with self.assertRaisesRegex(PdfImageNativeError, message):
                            render_native_pdf_image(sheet, bbox, seq, source_transform=(1, 0, 0, 1, 0, 0),
                                                    source_bounds=bounds, user_clip_pdf=(0, 0, 60, 60),
                                                    native_sampling_scale=scale)
                        self.assertEqual(allocate.call_count, int(admitted))


if __name__ == '__main__':
    unittest.main()
