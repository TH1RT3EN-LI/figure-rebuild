"""Independent fill/winding, real rendering and integration checks."""
import copy
from fractions import Fraction
import math
import unittest

from figure_rebuild.pdf_rect_clip import clip_convex_fill_to_rect
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def polygon(points):
    return [('M', points[0]), *(('L', p) for p in points[1:]), ('Z',)]


def winding(commands, point):
    contours, current = [], []
    for command in commands:
        if command[0] == 'M':
            current = [command[1]]
        elif command[0] == 'L':
            current.append(command[1])
        else:
            contours.append(current)
    answer = 0
    x, y = point
    for contour in contours:
        for a, b in zip(contour, contour[1:]+contour[:1]):
            cross = (b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])
            if a[1] <= y < b[1] and cross > 0:
                answer += 1
            elif b[1] <= y < a[1] and cross < 0:
                answer -= 1
    return answer


class ConvexFillRectClipTests(unittest.TestCase):
    def test_nested_opposite_contours_keep_the_hole_and_input(self):
        commands = polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
        commands += polygon([(5, 5), (5, 15), (15, 15), (15, 5)])
        before = copy.deepcopy(commands)
        result = clip_convex_fill_to_rect(commands, (10, 2, 18, 18))
        self.assertEqual(commands, before)
        self.assertEqual([x['source_orientation'] for x in result['proof']['contours']], [1, -1])
        self.assertEqual([x['intersection_orientation'] for x in result['proof']['contours']], [1, -1])
        for point, expected in [((12, 10), 0), ((17, 10), 1), ((12, 3), 1), ((9, 3), 0)]:
            self.assertEqual(winding(result['commands'], point), expected)

    def test_overlapping_same_winding_contours_preserve_sum(self):
        commands = polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        commands += polygon([(5, -2), (15, -2), (15, 8), (5, 8)])
        result = clip_convex_fill_to_rect(commands, (2, 2, 12, 12))
        for point in ((3, 3), (7, 3), (11, 3), (3, 9), (7, 9)):
            self.assertEqual(winding(result['commands'], point), winding(commands, point))

    def test_concave_star_bowtie_and_double_walk_are_rejected(self):
        for points in (
            [(0, 0), (10, 0), (3, 4), (5, 10)],
            [(0, 0), (10, 10), (0, 10), (10, 0)],
            [(0, 3), (10, 3), (2, 9), (5, 0), (8, 9)],
            [(0, 0), (10, 0), (10, 10), (0, 10)]*2,
        ):
            with self.subTest(points=points):
                self.assertIsNone(clip_convex_fill_to_rect(polygon(points), (2, 2, 8, 8)))

    def test_zero_area_and_boundary_contact_are_explicit_empty(self):
        for commands in (polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                         polygon([(0, 0), (1, 1), (2, 2)]),
                         [('M', (1, 1))]):
            with self.subTest(commands=commands):
                result = clip_convex_fill_to_rect(commands, (1, 1, 3, 3))
                self.assertEqual(result['commands'], ())
                self.assertTrue(result['proof']['empty_fill_intersection'])

    def test_exact_intersection_roundoff_is_reported_without_a_fake_zero_bound(self):
        result = clip_convex_fill_to_rect(polygon([(0, 0), (10, 3), (0, 9)]), (1, -1, 9, 10))
        error = Fraction(result['proof']['maximum_coordinate_roundoff_source_units_exact'])
        self.assertGreater(error, 0)
        self.assertLess(error, Fraction(1, 10**14))
        self.assertFalse(result['proof']['output_winding_preserved_exactly'])
        self.assertTrue(result['proof']['source_contour_orientation_preserved'])
        vertices = {command[1] for command in result['commands'] if command[0] != 'Z'}
        self.assertEqual(vertices, {(1., .3), (9., 2.7), (9., 3.6), (1., 8.4)})

    def test_rounding_must_not_collapse_gap_between_compound_contours(self):
        # Both clipped contours retain their orientation, yet rounding merges
        # their distinct left edges and erases the nonzero winding in between.
        commands = polygon([(0, 0), (3, 10), (5, 0)])
        commands += polygon([(1e-17, 0), (4, 0), (3, 10)])
        self.assertIsNone(clip_convex_fill_to_rect(commands, (-1, 1, 6, 2)))

    def test_implicit_fill_closure_empty_moveto_and_post_close_line(self):
        commands = [('M', (0, 0)), ('L', (10, 0)), ('L', (10, 10)), ('L', (0, 10)),
                    ('Z',), ('Z',), ('L', (3, 0)), ('L', (0, 3)), ('M', (99, 99))]
        result = clip_convex_fill_to_rect(commands, (1, 1, 9, 9))
        self.assertIsNotNone(result)
        self.assertEqual(len(result['proof']['contours']), 2)
        self.assertEqual(len(result['proof']['normalization_for_fill']['skipped_empty_subpaths']), 1)

    def test_invalid_coordinates_rules_and_budgets_are_not_empty_successes(self):
        valid = polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        for commands in (valid+[('M', (float('nan'), 0))], [('L', (1, 1))],
                         [('M', (0, 0)), ('C', (1, 0), (1, 1), (0, 1)), ('Z',)]):
            self.assertIsNone(clip_convex_fill_to_rect(commands, (1, 1, 9, 9)))
        for kwargs in ({'fill_rule': 'evenodd'}, {'max_commands': True}, {'max_commands': 4},
                       {'max_predicates': True}, {'max_predicates': 1}):
            self.assertIsNone(clip_convex_fill_to_rect(valid, (1, 1, 9, 9), **kwargs))
        for rect in ((True, 1, 9, 9), (1, 1, float('inf'), 9), (9, 1, 1, 9)):
            self.assertIsNone(clip_convex_fill_to_rect(valid, rect))

    def test_source_integration_preserves_holes_and_explicit_clip_stack(self):
        source = '<svg width="30" height="30"><defs><clipPath id="c"><rect x="10" y="2" width="8" height="16"/></clipPath></defs><path clip-path="url(#c)" d="M0 0H20V20H0Z M5 5V15H15V5Z" fill="#0000ff"/></svg>'
        doc = extract_outlined_svg(source)
        before = doc.paints[0].commands
        result = outline_paths(doc, glyph_mode='outline', region=(0, 0, 25, 25), transform=(2, 0, 0, 2, 0, 0))
        self.assertEqual(doc.paints[0].commands, before)
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(sum('moveTo' in c for c in result.objects[0]['commands']), 2)
        self.assertTrue(result.provenance[0]['polygon_fill_intersection']['source_contour_winding_preserved'])
        self.assertFalse(result.provenance[0]['clip_boundary_rounding'])

    def test_source_integration_does_not_launder_strokes_or_evenodd_holes(self):
        for attrs in ('stroke="black"', 'fill-rule="evenodd"'):
            doc = extract_outlined_svg('<svg width="30" height="30"><path '+attrs+' d="M0 0H20V20H0Z M5 5V15H15V5Z"/></svg>')
            with self.assertRaises(UnsupportedPdfPaintError):
                outline_paths(doc, glyph_mode='outline', region=(10, 2, 18, 18))

    @unittest.skipIf(fitz is None, 'optional source dependency missing')
    def test_real_pdf_clip_keeps_solid_pixels_and_confines_antialias_changes_to_edges(self):
        commands = polygon([(0, 0), (40, 0), (50, 20), (40, 40), (0, 40), (-10, 20)])
        commands += polygon([(8, 8), (8, 32), (32, 32), (32, 8)])
        result = clip_convex_fill_to_rect(commands, (15, 3, 45, 37))

        def render(path_commands, scale, with_clip):
            doc = fitz.open()
            try:
                page = doc.new_page(width=60, height=60)
                content = ['1 0 0 rg']
                if with_clip:
                    content += ['15 3 30 34 re W n']
                for command in path_commands:
                    if command[0] == 'Z':
                        content.append('h')
                    else:
                        content.append(' '.join(map(str, command[1])) + (' m' if command[0] == 'M' else ' l'))
                content.append('f')
                xref = doc.get_new_xref(); doc.update_object(xref, '<<>>'); doc.update_stream(xref, ' '.join(content).encode())
                page.set_contents(xref)
                return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True).samples
            finally:
                doc.close()

        # The geometrically identical shorter edges can produce different
        # antialias coverage in MuPDF. Do not convert that observation into a
        # claim of pixel equality, or tolerate changes to solid ink / holes.
        edges = []
        for command in result['commands']:
            if command[0] == 'M':
                first = previous = command[1]
            else:
                point = first if command[0] == 'Z' else command[1]
                edges.append((previous, point))
                previous = point

        def distance(point, edge):
            a, b = edge
            dx, dy = b[0]-a[0], b[1]-a[1]
            t = max(0., min(1., ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy)))
            return math.hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy)

        for scale in (1, 2, 4):
            original, derived = render(commands, scale, True), render(result['commands'], scale, False)
            self.assertEqual(len(original), len(derived))
            for offset in range(0, len(original), 4):
                before, after = original[offset:offset+4], derived[offset:offset+4]
                if before != after:
                    pixel = offset//4
                    point = ((pixel % (60*scale)+.5)/scale, 60-(pixel//(60*scale)+.5)/scale)
                    self.assertLessEqual(min(distance(point, edge) for edge in edges), 1/scale)
                    self.assertTrue(0 < before[3] < 255 and 0 < after[3] < 255)
                    self.assertEqual(before[:3], bytes((before[3], 0, 0)))
                    self.assertEqual(after[:3], bytes((after[3], 0, 0)))


if __name__ == '__main__':
    unittest.main()
