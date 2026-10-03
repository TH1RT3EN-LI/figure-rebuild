"""Polygon fill regions, holes, repeat walks and safe native quantization."""
from copy import deepcopy
from fractions import Fraction as F
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import figure_rebuild.pdf_winding as winding

from figure_rebuild.pdf_winding import (
    normalize_nonzero_polygons, UnsupportedPdfWindingError, _float_output, _Budget,
    verify_simple_loop_topology,
)

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def polygon(points):
    return [('M', list(points[0]))] + [('L', list(p)) for p in points[1:]] + [('Z',)]


def rectangle(x0, y0, x1, y1, reverse=False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return polygon(list(reversed(points)) if reverse else points)


def pdf_pixels(commands, evenodd=False, alpha=1):
    """Independent actual PDF f/f* oracle, including source alpha once."""
    document = fitz.open()
    try:
        page = document.new_page(width=60, height=60)
        page.set_mediabox(fitz.Rect(-10, -10, 50, 50))
        parts = ['q /Alpha gs .3 .5 .8 rg']
        for c in commands:
            if c[0] in ('M', 'L'):
                parts.append(' '.join(format(float(v), '.17f') for v in c[1]) +
                             (' m' if c[0] == 'M' else ' l'))
            else:
                parts.append('h')
        parts.append(('f*' if evenodd else 'f') + ' Q')
        document.xref_set_key(page.xref, 'Resources',
                              '<< /ExtGState << /Alpha << /ca ' + str(alpha) + ' >> >> >>')
        stream = document.get_new_xref()
        document.update_object(stream, '<< >>')
        document.update_stream(stream, '\n'.join(parts).encode())
        page.set_contents(stream)
        return page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=True).samples
    finally:
        document.close()


