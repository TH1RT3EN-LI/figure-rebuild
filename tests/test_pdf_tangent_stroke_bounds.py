"""Exact support certificates, bounded fallbacks and real PDF counterexamples."""
import copy
from decimal import Decimal
from fractions import Fraction as F
import json
import math
import subprocess
import sys
import unittest
from unittest import mock

from figure_rebuild.pdf_source import (
    UnsupportedPdfPaintError, extract_outlined_svg, outline_paths,
)
from figure_rebuild.pdf_stroke_bounds import (
    PdfTangentStrokeProofError, prove_tangent_stroke_support, stroke_envelope,
)


IDENTITY = (1, 0, 0, 1, 0, 0)
ROUNDED = [("M", (80, 60)), ("L", (140, 60)),
           ("C", (151, 60), (160, 69), (160, 80)), ("L", (160, 140)),
           ("C", (160, 151), (151, 160), (140, 160)), ("L", (80, 160)),
           ("C", (69, 160), (60, 151), (60, 140)), ("L", (60, 80)),
           ("C", (60, 69), (69, 60), (80, 60)), ("Z",)]

# Exact source XML excerpt from BYOL ccf-2020-07-f02, original PDF page 4.
# Full source PDF SHA d6a3c810134ffdddabe827689499cf5b4802227a381fe09795f637263af2f97d.
# Full outlined SVG SHA 4af0b0846a1b1c8b2b7710dff6251b9289cc8c2f850ca7092554b12373b72fb6.
# This is the real svg-paint-0-1-6 geometry and clip; fixture-local IDs differ.
BYOL_PATH = '''<path transform="matrix(.078535009,0,0,-.078535009,127.801,206.013)"
 stroke-width="7.9701" stroke-linecap="butt" stroke-miterlimit="10"
 stroke-linejoin="miter" fill="none" stroke="#000000"
 d="M351.879 908.531H72.9219C50.9102 908.531 33.0703 890.688 33.0703 868.68V589.723C33.0703 567.711 50.9102 549.871 72.9219 549.871H351.879C373.887 549.871 391.73 567.711 391.73 589.723V868.68C391.73 890.688 373.887 908.531 351.879 908.531ZM33.0703 549.871"/>'''
BYOL = '''<svg width="612" height="792"><defs><clipPath id="clip_1">
<path transform="matrix(.78535,0,0,-.78535,127.801,206.013)" d="M0 0H453.81V170.64H0Z"/>
</clipPath></defs><g clip-path="url(#clip_1)">'''+BYOL_PATH+'''</g></svg>'''


def transformed(commands, matrix):
    a, b, c, d, e, f = matrix
    return [(cmd[0], *[(a*x+c*y+e, b*x+d*y+f) for x, y in cmd[1:]]) for cmd in commands]


def path_data(commands):
    return " ".join(cmd[0]+" ".join(str(v) for p in cmd[1:] for v in p) for cmd in commands)


def source(commands, *, matrix=IDENTITY, extra="", cap="butt", width=10):
    return extract_outlined_svg('<svg width="400" height="400"><path fill="none" '
        f'stroke="black" stroke-width="{width}" stroke-linejoin="miter" stroke-miterlimit="10" '
        f'stroke-linecap="{cap}" transform="matrix({" ".join(map(str, matrix))})" '
        f'd="{path_data(commands)}" {extra}/></svg>')


def pdf_samples(commands, width, clip=None, scale=4):
    """Direct raw PDF oracle; no project/SVG/PPT renderer is involved."""
    try:
        import pymupdf as fitz
    except ImportError:
        raise unittest.SkipTest("Optional PyMuPDF source dependency unavailable")
    number = lambda value: format(Decimal(value), "f")
    with fitz.open() as doc:
        page = doc.new_page(width=320, height=320)
        tokens = ["q", "1 0 0 -1 0 320 cm"]
        if clip is not None:
            x0, y0, x1, y1 = clip
            tokens.append(" ".join(number(v) for v in (x0, y0, x1-x0, y1-y0))+" re W n")
        tokens.extend([number(width)+" w", "0 J", "0 j", "20 M", "0 G"])
        for cmd in commands:
            tokens.append(" ".join(number(v) for p in cmd[1:] for v in p)
                          +" "+{"M": "m", "L": "l", "C": "c", "Z": "h"}[cmd[0]])
        tokens.extend(["S", "Q"])
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, "\n".join(tokens).encode())
        page.set_contents(xref)
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).samples


