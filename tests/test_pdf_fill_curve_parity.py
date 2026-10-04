"""Source fill admission without changing a single original curve or paint."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from figure_rebuild.pdf_fill import prove_evenodd_nonzero_equivalent as prove
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError
from figure_rebuild.pdf_cubic_winding import UnsupportedPdfCubicWindingError

try:
    import pymupdf
except ImportError:
    pymupdf = None


OUTER = [("M", (2, 6)), ("C", (2, 3), (4, 2), (6, 2)),
         ("C", (9, 2), (10, 4), (10, 6)), ("C", (10, 9), (8, 10), (6, 10)),
         ("C", (3, 10), (2, 8), (2, 6)), ("Z",)]
INNER = [("M", (4, 6)), ("C", (4, 7), (5, 8), (6, 8)),
         ("C", (7, 8), (8, 7), (8, 6)), ("C", (8, 5), (7, 4), (6, 4)),
         ("C", (5, 4), (4, 5), (4, 6)), ("Z",)]


def reverse(commands):
    cursor = commands[0][1]
    segments = []
    for command in commands[1:-1]:
        segments.append([cursor, *command[1:]])
        cursor = command[-1]
    return [("M", cursor), *[("C", *points[-2::-1]) for points in reversed(segments)], ("Z",)]


def svg(commands, extra=""):
    data = " ".join(command[0] + " ".join(str(v) for point in command[1:] for v in point)
                    for command in commands)
    return extract_outlined_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<path d="{data}" fill="#ea4335" fill-rule="evenodd" {extra}/></svg>')


def pixels(commands, rule, alpha, scale):
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    with pymupdf.open() as doc:
        page = doc.new_page(width=16, height=16)
        rows = ["q /A gs .9 .2 .1 rg"]
        for command in commands:
            rows.append("h" if command[0] == "Z" else
                        " ".join(format(float(v), ".17f") for p in command[1:] for v in p) +
                        " " + {"M": "m", "L": "l", "C": "c"}[command[0]])
        rows.append(rule + " Q")
        doc.xref_set_key(page.xref, "Resources", '<< /ExtGState << /A << /ca ' + str(alpha) + ' >> >> >>')
        xref = doc.get_new_xref()
        doc.update_object(xref, "<< >>")
        doc.update_stream(xref, "\n".join(rows).encode())
        page.set_contents(xref)
        data = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True).samples
    if pymupdf.TOOLS.mupdf_warnings(reset=True):
        raise AssertionError("PDF fixture required parser recovery")
    return data


class IsolatedCurveParityTests(unittest.TestCase):
    def test_nested_opposite_winding_preserves_controls_and_receipt(self):
        commands = OUTER + INNER
        original = deepcopy(commands)
        proof = prove(commands)
        self.assertEqual(proof["proof"], "isolated_whole_cubic_arrangement_with_equal_winding_predicates")
        self.assertEqual((proof["contour_count"], proof["cubic_count"]), (2, 8))
        self.assertFalse(proof["source_commands_changed"])
        self.assertFalse(proof["curve_approximation"])
        self.assertFalse(proof["normalization_output_geometry_used"])
        self.assertLessEqual(proof["topology_certificate"]["exact_predicate_operations"], 250000)
        self.assertEqual(commands, original)
        self.assertEqual(json.loads(json.dumps(proof)), proof)

    def test_orientation_reflection_and_separate_islands(self):
        island = [(c[0], *[(x+11, y) for x, y in c[1:]]) for c in OUTER]
        for commands in (reverse(OUTER)+reverse(INNER), OUTER+INNER+island,
                         [(c[0], *[(-x, y) for x, y in c[1:]]) for c in OUTER+INNER]):
            self.assertIsNotNone(prove(commands))
        # An island inside the hole changes winding back to one, with no
        # nonzero even-winding region. These exact curves remain separated.
        small = [(c[0], *[(6+(x-6)/4, 6+(y-6)/4) for x, y in c[1:]]) for c in OUTER]
        self.assertIsNotNone(prove(OUTER+INNER+small))

    def test_same_winding_hole_cannot_be_admitted(self):
        self.assertIsNone(prove(OUTER+reverse(INNER)))
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(svg(OUTER+reverse(INNER)), glyph_mode="outline")

    def test_implicit_closure_and_repeated_line_are_proof_only(self):
        commands = [OUTER[0], ("L", OUTER[0][1]), *OUTER[1:-1], *INNER[:-1]]
        original = deepcopy(commands)
        proof = prove(commands)
        self.assertEqual(len(proof["fill_proof_normalization"]["implicit_closures"]), 2)
        self.assertEqual(proof["fill_proof_normalization"]["skipped_zero_length_line_indices"], [1])
        self.assertEqual(commands, original)

    def test_source_conversion_preserves_all_opcodes_alpha_and_clip(self):
        document = svg(OUTER+INNER, 'fill-opacity="0.35"')
        result = outline_paths(document, glyph_mode="outline", region=(0, 0, 16, 16))
        expected = []
        for op, *points in OUTER+INNER:
            if op == "Z": expected.append({"close": {}})
            elif op == "M": expected.append({"moveTo": dict(zip(("x", "y"), points[0]))})
            else: expected.append({"cubicTo": dict(zip(("x1", "y1", "x2", "y2", "x", "y"),
                                                      [v for point in points for v in point]))})
        self.assertEqual(result.objects[0]["commands"], expected)
        self.assertEqual(result.objects[0]["style"]["opacity"], 0.35)
        self.assertEqual(result.provenance[0]["source_paint_id"], document.paints[0].source_id)
        self.assertFalse(result.provenance[0]["fill_rule_equivalence"]["source_commands_changed"])
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "crosses clip/region"):
            outline_paths(document, glyph_mode="outline", region=(3, 0, 16, 16))

    def test_curve_intersection_and_bounded_failures_remain_unsupported(self):
        shifted = [(c[0], *[(x+4, y) for x, y in c[1:]]) for c in OUTER]
        self.assertIsNone(prove(OUTER+shifted))
        budget_error = UnsupportedPdfCubicWindingError("budget", "proof exhausted")
        with patch("figure_rebuild.pdf_fill.normalize_isolated_cubic_fill", side_effect=budget_error):
            self.assertIsNone(prove(OUTER+INNER))
        with patch("figure_rebuild.pdf_fill.normalize_isolated_cubic_fill", side_effect=RuntimeError("programming error")):
            with self.assertRaisesRegex(RuntimeError, "programming error"):
                prove(OUTER+INNER)
        self.assertIsNone(prove(OUTER+INNER[:-1]+[("C", (True, 1), (1, 2), (2, 3)), ("Z",)]))

    @unittest.skipIf(pymupdf is None, "PyMuPDF is optional")
    def test_actual_native_fill_predicates_rgba_and_counterexample(self):
        small = [(c[0], *[(6+(x-6)/4, 6+(y-6)/4) for x, y in c[1:]]) for c in OUTER]
        for commands in (OUTER+INNER, reverse(OUTER)+reverse(INNER), OUTER+INNER+small):
            self.assertIsNotNone(prove(commands))
            for alpha in (1, 0.35):
                for scale in (1, 2, 4):
                    self.assertEqual(pixels(commands, "f*", alpha, scale),
                                     pixels(commands, "f", alpha, scale))
        different = OUTER+reverse(INNER)
        self.assertIsNone(prove(different))
        self.assertNotEqual(pixels(different, "f*", 0.35, 4), pixels(different, "f", 0.35, 4))


if __name__ == "__main__":
    unittest.main()
