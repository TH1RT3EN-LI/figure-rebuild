"""Curve support uses actual source callbacks and an independent raw PDF oracle."""
import copy
from fractions import Fraction as Q
import json
import math
from pathlib import Path
import unittest

from figure_rebuild.pdf_source import UnsupportedPdfPaintError, extract_outlined_svg, outline_paths
from figure_rebuild.pdf_stroke_bounds import PdfCurveStrokeProofError, prove_bounded_curve_stroke_support


IDENTITY = (1, 0, 0, 1, 0, 0)
FIXTURE = json.loads((Path(__file__).parent/"fixtures/sampled-native-strokes.json").read_text())


def prove(commands, **kwargs):
    return prove_bounded_curve_stroke_support(commands, IDENTITY, 2, **kwargs)


def transform(commands, matrix):
    a, b, c, d, e, f = matrix
    return [(row[0], *[(a*x+c*y+e, b*x+d*y+f) for x, y in row[1:]]) for row in commands]


class CurveStrokeSupportTests(unittest.TestCase):
    def test_actual_six_native_source_paths_have_bounded_receipts_without_changes(self):
        for case in FIXTURE["cases"]:
            with self.subTest(figure=case["figure_id"], seq=case["native_source_seqno"]):
                commands = copy.deepcopy(case["commands"])
                state = case["native_stroke_state"]
                receipt = prove_bounded_curve_stroke_support(commands, IDENTITY, state["linewidth"],
                    state["miterlimit"], dasharray=case["dash_array"] or "none", bounding_depth=6)
                self.assertEqual(commands, case["commands"])
                self.assertFalse(receipt["source_commands_changed"])
                self.assertFalse(receipt["curve_approximation"])
                self.assertLessEqual(receipt["subdivision_nodes"], 4096)
                json.dumps(receipt, allow_nan=False)

    def test_actual_source_clips_distinguish_contained_and_partial_strokes(self):
        contained = []
        partial = []
        for case in FIXTURE["cases"]:
            st = case["native_stroke_state"]
            p = prove_bounded_curve_stroke_support(case["commands"], IDENTITY, st["linewidth"],
                st["miterlimit"], dasharray=case["dash_array"] or "none", bounding_depth=6)
            bb = list(map(Q, p["stroke_bounds_exact_rationals"]))
            a, b, c, d, e, f = map(Q, case["matrix"])
            self.assertEqual((b, c), (0, 0))
            bb = [a*bb[0]+e, d*bb[1]+f, a*bb[2]+e, d*bb[3]+f]
            inside = True
            for clip in case["actual_native_rectangular_clips"]:
                x0, y0, x1, y1 = map(Q, clip["rectangle_pdf_pt_exact"])
                inside &= bb[0] >= x0 and bb[1] >= y0 and bb[2] <= x1 and bb[3] <= y1
            (contained if inside else partial).append(case["native_source_seqno"])
        self.assertEqual(contained, [242, 289, 43])
        self.assertEqual(partial, [148, 172, 22])

    def test_control_hull_is_tightened_by_exact_subdivision(self):
        commands = [("M", (20, 20)), ("C", (20, 100), (100, 100), (100, 20))]
        p = prove(commands, bounding_depth=4)
        self.assertEqual(list(map(Q, p["centerline_bounds_exact_rationals"])), [20, 20, 100, 80])
        self.assertEqual(list(map(Q, p["stroke_bounds_exact_rationals"])), [19, 19, 101, 81])
        self.assertEqual(commands[1][2], (100, 100))

    def test_endpoint_zero_derivative_has_finite_one_sided_tangent(self):
        commands = [("M", (20, 20)), ("C", (20, 20), (30, 30), (40, 30)),
                    ("C", (50, 30), (60, 20), (60, 20))]
        p = prove(commands)
        self.assertEqual(len(p["cubic_regularity"]), 2)
        self.assertEqual(Q(p["joins"][0]["miter_factor_squared_upper_exact"]), 1)

    def test_regular_turning_derivative_needs_multiple_certified_intervals(self):
        # Real last cubic of source seq 22; the full derivative control hull
        # cannot establish a common positive projection without subdivision.
        case = next(c for c in FIXTURE["cases"] if c["native_source_seqno"] == 22)
        commands = [["M", case["commands"][-2][-1]], case["commands"][-1]]
        p = prove(commands)
        intervals = p["cubic_regularity"][0]["intervals"]
        self.assertGreater(len(intervals), 1)
        self.assertEqual(Q(intervals[0]["parameter_interval_exact"][0]), 0)
        self.assertEqual(Q(intervals[-1]["parameter_interval_exact"][1]), 1)
        for previous, following in zip(intervals, intervals[1:]):
            self.assertEqual(previous["parameter_interval_exact"][1], following["parameter_interval_exact"][0])

    def test_interior_stationary_derivative_and_fully_constant_curve_refused(self):
        for commands in [[("M", (0, 0)), ("C", (1, 0), (0, 0), (1, 0))],
                         [("M", (2, 3)), ("C", (2, 3), (2, 3), (2, 3))]]:
            with self.assertRaises(PdfCurveStrokeProofError):
                prove(commands)

    def test_miter_support_is_local_to_join_and_does_not_expand_distant_extrema(self):
        p = prove([("M", (0, 0)), ("L", (100, 0)), ("L", (100, 10))], miter_limit=8)
        bounds = list(map(Q, p["stroke_bounds_exact_rationals"]))
        self.assertEqual((bounds[0], bounds[3]), (-1, 11))
        self.assertGreater(bounds[2], 101)
        self.assertLess(bounds[2], Q(102))
        self.assertEqual(Q(p["joins"][0]["miter_factor_squared_upper_exact"]), 2)

    def test_reverse_join_is_bounded_by_declared_miter_cutoff(self):
        p = prove([("M", (20, 20)), ("L", (30, 20)), ("L", (20, 21))], miter_limit=3)
        self.assertEqual(Q(p["joins"][0]["local_radius_exact"]), 3)
        self.assertEqual(list(map(Q, p["stroke_bounds_exact_rationals"])), [19, 17, 33, 23])

    def test_round_and_bevel_joins_and_positive_dashes_stay_in_tube(self):
        commands = [("M", (20, 20)), ("L", (30, 20)), ("L", (20, 21))]
        for join in ("round", "bevel"):
            for cap in ("butt", "round"):
                solid = prove(commands, linejoin=join, linecap=cap)
                dashed = prove(commands, linejoin=join, linecap=cap, dasharray=[2, 1, 3])
                self.assertEqual(solid["stroke_bounds_exact_rationals"], dashed["stroke_bounds_exact_rationals"])
                self.assertTrue(dashed["dash_support_phase_independent"])

    def test_matrix_scales_width_and_never_reapplies_source_affine(self):
        commands = [("M", (10, 10)), ("L", (20, 10))]
        for matrix in [(-1, 0, 0, 1, 50, 0), (0, -1, 1, 0, 0, 50), (3, 4, -4, 3, 60, 20)]:
            output = transform(commands, matrix)
            p = prove_bounded_curve_stroke_support(output, matrix, 2)
            xs, ys = [r[1][0] for r in output], [r[1][1] for r in output]
            radius = Q(p["tube_radius_exact"])
            self.assertEqual(list(map(Q, p["stroke_bounds_exact_rationals"])),
                             [min(xs)-radius, min(ys)-radius, max(xs)+radius, max(ys)+radius])
            self.assertFalse(p["source_affine_applied_again"])

    def test_exact_dyadic_radius_preserves_boundary_contact(self):
        width = math.nextafter(1., math.inf)
        p = prove_bounded_curve_stroke_support([("M", (0, 0)), ("L", (10, 0))], IDENTITY, width)
        self.assertEqual(Q(p["tube_radius_exact"]), Q(width)/2)
        self.assertEqual(Q(p["stroke_bounds_exact_rationals"][3]), Q(width)/2)

    def test_butt_zero_line_and_move_only_subpath_are_not_emitted_or_changed(self):
        commands = [("M", (10, 10)), ("L", (10, 10)), ("L", (20, 10)), ("M", (999, 999))]
        original = copy.deepcopy(commands)
        p = prove(commands)
        self.assertEqual(commands, original)
        self.assertEqual(p["source_segments"], 1)
        self.assertEqual(len(p["nonpainting_empty_butt_subpaths"]), 1)
        self.assertEqual(list(map(Q, p["stroke_bounds_exact_rationals"])), [9, 9, 21, 11])
        with self.assertRaises(PdfCurveStrokeProofError):
            prove(commands, linecap="round")

    def test_implicit_closure_join_is_not_omitted(self):
        p = prove([("M", (20, 20)), ("L", (30, 20)), ("L", (30, 30)), ("Z",)])
        self.assertEqual(p["source_segments"], 3)
        self.assertEqual(len(p["joins"]), 3)

    def test_resource_and_format_budgets_fail_closed(self):
        commands = [("M", (20, 20)), ("C", (20, 40), (40, 40), (40, 20))]
        for kwargs in [{"command_budget": 1}, {"segment_budget": 129}, {"subpath_budget": True},
                       {"subdivision_budget": 2}, {"subdivision_budget": 4097}, {"bounding_depth": 9}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(PdfCurveStrokeProofError):
                prove(commands, **kwargs)

    def test_malformed_numbers_geometry_styles_and_transforms_are_refused(self):
        good = [("M", (20, 20)), ("L", (30, 20))]
        for commands in [[("L", (1, 2))], [("M", (1, 2)), ("Q", (3, 4), (5, 6))],
                         [("M", (1, math.nan)), ("L", (3, 4))], [("M", (1, 2)), ("Z",)],
                         [("M", (1, 2)), ("L", (3, 4)), ("Z",), ("L", (5, 6))],
                         [("M", (True, 2)), ("L", (3, 4))], [("M", (10**400, 2)), ("L", (3, 4))]]:
            with self.assertRaises(PdfCurveStrokeProofError):
                prove(commands)
        for kwargs in [{"linecap": "square"}, {"linejoin": "arcs"}, {"fill": "black"},
                       {"dasharray": []}, {"dasharray": [1, 0]}, {"dasharray": [1, -1]},
                       {"dasharray": [1, math.inf]}, {"dasharray": "1 2"}]:
            with self.assertRaises(PdfCurveStrokeProofError):
                prove(good, **kwargs)
        for matrix in [(1, 0, 0, 1.000000000001, 0, 0), (0, 0, 0, 0, 0, 0), (1, 1, 0, 1, 0, 0)]:
            with self.assertRaises(PdfCurveStrokeProofError):
                prove_bounded_curve_stroke_support(good, matrix, 2)
        for width, miter in [(0, 4), (-1, 4), (2, .5), (math.inf, 4)]:
            with self.assertRaises(PdfCurveStrokeProofError):
                prove_bounded_curve_stroke_support(good, IDENTITY, width, miter)

    def test_exact_support_is_outward_even_for_extreme_scales(self):
        for scale in [1e-150, 1e150]:
            p = prove_bounded_curve_stroke_support([("M", (scale, scale)), ("L", (2*scale, scale))],
                                                 IDENTITY, scale)
            exact = list(map(Q, p["stroke_bounds_exact_rationals"]))
            for i, value in enumerate(p["stroke_bounds_outward"]):
                self.assertTrue(math.isfinite(value))
                self.assertTrue(Q(value) >= exact[i] if i >= 2 else Q(value) <= exact[i])

    def test_new_explicit_receipt_does_not_bypass_automatic_clip_policy(self):
        svg = '<svg width="100" height="100"><defs><clipPath id="c"><rect x="20" y="20" width="20" height="20"/></clipPath></defs><path clip-path="url(#c)" stroke="black" stroke-width="2" fill="none" d="M10 30C20 10 40 10 50 30"/></svg>'
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(extract_outlined_svg(svg), glyph_mode="outline")

    def test_independent_raw_pdf_painted_pixels_fit_certified_bounds(self):
        try:
            import pymupdf as fitz
        except ImportError:
            self.skipTest("Optional original PDF dependency unavailable")
        examples = [[("M", (60, 60)), ("C", (60, 140), (140, 140), (140, 60))],
                    [("M", (60, 60)), ("L", (120, 60)), ("L", (120, 120))],
                    [("M", (60, 60)), ("L", (120, 60)), ("L", (65, 65))]]
        for commands in examples:
            for join in ("miter", "round", "bevel"):
                for cap in ("butt", "round"):
                    p = prove_bounded_curve_stroke_support(commands, IDENTITY, 10, 3, linejoin=join, linecap=cap)
                    bb = list(map(float, map(Q, p["stroke_bounds_exact_rationals"])))
                    with fitz.open() as doc:
                        page = doc.new_page(width=200, height=200)
                        tokens = ["q 1 0 0 -1 0 200 cm 10 w 3 M",
                                  str(("butt", "round").index(cap))+" J",
                                  str(("miter", "round", "bevel").index(join))+" j 0 G"]
                        for row in commands:
                            tokens.append(" ".join(str(v) for pt in row[1:] for v in pt)+" "+{"M": "m", "L": "l", "C": "c"}[row[0]])
                        tokens.append("S Q")
                        xref = doc.get_new_xref()
                        doc.update_object(xref, "<<>>")
                        doc.update_stream(xref, "\n".join(tokens).encode())
                        page.set_contents(xref)
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csGRAY)
                        touched = [(i % pix.width, i // pix.width) for i, value in enumerate(pix.samples) if value < 254]
                        self.assertTrue(touched)
                        for x, y in touched:
                            self.assertGreaterEqual((x+1)/2, bb[0])
                            self.assertGreaterEqual((y+1)/2, bb[1])
                            self.assertLessEqual(x/2, bb[2])
                            self.assertLessEqual(y/2, bb[3])


if __name__ == "__main__":
    unittest.main()