class TangentStrokeProofTests(unittest.TestCase):
    def test_all_closed_joins_and_regular_cubics_have_exact_witnesses(self):
        original = copy.deepcopy(ROUNDED)
        proof = prove_tangent_stroke_support(ROUNDED, IDENTITY, 10, 10)
        self.assertEqual(ROUNDED, original)
        self.assertEqual(proof["axis_support_exact_rationals"], ["5", "5"])
        self.assertEqual(proof["segment_count"], 8)
        self.assertEqual(len(proof["joins"]), 8)
        self.assertEqual(sum(j["closure_join"] for j in proof["joins"]), 1)
        for join in proof["joins"]:
            self.assertEqual(F(join["cross_exact"]), 0)
            self.assertGreater(F(join["dot_exact"]), 0)
        cubics = [s for s in proof["subpaths"][0]["segments"] if s["kind"] == "C"]
        self.assertEqual(len(cubics), 4)
        self.assertTrue(all(all(F(v) > 0 for v in s["regularity"]["positive_projection_coefficients_exact"])
                            for s in cubics))
        json.dumps(proof, allow_nan=False)

    def test_exact_similarities_include_reflection_rotation_and_scale(self):
        for matrix, radius in [((-1, 0, 0, 1, 300, 0), 5),
                               ((0, -1, 1, 0, 0, 300), 5),
                               ((3, 4, -4, 3, 700, -200), 25)]:
            with self.subTest(matrix=matrix):
                commands = transformed(ROUNDED, matrix)
                proof = prove_tangent_stroke_support(commands, matrix, 10)
                self.assertEqual(proof["axis_support_exact_rationals"], [str(radius)]*2)
                self.assertFalse(proof["source_affine_applied_again"])

    def test_butt_round_caps_and_trailing_move_keep_support(self):
        for cap in ("butt", "round"):
            commands = [("M", (0, 0)), ("L", (10, 0)), ("L", (20, 0)), ("M", (30, 5))]
            proof = prove_tangent_stroke_support(commands, IDENTITY, 2, linecap=cap)
            self.assertEqual(proof["support_bounds_exact"], ["-1", "-1", "31", "6"])
            self.assertEqual(proof["empty_move_only_subpaths"], 1)
            self.assertEqual(len(proof["joins"]), 1)

    def test_exact_implicit_closing_line_is_checked(self):
        # Move begins halfway along the top straight segment. Z completes
        # that segment; both its incoming join and closing join are smooth.
        commands = [("M", (100, 60)), *ROUNDED[1:-1], ("Z",)]
        proof = prove_tangent_stroke_support(commands, IDENTITY, 10)
        self.assertEqual(proof["segment_count"], 9)
        self.assertTrue(proof["subpaths"][0]["segments"][-1]["implicit_close"])
        self.assertEqual(len(proof["joins"]), 9)
        commands[0] = ("M", (100, 61))
        with self.assertRaises(PdfTangentStrokeProofError):
            prove_tangent_stroke_support(commands, IDENTITY, 10)

    def test_crossing_subpaths_do_not_invent_a_join(self):
        commands = [("M", (0, 0)), ("L", (20, 20)), ("M", (0, 20)), ("L", (20, 0))]
        proof = prove_tangent_stroke_support(commands, IDENTITY, 2)
        self.assertEqual(proof["joins"], [])
        self.assertEqual(proof["subpath_count"], 2)
        self.assertEqual(proof["support_bounds_exact"], ["-1", "-1", "21", "21"])

    def test_no_epsilon_admits_near_tangency_even_at_subnormal_scale(self):
        for deviation in (1e-12, math.ulp(0.0)):
            commands = [("M", (0, 0)), ("L", (1, 0)), ("L", (2, deviation))]
            with self.subTest(deviation=deviation), self.assertRaisesRegex(
                    PdfTangentStrokeProofError, "join_not_exactly_tangent"):
                prove_tangent_stroke_support(commands, IDENTITY, 1)

    def test_reverse_cusp_zero_derivatives_and_interior_reversal_reject(self):
        cases = [([("M", (0, 0)), ("L", (1, 0)), ("L", (0, 0))], "reverse_or_cusp"),
                 ([("M", (0, 0)), ("L", (0, 0))], "zero_line"),
                 ([("M", (0, 0)), ("C", (0, 0), (1, 0), (2, 0))], "zero_endpoint"),
                 ([("M", (0, 0)), ("C", (1, 0), (2, 0), (2, 0))], "zero_endpoint"),
                 ([("M", (0, 0)), ("C", (2, 0), (-2, 0), (0, 0))], "regular_halfplane"),
                 ([("M", (0, 0)), ("Z",)], "empty_closed")]
        for commands, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(PdfTangentStrokeProofError, reason):
                prove_tangent_stroke_support(commands, IDENTITY, 1)

    def test_nonuniform_shear_near_similarity_and_styles_reject(self):
        matrices = [(2, 0, 0, 1, 0, 0), (1, 0, 1, 1, 0, 0),
                    (1, 0, 0, math.nextafter(1, math.inf), 0, 0), (0, 0, 0, 0, 0, 0)]
        for matrix in matrices:
            with self.subTest(matrix=matrix), self.assertRaisesRegex(PdfTangentStrokeProofError, "similarity"):
                prove_tangent_stroke_support(ROUNDED, matrix, 10)
        for options in ({"linecap": "square"}, {"linejoin": "bevel"}, {"dasharray": "2,1"},
                        {"fill": "black"}, {"width": 0}, {"width": -1}, {"miter_limit": .5}):
            with self.subTest(options=options), self.assertRaises(PdfTangentStrokeProofError):
                prove_tangent_stroke_support(ROUNDED, IDENTITY, **({"width": 10}|options))

    def test_budget_types_limits_and_actual_counts_reject_conservatively(self):
        for options in ({"command_budget": True}, {"segment_budget": 0}, {"subpath_budget": "32"},
                        {"command_budget": 257}, {"segment_budget": 129}, {"subpath_budget": 33},
                        {"command_budget": 9}, {"segment_budget": 7}):
            with self.subTest(options=options), self.assertRaises(PdfTangentStrokeProofError):
                prove_tangent_stroke_support(ROUNDED, IDENTITY, 10, **options)
        commands = [("M", (0, 0)), *[("L", (i, 0)) for i in range(1, 129)]]
        self.assertEqual(prove_tangent_stroke_support(commands, IDENTITY, 1)["segment_count"], 128)
        with self.assertRaisesRegex(PdfTangentStrokeProofError, "segment_budget"):
            prove_tangent_stroke_support([*commands, ("L", (129, 0))], IDENTITY, 1)
        with self.assertRaisesRegex(PdfTangentStrokeProofError, "subpath_budget"):
            prove_tangent_stroke_support([*ROUNDED, ("M", (0, 0))], IDENTITY, 1, subpath_budget=1)
        with self.assertRaisesRegex(PdfTangentStrokeProofError, "command_budget"):
            prove_tangent_stroke_support([("M", (0, 0))]*257, IDENTITY, 1)

    def test_malformed_inputs_use_only_the_documented_failure_type(self):
        for commands in (None, [], [[[], [0, 0]]], [["M", [0]]], [["M", [True, 0]]],
                         [["L", [0, 0]]], [["M", [0, 0]], ["Q", [1, 0], [2, 0]]],
                         [["M", [0, 0]], ["Z"], ["L", [1, 0]]]):
            with self.subTest(commands=commands), self.assertRaises(PdfTangentStrokeProofError):
                prove_tangent_stroke_support(commands, IDENTITY, 1)
        for width in (True, float("nan"), float("inf"), 10**400, "1"):
            with self.subTest(width=str(width)[:20]), self.assertRaises(PdfTangentStrokeProofError):
                prove_tangent_stroke_support(ROUNDED, IDENTITY, width)
        with self.assertRaises(PdfTangentStrokeProofError):
            prove_tangent_stroke_support(ROUNDED, (1, 0, 0, 1), 1)

    def test_fraction_support_and_float_receipts_remain_outward_at_extremes(self):
        proof = prove_tangent_stroke_support([( "M", (0, 0)), ("L", (1, 0))],
                                             (1e-308, 0, 0, 1e-308, 0, 0), 1e-308)
        exact = F(proof["radius_upper_exact"])
        self.assertGreater(exact, 0)
        self.assertGreaterEqual(F(proof["radius_upper_float"]), exact)
        self.assertGreaterEqual(exact*exact, F(proof["radius_squared_exact"]))
        for index, (display, value) in enumerate(zip(proof["support_bounds_outward"], proof["support_bounds_exact"])):
            self.assertTrue(F(display) <= F(value) if index < 2 else F(display) >= F(value))
        with self.assertRaisesRegex(PdfTangentStrokeProofError, "support_output"):
            prove_tangent_stroke_support(ROUNDED, (1e308, 0, 0, 1e308, 0, 0), 1e308)

    def test_existing_stroke_envelope_api_and_legacy_results_are_unchanged(self):
        self.assertEqual(stroke_envelope(IDENTITY, 10, 10), (F(50), F(50)))
        self.assertEqual(stroke_envelope(IDENTITY, 10, 10, True), (F(5), F(5)))
        self.assertEqual(stroke_envelope(IDENTITY, 0, 4), (F(0), F(0)))

    def test_host_integer_format_limit_is_respected_via_declared_failure(self):
        if not hasattr(sys, "set_int_max_str_digits"):
            self.skipTest("Host Python has no integer formatting limit")
        # Isolate the host setting; implementation must not change it. This
        # legal float input produces rational receipt denominators exceeding
        # the host's low limit even though no numeric operation overflows.
        program = '''
import sys
from figure_rebuild.pdf_stroke_bounds import prove_tangent_stroke_support, PdfTangentStrokeProofError
sys.set_int_max_str_digits(640)
tiny = float.fromhex("0x0.0000000000001p-1022")
try:
    prove_tangent_stroke_support([("M", (0, 0)), ("L", (1, 0))],
                                (tiny, 0, 0, tiny, 0, 0), tiny)
except PdfTangentStrokeProofError as error:
    assert str(error) == "exact_receipt_format_limit", str(error)
else:
    raise AssertionError("formatter-limited proof should not be accepted")
assert sys.get_int_max_str_digits() == 640
assert prove_tangent_stroke_support([("M", (0, 0)), ("L", (1, 0))],
                                   (1, 0, 0, 1, 0, 0), 1)["status"] == "proven"
# A smaller receipt overflow with a representable legacy receipt exercises
# the actual lowerer fallback, rather than only testing the helper error.
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths
small = 2.0**-600
svg = f'<svg width="10" height="10"><path d="M0 0L1 0" fill="none" stroke="black" stroke-width="{small}" transform="matrix({small} 0 0 {small} 0 0)"/></svg>'
lowered = outline_paths(extract_outlined_svg(svg), glyph_mode="outline")
assert len(lowered.objects) == 1
receipt = lowered.provenance[0]["tangent_stroke_support"]
assert receipt["status"] == "not_proven" and receipt["reason"] == "exact_receipt_format_limit"
assert receipt["fallback"] == "unchanged_conservative_stroke_envelope"
assert sys.get_int_max_str_digits() == 640
'''
        completed = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)

    def test_direct_native_pdf_smooth_clipping_and_unsafe_miter_counterexample(self):
        proof = prove_tangent_stroke_support(ROUNDED, IDENTITY, 10, 20)
        clip = proof["support_bounds_outward"]
        for scale in (1, 2, 4):
            with self.subTest(scale=scale):
                self.assertEqual(pdf_samples(ROUNDED, 10, scale=scale),
                                 pdf_samples(ROUNDED, 10, clip, scale=scale))
        acute = [("M", (280, 130)), ("L", (180, 160)), ("L", (280, 170))]
        with self.assertRaises(PdfTangentStrokeProofError):
            prove_tangent_stroke_support(acute, IDENTITY, 20, 20)
        self.assertNotEqual(pdf_samples(acute, 20), pdf_samples(acute, 20, (170, 120, 290, 180)))


