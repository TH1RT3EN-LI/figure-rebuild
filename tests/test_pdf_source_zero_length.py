"""Zero-area source paint accounting must not hide visible end caps."""
import math
import unittest

from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError

try:
    import pymupdf
except ImportError:
    pymupdf = None


def source(attributes="", data="M8 8 L8 8"):
    width = "" if "stroke-width=" in attributes else 'stroke-width="4"'
    return extract_outlined_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                                f'<path d="{data}" fill="none" stroke="black" {width} '
                                f'{attributes}/></svg>')


class ZeroLengthSourceStrokeTests(unittest.TestCase):
    def test_butt_line_skip_has_original_identity_and_reason(self):
        for join in ("miter", "round", "bevel"):
            document = source(f'stroke-linejoin="{join}"')
            result = outline_paths(document, glyph_mode="outline")
            self.assertEqual(result.objects, [])
            self.assertEqual(result.provenance, [])
            self.assertEqual(result.skipped[0]["source_id"], document.paints[0].source_id)
            self.assertEqual(result.skipped[0]["proof"], "one_moveto_one_equal_lineto_no_fill_no_dash_butt_cap")
            self.assertFalse(result.skipped[0]["source_commands_changed"])
            self.assertEqual(result.skipped[0]["source_lexical_geometry"]["exact_local_point"], ["8", "8"])
            self.assertEqual(document.paints[0].commands, (("M", (8.0, 8.0)), ("L", (8.0, 8.0))))

    def test_caps_nearby_points_and_unknown_effects_are_not_skipped(self):
        for cap in ("round", "square"):
            result = outline_paths(source(f'stroke-linecap="{cap}"'), glyph_mode="outline")
            self.assertEqual(len(result.objects), 1)
            self.assertEqual(result.skipped, [])
        endpoint = math.nextafter(8.0, math.inf)
        result = outline_paths(source(data=f'M8 8 L{endpoint!r} 8'), glyph_mode="outline")
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(result.skipped, [])
        # Both local token parsing and a large source translation can collapse
        # a real difference in binary64. Neither is a zero-length proof.
        for data, attrs in [('M8 8 L8.00000000000000000000001 8', ''),
                            ('M0 8 L1e-10 8', 'transform="matrix(1,0,0,1,1e8,0)"')]:
            document = source(attrs, data=data)
            self.assertEqual(document.paints[0].commands[0][1], document.paints[0].commands[1][1])
            result = outline_paths(document, glyph_mode="outline")
            self.assertEqual(len(result.objects), 1)
            self.assertEqual(result.skipped, [])
        for attrs in ('filter="url(#unknown)"', 'stroke-width="-1"', 'stroke-linejoin="arcs"'):
            with self.assertRaises(UnsupportedPdfPaintError):
                outline_paths(source(attrs), glyph_mode="outline")

    @unittest.skipIf(pymupdf is None, "PyMuPDF is optional")
    def test_actual_pdf_butt_is_empty_but_round_is_visible(self):
        for cap in (0, 1):
            pymupdf.TOOLS.mupdf_warnings(reset=True)
            with pymupdf.open() as doc:
                page = doc.new_page(width=16, height=16)
                xref = doc.get_new_xref()
                doc.update_object(xref, "<< >>")
                doc.update_stream(xref, f'q 4 w {cap} J 1 j 8 8 m 8 8 l S Q'.encode())
                page.set_contents(xref)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=True)
                alpha = pix.samples[3::4]
            self.assertEqual(bool(any(alpha)), cap == 1)
            self.assertFalse(pymupdf.TOOLS.mupdf_warnings(reset=True))


if __name__ == "__main__":
    unittest.main()
