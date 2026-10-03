import math
import unittest

from figure_rebuild.pdf_fill import prove_evenodd_nonzero_equivalent


def polygon(*points):
    return [("M", points[0]), *[("L", p) for p in points[1:]], ("Z",)]


class PdfFillEquivalenceTests(unittest.TestCase):
    def test_both_orientations_and_concave_polygon(self):
        points = [(0, 0), (6, 0), (6, 6), (3, 2), (0, 6)]
        for order in (points, list(reversed(points))):
            proof = prove_evenodd_nonzero_equivalent(polygon(*order))
            self.assertEqual(proof["contour_count"], 1)
            self.assertFalse(proof["source_commands_changed"])

    def test_multiple_disjoint_interiors(self):
        commands = polygon((0, 0), (4, 0), (4, 2), (0, 2))
        commands += polygon((1, 4), (1, 5), (3, 5), (3, 4))
        self.assertEqual(prove_evenodd_nonzero_equivalent(commands)["contour_count"], 2)

    def test_nested_contours_remain_unsupported_for_both_orientations(self):
        outer = polygon((0, 0), (8, 0), (8, 8), (0, 8))
        inner = [(2, 2), (4, 2), (4, 4), (2, 4)]
        for points in (inner, list(reversed(inner))):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(outer+polygon(*points)))

    def test_crossing_and_touching_contours(self):
        first = polygon((0, 0), (4, 0), (4, 4), (0, 4))
        for second in (polygon((2, -1), (6, -1), (6, 2), (2, 2)),
                       polygon((4, 4), (5, 4), (5, 5), (4, 5)), first):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(first+second))

    def test_self_crossings_even_with_nonzero_signed_area(self):
        for points in (((0, 0), (4, 4), (0, 4), (4, 0)),
                       ((0, 0), (5, 4), (0, 3), (4, 0), (4, 5))):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(polygon(*points)))

    def test_adjacent_backtracking_and_nonadjacent_touch(self):
        for points in (((0, 0), (4, 0), (2, 0), (2, 4), (0, 4)),
                       ((0, 0), (4, 0), (4, 4), (2, 0), (0, 4))):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(polygon(*points)))

    def test_redundant_vertices_and_straight_continuation(self):
        commands = polygon((0, 0), (2, 0), (2, 0), (4, 0), (4, 4), (0, 4), (0, 0))
        self.assertIsNotNone(prove_evenodd_nonzero_equivalent(commands))

    def test_narrow_gap_is_not_changed_by_epsilon(self):
        first = polygon((0, 0), (1, 0), (1, 1), (0, 1))
        x = math.nextafter(1.0, math.inf)
        second = polygon((x, 0), (2, 0), (2, 1), (x, 1))
        self.assertIsNotNone(prove_evenodd_nonzero_equivalent(first+second))
        x = math.nextafter(1.0, -math.inf)
        second = polygon((x, 0), (2, 0), (2, 1), (x, 1))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(first+second))

    def test_large_coordinates_keep_small_intersections(self):
        base = 1e12
        first = polygon((base, base), (base+2, base), (base+2, base+2), (base, base+2))
        second = polygon((base+1, base-1), (base+3, base-1), (base+3, base+1), (base+1, base+1))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(first+second))

    def test_curves_open_degenerate_and_invalid_inputs_fail_closed(self):
        for commands in ([], [("M", (0, 0)), ("C", (1, 0), (1, 1), (0, 1)), ("Z",)],
                         polygon((0, 0), (1, 0), (2, 0)),
                         [("M", (0, 0)), ("L", (1, 1))],
                         polygon((0, 0), (1, 0), (float("nan"), 1)),
                         polygon((False, 0), (1, 0), (1, 1)), [("Q",)]):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_resource_limit_fails_closed(self):
        commands = []
        for x in range(17):
            commands += polygon((x*3, 0), (x*3+1, 0), (x*3+1, 1), (x*3, 1))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))


if __name__ == "__main__":
    unittest.main()