class PolygonWindingTests(unittest.TestCase):
    def normalized(self, commands, **kwargs):
        original = deepcopy(commands)
        result = normalize_nonzero_polygons(commands, **kwargs)
        self.assertEqual(commands, original)
        self.assertTrue(result['proof']['fill_only'])
        self.assertTrue(result['proof']['original_stroke_must_be_preserved_separately'])
        return result

    def assert_oracle(self, original, result):
        if fitz is not None:
            for alpha in (1, .37):
                self.assertEqual(pdf_pixels(original, alpha=alpha),
                                 pdf_pixels(result['commands'], evenodd=True, alpha=alpha))

    def test_overlapping_rectangles_form_one_boundary_and_alpha_applies_once(self):
        commands = rectangle(0, 0, 12, 8) + rectangle(6, 3, 18, 11)
        result = self.normalized(commands)
        self.assertEqual(result['proof']['output_ring_count'], 1)
        self.assertTrue(result['proof']['coordinate_export']['exact_binary64_coordinates'])
        self.assert_oracle(commands, result)

    def test_opposite_winding_hole_is_retained(self):
        commands = rectangle(0, 0, 20, 20) + rectangle(5, 5, 15, 15, reverse=True)
        result = self.normalized(commands)
        self.assertEqual(result['proof']['output_ring_count'], 2)
        self.assertEqual(sorted(result['proof']['coordinate_export']['topology']['orientation']), [-1, 1])
        self.assert_oracle(commands, result)

    def test_nested_same_direction_is_filled_and_island_inside_hole_survives(self):
        same = rectangle(0, 0, 24, 24) + rectangle(4, 4, 20, 20)
        self.assertEqual(self.normalized(same)['proof']['output_ring_count'], 1)
        commands = rectangle(0, 0, 24, 24) + rectangle(4, 4, 20, 20, True) + rectangle(8, 8, 16, 16)
        result = self.normalized(commands)
        self.assertEqual(result['proof']['output_ring_count'], 3)
        self.assert_oracle(commands, result)

    def test_repeated_walk_and_reverse_cancellation(self):
        walk = rectangle(0, 0, 20, 20)
        result = self.normalized(walk + walk)
        self.assertEqual(result['proof']['output_ring_count'], 1)
        self.assert_oracle(walk + walk, result)
        empty = self.normalized(walk + rectangle(0, 0, 20, 20, True))
        self.assertEqual(empty['commands'], [])
        self.assertTrue(empty['proof']['empty_fill'])

    def test_single_self_overlapping_elbow_becomes_simple_filled_region(self):
        commands = polygon([(0, 0), (0, 20), (30, 20), (30, 16), (2, 16), (4, 18), (4, 0)])
        result = self.normalized(commands)
        self.assertEqual(result['proof']['output_ring_count'], 1)
        self.assertTrue(any(abs(p['left_winding']) == 2 or abs(p['right_winding']) == 2
                            for p in result['proof']['boundary_winding_classification']) or
                        result['proof']['atomic_edge_count'] > 7)
        self.assert_oracle(commands, result)

    def test_implicit_closure_repeated_z_and_duplicate_vertices_are_fill_only(self):
        commands = [('M', [0, 0]), ('L', [0, 0]), ('L', [10, 0]), ('L', [10, 10]),
                    ('L', [0, 10]), ('M', [20, 0]), ('L', [30, 0]), ('L', [25, 10]), ('Z',), ('Z',)]
        result = self.normalized(commands)
        self.assertEqual(result['proof']['input_normalization_for_proof']['implicit_closures'], 1)
        self.assertEqual(result['proof']['input_normalization_for_proof']['zero_length_edges_ignored_for_proof'], 1)
        self.assertEqual(result['proof']['output_ring_count'], 2)

    def test_touching_components_and_bowtie_fail_closed(self):
        for commands in (rectangle(0, 0, 4, 4) + rectangle(4, 4, 8, 8),
                         polygon([(0, 0), (8, 8), (0, 8), (8, 0)])):
            with self.subTest(commands=commands), self.assertRaisesRegex(UnsupportedPdfWindingError, 'touching|branching'):
                self.normalized(commands)

    def test_unsupported_curves_bad_numbers_and_budget_exhaustion(self):
        for commands in ([('M', [0, 0]), ('C', [1, 0, 1, 1, 0, 1]), ('Z',)],
                         [('M', [float('nan'), 0])], [('M', [True, 0])]):
            with self.subTest(commands=commands), self.assertRaises(UnsupportedPdfWindingError):
                self.normalized(commands)
        with self.assertRaisesRegex(UnsupportedPdfWindingError, 'budget'):
            self.normalized(rectangle(0, 0, 10, 10), max_operations=1)
        with self.assertRaisesRegex(UnsupportedPdfWindingError, 'budget'):
            self.normalized(rectangle(0, 0, 10, 10), max_input_segments=3)

    def test_fractional_arrow_intersections_require_explicit_bounded_rounding(self):
        commands = rectangle(0, 0, 10, 2) + polygon([(10, 1), (8, -2), (14, 1), (8, 4)])
        with self.assertRaises(UnsupportedPdfWindingError) as caught:
            self.normalized(commands)
        self.assertEqual(caught.exception.code, 'coordinate_roundoff')
        result = self.normalized(commands, max_coordinate_error=1e-14)
        export = result['proof']['coordinate_export']
        self.assertFalse(export['exact_binary64_coordinates'])
        self.assertFalse(export['pointwise_fill_equivalence_after_rounding'])
        self.assertLessEqual(export['maximum_coordinate_error_bound'], 1e-14)
        self.assertIsNone(export['rgb_alpha_error_bound'])
        if fitz is not None:
            # Different PDF path tessellations can alter boundary antialiasing,
            # even when the emitted-coordinate error is below 1e-14. Check
            # strict filled interiors, not an invented universal RGB bound.
            original = pdf_pixels(commands)
            actual = pdf_pixels(result['commands'], evenodd=True)
            width = 240
            checked = 0
            for y in range(1, 239):
                for x in range(1, 239):
                    around = [((y + dy) * width + x + dx) * 4
                              for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
                    if all(original[k + 3] == 255 for k in around):
                        i = (y * width + x) * 4
                        self.assertEqual(actual[i:i+4], original[i:i+4])
                        checked += 1
            self.assertGreater(checked, 100)

    def test_rounding_cannot_merge_a_tiny_component_gap_even_with_same_orientation(self):
        delta = F(1, 2**54)
        left = [(F(0), F(0)), (F(1), F(0)), (F(1), F(1)), (F(0), F(1))]
        right = [(F(1)+delta, F(0)), (F(2), F(0)), (F(2), F(1)), (F(1)+delta, F(1))]
        with self.assertRaisesRegex(UnsupportedPdfWindingError, 'touch|intersect'):
            _float_output([left, right], F(1, 1000), _Budget(10000))

    def test_exact_fractions_at_nearby_non_touching_boundaries_stay_separate(self):
        delta = 2**-40
        commands = rectangle(0, 0, 1, 1) + rectangle(1+delta, 0, 2, 1)
        result = self.normalized(commands)
        self.assertEqual(result['proof']['output_ring_count'], 2)
        self.assertTrue(result['proof']['coordinate_export']['exact_binary64_coordinates'])

    def test_native_integer_remap_verifier_keeps_holes_and_rejects_gap_merge(self):
        reference = rectangle(0, 0, 20, 20) + rectangle(5, 5, 15, 15, True)
        candidate = rectangle(0, 0, F(200001, 10000), 20) + rectangle(5, 5, 15, 15, True)
        proof = verify_simple_loop_topology(reference, candidate)
        self.assertTrue(proof['topology_unchanged'])
        self.assertFalse(proof['pointwise_geometry_identical'])
        self.assertEqual(proof['maximum_vertex_coordinate_delta_exact'], '1/10000')
        separate = rectangle(0, 0, 1, 1) + rectangle(F(10001, 10000), 0, 2, 1)
        touching = rectangle(0, 0, 1, 1) + rectangle(1, 0, 2, 1)
        with self.assertRaises(UnsupportedPdfWindingError):
            verify_simple_loop_topology(separate, touching)

    def test_native_remap_cannot_move_hole_outside_or_reverse_it(self):
        reference = rectangle(0, 0, 20, 20) + rectangle(5, 5, 15, 15, True)
        for candidate in (rectangle(0, 0, 20, 20) + rectangle(25, 5, 35, 15, True),
                          rectangle(0, 0, 20, 20) + rectangle(5, 5, 15, 15)):
            with self.subTest(candidate=candidate), self.assertRaises(UnsupportedPdfWindingError):
                verify_simple_loop_topology(reference, candidate)

    def test_native_remap_overflow_fails_with_explicit_unsupported_error(self):
        enormous = 2**2048
        with self.assertRaises(UnsupportedPdfWindingError) as caught:
            verify_simple_loop_topology(rectangle(0, 0, 1, 1),
                                        rectangle(enormous, 0, enormous + 1, 1))
        self.assertEqual(caught.exception.code, 'float_range')

    def test_initial_close_is_invalid_but_repeated_close_after_move_is_valid(self):
        for commands in ([('Z',)], [('Z',)] + rectangle(0, 0, 2, 2)):
            with self.subTest(commands=commands), self.assertRaises(UnsupportedPdfWindingError) as caught:
                self.normalized(commands)
            self.assertEqual(caught.exception.code, 'input')
        result = self.normalized(rectangle(0, 0, 2, 2) + [('Z',), ('Z',)])
        self.assertEqual(result['proof']['output_ring_count'], 1)
        self.assertEqual(self.normalized([('M', [0, 0]), ('Z',), ('Z',)])['commands'], [])

    def test_intermediate_intersection_growth_is_bounded_before_decimal_receipts(self):
        denominator = 1 << 2048
        count = 0
        def perturbed(value):
            nonlocal count
            count += 1
            d = denominator + 2 * count + 1
            return F(value * d + 1, d)
        commands = []
        for points in ([(0, 0), (2, 0), (2, 2), (0, 2)],
                       [(1, -1), (3, -1), (3, 1), (1, 1)]):
            commands += polygon([tuple(perturbed(v) for v in p) for p in points])
        with self.assertRaises(UnsupportedPdfWindingError) as caught:
            self.normalized(commands, max_coordinate_error=F(1, 1000))
        self.assertEqual(caught.exception.code, 'budget')
        self.assertIn('fraction bit budget', str(caught.exception))

    def test_area_budget_is_charged_before_large_denominator_accumulation(self):
        commands = polygon([(i, F(1, (1 << 4090) + 2*i + 1)) for i in range(256)])
        with patch.object(winding, '_cross', wraps=winding._cross) as cross:
            with self.assertRaises(UnsupportedPdfWindingError) as caught:
                verify_simple_loop_topology(commands, commands, max_operations=1)
            self.assertEqual(caught.exception.code, 'budget')
            self.assertLessEqual(cross.call_count, 1)
        with self.assertRaises(UnsupportedPdfWindingError) as caught:
            verify_simple_loop_topology(commands, commands)
        self.assertEqual(caught.exception.code, 'budget')

    def test_collinear_simplification_shares_the_operation_budget(self):
        ring = [(F(i), F(0)) for i in range(100)] + [(F(99), F(1)), (F(0), F(1))]
        with self.assertRaises(UnsupportedPdfWindingError) as caught:
            winding._simplify(ring, _Budget(1))
        self.assertEqual(caught.exception.code, 'budget')

    def test_lower_host_decimal_limit_fails_closed_without_global_changes(self):
        code = '''
from fractions import Fraction
import sys
from figure_rebuild.pdf_winding import normalize_nonzero_polygons, UnsupportedPdfWindingError
d = Fraction(1, 2**2200 + 1)
try:
    normalize_nonzero_polygons([('M', [0, 0]), ('L', [d, 0]), ('L', [0, d]), ('Z',)])
except UnsupportedPdfWindingError as error:
    assert error.code == 'budget'
    assert 'decimal formatting budget' in str(error)
else:
    raise AssertionError('Expected explicit receipt budget rejection')
assert sys.get_int_max_str_digits() == 640
'''
        env = dict(os.environ, PYTHONINTMAXSTRDIGITS='640',
                   PYTHONPATH=str(Path(winding.__file__).resolve().parents[1]))
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
