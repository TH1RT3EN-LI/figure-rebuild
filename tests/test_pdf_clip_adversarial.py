"""Independent adversarial tests for proof-only clip classification and source integration."""
import copy
import math
import unittest

from figure_rebuild.pdf_clip import prove_clip_box_relation
from figure_rebuild.pdf_fill import prove_evenodd_nonzero_equivalent
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError


def poly(points, close=True):
    return [("M", points[0]), *[("L", p) for p in points[1:]], *([("Z",)] if close else [])]


def svg(defs, paint='M4 4H6V6H4Z'):
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:f="urn:foreign" width="30" height="30">'
            + defs + '<path clip-path="url(#c)" d="' + paint + '"/></svg>')


class PdfClipAdversarialTests(unittest.TestCase):
    def test_implicit_close_must_be_checked_for_boundary_crossing(self):
        commands = poly([(-2, -2), (3, -2), (3, 3)], close=False)
        self.assertIsNone(prove_clip_box_relation(commands, (0, 0, 1, 1)))

    def test_implicit_close_before_next_move_and_end(self):
        outer = poly([(-4, -4), (4, -4), (4, 4), (-4, 4)], close=False)
        inner = poly([(-2, -2), (2, -2), (2, 2), (-2, 2)], close=False)
        box = (-.5, -.5, .5, .5)
        proof = prove_clip_box_relation(outer+inner, box, fill_rule='evenodd')
        self.assertEqual(proof['relation'], 'outside')
        self.assertEqual(proof['winding_at_box_center'], 2)

    def test_cubic_crosses_box_with_all_controls_outside(self):
        commands = [('M', (-3, 0)), ('C', (-2, 3), (2, -3), (3, 0)),
                    ('L', (3, -4)), ('L', (-3, -4)), ('Z',)]
        self.assertIsNone(prove_clip_box_relation(commands, (-.125, -.125, .125, .125)))

    def test_exact_tangent_contact_fails_closed(self):
        # y(t)=3(1-t)^3-3(1-t)^2t-3(1-t)t^2+3t^3; y(1/2)=0.
        commands = [('M', (-1, 3)), ('C', (-1, -1), (1, -1), (1, 3)),
                    ('L', (1, 4)), ('L', (-1, 4)), ('Z',)]
        self.assertIsNone(prove_clip_box_relation(commands, (-.125, -1, .125, 0)))

    def test_reflection_changes_winding_sign_not_membership(self):
        commands = [('M', (-3, 0)), ('C', (-3, -3), (3, -3), (3, 0)),
                    ('C', (3, 3), (-3, 3), (-3, 0)), ('Z',)]
        reflected = [(c[0], *[(-p[0], p[1]) for p in c[1:]]) for c in commands]
        first = prove_clip_box_relation(commands, (-.125, -.125, .125, .125))
        second = prove_clip_box_relation(reflected, (-.125, -.125, .125, .125))
        self.assertEqual(first['relation'], second['relation'])
        self.assertEqual(first['winding_at_box_center'], -second['winding_at_box_center'])

    def test_closed_single_cubic_clip_lobe_keeps_actual_winding(self):
        commands = [('M', (0, 0)), ('C', (4, 4), (-4, 4), (0, 0)), ('Z',)]
        original = copy.deepcopy(commands)
        proof = prove_clip_box_relation(commands, (-.125, .875, .125, 1.125))
        self.assertEqual(proof['relation'], 'inside')
        self.assertEqual(abs(proof['winding_at_box_center']), 1)
        self.assertEqual(commands, original)
        # Fill-rule equivalence is intentionally a different, stricter predicate.
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_inner_boundary_inside_box_prevents_constant_occupancy_proof(self):
        outer = poly([(-8, -8), (8, -8), (8, 8), (-8, 8)])
        inner = poly([(-1, -1), (1, -1), (1, 1), (-1, 1)])
        self.assertIsNone(prove_clip_box_relation(outer+inner, (-3, -3, 3, 3), fill_rule='evenodd'))

    def test_self_crossing_clip_lobe_can_be_certified_away_from_boundary(self):
        bow = poly([(-3, -3), (3, 3), (-3, 3), (3, -3)])
        proof = prove_clip_box_relation(bow, (-.125, 1, .125, 1.5), fill_rule='evenodd')
        self.assertEqual(proof['relation'], 'inside')

    def test_narrow_representable_gap_not_closed_by_epsilon(self):
        x = math.nextafter(1.0, math.inf)
        commands = poly([(x, 0), (2, 0), (2, 2), (x, 2)])
        self.assertEqual(prove_clip_box_relation(commands, (0, .5, 1, 1.5))['relation'], 'outside')
        touching = poly([(1, 0), (2, 0), (2, 2), (1, 2)])
        self.assertIsNone(prove_clip_box_relation(touching, (0, .5, 1, 1.5)))

    def test_budget_exhaustion_does_not_classify(self):
        commands = [('M', (-3, 0)), ('C', (-3, -3), (3, -3), (3, 0)),
                    ('C', (3, 3), (-3, 3), (-3, 0)), ('Z',)]
        self.assertIsNone(prove_clip_box_relation(commands, (-.125, -.125, .125, .125), max_depth=0))
        self.assertIsNone(prove_clip_box_relation(commands, (-.125, -.125, .125, .125), max_segments=4))

    def test_sibling_children_union_does_not_cancel_opposite_windings(self):
        defs = '<defs><clipPath id="c"><path d="M0 0H10V10H0Z"/><path d="M0 0V10H10V0Z"/></clipPath></defs>'
        result = outline_paths(extract_outlined_svg(svg(defs)), glyph_mode='outline')
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(result.provenance[0]['clip_geometry_proofs'][0]['relation'], 'inside')

    def test_inside_child_cannot_hide_unsupported_sibling_attributes(self):
        defs = '<defs><clipPath id="c"><path d="M0 0H10V10H0Z"/><path filter="url(#unknown)" d="M0 0L9 9"/></clipPath></defs>'
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(extract_outlined_svg(svg(defs)), glyph_mode='outline')

    def test_inherited_clip_rule_from_defs_not_silently_lost(self):
        defs = '<defs clip-rule="evenodd"><clipPath id="c"><path d="M0 0H10V10H0Z M3 3H7V7H3Z"/></clipPath></defs>'
        try:
            result = outline_paths(extract_outlined_svg(svg(defs)), glyph_mode='outline')
        except UnsupportedPdfPaintError:
            return  # Explicitly unsupported inheritance is safe.
        self.assertEqual(result.objects, [], 'Inherited evenodd hole must not become nonzero filled interior')

    def test_foreign_namespace_clip_fast_path_rejected(self):
        defs = '<defs><f:clipPath id="c"><f:rect x="10" y="10" width="5" height="5"/></f:clipPath></defs>'
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(extract_outlined_svg(svg(defs, 'M1 1H2V2H1Z')), glyph_mode='outline')

    def test_fill_raw_command_and_iterator_budgets_are_explicit(self):
        curve = [('M', (0, 0)), ('C', (1, 0), (1, 1), (0, 1)), ('Z',)]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(curve[:-1]+[('L', (0, 1))]*10000+[('Z',)]))
        malformed_prefix = [('C', (8, 8), (9, 9), (10, 10)), *poly([(0, 0), (1, 0), (1, 1), (0, 1)])]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(iter(malformed_prefix)))


if __name__ == '__main__':
    unittest.main()
