"""PDF occurrence placement regressions; compare extracted pixels to the page."""
import base64
import hashlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from figure_rebuild.pdf_images import (
    UnsupportedPdfImageError, _paint_order, _svg_images, extract_pdf_images,
)

try:
    import pymupdf
except ImportError:
    pymupdf = None


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


@unittest.skipIf(pymupdf is None, 'optional PyMuPDF source dependency is unavailable')
class PdfImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'source.pdf'

    def document(self, width=200, height=260):
        document = pymupdf.open()
        self.addCleanup(document.close)
        return document, document.new_page(width=width, height=height)

    def paint(self, document, page, image, rect, *, xref=0, clip=None, flip_x=False, flip_y=False):
        xref = page.insert_image(pymupdf.Rect(rect), stream=png(image) if not xref else None,
                                 xref=xref, keep_proportion=False)
        stream_xref = page.get_contents()[-1]
        content = document.xref_stream(stream_xref)
        if flip_x or flip_y:
            x0, y0, x1, y1 = rect
            width, height = x1-x0, y1-y0
            matrix = [(-width if flip_x else width), 0, 0, (-height if flip_y else height),
                      x1 if flip_x else x0, page.rect.height-(y0 if flip_y else y1)]
            replacement = (' '.join(str(value) for value in matrix) + ' cm').encode()
            content = re.sub(rb'[-+\d.eE ]+ cm', replacement, content)
        if clip is not None:
            x0, y0, x1, y1 = clip
            clip_command = f'q {x0} {page.rect.height-y1} {x1-x0} {y1-y0} re W n\n'.encode()
            content = clip_command + content + b'\nQ'
        document.update_stream(stream_xref, content)
        return xref

    def save(self, document):
        document.save(self.path)
        return self.path

    def assert_sample_matches_page(self, record, page, x, y, background=(255, 255, 255), tolerance=2):
        """Independently apply returned frame/crop contract at a source point."""
        image = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        frame, crop = record['visible_frame'], record['crop']
        u = crop['left'] + (x-frame['x'])/frame['width']*(1-crop['left']-crop['right'])
        v = crop['top'] + (y-frame['y'])/frame['height']*(1-crop['top']-crop['bottom'])
        rgba = image.getpixel((int(u*image.width), int(v*image.height)))
        expected = tuple(round(c*rgba[3]/255+b*(1-rgba[3]/255)) for c, b in zip(rgba[:3], background))
        reference = page.get_pixmap(alpha=False).pixel(int(x), int(y))
        self.assertLessEqual(max(abs(a-b) for a, b in zip(expected, reference)), tolerance,
                             (expected, reference, record['crop']))

    def test_clipped_tall_stripe_uses_full_transform_not_text_bbox(self):
        document, page = self.document()
        image = Image.new('RGB', (34, 236))
        for y in range(236):
            for x in range(34):
                image.putpixel((x, y), (x*7, y, 255-x*7))
        self.paint(document, page, image, (10, 10, 44, 246), clip=(43, 10, 44, 246))
        path = self.save(document)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result, = extract_pdf_images(path)
        self.assertEqual(result['asset_size'], [34, 236])
        self.assertEqual(result['full_source_box'], dict(x=10, y=10, width=34, height=236))
        self.assertEqual(result['visible_frame'], dict(x=43, y=10, width=1, height=236))
        self.assertAlmostEqual(result['crop']['left'], 33/34)
        self.assertEqual(result['provenance']['text_dict_bbox_pdf_pt'], [43, 10, 44, 246])
        self.assertFalse(result['provenance']['text_dict_bbox_used_for_placement'])
        self.assertEqual(result['fit'], 'stretch')
        self.assert_sample_matches_page(result, page, 43.5, 25.5)
        self.assert_sample_matches_page(result, page, 43.5, 225.5)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_same_xref_and_same_full_bbox_keep_occurrence_clips_and_paint_order(self):
        document, page = self.document()
        image = Image.new('RGB', (40, 60), 'red')
        image.paste('blue', (20, 0, 40, 60))
        xref = self.paint(document, page, image, (20, 20, 60, 80), clip=(20, 20, 40, 80))
        page.draw_rect(pymupdf.Rect(100, 100, 120, 120), fill=(0, 1, 0), color=None)
        self.paint(document, page, image, (20, 20, 60, 80), xref=xref, clip=(40, 20, 60, 80))
        first, second = extract_pdf_images(self.save(document))
        self.assertEqual([first['image_index'], second['image_index']], [0, 1])
        self.assertEqual([first['xref'], second['xref']], [xref, xref])
        self.assertEqual([first['paint_seqno'], second['paint_seqno']], [0, 2])
        self.assertEqual(first['asset_sha256'], second['asset_sha256'])
        self.assertEqual(first['crop']['right'], .5)
        self.assertEqual(second['crop']['left'], .5)
        self.assert_sample_matches_page(first, page, 25.5, 30.5)
        self.assert_sample_matches_page(second, page, 50.5, 30.5)

    def test_negative_transforms_bake_flips_before_crop(self):
        for flip_x, flip_y in ((True, False), (False, True), (True, True)):
            with self.subTest(flip_x=flip_x, flip_y=flip_y):
                document, page = self.document()
                image = Image.new('RGB', (40, 60), 'red')
                image.paste('blue', (20, 0, 40, 30))
                image.paste('green', (0, 30, 20, 60))
                image.paste('yellow', (20, 30, 40, 60))
                self.paint(document, page, image, (20, 20, 60, 80), flip_x=flip_x, flip_y=flip_y,
                           clip=(20, 20, 50, 70))
                path = Path(self.temporary.name)/f'flip-{flip_x}-{flip_y}.pdf'
                document.save(path)
                record, = extract_pdf_images(path)
                self.assertEqual(record['provenance']['asset_flips'], dict(horizontal=flip_x, vertical=flip_y))
                self.assertEqual(record['crop']['right'], .25)
                self.assertAlmostEqual(record['crop']['bottom'], 1/6)
                self.assert_sample_matches_page(record, page, 25.5, 25.5)
                self.assert_sample_matches_page(record, page, 45.5, 65.5)

    def test_smask_preserves_alpha_and_reference_compositing(self):
        document, page = self.document()
        page.draw_rect(page.rect, fill=(0, 0, 1), color=None)
        image = Image.new('RGBA', (40, 60), (255, 0, 0, 128))
        image.paste((0, 255, 0, 64), (20, 0, 40, 60))
        self.paint(document, page, image, (20, 20, 60, 80))
        record, = extract_pdf_images(self.save(document))
        self.assertIsInstance(record['provenance']['soft_mask_xref_candidate'], int)
        self.assertEqual(len(record['provenance']['svg_soft_mask_ids']), 1)
        asset = Image.open(io.BytesIO(record['asset_bytes']))
        self.assertEqual(asset.getpixel((0, 0))[3], 128)
        self.assertEqual(asset.getpixel((30, 0))[3], 64)
        self.assert_sample_matches_page(record, page, 25.5, 25.5, background=(0, 0, 255))
        self.assert_sample_matches_page(record, page, 45.5, 25.5, background=(0, 0, 255))

    def test_identical_rgb_resources_with_different_masks_keep_occurrence_alpha(self):
        document, page = self.document()
        first_xref = self.paint(document, page, Image.new('RGBA', (20, 20), (255, 0, 0, 64)), (10, 10, 30, 30))
        second_xref = self.paint(document, page, Image.new('RGBA', (20, 20), (255, 0, 0, 128)), (50, 10, 70, 30))
        self.assertNotEqual(first_xref, second_xref)
        first, second = extract_pdf_images(self.save(document))
        # MuPDF may assign the last RGB-digest-equivalent xref to both infos.
        # Candidate resource IDs must not drive extraction or occurrence identity.
        self.assertIn('candidate_not_occurrence_identity', first['xref_identity'])
        self.assertFalse(first['provenance']['soft_mask_resource_identity_verified'])
        self.assertEqual(Image.open(io.BytesIO(first['asset_bytes'])).getpixel((0, 0))[3], 64)
        self.assertEqual(Image.open(io.BytesIO(second['asset_bytes'])).getpixel((0, 0))[3], 128)
        self.assert_sample_matches_page(first, page, 15.5, 15.5)
        self.assert_sample_matches_page(second, page, 55.5, 15.5)

    def test_nested_clip_region_and_anisotropic_source_mapping(self):
        document, page = self.document()
        self.paint(document, page, Image.new('RGB', (10, 10), 'red'), (20, 20, 100, 120),
                   clip=(30, 30, 90, 110))
        stream = page.get_contents()[-1]
        document.update_stream(stream, b'q 40 160 40 60 re W n\n'+document.xref_stream(stream)+b'\nQ')
        record, = extract_pdf_images(self.save(document), region=[45, 45, 75, 95],
                                     source_transform=[[2, 0, -10], [0, 3, -20]])
        self.assertEqual(record['full_source_box'], dict(x=30, y=40, width=160, height=300))
        self.assertEqual(record['visible_frame'], dict(x=80, y=115, width=60, height=150))
        self.assertAlmostEqual(record['crop']['left'], 25/80)
        self.assertAlmostEqual(record['crop']['top'], 25/100)
        self.assertEqual(len(record['provenance']['active_rectangular_clips']), 2)

    def test_inline_image_is_an_occurrence_without_resource_xref(self):
        document, page = self.document()
        xref = document.get_new_xref()
        document.update_object(xref, '<<>>')
        document.update_stream(xref, b'q 20 0 0 10 10 240 cm\nBI /W 2 /H 1 /CS /RGB /BPC 8 ID '
                               + bytes([255, 0, 0, 0, 0, 255]) + b' EI\nQ')
        page.set_contents(xref)
        record, = extract_pdf_images(self.save(document))
        self.assertEqual(record['xref'], 0)
        self.assertEqual(record['asset_size'], [2, 1])
        self.assert_sample_matches_page(record, page, 12.5, 12.5)

    def test_nonrectangular_clip_and_rotated_image_fail_closed(self):
        document, page = self.document()
        self.paint(document, page, Image.new('RGB', (10, 10), 'red'), (20, 20, 80, 80))
        stream = page.get_contents()[-1]
        document.update_stream(stream, b'q 20 240 m 80 240 l 20 180 l h W n\n'
                               +document.xref_stream(stream)+b'\nQ')
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'rectangle'):
            extract_pdf_images(self.save(document))
        rotated, sheet = self.document()
        sheet.insert_image(pymupdf.Rect(20, 20, 80, 80), stream=png(Image.new('RGB', (10, 10), 'red')), rotate=90)
        path = Path(self.temporary.name)/'rotated.pdf'
        rotated.save(path)
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
            extract_pdf_images(path)

    def test_selection_uses_occurrence_index_not_xref(self):
        document, page = self.document()
        image = Image.new('RGB', (10, 10), 'red')
        xref = self.paint(document, page, image, (10, 10, 20, 20))
        self.paint(document, page, image, (50, 50, 60, 60), xref=xref)
        path = self.save(document)
        record, = extract_pdf_images(path, image_indices=[1])
        self.assertEqual(record['image_index'], 1)
        self.assertEqual(record['full_source_box']['x'], 50)
        self.assertEqual(extract_pdf_images(path, image_indices=[]), [])
        with self.assertRaises(ValueError):
            extract_pdf_images(path, image_indices=[9])
        with self.assertRaises(ValueError):
            extract_pdf_images(path, source_transform=[[0, 0, 0], [0, 1, 0]])

    def test_unselected_opacity_does_not_block_supported_occurrence(self):
        document, page = self.document()
        self.paint(document, page, Image.new('RGB', (10, 10), 'red'), (10, 10, 20, 20))
        self.paint(document, page, Image.new('RGB', (10, 10), 'blue'), (50, 10, 60, 20))
        resources = int(document.xref_get_key(page.xref, 'Resources')[1].split()[0])
        document.xref_set_key(resources, 'ExtGState', '<< /HalfAlpha << /ca 0.5 >> >>')
        stream = page.get_contents()[-1]
        document.update_stream(stream, b'q /HalfAlpha gs\n'+document.xref_stream(stream)+b'\nQ')
        path = self.save(document)
        record, = extract_pdf_images(path, image_indices=[0])
        self.assertEqual(record['image_index'], 0)
        self.assert_sample_matches_page(record, page, 15.5, 15.5)
        for selection in (None, [1]):
            with self.subTest(selection=selection), self.assertRaisesRegex(UnsupportedPdfImageError, 'compositing'):
                extract_pdf_images(path, image_indices=selection)

    def test_negative_source_mapping_flips_asset_and_crop_together(self):
        document, page = self.document()
        image = Image.new('RGB', (40, 60), 'red')
        image.paste('blue', (20, 0, 40, 60))
        self.paint(document, page, image, (20, 20, 60, 80), clip=(20, 20, 50, 80))
        record, = extract_pdf_images(self.save(document), source_transform=[[-2, 0, 200], [0, 2, 0]])
        self.assertEqual(record['full_source_box'], dict(x=80, y=40, width=80, height=120))
        self.assertEqual(record['visible_frame'], dict(x=100, y=40, width=60, height=120))
        self.assertEqual(record['crop']['left'], .25)
        self.assertEqual(record['crop']['right'], 0)
        self.assertEqual(Image.open(io.BytesIO(record['asset_bytes'])).getpixel((0, 0)), (0, 0, 255, 255))

    def test_skewed_image_fails_closed(self):
        document, page = self.document()
        self.paint(document, page, Image.new('RGB', (10, 10), 'red'), (20, 20, 80, 80))
        stream = page.get_contents()[-1]
        content = document.xref_stream(stream)
        document.update_stream(stream, re.sub(rb'[-+\d.eE ]+ cm', b'60 15 0 60 20 180 cm', content))
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
            extract_pdf_images(self.save(document))


