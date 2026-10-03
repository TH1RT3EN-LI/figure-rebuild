"""Exact Jordan-contour certificate for concave single-cubic arrowheads."""
import copy
import math
import unittest

from figure_rebuild.pdf_fill import (
    _prove_cubic_two_line_contour,
    prove_evenodd_nonzero_equivalent,
)

A, B, C = (0, 0), (-2, 4), (2, 4)


def contour(p1=(-1, 2), p2=(1, 2), a=A, b=B, c=C):
    return [('M', a), ('L', b), ('C', p1, p2, c), ('L', a), ('Z',)]


class CurvedTriangleProofTests(unittest.TestCase):
    def test_concave_controls_need_not_form_convex_polygon(self):
        self.assertIsNotNone(_prove_cubic_two_line_contour(contour()))

    def test_outward_bulging_curve_is_also_valid(self):
        self.assertIsNotNone(_prove_cubic_two_line_contour(contour((-1, 8), (1, 8))))

    def test_controls_at_apex_cannot_make_curve_touch_apex(self):
        self.assertIsNotNone(_prove_cubic_two_line_contour(contour(A, A)))

    def test_zero_endpoint_derivatives_are_safe(self):
        self.assertIsNotNone(_prove_cubic_two_line_contour(contour(B, C)))

    def test_all_cyclic_start_positions_and_close_variants(self):
        p1, p2 = (-1, 2), (1, 2)
        variants = [
            contour(),
            [('M', A), ('L', B), ('C', p1, p2, C), ('Z',)],
            [('M', B), ('C', p1, p2, C), ('L', A), ('Z',)],
            [('M', C), ('L', A), ('L', B), ('C', p1, p2, C), ('Z',)],
        ]
        for commands in variants:
            with self.subTest(commands=commands):
                self.assertIsNotNone(_prove_cubic_two_line_contour(commands))

    def test_reverse_orientation_and_reflection(self):
        commands = [('M', A), ('L', C), ('C', (1, 2), (-1, 2), B), ('Z',)]
        self.assertIsNotNone(_prove_cubic_two_line_contour(commands))
        transforms = [lambda x, y: (-x, y), lambda x, y: (-y, x),
                      lambda x, y: (x+123, y-234)]
        for transform in transforms:
            mapped = [(c[0], *[transform(*p) for p in c[1:]]) for c in contour()]
            self.assertIsNotNone(_prove_cubic_two_line_contour(mapped))

    def test_controls_inside_wedge_do_not_replace_self_injectivity_proof(self):
        # Both controls satisfy y >= 2*abs(x). This curve crosses itself at
        # t=(7-sqrt(21))/14 and 1-t: x=0, y=64/7. It must still reject.
        self.assertIsNone(_prove_cubic_two_line_contour(contour((4, 16), (-4, 16))))

    def test_curve_leaving_either_line_halfplane_rejected(self):
        for p1, p2 in [((-5, 4), (1, 3)), ((-1, 3), (5, 4))]:
            self.assertIsNone(_prove_cubic_two_line_contour(contour(p1, p2)))

    def test_tiny_negative_halfplane_is_not_erased_by_epsilon(self):
        self.assertIsNotNone(_prove_cubic_two_line_contour(contour((-1, 2), (1, 2))))
        outside = (-1, math.nextafter(2, -math.inf))
        self.assertIsNone(_prove_cubic_two_line_contour(contour(outside, (1, 2))))

    def test_collinear_triangle_corners_rejected(self):
        commands = contour((0, 2), (1, 2), a=(0, 0), b=(1, 0), c=(2, 0))
        self.assertIsNone(_prove_cubic_two_line_contour(commands))

    def test_repeated_corners_and_zero_length_lines_rejected(self):
        self.assertIsNone(_prove_cubic_two_line_contour(contour(a=B)))
        self.assertIsNone(_prove_cubic_two_line_contour(contour(c=B)))

    def test_compound_or_extra_segment_not_inferred_safe(self):
        self.assertIsNone(_prove_cubic_two_line_contour(contour()+contour()))
        extra = [*contour()[:-2], ('L', (4, 1)), *contour()[-2:]]
        self.assertIsNone(_prove_cubic_two_line_contour(extra))

    def test_nonfinite_bool_and_one_shot_input_rejected(self):
        for point in [(float('nan'), 2), (float('inf'), 2), (True, 2)]:
            self.assertIsNone(_prove_cubic_two_line_contour(contour(point)))
        self.assertIsNone(_prove_cubic_two_line_contour(iter(contour())))

    def test_open_or_malformed_path_rejected(self):
        self.assertIsNone(_prove_cubic_two_line_contour(contour()[:-1]))
        quadratic = [('M', A), ('L', B), ('Q', (1, 2), C), ('Z',)]
        self.assertIsNone(_prove_cubic_two_line_contour(quadratic))

    def test_no_source_command_rewriting(self):
        commands = contour()
        before = copy.deepcopy(commands)
        receipt = _prove_cubic_two_line_contour(commands)
        self.assertEqual(commands, before)
        self.assertFalse(receipt['source_commands_changed'])
        self.assertFalse(receipt['curve_approximation'])

    def test_sufficient_projection_can_conservatively_reject_injective_curve(self):
        # x'(t)=12*(1-2*t)^2 is nonnegative. The chosen sufficient proof cannot
        # establish this from weakly ordered control values and may reject.
        self.assertIsNone(_prove_cubic_two_line_contour(contour(C, B)))


class PublicCurvedTriangleTests(unittest.TestCase):
    def test_public_dispatch_retains_concave_curve_and_records_certificate(self):
        commands = contour()
        before = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof['proof'],
                         'single_cubic_two_lines_strict_halfplanes_and_monotone_projection')
        self.assertEqual(proof['straight_segment_count'], 2)
        self.assertEqual(commands, before)

    def test_public_dispatch_does_not_accept_wedge_contained_self_intersection(self):
        self.assertIsNone(prove_evenodd_nonzero_equivalent(contour((4, 16), (-4, 16))))


if __name__ == '__main__':
    unittest.main()
