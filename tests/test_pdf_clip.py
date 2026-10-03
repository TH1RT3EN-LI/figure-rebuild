"""Analytic occupancy oracles for the proof-only cubic clip classifier."""
import math
import unittest

from figure_rebuild.pdf_clip import prove_clip_box_relation
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError


def polygon(points, close=True):
    return [("M", points[0]), *(("L", p) for p in points[1:]), *([("Z",)] if close else [])]


def square(lo, hi):
    return polygon([(lo, lo), (hi, lo), (hi, hi), (lo, hi)])


def svg(clip, paint, *, nested=""):
    return extract_outlined_svg('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        f'<defs><clipPath id="clip">{clip}</clipPath>{nested}</defs>{paint}</svg>')


class PdfClipProofTests(unittest.TestCase):
    def relation(self, commands, box, expected, **kwargs):
        original = list(commands)
        result = prove_clip_box_relation(commands, box, **kwargs)
        self.assertIsNotNone(result)
        self.assertEqual(result["relation"], expected)
        self.assertEqual(commands, original)
        self.assertFalse(result["output_geometry_approximated"])
        return result

    def test_whole_box_inside_outside_and_touch(self):
        commands = square(0, 10)
        self.relation(commands, (1, 1, 9, 9), "inside")
        self.relation(commands, (11, 1, 12, 9), "outside")
        for box in ((0, 1, 9, 9), (-1, 1, 1, 9), (-1, -1, 11, 11)):
            self.assertIsNone(prove_clip_box_relation(commands, box))

    def test_holes_winding_and_evenodd_are_distinct(self):
        commands = square(0, 10) + square(3, 7)
        self.assertEqual(self.relation(commands, (4, 4, 6, 6), "inside")["winding_at_box_center"], 2)
        self.relation(commands, (4, 4, 6, 6), "outside", fill_rule="evenodd")
        reverse_inner = polygon([(3, 3), (3, 7), (7, 7), (7, 3)])
        self.relation(square(0, 10)+reverse_inner, (4, 4, 6, 6), "outside")

    def test_open_subpaths_close_implicitly_without_merging(self):
        commands = polygon([(0, 0), (10, 0), (10, 10), (0, 10)], close=False)
        commands += polygon([(3, 3), (7, 3), (7, 7), (3, 7)], close=False)
        self.relation(commands, (1, 1, 2, 2), "inside", fill_rule="evenodd")
        self.relation(commands, (4, 4, 6, 6), "outside", fill_rule="evenodd")

    def test_exact_split_separates_curve_whose_initial_hull_overlaps_box(self):
        # The curve has y=2-9t+9t^2, hence minimum y=-1/4 at x=0.
        commands = [("M", (-3, 2)), ("C", (-2, -1), (2, -1), (3, 2)), ("Z",)]
        result = self.relation(commands, (-.05, 0, .05, .1), "inside")
        self.assertGreater(result["maximum_subdivision_depth"], 0)
        self.relation(commands, (-.05, -.5, .05, -.4), "outside")
        self.assertIsNone(prove_clip_box_relation(commands, (-.1, -.25, .1, -.1), max_depth=12))
        self.assertIsNone(prove_clip_box_relation(commands, (-.05, 0, .05, .1), max_depth=0))

    def test_diagonal_separation_uses_entire_hull_not_axis_bounding_box(self):
        self.relation(polygon([(0, 0), (10, 10), (10, 0)]), (-.1, 5, 1, 6), "outside")

    def test_self_intersection_classified_by_winding_not_contour_orientation(self):
        commands = polygon([(0, 0), (10, 10), (0, 10), (10, 0)])
        self.relation(commands, (4, 8, 6, 9), "inside")
        self.relation(commands, (1, 4, 2, 6), "outside")
        self.assertIsNone(prove_clip_box_relation(commands, (4, 4, 6, 6)))

    def test_sub_ulp_gap_does_not_become_contact(self):
        lo = math.nextafter(1., math.inf)
        self.relation(square(0, 1), (lo, .2, 2, .8), "outside")
        self.assertIsNone(prove_clip_box_relation(square(0, 1), (1, .2, 2, .8)))

    def test_invalid_and_exhausted_budgets_fail_closed(self):
        for commands in ([], [("L", (0, 0))], [("M", (False, 1))], [("M", (0, 0)), ("Q", (1, 1), (2, 2))]):
            self.assertIsNone(prove_clip_box_relation(commands, (0, 0, 1, 1)))
        for options in ({"max_depth":True}, {"max_segments":1}, {"fill_rule":"unknown"}):
            self.assertIsNone(prove_clip_box_relation(square(0, 10), (1, 1, 2, 2), **options))

    def test_source_curve_noop_preserves_exact_paint_commands(self):
        clip = '<path d="M0 0L20 0L20 10C20 20 0 20 0 10Z"/>'
        doc = svg(clip, '<path clip-path="url(#clip)" d="M5 5C6 7 8 7 9 5Z"/>')
        result = outline_paths(doc, glyph_mode="outline")
        self.assertEqual(result.provenance[0]["clip_geometry_proofs"][0]["relation"], "inside")
        self.assertEqual(result.objects[0]["commands"][1]["cubicTo"], {"x1":6, "y1":7, "x2":8, "y2":7, "x":9, "y":5})

    def test_source_hole_outside_is_auditable_skip(self):
        clip = '<path clip-rule="evenodd" d="M0 0H10V10H0Z M3 3H7V7H3Z"/>'
        result = outline_paths(svg(clip, '<path clip-path="url(#clip)" d="M4 4H6V6H4Z"/>'), glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(result.skipped[0]["clip_geometry_proofs"][0]["relation"], "outside")

    def test_close_then_continue_is_not_a_rectangular_clip(self):
        # The first line closes with zero area; the remaining triangle must
        # not be promoted to its four-corner rectangular envelope.
        clip = '<path d="M0 0H10ZL10 10L0 10Z"/>'
        result = outline_paths(svg(clip, '<path clip-path="url(#clip)" d="M7 1H8V2H7Z"/>'),
                               glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(result.skipped[0]["clip_geometry_proofs"][0]["relation"], "outside")

    def test_rectangular_path_clip_requires_a_known_winding_rule(self):
        clip = '<path d="M0 0H10V10H0Z" clip-rule="unexpected"/>'
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "winding rule"):
            outline_paths(svg(clip, '<path clip-path="url(#clip)" d="M1 1H2V2H1Z"/>'),
                          glyph_mode="outline")

    def test_sibling_clip_paths_union_and_clip_stack_intersection(self):
        clip = '<path d="M0 0H10V10H0Z"/><path d="M30 30H40V40H30Z"/>'
        paint = '<path clip-path="url(#clip)" d="M1 1H2V2H1Z"/>'
        self.assertEqual(len(outline_paths(svg(clip, paint), glyph_mode="outline").objects), 1)
        nested = '<clipPath id="other"><path d="M30 30H40V40H30Z"/></clipPath>'
        result = outline_paths(svg(clip, '<g clip-path="url(#other)">'+paint+'</g>', nested=nested), glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(len(result.skipped), 1)

    def test_clip_proof_includes_stroke_bounds_and_rejects_unknown_effects(self):
        clip = '<path d="M0 0L10 0L10 10C10 11 0 11 0 10Z"/>'
        paint = '<path clip-path="url(#clip)" d="M2 2H8V8H2Z" stroke="black" stroke-width="6"/>'
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "Complex clip"):
            outline_paths(svg(clip, paint), glyph_mode="outline")
        bad = '<path d="M0 0H10V10H0Z" opacity=".5"/>'
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(svg(bad, '<path clip-path="url(#clip)" d="M1 1H2V2H1Z"/>'), glyph_mode="outline")


if __name__ == "__main__":
    unittest.main()