class PdfImageIdentityTests(unittest.TestCase):
    def test_missing_nonselected_synthetic_paint_does_not_shift_selected_sequence(self):
        boxes = [[0, 0, 10, 10], [20, 20, 30, 30], [40, 40, 50, 50]]
        infos = [{'bbox': box} for box in boxes]
        paints = [(7, boxes[0]), (12, boxes[2])]
        self.assertEqual(_paint_order(infos, paints, {2}), {2: (12, boxes[2])})
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'missing or ambiguous'):
            _paint_order(infos, paints, None)

    def test_ambiguous_identical_bbox_with_missing_paint_is_rejected(self):
        box = [0, 0, 10, 10]
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'ambiguous'):
            _paint_order([{'bbox': box}, {'bbox': box}], [(7, box)], {1})

    def test_group_opacity_does_not_silently_flatten(self):
        encoded = base64.b64encode(png(Image.new('RGB', (2, 2), 'red'))).decode()
        svg = f'<svg><g opacity=".5"><image width="2" height="2" href="data:image/png;base64,{encoded}"/></g></svg>'
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'compositing'):
            _svg_images(svg)

    def test_group_mask_is_not_distributed_over_multiple_images(self):
        encoded = base64.b64encode(png(Image.new('RGB', (2, 2), 'white'))).decode()
        image = f'<image width="2" height="2" href="data:image/png;base64,{encoded}"/>'
        svg = f'<svg><defs><mask id="mask">{image}</mask></defs><g mask="url(#mask)">{image}{image}</g></svg>'
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'compositing'):
            _svg_images(svg)

    def test_selection_preserves_ancestor_compositing_rejection(self):
        encoded = base64.b64encode(png(Image.new('RGB', (2, 2), 'white'))).decode()
        image = f'<image width="2" height="2" href="data:image/png;base64,{encoded}"/>'
        for effect in ('opacity=".5"', 'mask="url(#mask)"'):
            svg = f'<svg><defs><mask id="mask">{image}</mask></defs>{image}<g {effect}>{image}{image}</g></svg>'
            with self.subTest(effect=effect):
                self.assertEqual(len(_svg_images(svg, {0})), 3)
                with self.assertRaisesRegex(UnsupportedPdfImageError, 'compositing'):
                    _svg_images(svg, {1})


if __name__ == '__main__':
    unittest.main()
