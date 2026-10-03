"""Exact clip geometry, winding, affine composition and strict rejection."""
import hashlib
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from figure_rebuild.pdf_image_clips import PdfImageClipError, parse_image_clip


IDENTITY = (1, 0, 0, 1, 0, 0)


def clip(body, attributes=''):
    return ET.fromstring(f'<clipPath {attributes}>{body}</clipPath>')


class PdfImageClipTests(unittest.TestCase):
    def test_cubic_preserves_controls_and_implicitly_closes(self):
        result = parse_image_clip(clip('<path d="M1 2 C3 -8 7 12 9 2"/>'), IDENTITY)
        self.assertEqual(result['commands'], [['M', [1., 2.]],
                         ['C', [3., -8.], [7., 12.], [9., 2.]], ['Z']])
        self.assertEqual(result['bounds'], [1., -8., 9., 12.])
        self.assertFalse(result['geometry_approximated'])
        self.assertEqual(result['source_xml_sha256'],
                         hashlib.sha256(result['source_xml'].encode()).hexdigest())

    def test_holes_keep_opposite_orientation_and_child_clip_rule_wins(self):
        node = clip('<path clip-rule="nonzero" fill-rule="evenodd" '
                    'd="M0 0H10V10H0Z M2 2V8H8V2Z"/>', 'clip-rule="evenodd"')
        result = parse_image_clip(node, IDENTITY)
        self.assertEqual(result['rule'], 'nonzero')
        self.assertEqual(result['commands'], [
            ['M', [0., 0.]], ['L', [10., 0.]], ['L', [10., 10.]], ['L', [0., 10.]], ['L', [0., 0.]], ['Z'],
            ['M', [2., 2.]], ['L', [2., 8.]], ['L', [8., 8.]], ['L', [8., 2.]], ['L', [2., 2.]], ['Z']])
        inherited = parse_image_clip(clip('<path d="M0 0H2V2Z"/>',
                                          'style="clip-rule:evenodd"'), IDENTITY)
        self.assertEqual(inherited['rule'], 'evenodd')
        self.assertEqual(parse_image_clip(clip('<path fill-rule="evenodd" d="M0 0H2V2Z"/>'),
                                         IDENTITY)['rule'], 'nonzero')

    def test_negative_context_and_nested_matrices_map_every_control(self):
        node = clip('<path transform="scale(2,-3)" d="M1 2C2 3 4 5 6 7Z"/>',
                    'transform="translate(10,20)"')
        result = parse_image_clip(node, (-1, 0, 0, 2, 100, 200))
        self.assertEqual(result['commands'], [
            ['M', [88., 228.]], ['C', [86., 222.], [82., 210.], [78., 198.]], ['L', [88., 228.]], ['Z']])
        self.assertEqual(result['bounds'], [78., 198., 88., 228.])

    def test_quadratic_and_smooth_curves_become_exact_cubics(self):
        result = parse_image_clip(clip('<path d="M0 0Q3 6 6 0T12 0C13 2 14 2 15 0S17 -2 18 0"/>'), IDENTITY)
        self.assertEqual(result['commands'], [
            ['M', [0., 0.]], ['C', [2., 4.], [4., 4.], [6., 0.]],
            ['C', [8., -4.], [10., -4.], [12., 0.]],
            ['C', [13., 2.], [14., 2.], [15., 0.]],
            ['C', [16., -2.], [17., -2.], [18., 0.]], ['Z']])

    def test_relative_repetition_open_subpaths_and_degenerate_subpath(self):
        result = parse_image_clip(clip('<path d="m1 2 3 0h2 2v4z m10 10h4 M40 40"/>'), IDENTITY)
        self.assertEqual(result['commands'], [
            ['M', [1., 2.]], ['L', [4., 2.]], ['L', [6., 2.]], ['L', [8., 2.]],
            ['L', [8., 6.]], ['L', [1., 2.]], ['Z'], ['M', [11., 12.]], ['L', [15., 12.]], ['Z'],
            ['M', [40., 40.]], ['Z']])

    def test_rectangle_and_affine_shear(self):
        result = parse_image_clip(clip('<rect x="1" y="2" width="3" height="4" '
                                       'transform="matrix(1,0,2,1,0,0)"/>'), IDENTITY)
        self.assertEqual(result['commands'], [
            ['M', [5., 2.]], ['L', [8., 2.]], ['L', [16., 6.]], ['L', [13., 6.]], ['Z']])

    def test_unknown_geometry_effects_and_units_fail_closed(self):
        cases = [('<g><path d="M0 0L1 1Z"/></g>', ''),
                 ('<use href="#x"/>', ''), ('<path d="M0 0L1 1Z" mask="url(#m)"/>', ''),
                 ('<path d="M0 0L1 1Z" style="filter:none"/>', ''),
                 ('<path d="M0 0L1 1Z"/>', 'clip-path="url(#c)"'),
                 ('<path d="M0 0L1 1Z"/>', 'clipPathUnits="objectBoundingBox"'),
                 ('<rect width="2" height="3" rx="0"/>', ''),
                 ('<path d="M0 0L1 1Z" unknown="x"/>', ''),
                 ('<path d="M0 0L1 1Z" transform="rotate(10)"/>', '')]
        for body, attrs in cases:
            with self.subTest(body=body, attrs=attrs), self.assertRaises(PdfImageClipError):
                parse_image_clip(clip(body, attrs), IDENTITY)

    def test_malformed_nonfinite_and_unsupported_commands_rejected(self):
        for data in ('', 'L0 0', 'M0 0A1 1 0 0 0 2 2', 'M0 0R1 2', 'M0 0garbage',
                     'M0 0 C1 2 3', 'M0 0L', 'M0 0Z1 2', 'M0,,0', 'M0 0,',
                     'M0 0L1e999 2', 'MNaN 0', 'M0 0;L1 2'):
            with self.subTest(data=data), self.assertRaises(PdfImageClipError):
                parse_image_clip(clip(f'<path d="{data}"/>'), IDENTITY)
        for matrix in ((1, 2), (1, 0, 0, 1, float('inf'), 0), (2, 0, 0, 1, 0, 0)):
            with self.subTest(matrix=matrix), self.assertRaises(PdfImageClipError):
                parse_image_clip(clip('<path d="M1e308 0L1e308 1Z"/>'), matrix)

    def test_budgets_fail_before_unbounded_output(self):
        node = clip('<path d="M0 0L1 1Z"/>')
        for name, value in [('MAX_SOURCE_BYTES', 10), ('MAX_PATH_TOKENS', 3), ('MAX_COMMANDS', 2)]:
            with self.subTest(name=name), patch('figure_rebuild.pdf_image_clips.'+name, value):
                with self.assertRaises(PdfImageClipError):
                    parse_image_clip(node, IDENTITY)


if __name__ == '__main__':
    unittest.main()
