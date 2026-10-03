"""Exact support predicates plus independent native PDF stroke observations."""
from fractions import Fraction as F
from decimal import Decimal
import json
import math
from pathlib import Path
import unittest

from figure_rebuild.pdf_stroke_bounds import PdfStrokeBoundsError, _sqrt_upper, stroke_envelope


def render(commands, matrix, width, cap, join, miter=4, scale=4):
    try:
        import pymupdf as fitz
    except ImportError:
        raise unittest.SkipTest("Optional PyMuPDF source dependency unavailable")
    from PIL import Image
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    # PDF real-number syntax has no scientific notation.
    number = lambda n: format(Decimal(n), "f")
    lines = ["q", "1 0 0 -1 0 300 cm",
             " ".join(number(n) for n in matrix)+" cm",
             f"{number(width)} w {number(miter)} M",
             f"{dict(butt=0, round=1, square=2)[cap]} J",
             f"{dict(miter=0, round=1, bevel=2)[join]} j", "0 0 0 RG"]
    for c in commands:
        lines.append(" ".join(number(n) for p in c[1:] for n in p)
                     +" "+dict(M="m", L="l", C="c", Z="h")[c[0]])
    lines.extend(["S", "Q"])
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, "\n".join(lines).encode())
    page.set_contents(xref)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples[3::4])


def centerline_bounds(commands, matrix):
    a, b, c, d, e, f = map(F, matrix)
    points = [(a*F(p[0])+c*F(p[1])+e, b*F(p[0])+d*F(p[1])+f)
              for cmd in commands for p in cmd[1:]]
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


