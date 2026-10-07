"""Native image forwarding against actual PDF paints and transparency groups."""
import io
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from PIL import Image

from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError
from figure_rebuild.pdf_image_render import render_native_pdf_image
from figure_rebuild.pdf_image_native import PdfImageNativeError

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


@unittest.skipIf(fitz is None, 'optional PyMuPDF source dependency missing')
class NativeOccurrenceRenderTests(unittest.TestCase):
    def fixture(self, *, background=True, affine=False, clip=False, group=True, repeated=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        document = fitz.open()
        self.addCleanup(document.close)
        sheet = document.new_page(width=120, height=100)
        if background:
            sheet.draw_rect(sheet.rect, fill=(0, 0, 1), color=None)
            sheet.insert_text((10, 93), 'independent text')
        image = Image.new('RGBA', (24, 32), (255, 0, 0, 64))
        image.paste((0, 255, 0, 192), (12, 0, 24, 32))
        sheet.insert_image(fitz.Rect(20.25, 10.25, 80.75, 70.75), stream=png(image), keep_proportion=False)
        xref = sheet.get_contents()[-1]
        data = document.xref_stream(xref)
        if affine:
            data = re.sub(rb'[-+\d.eE ]+ cm', b'50 12 -14 54 35 22 cm', data)
        if clip:
            data = b'q 20 90 m 95 90 l 20 20 l h W n\n'+data+b'\nQ'
        document.update_stream(xref, data)
        if background:
            # A later independent paint overlaps the image. It must not be
            # accidentally burned into this image's raster asset.
            sheet.draw_rect(fitz.Rect(34, 24, 39, 29), fill=(0, 0, 0), color=None)
        if repeated:
            second = Image.new('RGBA', (24, 32), (255, 0, 0, 128))
            second.paste((0, 255, 0, 32), (12, 0, 24, 32))
            sheet.insert_image(fitz.Rect(20.25, 10.25, 80.75, 70.75), stream=png(second), keep_proportion=False)
        if group:
            resources = document.xref_get_key(sheet.xref, 'Resources')[1]
            contents = b'\n'.join(document.xref_stream(x) for x in sheet.get_contents())
            form = document.get_new_xref()
            document.update_object(form, '<< /Type /XObject /Subtype /Form /BBox [0 0 120 100] '
                                   '/Resources '+resources+' /Group << /S /Transparency /CS /DeviceRGB '
                                   '/I false /K false >> >>')
            document.update_stream(form, contents)
            document.xref_set_key(sheet.xref, 'Resources', f'<< /XObject << /Figure {form} 0 R >> >>')
            document.xref_set_key(sheet.xref, 'Group', '<< /S /Transparency /CS /DeviceRGB >>')
            stream = document.get_new_xref()
            document.update_object(stream, '<< >>')
            document.update_stream(stream, b'q /Figure Do Q')
            sheet.set_contents(stream)
        else:
            form = None
        path = Path(tmp.name)/'source.pdf'
        document.save(path)
        return document, sheet, path, form

    def extract(self, path, **kwargs):
        return extract_pdf_images(path, native_occurrence_rendering=True, **kwargs)

    def rgba_at(self, record, x, y, mapping=None):
        image = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        if mapping:
            x, y = mapping[0][0]*x+mapping[0][2], mapping[1][1]*y+mapping[1][2]
        frame = record['box']
        return image.getpixel((int((x-frame['x'])*8), int((y-frame['y'])*8)))

    def test_original_groups_and_mask_preserved_without_other_paints(self):
        _, sheet, path, _ = self.fixture()
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'group'):
            extract_pdf_images(path)
        record, = self.extract(path)
        receipt = record['provenance']['native_image']
        self.assertEqual(receipt['image_paints_forwarded'], 1)
        self.assertEqual(receipt['independent_text_path_shading_other_image_paints_forwarded'], 0)
        self.assertEqual(len(receipt['groups']), 2)
        self.assertTrue(receipt['groups'][0]['isolated'])
        self.assertFalse(receipt['groups'][1]['isolated'])
        self.assertTrue(all(g['end_paint_seqno_exclusive'] > g['begin_paint_seqno'] for g in receipt['groups']))
        self.assertEqual(self.rgba_at(record, 36.5, 26.5), (255, 0, 0, 64))
        self.assertEqual(sheet.get_pixmap().pixel(36, 26), (0, 0, 0))
        for x, y in ((27.5, 35.5), (65.5, 35.5)):
            rgba = self.rgba_at(record, x, y)
            composite = tuple(round(c*rgba[3]/255+b*(1-rgba[3]/255))
                              for c, b in zip(rgba[:3], (0, 0, 255)))
            actual = sheet.get_pixmap().pixel(int(x), int(y))
            self.assertLessEqual(max(abs(a-b) for a, b in zip(composite, actual)), 2)
        self.assertEqual(self.rgba_at(record, 20.0625, 10.0625)[3], 0)
        self.assertEqual(record['box'], record['visible_frame'])
        self.assertEqual(record['box'], record['asset_source_box'])
        self.assertEqual(record['crop'], dict(left=0., top=0., right=0., bottom=0.))
        self.assertTrue(all(0 <= v < 1 for v in receipt['transparent_padding_source_px']))

    def test_native_affine_image_only_matches_independent_page_reference(self):
        _, sheet, path, _ = self.fixture(background=False, affine=True, clip=True)
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
            self.extract(path)
        record, = self.extract(path, allow_affine_rasterization=True)
        actual = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        native = sheet.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True)
        reference = Image.open(io.BytesIO(native.tobytes('png'))).convert('RGBA')
        frame = record['box']
        reference = reference.crop(tuple(int(v*8) for v in
                                         (frame['x'], frame['y'], frame['x']+frame['width'], frame['y']+frame['height'])))
        # Compare premultiplied values: large straight-RGB differences at alpha
        # near zero are not large visible differences.
        total, maximum = 0, 0
        pixels_a, pixels_b = actual.tobytes(), reference.tobytes()
        for offset in range(0, len(pixels_a), 4):
            a, b = pixels_a[offset:offset+4], pixels_b[offset:offset+4]
            pa = [v*a[3]/255 for v in a[:3]]+[a[3]]
            pb = [v*b[3]/255 for v in b[:3]]+[b[3]]
            delta = [abs(x-y) for x, y in zip(pa, pb)]
            total += sum(delta)
            maximum = max(maximum, *delta)
        self.assertLessEqual(maximum, 3)
        self.assertLess(total/(actual.width*actual.height*4), .02)

    def test_negative_anisotropic_source_mapping_and_roi_padding(self):
        _, _, path, _ = self.fixture(clip=True)
        mapping = [[-2, 0, 230], [0, 2.5, -4]]
        record, = self.extract(path, source_transform=mapping, region=[30.4, 20.4, 75.2, 65.2])
        self.assertEqual(self.rgba_at(record, 35.5, 30.5, mapping), (255, 0, 0, 64))
        self.assertEqual(self.rgba_at(record, 30.1, 30.5, mapping)[3], 0)
        self.assertEqual(self.rgba_at(record, 75.4, 30.5, mapping)[3], 0)
        self.assertEqual(record['provenance']['derived_asset_final_axis_flips'],
                         {'horizontal': False, 'vertical': False})
        receipt = record['provenance']['native_image']
        self.assertEqual(receipt['source_to_sample_matrix'][0], -16)
        self.assertEqual(receipt['source_to_sample_matrix'][3], 20)
        self.assertTrue(all(0 <= v < 1 for v in receipt['transparent_padding_source_px']))

    def test_repeated_rgb_resource_with_distinct_masks_selects_actual_paint(self):
        document, sheet, path, form = self.fixture(background=False, repeated=True)
        first, = self.extract(path, image_indices=[0])
        second, = self.extract(path, image_indices=[1])
        self.assertEqual(first['provenance']['image_info_digest'], second['provenance']['image_info_digest'])
        self.assertLess(first['paint_seqno'], second['paint_seqno'])
        self.assertEqual(self.rgba_at(first, 25.5, 30.5)[3], 64)
        # Independently suppress the first known fixture paint in memory while
        # retaining the original group/mask interpretation of the second.
        stream = document.xref_stream(form)
        self.assertEqual(stream.count(b'/fzImg0 Do'), 1)
        document.update_stream(form, stream.replace(b'/fzImg0 Do', b''))
        reference = Image.open(io.BytesIO(sheet.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True).tobytes('png')))
        self.assertEqual(self.rgba_at(second, 25.5, 30.5), reference.getpixel((204, 244)))
        self.assertNotEqual(first['provenance']['native_image']['attached_mask']['native_digest'],
                            second['provenance']['native_image']['attached_mask']['native_digest'])

    def test_unsupported_group_effects_do_not_return_pixels(self):
        for field, value in (('Group/I', 'true'), ('Group/K', 'true'), ('Group/CS', '/DeviceCMYK')):
            with self.subTest(field=field):
                document, sheet, path, form = self.fixture()
                # A nonisolated Form inherits its parent's blend colorspace;
                # use the actual page blending space for this rejection case.
                document.xref_set_key(sheet.xref if field == 'Group/CS' else form, field, value)
                changed = path.with_name('unsupported.pdf')
                document.save(changed)
                with self.assertRaisesRegex(UnsupportedPdfImageError, 'group'):
                    self.extract(changed)

    def test_native_callback_failure_and_tampered_bboxlog_fail_closed(self):
        _, sheet, _, _ = self.fixture(background=False)
        bbox = sheet.get_bboxlog()
        seqno = next(i for i, row in enumerate(bbox) if row[0] == 'fill-image')
        arguments = dict(source_transform=(1, 0, 0, 1, 0, 0), source_bounds=[20.25, 10.25, 80.75, 70.75],
                         user_clip_pdf=[0, 0, 120, 100])
        with mock.patch.object(fitz.mupdf, 'll_fz_fill_image', side_effect=RuntimeError('injected forwarding failure')):
            with self.assertRaisesRegex(PdfImageNativeError, 'injected forwarding failure'):
                render_native_pdf_image(sheet, bbox, seqno, **arguments)
        altered = list(bbox)
        kind, box = altered[seqno]
        altered[seqno] = (kind, (box[0]+1, *box[1:]))
        with self.assertRaisesRegex(PdfImageNativeError, 'sequence differs'):
            render_native_pdf_image(sheet, altered, seqno, **arguments)

    def test_multiply_and_nonunit_alpha_remain_unsupported_outside_roi(self):
        for effect in ('/BM /Multiply', '/ca .5'):
            with self.subTest(effect=effect):
                document, _, path, form = self.fixture()
                resources = int(document.xref_get_key(form, 'Resources')[1].split()[0])
                document.xref_set_key(resources, 'ExtGState', '<< /Effect << '+effect+' >> >>')
                data = document.xref_stream(form)
                document.update_stream(form, data.replace(b'/fzImg0 Do', b'/Effect gs /fzImg0 Do'))
                changed = path.with_name('unsupported-effect.pdf')
                document.save(changed)
                for region in (None, [200, 200, 201, 201]):
                    with self.subTest(region=region), self.assertRaises(UnsupportedPdfImageError):
                        self.extract(changed, region=region)

    def test_missing_native_api_fails_closed(self):
        _, sheet, _, _ = self.fixture(background=False)
        bbox = sheet.get_bboxlog()
        seqno = next(i for i, row in enumerate(bbox) if row[0] == 'fill-image')
        original = fitz.mupdf.fz_new_draw_device
        del fitz.mupdf.fz_new_draw_device
        try:
            with self.assertRaisesRegex(PdfImageNativeError, 'lacks required'):
                render_native_pdf_image(sheet, bbox, seqno, source_transform=(1, 0, 0, 1, 0, 0),
                                        source_bounds=[20.25, 10.25, 80.75, 70.75], user_clip_pdf=[0, 0, 120, 100])
        finally:
            fitz.mupdf.fz_new_draw_device = original

    def test_cmyk_jpeg_color_filtering_matches_native_page_at_same_grid(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with fitz.open() as document:
            sheet = document.new_page(width=120, height=100)
            image = Image.new('CMYK', (24, 32), (20, 170, 180, 30))
            image.paste((160, 10, 20, 40), (12, 0, 24, 32))
            encoded = io.BytesIO()
            image.save(encoded, format='JPEG')
            xref = sheet.insert_image(fitz.Rect(20.5, 10.5, 80.5, 70.5), stream=encoded.getvalue(), keep_proportion=False)
            document.xref_set_key(xref, 'Intent', '/RelativeColorimetric')
            path = Path(tmp.name)/'cmyk.pdf'
            document.save(path)
            record, = self.extract(path)
            reference = Image.open(io.BytesIO(sheet.get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True).tobytes('png')))
        actual = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        frame = record['box']
        reference = reference.convert('RGBA').crop(tuple(int(v*8) for v in
                      (frame['x'], frame['y'], frame['x']+frame['width'], frame['y']+frame['height'])))
        self.assertEqual(actual.tobytes(), reference.tobytes())
        self.assertIn('CMYK', record['provenance']['native_image']['native_colorspace'])
        self.assertEqual(record['provenance']['native_image']['color_params']['ri'], 1)

    def test_new_option_requires_an_explicit_boolean(self):
        _, _, path, _ = self.fixture()
        for value in (1, None, 'yes'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'boolean'):
                extract_pdf_images(path, native_occurrence_rendering=value)


if __name__ == '__main__':
    unittest.main()
