"""Compare occurrence-only alpha clipping against real PDF reference paints."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError

try:
    import pymupdf
except ImportError:
    pymupdf = None


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


@unittest.skipIf(pymupdf is None, 'optional source dependency missing')
class PdfComplexImageClipTests(unittest.TestCase):
    def fixture(self, commands, *, rule='W', alpha=255, nested=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        document = pymupdf.open()
        self.addCleanup(document.close)
        sheet = document.new_page(width=120, height=110)
        # These are independent source paints and must never enter the asset.
        sheet.draw_rect(sheet.rect, fill=(0, 0, 1), color=None)
        sheet.insert_text((15, 105), 'independent text')
        image = Image.new('RGBA', (16, 16), (255, 0, 0, alpha))
        image.paste((0, 255, 0, alpha), (8, 0, 16, 16))
        sheet.insert_image(pymupdf.Rect(10, 10, 110, 90), stream=png(image), keep_proportion=False)
        xref = sheet.get_contents()[-1]
        stream = document.xref_stream(xref)
        if nested:
            stream = nested.encode()+b'\n'+stream
        document.update_stream(xref, b'q\n'+commands.encode()+b' '+rule.encode()+b' n\n'+stream+b'\nQ')
        path = Path(temporary.name)/'source.pdf'
        document.save(path)
        return sheet, path

    def sample(self, record, sheet, x, y, mapping=None, tolerance=3):
        mapping = mapping or [[1, 0, 0], [0, 1, 0]]
        xx, yy = mapping[0][0]*x+mapping[0][2], mapping[1][1]*y+mapping[1][2]
        image = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        box = record['box']
        px = min(image.width-1, max(0, int((xx-box['x'])/box['width']*image.width)))
        py = min(image.height-1, max(0, int((yy-box['y'])/box['height']*image.height)))
        rgba = image.getpixel((px, py))
        composite = tuple(round(v*rgba[3]/255+b*(1-rgba[3]/255)) for v, b in zip(rgba[:3], (0, 0, 255)))
        expected = sheet.get_pixmap(alpha=False).pixel(int(x), int(y))
        self.assertLessEqual(max(abs(a-b) for a, b in zip(composite, expected)), tolerance)
        return rgba

    def test_concave_and_disjoint_polygons_keep_holes_transparent_without_page_paints(self):
        sheet, path = self.fixture('10 100 m 50 100 l 50 65 l 30 65 l 30 20 l 10 20 l h 80 100 m 110 100 l 110 20 l 80 20 l h')
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        record, = extract_pdf_images(path)
        self.assertEqual(record['crop'], dict(left=0, top=0, right=0, bottom=0))
        self.assertEqual(self.sample(record, sheet, 20.5, 20.5)[3], 255)
        self.assertEqual(self.sample(record, sheet, 60.5, 40.5)[3], 0)
        self.assertEqual(self.sample(record, sheet, 40.5, 70.5)[3], 0)
        self.assertEqual(self.sample(record, sheet, 95.5, 70.5)[3], 255)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
        receipt = record['provenance']['clip_rasterization']
        self.assertFalse(receipt['source_path_or_text_paints_rasterized'])
        self.assertTrue(receipt['derived_image_pixels_resampled'])

    def test_native_cubic_nonzero_hole_is_not_replaced_by_union_or_bbox(self):
        # Outer rectangular contour, inner clockwise circle: nonzero hole.
        commands = ('10 100 m 110 100 l 110 20 l 10 20 l h '
                    '80 60 m 80 71.0457 71.0457 80 60 80 c '
                    '48.9543 80 40 71.0457 40 60 c '
                    '40 48.9543 48.9543 40 60 40 c '
                    '71.0457 40 80 48.9543 80 60 c h')
        sheet, path = self.fixture(commands)
        record, = extract_pdf_images(path)
        self.assertEqual(self.sample(record, sheet, 60.5, 50.5)[3], 0)
        self.assertEqual(self.sample(record, sheet, 20.5, 50.5)[3], 255)
        geometry = record['provenance']['active_image_clips'][0]
        self.assertEqual(sum(c[0] == 'C' for c in geometry['commands']), 4)
        self.assertEqual(geometry['rule'], 'nonzero')
        self.assertFalse(record['provenance']['clip_rasterization']['clip_geometry_approximated'])

    def test_evenodd_same_orientation_hole_and_nonzero_fill_are_distinct(self):
        commands = '10 100 m 110 100 l 110 20 l 10 20 l h 40 80 m 80 80 l 80 40 l 40 40 l h'
        for rule, expected_alpha in [('W*', 0), ('W', 255)]:
            with self.subTest(rule=rule):
                sheet, path = self.fixture(commands, rule=rule)
                record, = extract_pdf_images(path)
                self.assertEqual(self.sample(record, sheet, 60.5, 50.5)[3], expected_alpha)
                self.assertEqual(record['provenance']['active_image_clips'][0]['rule'],
                                 'evenodd' if rule == 'W*' else 'nonzero')

    def test_nested_clip_soft_alpha_and_negative_source_mapping(self):
        sheet, path = self.fixture('10 100 m 110 100 l 10 20 l h', alpha=128,
                                   nested='30 30 60 60 re W n')
        mapping = [[-2, 0, 240], [0, 3, -9]]
        record, = extract_pdf_images(path, source_transform=mapping)
        self.assertEqual(len(record['provenance']['active_image_clips']), 2)
        self.assertLessEqual(max(record['provenance']['clip_rasterization']['actual_sampling_pitch_source_px']), .125)
        self.assertAlmostEqual(self.sample(record, sheet, 40.5, 30.5, mapping)[3], 128, delta=1)
        self.assertEqual(self.sample(record, sheet, 85.5, 75.5, mapping)[3], 0)
        self.assertEqual(record['asset_source_box'], record['visible_frame'])
        self.assertNotEqual(record['asset_source_box'], record['full_source_box'])

    def test_repeated_resource_keeps_different_occurrence_clip_and_sequence(self):
        sheet, path = self.fixture('10 100 m 110 100 l 10 20 l h')
        with pymupdf.open(path) as document:
            page = document[0]
            info, = page.get_image_info(xrefs=True)
            page.insert_image(pymupdf.Rect(10, 10, 110, 90), xref=info['xref'], keep_proportion=False)
            xref = page.get_contents()[-1]
            document.update_stream(xref, b'q 110 20 m 110 100 l 10 20 l h W n\n'+document.xref_stream(xref)+b'\nQ')
            repeated = path.with_name('repeated.pdf')
            document.save(repeated)
        first, second = extract_pdf_images(repeated)
        self.assertEqual(first['xref'], second['xref'])
        self.assertEqual([first['image_index'], second['image_index']], [0, 1])
        self.assertLess(first['paint_seqno'], second['paint_seqno'])
        self.assertNotEqual(first['asset_sha256'], second['asset_sha256'])
        self.assertEqual(first['provenance']['encoded_image_sha256'], second['provenance']['encoded_image_sha256'])

    def test_negative_image_transform_is_not_reflected_twice(self):
        import re
        for reflected_matrix in (b'-100 0 0 80 110 20 cm', b'100 0 0 -80 10 100 cm'):
            with self.subTest(matrix=reflected_matrix):
                _, path = self.fixture('10 100 m 110 100 l 10 20 l h')
                with pymupdf.open(path) as document:
                    sheet = document[0]
                    xref = sheet.get_contents()[-1]
                    document.update_stream(xref, re.sub(rb'[-+\d.eE ]+ cm', reflected_matrix,
                                                       document.xref_stream(xref)))
                    reflected = path.with_name('reflected.pdf')
                    document.save(reflected)
                    record, = extract_pdf_images(reflected)
                    self.sample(record, sheet, 20.5, 20.5)
                    self.sample(record, sheet, 80.5, 20.5)
                    self.assertEqual(self.sample(record, sheet, 95.5, 80.5)[3], 0)

    def test_sampling_budget_fails_without_silently_lowering_resolution(self):
        _, path = self.fixture('10 100 m 110 100 l 10 20 l h')
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'sampling budget'):
            extract_pdf_images(path, source_transform=[[1000000, 0, 0], [0, 1000000, 0]])

    def test_sub_point_thin_strip_keeps_tall_sampling_frame(self):
        _, path = self.fixture('10 100 m 110 100 l 10 20 l h')
        record, = extract_pdf_images(path, region=[10, 10, 10.2, 90])
        self.assertEqual(record['asset_size'], [2, 640])
        self.assertLessEqual(max(record['provenance']['clip_rasterization']['actual_sampling_pitch_source_px']), .125)
        image = Image.open(io.BytesIO(record['asset_bytes']))
        self.assertEqual(image.getpixel((0, 320)), (255, 0, 0, 255))

    def test_native_cmyk_and_nonidentity_decode_match_source_pdf(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data = io.BytesIO()
        Image.new('CMYK', (12, 12), (0, 200, 150, 20)).save(data, format='JPEG')
        with pymupdf.open() as document:
            sheet = document.new_page(width=100, height=100)
            sheet.insert_image(pymupdf.Rect(10, 10, 90, 90), stream=data.getvalue())
            path = Path(temporary.name)/'cmyk.pdf'
            document.save(path)
        record, = extract_pdf_images(path)
        image = Image.open(io.BytesIO(record['asset_bytes']))
        with pymupdf.open(path) as document:
            reference = document[0].get_pixmap().pixel(50, 50)
        self.assertLessEqual(max(abs(a-b) for a, b in zip(image.getpixel((6, 6))[:3], reference)), 1)
        self.assertFalse(record['provenance']['svg_encoded_image_used_for_pixels'])
        _, rgb = self.fixture('10 100 m 110 100 l 10 20 l h')
        with pymupdf.open(rgb) as document:
            info, = document[0].get_image_info(xrefs=True)
            document.xref_set_key(info['xref'], 'Decode', '[1 0 1 0 1 0]')
            decoded = Path(temporary.name)/'nonidentity-decode.pdf'
            document.save(decoded)
        record, = extract_pdf_images(decoded)
        with pymupdf.open(decoded) as document:
            self.sample(record, document[0], 20.5, 20.5)
            self.sample(record, document[0], 80.5, 20.5)


if __name__ == '__main__':
    unittest.main()
