"""Whole-C identity, exact topology, integer quantization and resource guards."""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import os
import subprocess
import sys
import unittest
from xml.etree import ElementTree as ET

from figure_rebuild.pdf_cubic_winding import (
    normalize_isolated_cubic_fill as normalize,
    verify_isolated_cubic_integer_encoding as verify,
    UnsupportedPdfCubicWindingError as Unsupported,
)
from figure_rebuild.native_cubic_geometry import (
    decode_native_cubic_path, verify_native_cubic_path_encoding,
)

try:
    import pymupdf
except ImportError:
    pymupdf = None

ROUND = [('M', (0, 0)), ('C', (1, -1), (3, -1), (4, 0)),
         ('L', (4, 4)), ('L', (0, 4)), ('Z',)]
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def rectangle(x0, y0, x1, y1, reverse=False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if reverse:
        points.reverse()
    return [('M', points[0])] + [('L', p) for p in points[1:]] + [('Z',)]


def translated(commands, x, y):
    return [(c[0], *[(p[0]+x, p[1]+y) for p in c[1:]]) for c in commands]


def rounded(commands):
    def n(v):
        v = F(v) + F(1, 2)
        return v.numerator // v.denominator
    return [(c[0], *[tuple(n(v) for v in p) for p in c[1:]]) for c in commands]


def xml_path(commands, width=100, height=100):
    result = ET.Element(A+'path', {'w': str(width), 'h': str(height)})
    for c in commands:
        node = ET.SubElement(result, A+{'M':'moveTo', 'L':'lnTo', 'C':'cubicBezTo', 'Z':'close'}[c[0]])
        for p in c[1:]:
            ET.SubElement(node, A+'pt', {'x':str(p[0]), 'y':str(p[1])})
    return result


def fractional_intersections():
    return ROUND + [('M', (2, 2)), ('L', (7, 3)), ('L', (3, 7)), ('Z',)]


def pixels(commands, rule, alpha):
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=16, height=16)
        rows = ['q 1 0 0 1 3 3 cm /A gs .3 .5 .8 rg']
        for c in commands:
            rows.append('h' if c[0] == 'Z' else ' '.join(format(float(v), '.17f') for p in c[1:] for v in p)+' '+{'M':'m','L':'l','C':'c'}[c[0]])
        rows.append(rule+' Q')
        doc.xref_set_key(page.xref, 'Resources', '<< /ExtGState << /A << /ca '+str(alpha)+' >> >> >>')
        xref = doc.get_new_xref(); doc.update_object(xref, '<< >>')
        doc.update_stream(xref, '\n'.join(rows).encode()); page.set_contents(xref)
        return page.get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=True).samples
    finally:
        doc.close()


