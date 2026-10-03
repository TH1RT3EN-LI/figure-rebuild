"""Source conversion must retain stroke paint, clipping and source identity."""
import unittest

from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths, UnsupportedPdfPaintError


def source(body):
    return extract_outlined_svg('<svg width="50" height="50">'+body+'</svg>')


class AxisButtStrokeSourceIntegration(unittest.TestCase):
    def test_inside_keeps_original_commands_style_and_cap_endpoints(self):
        doc = source('<path d="M0 5L10 5" fill="none" stroke="#00aaff" stroke-width="2"/>')
        original = outline_paths(doc, glyph_mode='outline')
        clipped = outline_paths(doc, glyph_mode='outline', region=(0, 4, 10, 6))
        self.assertEqual(clipped.objects, original.objects)
        self.assertEqual(clipped.provenance[0]['axis_butt_stroke_intersection']['relation'], 'inside')
        self.assertEqual(clipped.provenance[0]['stroke_bounds_proof']['source_bounds_outward'], [0., 4., 10., 6.])
        self.assertEqual(clipped.provenance[0]['clip_boundary_rounding'], [])

    def test_intersection_maps_stroke_alpha_once_and_preserves_affine_identity(self):
        doc = source('<g transform="matrix(2 0 0 -2 20 40)"><path d="M0 5L10 5" fill="none" '
                     'stroke="#ab1234" stroke-width="2" stroke-opacity=".5" opacity=".6" fill-opacity=".1"/></g>')
        before = doc.paints[0].commands
        result = outline_paths(doc, glyph_mode='outline', region=(25, 29, 35, 35),
                               transform=(2, 0, 0, 2, -50, -58))
        self.assertEqual(doc.paints[0].commands, before)
        self.assertEqual(len(result.objects), 1)
        obj = result.objects[0]
        self.assertEqual(obj['style'], {'fill': '#ab1234', 'stroke': 'none', 'stroke_width': 0, 'opacity': .3})
        self.assertEqual(obj['commands'], [
            {'moveTo': {'x': 0., 'y': 0.}}, {'lineTo': {'x': 20., 'y': 0.}},
            {'lineTo': {'x': 20., 'y': 6.}}, {'lineTo': {'x': 0., 'y': 6.}}, {'close': {}}])
        receipt = result.provenance[0]
        self.assertEqual(receipt['source_paint_id'], doc.paints[0].source_id)
        self.assertEqual(receipt['source_paint_part'], 'clipped-stroke-fill')
        self.assertEqual(receipt['stroke_native_fields'], {})
        self.assertTrue(receipt['axis_butt_stroke_intersection']['same_paint_required'])

    def test_empty_finite_butt_support_does_not_publish_a_phantom_cap(self):
        doc = source('<path d="M0 5L10 5" fill="none" stroke="#123456" stroke-width="2"/>')
        result = outline_paths(doc, glyph_mode='outline', region=(11, 3, 12, 7))
        self.assertEqual(result.objects, [])
        self.assertEqual(result.skipped[0]['axis_butt_stroke_intersection']['relation'], 'outside')

    def test_disjoint_compound_stays_one_paint_and_clip_stack_is_intersected(self):
        doc = source('<defs><clipPath id="c"><rect x="4" y="0" width="12" height="20"/></clipPath></defs>'
                     '<path clip-path="url(#c)" d="M0 5L20 5M0 10L20 10" fill="none" '
                     'stroke="#123456" stroke-width="2" opacity=".4" stroke-opacity=".5"/>')
        result = outline_paths(doc, glyph_mode='outline', region=(3, 0, 17, 15))
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(result.objects[0]['style']['opacity'], .2)
        self.assertEqual(sum('moveTo' in c for c in result.objects[0]['commands']), 2)
        for command in result.objects[0]['commands']:
            if 'close' not in command:
                self.assertIn(next(iter(command.values()))['x'], (4., 16.))

    def test_unknown_effects_curves_overlaps_and_dash_are_not_laundered(self):
        for attrs, path in (
            ('mask="url(#unknown)"', 'M0 5L20 5'),
            ('stroke-dasharray="2 2"', 'M0 5L20 5'),
            ('stroke-linecap="round"', 'M0 5L20 5'),
            ('', 'M0 5C10 5 10 8 20 8'),
            ('', 'M0 5L20 5M10 0L10 15'),
        ):
            with self.subTest(attrs=attrs, path=path), self.assertRaises(UnsupportedPdfPaintError):
                outline_paths(source('<path d="'+path+'" fill="none" stroke="#123456" '
                                     'stroke-width="2" '+attrs+'/>'), glyph_mode='outline', region=(3, 0, 17, 15))


if __name__ == '__main__':
    unittest.main()
