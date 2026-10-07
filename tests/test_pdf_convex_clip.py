from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import tempfile
import unittest

from figure_rebuild.pdf_convex_clip import (clip_fill_at_convex_line_boundaries,
                                           UnsupportedConvexClipError)

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def rectangle(x0=0, y0=0, x1=10, y1=10):
    return [('M',(x0,y0)), ('L',(x1,y0)), ('L',(x1,y1)), ('L',(x0,y1)), ('Z',)]


def clip(commands, paths=None, **kwargs):
    return clip_fill_at_convex_line_boundaries(commands,
        [{'commands':p, 'fill_rule':'nonzero'}for p in (paths or [rectangle()])],
        fill_rule='nonzero', **kwargs)


class ConvexLineBoundaryTests(unittest.TestCase):
    def test_curve_head_preserved_and_only_straight_shaft_clipped(self):
        curve = ('C',(8,0),(8,2),(10,2))
        source = [('M',(10,0)),curve,('L',(0,2)),('L',(0,0)),('L',(10,0)),('Z',)]
        result = clip(source,[rectangle(7,-1,15,3)])
        self.assertEqual([c for c in result['commands']if c[0]=='C'],[curve])
        self.assertIn(('L',(F(7),F(2))),result['commands'])
        self.assertIn(('L',(F(7),F(0))),result['commands'])
        self.assertEqual(result['provenance']['line_crossings'],2)
        self.assertFalse(result['provenance']['curves_flattened'])
        self.assertFalse(result['provenance']['raster_equivalence_proved'])

    def test_clockwise_and_counterclockwise_clips_agree(self):
        source = rectangle(-2,-2,8,8)
        ccw = rectangle();cw = [('M',(0,0)),('L',(0,10)),('L',(10,10)),('L',(10,0)),('Z',)]
        first,second = clip(source,[ccw]),clip(source,[cw])
        def vertices(r):return {p for c in r['commands']for p in c[1:]}
        self.assertEqual(vertices(first),vertices(second))
        self.assertEqual(vertices(first),{(F(0),F(0)),(F(8),F(0)),(F(8),F(8)),(F(0),F(8))})

    def test_diagonal_convex_clip_uses_exact_line_intersections(self):
        triangle = [('M',(0,0)),('L',(7,0)),('L',(0,7)),('Z',)]
        result = clip(rectangle(1,1,6,6),[triangle])
        points = {p for c in result['commands']for p in c[1:]}
        self.assertEqual(points,{(F(1),F(1)),(F(6),F(1)),(F(1),F(6))})

    def test_zero_chord_cubic_is_not_deleted(self):
        source = [('M',(1,1)),('C',(4,1),(4,4),(1,1)),('Z',)]
        result = clip(source,[rectangle(0,0,5,5)])
        self.assertEqual(result['commands'],source)
        self.assertEqual(result['provenance']['output_cubic_count'],1)
        self.assertTrue(clip(source,[rectangle(10,0,12,5)])['provenance']['geometric_fill_intersection_empty'])

    def test_curve_plane_crossing_fails_without_changing_inputs(self):
        source = [('M',(0,0)),('C',(2,0),(0,2),(2,2)),('L',(0,2)),('Z',)]
        before = deepcopy(source)
        with self.assertRaisesRegex(UnsupportedConvexClipError,'Cubic control hull crosses'):
            clip(source,[rectangle(1,-1,3,3)])
        self.assertEqual(source,before)

    def test_outside_fill_is_accounted_geometrically(self):
        result = clip(rectangle(20,20,30,30))
        self.assertEqual(result['commands'],[])
        self.assertTrue(result['provenance']['geometric_fill_intersection_empty'])
        self.assertFalse(result['provenance']['raster_equivalence_proved'])

    def test_all_clips_validated_even_when_an_earlier_clip_excludes_paint(self):
        with self.assertRaisesRegex(UnsupportedConvexClipError,'not convex'):
            clip(rectangle(20,20,30,30),[rectangle(),[('M',(0,0)),('L',(5,0)),
                 ('L',(2,2)),('L',(5,5)),('L',(0,5)),('Z',)]])

    def test_empty_trailing_move_is_proved_fill_empty_not_a_small_contour(self):
        polygon = rectangle()+[('M',(0,0))]
        result = clip(rectangle(1,1,2,2),[polygon])
        self.assertEqual(result['provenance']['clip_empty_move_subpaths'],[1])
        self.assertEqual(result['provenance']['source_cubic_count'],0)
        with self.assertRaisesRegex(UnsupportedConvexClipError,'one drawable source contour'):
            clip(rectangle(1,1,2,2)+rectangle(3,3,4,4))

    def test_tiny_nonzero_boundary_crossing_is_not_tolerated_away(self):
        epsilon = F(1,2**50)
        result = clip(rectangle(-epsilon,1,2,2))
        self.assertEqual(result['provenance']['line_crossings'],2)
        self.assertTrue(all(p[0]>=0 for c in result['commands']for p in c[1:]))

    def test_multiply_wound_clip_is_rejected_for_evenodd_safety(self):
        repeated = [('M',(0,0)),('L',(10,0)),('L',(10,10)),('L',(0,10)),
                    ('L',(0,0)),('L',(10,0)),('L',(10,10)),('L',(0,10)),('Z',)]
        with self.assertRaisesRegex(UnsupportedConvexClipError,'vertices must be distinct'):
            clip(rectangle(1,1,2,2),[repeated])

    def test_operations_commands_and_intermediate_rational_sizes_are_bounded(self):
        for options in [{'max_operations':1},{'max_commands':3},{'max_integer_bits':10}]:
            with self.subTest(options=options):
                with self.assertRaisesRegex(UnsupportedConvexClipError,'budget'):
                    clip(rectangle(1,1,2,2),[rectangle(0,0,511,511)],**options)
        with self.assertRaisesRegex(UnsupportedConvexClipError,'rational size'):
            clip(rectangle(F(1,512),1,2,2),max_integer_bits=8)
        for name,value in [('max_operations',True),('max_commands',10001),('max_integer_bits',4097)]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(UnsupportedConvexClipError,'Invalid'):
                    clip(rectangle(),**{name:value})

    def test_malformed_and_unsupported_geometry_has_no_output(self):
        for source in [[('L',(0,0))],[('M',(True,0))],[('M',(float('inf'),0))],
                       [('M',(0,0)),('Q',(1,1),(2,0))],[]]:
            with self.subTest(source=source):
                with self.assertRaises(UnsupportedConvexClipError):clip(source)
        with self.assertRaises(UnsupportedConvexClipError):
            clip(rectangle(),[ [('M',(0,0)),('C',(1,0),(1,1),(0,0)),('Z',)] ])

    @unittest.skipUnless(fitz,'source extra requires PyMuPDF')
    def test_root_source_geometry_and_exact_clipped_output_render_agree(self):
        from figure_rebuild.pdf_source import extract_outlined_svg,_complex_clip_shapes
        def obj(doc,text,data=None):
            xref=doc.get_new_xref();doc.update_object(xref,text)
            if data is not None:doc.update_stream(xref,data)
            return xref
        with tempfile.TemporaryDirectory()as tmp:
            source_path=Path(tmp)/'source.pdf'
            with fitz.open()as doc:
                page=doc.new_page(width=200,height=120)
                page.set_contents(obj(doc,'<< >>',b'q 40 40 80 40 re W n 0 0 1 rg 80 50 m 80 70 60 70 60 50 c 0 50 l 0 45 l 80 45 l h f Q'))
                doc.save(source_path)
            with fitz.open(source_path)as doc:
                svg=doc[0].get_svg_image(text_as_path=True)
                expected=doc[0].get_pixmap(matrix=fitz.Matrix(4,4),alpha=True).samples
            source=extract_outlined_svg(svg).paints[0]
            # Expanded commands already contain root coordinates; the transform
            # is source provenance, not an instruction to apply it again.
            self.assertEqual(source.commands[0],('M',(80,70)))
            clips=[]
            for context in source.clips:
                shapes=_complex_clip_shapes(context);self.assertEqual(len(shapes),1)
                commands,rule=shapes[0];clips.append({'commands':commands,'fill_rule':rule})
            result=clip_fill_at_convex_line_boundaries(source.commands,clips,fill_rule='nonzero')
            content=['0 0 1 rg']
            for command in result['commands']:
                op=command[0]
                points=[(float(p[0]),120-float(p[1]))for p in command[1:]]
                if op=='M':content.append(f'{points[0][0]} {points[0][1]} m')
                elif op=='L':content.append(f'{points[0][0]} {points[0][1]} l')
                elif op=='C':content.append(' '.join(str(v)for p in points for v in p)+' c')
                else:content.append('h')
            content.append('f')
            with fitz.open()as doc:
                page=doc.new_page(width=200,height=120)
                page.set_contents(obj(doc,'<< >>',' '.join(content).encode()))
                actual=page.get_pixmap(matrix=fitz.Matrix(4,4),alpha=True).samples
            self.assertEqual(len(expected),len(actual))
            errors=[abs(a-b)for a,b in zip(expected,actual)]
            self.assertLessEqual(max(errors),1)
            self.assertLess(sum(errors)/len(errors),.01)


if __name__=='__main__':unittest.main()
