"""Independent topology adversaries for the single-contour hull certificate."""
import copy
from fractions import Fraction
import math
import unittest

from figure_rebuild.pdf_fill import (
    _convex_hull, _hulls_separated, _prove_disjoint_segment_hulls,
    prove_evenodd_nonzero_equivalent,
)


def wavy():
    return [("M", (0, 0)), ("C", (1, 1), (2, -1), (3, 0)),
            ("L", (3, 3)), ("L", (0, 3)), ("Z",)]


def exact(points):
    return [tuple(Fraction(v) for v in p) for p in points]


class PdfFillSegmentHullTests(unittest.TestCase):
    def test_concave_control_polygon_is_simple_without_geometry_changes(self):
        commands = wavy()
        original = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["proof"],
                         "single_jordan_contour_with_pairwise_disjoint_segment_control_hulls")
        self.assertEqual(proof["segment_pairs_checked"], 6)
        self.assertEqual(proof["segment_count"], 4)
        self.assertFalse(proof["curve_approximation"])
        self.assertEqual(commands, original)

    def test_reversal_reflection_translation_preserve_proof(self):
        reversed_commands = [("M", (0, 0)), ("L", (0, 3)), ("L", (3, 3)),
                             ("L", (3, 0)), ("C", (2, -1), (1, 1), (0, 0)), ("Z",)]
        for commands in (wavy(), reversed_commands):
            for sign in (-1, 1):
                transformed = [(c[0], *[(sign*p[0]+100, p[1]-200) for p in c[1:]])
                               for c in commands]
                self.assertIsNotNone(_prove_disjoint_segment_hulls(transformed))

    def test_implicit_closure_and_zero_line_share_public_receipt(self):
        commands = wavy()[:-1]
        commands.insert(1, ("L", (0, 0)))
        proof = prove_evenodd_nonzero_equivalent(commands)
        receipt = proof["fill_proof_normalization"]
        self.assertEqual(receipt["skipped_zero_length_line_indices"], [1])
        self.assertEqual(len(receipt["implicit_closures"]), 1)

    def test_nonadjacent_endpoint_touch_rejected(self):
        commands = wavy()[:-1]+[("L", (3, 0)), ("Z",)]
        self.assertIsNone(_prove_disjoint_segment_hulls(commands))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_nonadjacent_crossing_rejected(self):
        commands = [("M", (0, 0)), ("C", (1, 1), (2, -1), (3, 0)),
                    ("L", (0, 3)), ("L", (3, 3)), ("Z",)]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_adjacent_collinear_retrace_rejected(self):
        commands = wavy()[:-1]+[("L", (1, 3)), ("Z",)]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_retraced_cubic_rejected(self):
        commands = wavy()[:2]+[("C", (2, -1), (1, 1), (0, 0)),
                              ("L", (0, 3)), ("Z",)]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_cubic_self_intersection_still_needs_injectivity(self):
        commands = [("M", (0, 0)), ("L", (-2, 4)),
                    ("C", (4, 16), (-4, 16), (2, 4)), ("Z",)]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_closed_single_cubic_and_two_segment_lens_outside_fallback(self):
        for commands in (
            [("M", (0, 0)), ("C", (1, 2), (-1, 2), (0, 0)), ("Z",)],
            [("M", (0, 0)), ("C", (1, 0), (1, 1), (0, 1)), ("Z",)],
        ):
            self.assertIsNone(_prove_disjoint_segment_hulls(commands))

    def test_multiple_contours_and_nested_holes_do_not_enter_fallback(self):
        inner = [("M", (1, 1)), ("L", (2, 1)), ("L", (2, 2)), ("L", (1, 2)), ("Z",)]
        duplicate = wavy()+wavy()
        self.assertIsNone(prove_evenodd_nonzero_equivalent(wavy()+inner))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(duplicate))
        translated = [(c[0], *[(p[0]+10, p[1]) for p in c[1:]]) for c in wavy()]
        self.assertIsNone(_prove_disjoint_segment_hulls(wavy()+translated))

    def test_exact_hull_removes_only_interior_and_collinear_controls(self):
        points = exact([(0, 0), (1, 0), (2, 0), (2, 2), (0, 2), (1, 1), (0, 0)])
        self.assertEqual(_convex_hull(points), exact([(0, 0), (2, 0), (2, 2), (0, 2)]))

    def test_adjacent_point_contact_requires_true_shared_point(self):
        first = exact([(0, 0), (1, 0), (0, 1)])
        second = exact([(1, 0), (2, 0), (2, 1)])
        self.assertFalse(_hulls_separated(first, second))
        self.assertTrue(_hulls_separated(first, second, exact([(1, 0)])[0]))
        self.assertFalse(_hulls_separated(first, second, exact([(0, 0)])[0]))

    def test_common_edge_and_area_never_accepted_as_endpoint_contact(self):
        first = exact([(0, 0), (1, 0), (1, 1), (0, 1)])
        shared_edge = exact([(1, 0), (2, 0), (2, 1), (1, 1)])
        overlapping = exact([(0, 0), (2, 0), (2, 2), (0, 2)])
        for second in (shared_edge, overlapping, first):
            self.assertFalse(_hulls_separated(first, second, exact([(1, 0)])[0]))

    def test_one_ulp_gap_touch_and_overlap_are_distinct(self):
        first = exact([(0, 0), (1, 0), (1, 1), (0, 1)])
        for edge, expected in ((math.nextafter(1, math.inf), True),
                               (1.0, False), (math.nextafter(1, -math.inf), False)):
            second = exact([(edge, 0), (2, 0), (2, 1), (edge, 1)])
            self.assertEqual(_hulls_separated(first, second), expected)

    def test_collinear_forward_lines_only_meet_at_shared_endpoint(self):
        first = exact([(0, 0), (1, 1)])
        forward = exact([(1, 1), (2, 2)])
        backward = exact([(1, 1), (Fraction(1, 2), Fraction(1, 2))])
        shared = exact([(1, 1)])[0]
        self.assertTrue(_hulls_separated(first, forward, shared))
        self.assertFalse(_hulls_separated(first, backward, shared))

    def test_zero_lines_and_malformed_commands_fail_private_boundary(self):
        for commands in (wavy()[:1]+[("L", (0, 0))]+wavy()[1:],
                         wavy()[:-1], wavy()+[("Z",)],
                         [("L", (0, 0))]+wavy(),
                         wavy()[:1]+[("C", (0, 0), (1, 1), (float("inf"), 0))]+wavy()[2:]):
            self.assertIsNone(_prove_disjoint_segment_hulls(commands))

    def test_control_budget_rejects_before_unbounded_pair_work(self):
        commands = wavy()[:2]+[("L", (3, y)) for y in range(1, 300)]+[("L", (0, 300)), ("Z",)]
        self.assertIsNone(_prove_disjoint_segment_hulls(commands))


if __name__ == "__main__":
    unittest.main()