class PdfStrokeBoundsTests(unittest.TestCase):
    def test_upward_sqrt_exact_and_extreme_fractions(self):
        values = [F(0), F(1), F(2), F(25, 16), F(1, 2**2148),
                  F(2**2048), F(2**2048-1), F(2**2048+1),
                  F(2**1074+1, 2**2148)]
        for value in values:
            with self.subTest(value=str(value)[:40]):
                upper = _sqrt_upper(value)
                self.assertIsInstance(upper, F)
                self.assertGreaterEqual(upper*upper, value)
                if value:
                    self.assertLessEqual(upper*upper, value*(1+F(1, 2**50)))
        self.assertEqual(_sqrt_upper(F(25, 16)), F(5, 4))
        self.assertEqual(_sqrt_upper(F(1, 2**2148)), F(1, 2**1074))

    def test_round_bevel_support_is_exact_on_diagonal_matrix(self):
        for join in ("round", "bevel"):
            for cap in ("butt", "round"):
                self.assertEqual(stroke_envelope((2, 0, 0, -3, 4, 5), 10, 4,
                                                linecap=cap, linejoin=join), (F(10), F(15)))

    def test_square_cap_requires_sqrt_two_not_halfwidth_only(self):
        ex, ey = stroke_envelope((1, 0, 0, 1, 0, 0), 10, 4,
                                 linecap="square", linejoin="round")
        self.assertGreaterEqual(ex*ex, 50)
        self.assertEqual(ex, ey)
        image = render([["M", [-10, -10]], ["L", [10, 10]]],
                       (1, 0, 0, 1, 100, 100), 10, "square", "round", scale=8)
        # The diagonal square cap actually paints beyond the incorrect +5pt
        # x bound. The correct support encloses that observed extension.
        box = image.getbbox()
        self.assertGreater(box[2], 115*8)
        self.assertLessEqual(box[2], math.ceil((110+ex)*8)+1)

    def test_acute_miter_counterexample_is_not_treated_as_round_join(self):
        image = render([["M", [-10, 20]], ["L", [0, 0]], ["L", [10, 20]]],
                       (1, 0, 0, 1, 100, 100), 10, "butt", "miter", miter=10, scale=8)
        box = image.getbbox()
        self.assertLess(box[1], 95*8)
        self.assertEqual(stroke_envelope((1, 0, 0, 1, 0, 0), 10, 10, False), (F(50), F(50)))
        self.assertGreaterEqual(box[1], (100-50)*8)

    def test_legacy_miter_and_exact_rectangle_special_case(self):
        self.assertEqual(stroke_envelope((3, 4, -4, 3, 1, 2), 2, 4, True), (F(5), F(5)))
        self.assertEqual(stroke_envelope((3, 4, -4, 3, 1, 2), 2, 4, False), (F(28), F(28)))
        near = (1, 0, 0, math.nextafter(1, math.inf), 0, 0)
        result = stroke_envelope(near, 2, 4, True)
        self.assertEqual(result, (F(4), 4*F(near[3])))
        self.assertNotEqual(result, (F(1), F(1)))

    def test_arbitrary_affine_rational_direction_support_stress(self):
        matrices = [(1, .75, -.3, 2, 0, 0), (0, -2, 3, 0, 0, 0),
                    (-3, 2, 4, -.5, 0, 0), (1, 0, 0, math.nextafter(1, math.inf), 0, 0),
                    (0, 0, 0, 0, 0, 0), (1e-300, 2e-300, -3e-300, 4e-300, 0, 0),
                    (1e300, 2e300, -3e300, 4e300, 0, 0)]
        for matrix in matrices:
            for cap in ("butt", "round", "square"):
                envelope = stroke_envelope(matrix, 6, 4, linecap=cap, linejoin="bevel")
                a, b, c, d = map(F, matrix[:4])
                factor = 2 if cap == "square" else 1
                self.assertGreaterEqual(envelope[0]**2, 9*factor*(a*a+c*c))
                self.assertGreaterEqual(envelope[1]**2, 9*factor*(b*b+d*d))
                for i in range(-12, 13):
                    t = F(i, 7)
                    u, v = (1-t*t)/(1+t*t), 2*t/(1+t*t)
                    # Rational Pythagorean normal/tangent directions have
                    # exactly unit length and are orthogonal.
                    offsets = [(3*u, 3*v)]
                    if cap == "square":
                        offsets += [(3*(u-v), 3*(v+u)), (3*(u+v), 3*(v-u))]
                    for x, y in offsets:
                        self.assertLessEqual(abs(a*x+c*y), envelope[0])
                        self.assertLessEqual(abs(b*x+d*y), envelope[1])

    def test_native_affine_cap_join_stress_stays_inside_support_cells(self):
        commands = [["M", [-20, 0]], ["C", [-10, -15], [10, 15], [20, 0]],
                    ["L", [10, 20]], ["L", [-15, 10]]]
        matrices = [(1, 0, 0, 1, 150, 150), (1, .7, -.4, 1.3, 150, 150),
                    (-1.5, .3, .9, .6, 150, 150), (.01, 2, -1, .04, 150, 150),
                    (0, -2, 3, 0, 150, 150), (1, 0, .00001, 1.00001, 150, 150)]
        for matrix in matrices:
            for join in ("round", "bevel", "miter"):
                for cap in ("butt", "round", "square"):
                    with self.subTest(matrix=matrix[:4], join=join, cap=cap):
                        ex, ey = stroke_envelope(matrix, 7, 4, linecap=cap, linejoin=join)
                        x0, y0, x1, y1 = centerline_bounds(commands, matrix)
                        alpha = render(commands, matrix, 7, cap, join, scale=4)
                        actual = alpha.getbbox()
                        self.assertIsNotNone(actual)
                        # A raster pixel may intersect a mathematical boundary
                        # while its center is outside. One cell also covers
                        # the native renderer's float32 conversion in this range.
                        self.assertGreaterEqual(actual[0], math.floor((x0-ex)*4)-1)
                        self.assertGreaterEqual(actual[1], math.floor((y0-ey)*4)-1)
                        self.assertLessEqual(actual[2], math.ceil((x1+ex)*4)+1)
                        self.assertLessEqual(actual[3], math.ceil((y1+ey)*4)+1)

    def test_actual_d4rt_stroke_fits_clip_with_round_join_support(self):
        x = json.loads((Path(__file__).parent/"fixtures/d4rt-stroke248.json").read_text())
        ex, ey = stroke_envelope(x["matrix"], x["width"], x["miter_limit"], False,
                                 linecap=x["linecap"], linejoin=x["linejoin"])
        points = [tuple(map(F, p)) for c in x["world_commands"] for p in c[1:]]
        min_y = min(p[1] for p in points)
        clip_top = F(x["effective_clip"][1])
        self.assertGreater(min_y-ey-clip_top, F(36, 100))
        old = stroke_envelope(x["matrix"], x["width"], x["miter_limit"], False)
        self.assertLess(min_y-old[1], clip_top)
        self.assertEqual(ex, ey)

    def test_zero_width_means_svg_zero_geometry_not_pdf_hairline(self):
        for cap in ("butt", "round", "square"):
            self.assertEqual(stroke_envelope((2, 1, 3, 4, 5, 6), 0, 4,
                                            linecap=cap, linejoin="bevel"), (F(0), F(0)))

    def test_unknown_nonfinite_and_invalid_inputs_reject(self):
        valid = (1, 0, 0, 1, 0, 0)
        cases = [(valid, -1, 4, {}), (valid, True, 4, {}), (valid, 1, 0, {}),
                 (valid, float("nan"), 4, {}), (valid, 1, float("inf"), {}),
                 ((1, 0, 0, 1), 1, 4, {}), ((1, 0, 0, 1, 0, float("nan")), 1, 4, {}),
                 ((True, 0, 0, 1, 0, 0), 1, 4, {}), (valid, 10**1000, 4, {}),
                 (valid, 1, 4, {"linecap": "flat"}), (valid, 1, 4, {"linejoin": "arcs"}),
                 (valid, 1, 4, {"rectangle": 1})]
        for matrix, width, limit, options in cases:
            with self.subTest(options=options), self.assertRaises(PdfStrokeBoundsError):
                stroke_envelope(matrix, width, limit, **options)


if __name__ == "__main__":
    unittest.main()