class CubicWindingTests(unittest.TestCase):
    def normalized(self, commands, **kwargs):
        old = deepcopy(commands)
        result = normalize(commands, **kwargs)
        self.assertEqual(commands, old)
        self.assertFalse(result['proof']['simplify_used'])
        self.assertFalse(result['proof']['native_integer_encoding_verified'])
        self.assertEqual(result['proof']['compound_paint_count'], 1)
        self.assertTrue(all(type(v) is F for c in result['commands'] for p in c[1:] for v in p))
        return result

    def rejected(self, commands, code=None, **kwargs):
        old = deepcopy(commands)
        with self.assertRaises(Unsupported) as caught:
            normalize(commands, **kwargs)
        self.assertEqual(commands, old)
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_scalar_policy_zero_float_polygon_only_and_empty_fill(self):
        floating = [('M',(-0.0,0.0)),*ROUND[1:]]
        self.normalized(floating)
        polygon = self.normalized(rectangle(0,0,2,2))
        self.assertEqual(polygon['proof']['source_curve_count'],0)
        empty = self.normalized([])
        self.assertTrue(empty['proof']['empty_fill'])
        self.assertEqual(empty['commands'],[])
        self.assertTrue(verify([],[])['exact_pointwise_fill_equivalence_to_initial_native'])
        self.rejected([('M',('0',0)),*ROUND[1:]],'input')
        self.rejected([(['M'],(0,0))], 'input')

    def test_whole_control_identity_and_reverse(self):
        result = self.normalized(ROUND)
        self.assertEqual(result['curve_identities'][0]['direction'], 'forward')
        reverse = [('M',(0,0)),('L',(0,4)),('L',(4,4)),('L',(4,0)),
                   ('C',(3,-1),(1,-1),(0,0)),('Z',)]
        result = self.normalized(reverse)
        identity = result['curve_identities'][0]
        self.assertEqual(identity['direction'], 'reverse')
        self.assertEqual(identity['output_controls'], list(reversed(identity['original_controls'])))
        verify(reverse, rounded(result['commands']))

    def test_protected_collinear_endpoints(self):
        source = [('M',(0,0)),('L',(2,0)),('C',(3,-1),(5,-1),(6,0)),
                  ('L',(8,0)),('L',(8,4)),('L',(0,4)),('Z',)]
        result = self.normalized(source)
        self.assertIn(('L',(F(2),F(0))), result['commands'])
        self.assertIn(('C',(F(3),F(-1)),(F(5),F(-1)),(F(6),F(0))), result['commands'])
        self.assertTrue(any(r['protected_C_endpoint'] for r in result['vertex_origins']))

    def test_overlap_and_exact_intersection_origins(self):
        result = self.normalized(fractional_intersections())
        self.assertEqual(result['proof']['output_ring_count'], 1)
        origins = [v for v in result['vertex_origins'] if v['origin']=='new_L_L_intersection']
        self.assertTrue(origins)
        self.assertTrue(all(o['source_line_pairs'] for o in origins))
        self.assertTrue(any(v.denominator>1 for c in result['commands'] for p in c[1:] for v in p))

    def test_hole_and_same_winding_nested_fill(self):
        hole = self.normalized(ROUND+rectangle(1,1,3,3,True))
        self.assertEqual(sorted(hole['proof']['chord_loop_topology']['orientation']), [-1,1])
        verify(ROUND+rectangle(1,1,3,3,True), rounded(hole['commands']))
        full = self.normalized(ROUND+rectangle(1,1,3,3))
        self.assertEqual(full['proof']['output_ring_count'], 1)

    def test_internal_curve_discard_has_nonboundary_receipt(self):
        result = self.normalized(rectangle(-5,-5,10,10)+ROUND)
        self.assertFalse(any(c[0]=='C' for c in result['commands']))
        identity = result['curve_identities'][0]
        self.assertEqual(identity['decision'], 'discarded_nonboundary')
        self.assertTrue(identity['classification']['left_winding'])
        self.assertTrue(identity['classification']['right_winding'])

    def test_adjacent_two_c_and_endpoint_zero_derivative(self):
        source = [('M',(0,0)),('C',(0,0),(4,0),(4,0)),('C',(4,1),(4,3),(4,4)),
                  ('L',(0,4)),('Z',)]
        result = self.normalized(source)
        self.assertEqual(result['proof']['source_curve_count'], 2)
        verify(source, rounded(result['commands']))

    def test_zero_length_l_does_not_modify_input(self):
        result = self.normalized(ROUND[:1]+[('L',(0,0))]+ROUND[1:])
        self.assertIn(1, result['proof']['zero_length_L_ignored_for_proof'])

    def test_exact_tiny_gap_and_contact(self):
        gap = F(1,2**100)
        self.normalized(ROUND+rectangle(1,-2,2,-1-gap))
        self.rejected(ROUND+rectangle(1,-2,2,-1), 'curve_hull_isolation')

    def test_cross_subpath_contact_and_duplicate_walk(self):
        self.rejected(ROUND+[('M',(4,0)),('L',(7,-3)),('L',(7,0)),('Z',)], 'curve_hull_isolation')
        self.rejected(ROUND+ROUND, 'curve_hull_isolation')

    def test_adjacent_hull_overlap_and_two_edge_lens(self):
        self.rejected([('M',(0,0)),('C',(1,-1),(3,-1),(4,0)),('L',(2,F(-1,2))),('L',(0,4)),('Z',)], 'curve_hull_isolation')
        self.rejected([('M',(0,0)),('C',(1,-1),(3,-1),(4,0)),('Z',)], 'curve_hull_isolation')

    def test_closed_or_unproved_curve_rejected(self):
        self.rejected([('M',(0,0)),('C',(1,2),(-1,2),(0,0)),('Z',)], 'curve_injectivity')
        self.rejected([('M',(0,0)),('C',(4,4),(-4,-4),(1,0)),('L',(1,5)),('L',(0,5)),('Z',)], 'curve_injectivity')

    def test_command_structure_and_finite_number_guards(self):
        for cmds in [[('Z',)]+ROUND, ROUND[:-1], [('M',(True,0)),*ROUND[1:]],
                     [('M',(float('nan'),0)),*ROUND[1:]], [('M',([[0]],0))],
                     [('M',(0,0)),('Q',(1,1),(2,0)),('Z',)]]:
            with self.subTest(cmds=repr(cmds)[:100]):self.rejected(cmds)
        with self.assertRaises(ValueError):normalize(ROUND,max_operations=True)

    def test_shared_operations_commands_segments_and_atoms_budgets(self):
        for kwargs in [{'max_operations':1},{'max_operations':100},{'max_commands':2},
                       {'max_input_segments':2},{'max_atomic_edges':2}]:
            with self.subTest(kwargs=kwargs):self.rejected(ROUND,'budget',**kwargs)
        class UnreadableList(list):
            def __iter__(self):raise AssertionError('Must not traverse unsupported container')
        with self.assertRaises(Unsupported) as caught:
            normalize(UnreadableList(ROUND))
        self.assertEqual(caught.exception.code, 'budget')

    def test_fraction_input_and_intermediate_bit_guards(self):
        self.rejected([('M',(F(1,2**4097),0)),*ROUND[1:]], 'budget')
        denominator = 1<<2048; counter = 0
        def perturb(v):
            nonlocal counter
            counter += 1; den = denominator+2*counter+1
            return F(v*den+1,den)
        commands=[]
        for points in [[(0,0),(2,0),(2,2),(0,2)],[(1,-1),(3,-1),(3,1),(1,1)]]:
            points=[tuple(perturb(v) for v in p) for p in points]
            commands += [('M',points[0])]+[('L',p) for p in points[1:]]+[('Z',)]
        self.rejected(commands, 'budget')

    def test_host_decimal_format_limit_stays_unchanged_and_fails_closed(self):
        code = '''
from fractions import Fraction as F
from figure_rebuild.pdf_cubic_winding import normalize_isolated_cubic_fill, UnsupportedPdfCubicWindingError
import sys
n=2**2200
c=[('M',(n,0)),('C',(n+1,-1),(n+3,-1),(n+4,0)),('L',(n+4,4)),('L',(n,4)),('Z',)]
try: normalize_isolated_cubic_fill(c)
except UnsupportedPdfCubicWindingError as e:
 assert e.code=='budget',e.code
 assert sys.get_int_max_str_digits()==640
 print('explicit-budget')
else: raise AssertionError('Decimal receipt guard not exercised')
'''
        env={**os.environ,'PYTHONINTMAXSTRDIGITS':'640',
             'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
        run=subprocess.run([sys.executable,'-c',code],env=env,text=True,capture_output=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('explicit-budget',run.stdout)

    def test_integer_verifier_recomputes_independent_source(self):
        source=fractional_intersections(); result=self.normalized(source)
        candidate=rounded(result['commands'])
        result['proof']['source_curve_count']=-999  # Caller receipt never consumed.
        receipt=verify(source,candidate)
        self.assertTrue(receipt['C_controls_and_protected_endpoints_identical'])
        self.assertFalse(receipt['exact_pointwise_fill_equivalence_to_initial_native'])
        self.assertTrue(any(F(v)>0 for v in receipt['maximum_new_coordinate_error_local_units']))
        self.assertFalse(receipt['existing_source_to_initial_native_error_included'])
        with self.assertRaises(TypeError):verify(result,candidate,proof=result['proof'])

    def test_integer_verifier_rejects_c_control_or_endpoint_movement(self):
        source=ROUND; candidate=rounded(normalize(source)['commands'])
        for command,point in [(1,1),(1,3),(0,1)]:
            changed=deepcopy(candidate); row=list(changed[command]); p=list(row[point]);p[0]+=1;row[point]=tuple(p);changed[command]=tuple(row)
            with self.subTest(command=command,point=point),self.assertRaises(Unsupported):verify(source,changed)

    def test_integer_verifier_rejects_noninteger_mismatched_or_overerror_input(self):
        source=fractional_intersections(); exact=normalize(source)['commands']; candidate=rounded(exact)
        with self.assertRaises(Unsupported):verify(source,exact)
        with self.assertRaises(Unsupported):verify(source,candidate,max_coordinate_error=0)
        with self.assertRaises(Unsupported):verify(source,candidate[:-1])
        with self.assertRaises(Unsupported):verify([('M',(0.,0)),*source[1:]],candidate)
        with self.assertRaises(ValueError):verify(source,candidate,max_coordinate_error=F(3,4))

    def test_new_line_intersections_collapsing_on_grid_are_rejected(self):
        source=translated(ROUND,200,200)+rectangle(-10,-10,5,10)+[('M',(0,0)),('L',(100,1)),('L',(100,2)),('Z',)]
        exact=self.normalized(source); candidate=rounded(exact['commands'])
        with self.assertRaises(Unsupported):verify(source,candidate)

    @unittest.skipIf(pymupdf is None, 'Optional actual PDF oracle requires PyMuPDF')
    def test_actual_pdf_nonzero_vs_whole_curve_evenodd_applies_alpha_once(self):
        for source in [ROUND+rectangle(2,2,6,6),ROUND+rectangle(1,1,3,3,True)]:
            result=self.normalized(source)
            for alpha in [1,.37,.5]:
                with self.subTest(alpha=alpha):self.assertEqual(pixels(source,'f',alpha),pixels(result['commands'],'f*',alpha))


class NativeCubicGeometryTests(unittest.TestCase):
    def test_actual_serialized_integer_xml_reproof(self):
        source=fractional_intersections(); candidate=rounded(normalize(source)['commands'])
        original=xml_path(source); output=ET.fromstring(ET.tostring(xml_path(candidate)))
        before=ET.tostring(original),ET.tostring(output)
        proof=verify_native_cubic_path_encoding(original,output,slide_extents=(100,100))
        self.assertEqual(before,(ET.tostring(original),ET.tostring(output)))
        self.assertTrue(proof['path_attributes_and_dimensions_unchanged'])
        self.assertFalse(proof['parent_orientation_paint_and_source_identity_verified'])
        self.assertTrue(all(F(v)<=F(1,2) for v in proof['maximum_new_coordinate_error_slide_emu']))

    def test_nonunit_path_scale_enforces_slide_not_only_local_cap(self):
        source=fractional_intersections(); candidate=rounded(normalize(source)['commands'])
        original=xml_path(source); output=xml_path(candidate)
        verify_native_cubic_path_encoding(original,output,slide_extents=(50,50),max_slide_coordinate_error=F(1,4))
        with self.assertRaises(Unsupported):verify_native_cubic_path_encoding(original,output,slide_extents=(200,200))

    def test_path_dimensions_attributes_cannot_change(self):
        source=ROUND; original=xml_path(source); output=xml_path(rounded(normalize(source)['commands']))
        for key,value in [('w','101'),('stroke','false'),('extrusionOk','false')]:
            altered=deepcopy(output);altered.set(key,value)
            with self.subTest(key=key),self.assertRaises(Unsupported):verify_native_cubic_path_encoding(original,altered,slide_extents=(100,100))

    def test_outside_and_negative_controls_are_not_clipped_by_path_dimensions(self):
        source=translated(ROUND,-20,-20);original=xml_path(source,width=1,height=1)
        result=decode_native_cubic_path(original)
        self.assertEqual(result['commands'],source)
        output=xml_path(rounded(normalize(source)['commands']),width=1,height=1)
        verify_native_cubic_path_encoding(original,output,slide_extents=(1,1))

    def test_native_integer_syntax_foreign_nodes_and_formula_rejected(self):
        for text in ['01','+1','1.0','1e2','val x','9'*10000,str(2**31)]:
            node=xml_path(ROUND);node[0][0].set('x',text)
            with self.subTest(text=text[:20]),self.assertRaises(Unsupported):decode_native_cubic_path(node)
        for mutation in ['namespace','extra_node','nested_point','text','fill']:
            node=xml_path(ROUND)
            if mutation=='namespace':node[0].tag='{foreign}moveTo'
            elif mutation=='extra_node':ET.SubElement(node,A+'arcTo')
            elif mutation=='nested_point':ET.SubElement(node[0][0],A+'pt')
            elif mutation=='text':node.text=' '*100000
            else:node.set('fill','none')
            with self.subTest(mutation=mutation),self.assertRaises(Unsupported):decode_native_cubic_path(node)

    def test_malformed_element_tags_and_attribute_containers_fail_closed(self):
        node=xml_path(ROUND);node[0].tag=[]
        with self.assertRaises(Unsupported):decode_native_cubic_path(node)
        class Trap(dict):
            def __iter__(self):raise AssertionError('Must reject before attribute traversal')
        node=xml_path(ROUND);node.attrib=Trap({'w':'100','h':'100',**{f'bad{i}':'x' for i in range(10000)}})
        with self.assertRaises(Unsupported):decode_native_cubic_path(node)
        node=xml_path(ROUND);node[0][0].attrib=Trap({'x':'0','y':'0'})
        with self.assertRaises(Unsupported):decode_native_cubic_path(node)
        node=xml_path(ROUND);node.attrib.update({f'bad{i}':'x' for i in range(10000)})
        with self.assertRaises(Unsupported):decode_native_cubic_path(node)

    def test_decode_and_geometry_share_operation_budget(self):
        source=ROUND; original=xml_path(source); output=xml_path(rounded(normalize(source)['commands']))
        for limit in [1,100,500]:
            with self.subTest(limit=limit),self.assertRaises(Unsupported):verify_native_cubic_path_encoding(original,output,slide_extents=(100,100),max_operations=limit)
        with self.assertRaises(Unsupported):verify_native_cubic_path_encoding(original,output,slide_extents=(100.,100))


if __name__ == '__main__':
    unittest.main()
