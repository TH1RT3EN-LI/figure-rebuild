"""PDF device hairlines and preview bytes use actual operators, not declarations."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from figure_rebuild.native_pdf_preview import sample_pdf_previews
try:
    import pymupdf
except ImportError:
    pymupdf = None


@unittest.skipUnless(pymupdf, 'optional PyMuPDF dependency')
class NativePdfPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'native.pdf'
        self.dimensions = {'cx': 200 * 9525, 'cy': 100 * 9525}

    def write(self, *, width=150, height=75, pages=1, zero=True):
        with pymupdf.open() as pdf:
            for _ in range(pages):
                page = pdf.new_page(width=width, height=height)
                page.draw_line((15, 27.375), (135, 27.375), color=(.8, .8, .8), width=1)
                if zero:
                    # PyMuPDF Shape's width=0 omits the width operator and even
                    # the stroke color. Bind the genuine PDF zero-width state.
                    stream = page.get_contents()[0]
                    pdf.update_stream(stream, b'0 w\n' + pdf.xref_stream(stream))
                self.assertEqual(page.get_drawings()[0]['width'], 0 if zero else 1)
            pdf.save(self.path)

    def test_real_zero_operator_is_visible_thin_at_all_scales_and_bytes_replay(self):
        self.write(); before = self.path.read_bytes()
        outputs, receipt = sample_pdf_previews(self.path, self.dimensions)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual((outputs, receipt), sample_pdf_previews(self.path, self.dimensions))
        self.assertFalse(receipt['reference_pixels_used']); self.assertFalse(receipt['source_pixel_equivalence'])
        for scale, data in outputs.items():
            image = Image.open(io.BytesIO(data)).convert('RGB')
            self.assertEqual(image.size, (200 * scale, 100 * scale))
            changed = [y for y in range(image.height) if image.getpixel((image.width // 2, y))[0] < 255]
            self.assertTrue(changed); self.assertLessEqual(len(changed), 2)
            self.assertEqual(image.getpixel((image.width // 2, 0)), (255, 255, 255))

    def test_page_rounding_uses_actual_page_ratios_without_resizing(self):
        self.write(height=75.01)
        outputs, receipt = sample_pdf_previews(self.path, self.dimensions)
        self.assertAlmostEqual(receipt['scales']['4']['matrix'][3], 400 / receipt['page_rect_points'][3])
        for scale, data in outputs.items():
            self.assertEqual(Image.open(io.BytesIO(data)).size, (200 * scale, 100 * scale))

    def test_receipt_numeric_tokens_survive_node_json_without_rounding(self):
        self.write(height=75.01)
        _, receipt = sample_pdf_previews(self.path, self.dimensions)
        self.assertIs(type(receipt['page_rect_points'][0]), int)
        self.assertIs(type(receipt['scales']['1']['matrix'][1]), int)
        self.assertIs(type(receipt['scales']['1']['width']), int)
        self.assertIs(type(receipt['scales']['1']['alpha']), bool)
        with pymupdf.open(self.path) as pdf:
            expected = 100 / pdf[0].rect.height
        self.assertIs(type(receipt['scales']['1']['matrix'][3]), float)
        self.assertEqual(receipt['scales']['1']['matrix'][3], expected)

    def test_surface_and_integer_budgets_fail_before_opening_pdf(self):
        for dimensions in ({'cx': True, 'cy': 100}, {'cx': 0, 'cy': 100}, {'cx': 100},
                           {'cx': 20000 * 9525, 'cy': 20000 * 9525}):
            with patch.object(pymupdf, 'open', side_effect=AssertionError('allocation before planning')):
                with self.assertRaises(ValueError): sample_pdf_previews(self.path, dimensions)

    def test_extra_pages_and_geometry_cannot_be_hidden_by_preview_dimensions(self):
        self.write(pages=2)
        with self.assertRaisesRegex(ValueError, 'one'): sample_pdf_previews(self.path, self.dimensions)
        self.write(width=160)
        with self.assertRaisesRegex(ValueError, 'geometry'): sample_pdf_previews(self.path, self.dimensions)


if __name__ == '__main__': unittest.main()