class TangentStrokeLowererTests(unittest.TestCase):
    def test_actual_byol_source_excerpt_retains_geometry_style_and_clip(self):
        doc = extract_outlined_svg(BYOL)
        before = copy.deepcopy(doc.paints[0])
        roi = (101, 52, 512, 249)
        result = outline_paths(doc, glyph_mode="outline", region=roi, transform=(2, 0, 0, 2, -202, -104))
        self.assertEqual(doc.paints[0], before)
        self.assertEqual(len(result.objects), 1)
        self.assertFalse(result.skipped)
        obj, record = result.objects[0], result.provenance[0]
        self.assertEqual(len(obj["commands"]), 11)
        self.assertEqual(sum("cubicTo" in c for c in obj["commands"]), 4)
        self.assertIn("close", obj["commands"][-2])
        self.assertIn("moveTo", obj["commands"][-1])
        for original, actual in zip(before.commands, obj["commands"]):
            op, *points = original
            pts = [(2*x-202, 2*y-104) for x, y in points]
            if op == "Z":
                self.assertEqual(actual, {"close": {}})
            elif op in ("M", "L"):
                self.assertEqual(actual, {"moveTo" if op == "M" else "lineTo": {"x": pts[0][0], "y": pts[0][1]}})
            else:
                self.assertEqual(actual, {"cubicTo": {"x1": pts[0][0], "y1": pts[0][1], "x2": pts[1][0],
                                                       "y2": pts[1][1], "x": pts[2][0], "y": pts[2][1]}})
        self.assertEqual(record["clip_context"], list(before.clips))
        self.assertEqual(record["group_context"], list(before.groups))
        self.assertEqual(obj["style"], {"fill": "none", "stroke": "#000000", "stroke_width": 7.9701*2*.078535009,
                                        "opacity": 1, "stroke_linecap": "butt", "stroke_linejoin": "miter", "stroke_miterlimit": 10})
        proof = record["tangent_stroke_support"]
        self.assertEqual(proof["status"], "proven")
        self.assertEqual(len(proof["joins"]), 8)
        self.assertGreater(proof["support_bounds_outward"][0]-127.801, 2.28)
        self.assertEqual(record["source_paint_part"], "original")
        with mock.patch("figure_rebuild.pdf_source.prove_tangent_stroke_support",
                        side_effect=PdfTangentStrokeProofError("unproven")):
            with self.assertRaisesRegex(UnsupportedPdfPaintError, "crosses"):
                outline_paths(doc, glyph_mode="outline", region=roi)

    def test_unproven_geometry_and_budget_use_the_original_envelope(self):
        commands = [("M", (100, 100)), ("L", (110, 100)), ("L", (120, 101))]
        result = outline_paths(source(commands), glyph_mode="outline")
        rec = result.provenance[0]
        self.assertEqual(rec["tangent_stroke_support"]["status"], "not_proven")
        self.assertEqual(rec["stroke_bounds_proof"]["axis_support_exact_rationals"], ["50", "50"])
        commands = [("M", (0, 0)), *[("L", (i, 0)) for i in range(1, 130)]]
        result = outline_paths(source(commands), glyph_mode="outline")
        self.assertEqual(result.provenance[0]["tangent_stroke_support"]["reason"], "segment_budget")
        self.assertEqual(result.provenance[0]["stroke_bounds_proof"]["axis_support_exact_rationals"], ["50", "50"])

    def test_helper_programming_errors_are_not_silently_downgraded(self):
        with mock.patch("figure_rebuild.pdf_source.prove_tangent_stroke_support", side_effect=RuntimeError("bug")):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                outline_paths(source(ROUNDED), glyph_mode="outline")

    def test_skip_receipt_keeps_the_source_support_certificate(self):
        result = outline_paths(source(ROUNDED), glyph_mode="outline", region=(170, 50, 180, 170))
        self.assertFalse(result.objects)
        self.assertEqual(result.skipped[0]["tangent_stroke_support"]["status"], "proven")
        self.assertEqual(result.skipped[0]["tangent_stroke_support"]["applies_to"],
                         "original_source_stroke_before_clip_intersection")

    def test_later_stroke_intersection_does_not_relabel_source_proof_as_fill(self):
        doc = source([("M", (-10, 5)), ("L", (20, 5))], width=2)
        result = outline_paths(doc, glyph_mode="outline", region=(0, 0, 10, 10))
        self.assertEqual(result.objects[0]["style"]["stroke"], "none")
        self.assertEqual(result.provenance[0]["source_paint_part"], "clipped-stroke-fill")
        self.assertEqual(result.provenance[0]["tangent_stroke_support"]["applies_to"],
                         "original_source_stroke_before_clip_intersection")
        self.assertIsNone(result.provenance[0]["stroke_bounds_proof"])

    def test_effects_and_tight_or_unknown_clips_still_reject(self):
        docs = [BYOL.replace('<g clip-path="url(#clip_1)">', '<g opacity=".5" clip-path="url(#clip_1)">'),
                BYOL.replace('<g clip-path="url(#clip_1)">', '<g mask="url(#unknown)" clip-path="url(#clip_1)">'),
                BYOL.replace('M0 0H453.81V170.64H0Z', 'M0 0H1V1H0Z'),
                BYOL.replace('M0 0H453.81V170.64H0Z', 'M0 0C1 2 3 4 5 6Z')]
        for svg in docs:
            with self.subTest(svg=svg[:100]):
                try:
                    result = outline_paths(extract_outlined_svg(svg), glyph_mode="outline")
                except UnsupportedPdfPaintError:
                    continue
                # Proved disjoint clips may skip; they never emit the path.
                self.assertFalse(result.objects)
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "crosses"):
            outline_paths(extract_outlined_svg(BYOL), glyph_mode="outline", region=(132, 130, 162, 165))

    def test_dash_and_nonuniform_transforms_do_not_bypass_existing_policies(self):
        dashed = source(ROUNDED, extra='stroke-dasharray="2,1"')
        with mock.patch("figure_rebuild.pdf_source.prove_tangent_stroke_support") as helper:
            outline_paths(dashed, glyph_mode="outline")
            helper.assert_not_called()
        for src_matrix, target_matrix in [((2, 0, 0, 1, 0, 0), IDENTITY), (IDENTITY, (2, 0, 0, 1, 0, 0))]:
            with self.assertRaisesRegex(UnsupportedPdfPaintError, "Nonuniform"):
                outline_paths(source(ROUNDED, matrix=src_matrix), glyph_mode="outline", transform=target_matrix)
        near = (1, 0, 0, math.nextafter(1, math.inf), 0, 0)
        result = outline_paths(source(ROUNDED, matrix=near), glyph_mode="outline")
        self.assertEqual(result.provenance[0]["tangent_stroke_support"]["reason"], "not_exact_nondegenerate_similarity")


if __name__ == "__main__":
    unittest.main()
