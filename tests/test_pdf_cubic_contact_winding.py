"""Endpoint provenance, whole-C restoration and shared native proof budgets.

All maintained fixtures are synthetic; no author PDF/image data is required.
"""
from fractions import Fraction as F
from copy import deepcopy
import unittest
import os
from pathlib import Path
import subprocess
import sys

try:
    import pymupdf
except ImportError:
    pymupdf = None
from figure_rebuild import pdf_cubic_winding as _cubic
from figure_rebuild.pdf_cubic_contact_winding import (
    normalize_endpoint_contact_cubic_fill as normalize,
    verify_endpoint_contact_cubic_integer_encoding as verify,
    UnsupportedPdfCubicWindingError as Unsupported,
    _normalize_with_budget,
)
from figure_rebuild.native_cubic_geometry import (
    decode_native_cubic_path,
    verify_native_cubic_path_encoding,
    _verify_native_cubic_path_encoding_with_budget,
)
from xml.etree import ElementTree as ET
R=[('M',(0,0)),('C',(1,-1),(3,-1),(4,0)),('L',(4,4)),('L',(0,4)),('Z',)]
def rect(a,b,c,d):return [('M',(a,b)),('L',(c,b)),('L',(c,d)),('L',(a,d)),('Z',)]
def tf(cmd,dx=0,dy=0,sx=1,sy=1):return [(c[0],*[(sx*x+dx,sy*y+dy) for x,y in c[1:]]) for c in cmd]
def reverse(commands):
 lim=_cubic._limits(128,512,2048,100000,80);b=_cubic._Arithmetic(100000);_,contours,_,_=_cubic._parse(commands,lim,b);out=[]
 for row in contours:
  out.append(('M',row[-1]['points'][-1]))
  for s in reversed(row):out.append((s['kind'],*reversed(s['points'][:-1])))
  out.append(('Z',))
 return out
def rounded(commands):
 def rnd(x):x=F(x)+F(1,2);return x.numerator//x.denominator
 return [(c[0],*[tuple(rnd(x) for x in p) for p in c[1:]]) for c in commands]
class ContactCurveIdentityTests(unittest.TestCase):
 def test_single(self):
  r=normalize(R);self.assertEqual(len(r['curve_identities']),1);self.assertEqual(r['proof']['output_proof_C_fragments'],0)
 def test_contact_union_preserves_C(self):
  src=R+rect(4,0,6,4);copy=deepcopy(src);r=normalize(src);self.assertEqual(src,copy);self.assertEqual(r['curve_identities'][0]['decision'],'restored_whole');verify(src,rounded(r['commands']))
 def test_reverse_contact_union(self):
  src=reverse(R+rect(4,0,6,4));r=normalize(src);verify(src,rounded(r['commands']))
 def test_nonboundary_C_fully_removed(self):
  r=normalize(rect(-3,-3,8,8)+R);self.assertEqual(r['curve_identities'][0]['decision'],'discarded_nonboundary');self.assertFalse(any(c[0]=='C' for c in r['commands']))
 def test_hole_and_island(self):
  src=rect(-3,-3,8,8)+reverse(R)+rect(1,1,3,3);r=normalize(src);self.assertEqual(len(r['proof']['chord_loop_topology']['orientation']),3);verify(src,rounded(r['commands']))
 def test_touching_boundary_rejected(self):
  with self.assertRaises(Unsupported) as ctx:normalize(R+rect(4,-2,6,0))
  self.assertEqual(ctx.exception.code,'touching_boundary')
 def test_true_C_line_cross_rejected(self):
  with self.assertRaises(Unsupported) as ctx:normalize(R+rect(2,-3,6,3))
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_true_C_C_cross_rejected(self):
  with self.assertRaises(Unsupported):normalize(R+[('M',(2,-2)),('C',(1,0),(3,1),(5,1)),('L',(5,4)),('L',(2,4)),('Z',)])
 def test_closed_single_C_rejected(self):
  with self.assertRaises(Unsupported):normalize([('M',(0,0)),('C',(4,4),(-4,4),(0,0)),('Z',)])
 def test_identical_C_overlap_rejected(self):
  with self.assertRaises(Unsupported):normalize(R+reverse(R))
 def test_protected_control_one_unit_move_rejected(self):
  out=rounded(normalize(R)['commands']);i=next(i for i,c in enumerate(out) if c[0]=='C');c=out[i];out[i]=('C',(c[1][0]+1,c[1][1]),*c[2:])
  with self.assertRaises(Unsupported):verify(R,out)
 def test_narrow_LL_grid_merge_rejected(self):
  src=tf(R,200,200)+rect(-10,-10,5,10)+[('M',(0,0)),('L',(100,1)),('L',(100,2)),('Z',)]
  exact=normalize(src)
  with self.assertRaises(Unsupported):verify(src,rounded(exact['commands']))
 def test_native_floats_rejected(self):
  src=[('M',(0.0,0)),*R[1:]]
  with self.assertRaises(Unsupported):verify(src,rounded(normalize(R)['commands']))
 def test_tiny_budget_rejected(self):
  with self.assertRaises(Unsupported) as ctx:normalize(R,max_operations=1)
  self.assertEqual(ctx.exception.code,'budget')
 def test_large_input_rejected(self):
  with self.assertRaises(Unsupported):normalize(R*120)
 def test_fraction_bit_budget_rejected(self):
  with self.assertRaises(Unsupported):normalize([('M',(F(1,2**5000+1),0)),*R[1:]])
 def test_budget_types(self):
  for v in [False,0,1.5]:
   with self.assertRaises(ValueError):normalize(R,max_operations=v)
 def test_subdivision_budget_types(self):
  for v in [True,2,-1,0.5]:
   with self.assertRaises(ValueError):normalize(R,proof_split_depth=v)
class EndpointIdentityTests(unittest.TestCase):
 def fixture(self):
  a=[('M',(-4,-4)),('C',(-2,-2),(2,2),(4,4)),('L',(-4,4)),('Z',)]
  b=[('M',(-4,4)),('C',(-2,2),(2,-2),(4,-4)),('L',(-4,-4)),('Z',)]
  return a+b
 def test_two_midpoint_interior_cross_boundary_rejected(self):
  with self.assertRaises(Unsupported) as ctx:normalize(self.fixture())
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_covered_two_midpoint_cross_rejected_before_arrangement(self):
  source=rect(-10,-10,10,10)*2+self.fixture()
  with self.assertRaises(Unsupported) as ctx:normalize(source)
  self.assertEqual(ctx.exception.code,'contact_domain')
  bad=[x for x in ctx.exception.details['blockers'] if x['coincident_piece_endpoint']==['0','0']]
  self.assertTrue(bad);self.assertTrue(all(x['admitted_endpoint'] is None for x in bad))
 def test_independent_integer_reproof_rejects_covered_source_cross(self):
  source=rect(-10,-10,10,10)*2+self.fixture()
  with self.assertRaises(Unsupported) as ctx:verify(source,rect(-10,-10,10,10))
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_shared_original_endpoint_does_not_admit_other_interior_cross(self):
  # Both originals start (0,0), and meet again at t=1/2 at (4,0).
  # Derivatives there are (9,-9) and (9,15), so this is transverse.
  a=[('M',(0,0)),('C',(2,6),(6,-6),(8,0)),('L',(8,15)),('L',(0,15)),('Z',)]
  b=[('M',(0,0)),('C',(2,-6),(6,2),(8,12)),('L',(8,-15)),('L',(0,-15)),('Z',)]
  with self.assertRaises(Unsupported) as ctx:normalize(rect(-20,-20,20,20)*3+a+b)
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_C_midpoint_vs_other_original_C_endpoint_rejected(self):
  a=[('M',(-4,-4)),('C',(-2,-2),(2,2),(4,4)),('L',(-4,4)),('Z',)]
  b=[('M',(0,0)),('C',(1,-1),(3,-3),(4,-4)),('L',(0,-4)),('Z',)]
  with self.assertRaises(Unsupported) as ctx:normalize(rect(-10,-10,10,10)*3+a+b)
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_C_midpoint_vs_L_endpoint_rejected(self):
  a=[('M',(-4,-4)),('C',(-2,-2),(2,2),(4,4)),('L',(-4,4)),('Z',)]
  with self.assertRaises(Unsupported) as ctx:normalize(rect(-10,-10,10,10)*3+a+rect(0,-2,2,0))
  self.assertEqual(ctx.exception.code,'contact_domain')
 def test_same_C_consecutive_midpoint_is_allowed_and_identified(self):
  r=normalize(R)
  joins=[x for x in r['proof']['contact_graph'] if x['endpoint_identity_kind']=='same_original_C_consecutive_proof_join']
  self.assertEqual(len(joins),1);self.assertEqual(joins[0]['original_segments'][0],joins[0]['original_segments'][1])
  self.assertEqual(r['proof']['output_proof_C_fragments'],0)


LENS = [('M', (0, 0)), ('C', (1, -2), (3, -2), (4, 0)),
        ('C', (3, 2), (1, 2), (0, 0)), ('Z',)]
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def xml_path(commands, width=100, height=100):
    path = ET.Element(A+'path', {'w': str(width), 'h': str(height)})
    for command in commands:
        node = ET.SubElement(path, A+{'M': 'moveTo', 'L': 'lnTo',
                                    'C': 'cubicBezTo', 'Z': 'close'}[command[0]])
        for point in command[1:]:
            ET.SubElement(node, A+'pt', dict(zip(('x', 'y'), map(str, point))))
    return path


class ContactNativeAndBudgetTests(unittest.TestCase):
    def test_optional_native_attributes_require_builtin_strings(self):
        class EqualToAnything:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class HookedString(str):
            def __eq__(self, other):
                raise RuntimeError('Attribute comparison hook must not run')

            def __ne__(self, other):
                raise RuntimeError('Attribute comparison hook must not run')

        for key in ('fill', 'stroke', 'extrusionOk'):
            for value in (EqualToAnything(), HookedString('norm'), True, None):
                source = xml_path(R)
                source.attrib[key] = value
                with self.subTest(key=key, value_type=type(value).__name__):
                    with self.assertRaises(Unsupported) as caught:
                        decode_native_cubic_path(source)
                    self.assertEqual(caught.exception.code, 'native_xml')
                    for mode in ('isolated', 'endpoint_contact'):
                        with self.assertRaises(Unsupported) as caught:
                            verify_native_cubic_path_encoding(
                                source, xml_path(R), slide_extents=(100, 100), proof_mode=mode)
                        self.assertEqual(caught.exception.code, 'native_xml')

    def test_lens_requires_proof_halves_but_keeps_two_whole_Cs(self):
        with self.assertRaises(Unsupported):
            normalize(LENS, proof_split_depth=0)
        result = normalize(LENS, proof_split_depth=1)
        self.assertEqual(sum(c[0] == 'C' for c in result['commands']), 2)
        self.assertEqual(result['proof']['proof_mode'], 'endpoint_contact')
        self.assertEqual(result['proof']['source_proxy_segment_limit'], 256)
        self.assertTrue(all(row['decision'] == 'restored_whole'
                            for row in result['curve_identities']))
        verify(LENS, rounded(result['commands']))

    def test_default_native_stays_isolated(self):
        source = xml_path(LENS)
        candidate = xml_path(rounded(normalize(LENS)['commands']))
        with self.assertRaises(Unsupported):
            verify_native_cubic_path_encoding(source, candidate, slide_extents=(100, 100))
        proof = verify_native_cubic_path_encoding(
            source, candidate, slide_extents=(100, 100), proof_mode='endpoint_contact')
        self.assertEqual(proof['proof_split_depth'], 1)
        self.assertEqual(proof['geometry']['proof_mode'], 'endpoint_contact')
        self.assertEqual(proof['maximum_new_coordinate_error_slide_emu'], ['0', '0'])

    def test_isolated_default_and_explicit_receipts_identical(self):
        source = xml_path(R)
        candidate = xml_path(rounded(_cubic.normalize_isolated_cubic_fill(R)['commands']))
        a = verify_native_cubic_path_encoding(source, candidate, slide_extents=(100, 100))
        b = verify_native_cubic_path_encoding(source, candidate, slide_extents=(100, 100), proof_mode='isolated')
        self.assertEqual(a, b)
        self.assertNotIn('proof_mode', a)
        self.assertNotIn('proof_mode', a['geometry'])

    def test_unknown_or_conflicting_proof_configuration_rejected(self):
        source = xml_path(R)
        for kwargs in ({'proof_mode': 'auto'}, {'proof_mode': True},
                       {'proof_mode': 'isolated', 'proof_split_depth': 0},
                       {'proof_mode': 'endpoint_contact', 'proof_split_depth': True},
                       {'proof_mode': 'endpoint_contact', 'proof_split_depth': 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                verify_native_cubic_path_encoding(source, source, slide_extents=(100, 100), **kwargs)

    def test_each_explicit_geometry_budget_propagates(self):
        for kwargs in ({'max_input_segments': 1}, {'max_commands': 2},
                       {'max_atomic_edges': 1}, {'max_operations': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Unsupported) as caught:
                normalize(LENS, **kwargs)
            self.assertEqual(caught.exception.code, 'budget')
        for key in ('max_input_segments', 'max_commands', 'max_atomic_edges',
                    'max_operations', 'max_probe_halvings'):
            for value in (0, True, 1.5):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize(R, **{key: value})
        result = normalize(LENS, max_input_segments=2, max_commands=4,
                           max_atomic_edges=4, max_probe_halvings=1)
        self.assertEqual(result['proof']['source_proxy_segment_limit'], 4)
        self.assertEqual(result['proof']['output_proxy_segment_limit'], 8)

    def test_private_counter_phase_deltas_and_no_reset(self):
        limits = _cubic._limits(128, 512, 2048, 1_000_000, 80)
        budget = _cubic._Arithmetic(1_000_000)
        budget.spend(19)
        result = _normalize_with_budget(LENS, limits, budget)
        normalization_cost = budget.used - 19
        self.assertEqual(result['proof']['exact_predicate_operations'], normalization_cost)
        source = xml_path(LENS)
        candidate = xml_path(rounded(result['commands']))
        start = budget.used
        proof = _verify_native_cubic_path_encoding_with_budget(
            source, candidate, limits, budget, slide_extents=(100, 100),
            proof_mode='endpoint_contact')
        verify_cost = budget.used - start
        self.assertEqual(proof['exact_predicate_operations_including_decode'], verify_cost)
        public = verify_native_cubic_path_encoding(
            source, candidate, slide_extents=(100, 100), proof_mode='endpoint_contact')
        self.assertEqual(public, proof)
        tight = _cubic._Arithmetic(normalization_cost + verify_cost - 1)
        _normalize_with_budget(LENS, limits, tight)
        with self.assertRaises(Unsupported) as caught:
            _verify_native_cubic_path_encoding_with_budget(
                source, candidate, limits, tight, slide_extents=(100, 100),
                proof_mode='endpoint_contact')
        self.assertEqual(caught.exception.code, 'budget')

    def test_one_public_native_budget_includes_decode_and_reproof(self):
        source = xml_path(LENS)
        candidate = xml_path(rounded(normalize(LENS)['commands']))
        result = verify_native_cubic_path_encoding(
            source, candidate, slide_extents=(100, 100), proof_mode='endpoint_contact')
        total = result['exact_predicate_operations_including_decode']
        geometry = result['geometry']['exact_predicate_operations']
        self.assertGreater(total, geometry)
        with self.assertRaises(Unsupported):
            verify_native_cubic_path_encoding(
                source, candidate, slide_extents=(100, 100),
                proof_mode='endpoint_contact', max_operations=total-1)
        verify_native_cubic_path_encoding(
            source, candidate, slide_extents=(100, 100),
            proof_mode='endpoint_contact', max_operations=total)

    def test_slide_error_bound_applies_after_anisotropic_mapping(self):
        src = R + [('M', (2, 2)), ('L', (7, 3)), ('L', (3, 7)), ('Z',)]
        result = normalize(src)
        candidate = rounded(result['commands'])
        source_xml = xml_path(src)
        candidate_xml = xml_path(candidate)
        verify_native_cubic_path_encoding(
            source_xml, candidate_xml, slide_extents=(100, 100), proof_mode='endpoint_contact')
        with self.assertRaises(Unsupported):
            verify_native_cubic_path_encoding(
                source_xml, candidate_xml, slide_extents=(10000, 10000), proof_mode='endpoint_contact')

    def test_zero_negative_and_empty_policies(self):
        self.assertEqual(normalize([])['commands'], [])
        src = [('M', (-0.0, 0)), *R[1:]]
        self.assertEqual(normalize(src)['commands'], normalize(R)['commands'])
        old = tf(R, -10, -10)
        result = normalize(old)
        verify_native_cubic_path_encoding(
            xml_path(old, 1, 1), xml_path(rounded(result['commands']), 1, 1),
            slide_extents=(1, 1), proof_mode='endpoint_contact')


class ContactRenderingAndFormattingTests(unittest.TestCase):
    def test_host_decimal_formatter_limit_is_not_changed(self):
        code = """
from fractions import Fraction as F
from figure_rebuild.pdf_cubic_contact_winding import normalize_endpoint_contact_cubic_fill, UnsupportedPdfCubicWindingError
import sys
n=2**2200
source=[('M',(n,0)),('C',(n+1,-1),(n+3,-1),(n+4,0)),('L',(n+4,4)),('L',(n,4)),('Z',)]
try: normalize_endpoint_contact_cubic_fill(source)
except UnsupportedPdfCubicWindingError as error:
    assert error.code=='budget', error.code
    assert sys.get_int_max_str_digits()==640
    print('explicit-budget')
else: raise AssertionError('Decimal receipt guard not exercised')
"""
        env = {**os.environ, 'PYTHONINTMAXSTRDIGITS': '640',
               'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
        run = subprocess.run([sys.executable, '-c', code], env=env,
                             text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('explicit-budget', run.stdout)

    @unittest.skipIf(pymupdf is None, 'Optional PDF oracle requires PyMuPDF')
    def test_actual_pdf_contact_union_lens_and_hole_apply_alpha_once(self):
        def pixels(commands, operator, alpha, scale):
            with pymupdf.open() as doc:
                page = doc.new_page(width=20, height=20)
                rows = ['q 1 0 0 1 5 5 cm /A gs .3 .5 .8 rg']
                for command in commands:
                    values = ' '.join(format(float(v), '.17f')
                                      for point in command[1:] for v in point)
                    rows.append('h' if command[0] == 'Z' else
                                values+' '+{'M':'m', 'L':'l', 'C':'c'}[command[0]])
                rows.append(operator+' Q')
                doc.xref_set_key(page.xref, 'Resources',
                    '<< /ExtGState << /A << /ca '+str(alpha)+' >> >> >>')
                xref = doc.get_new_xref()
                doc.update_object(xref, '<< >>')
                doc.update_stream(xref, '\n'.join(rows).encode())
                page.set_contents(xref)
                return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                       alpha=True).samples
        for source in (R+rect(4,0,6,4), LENS,
                       rect(-3,-3,8,8)+reverse(R)+rect(1,1,3,3)):
            result = normalize(source)
            for alpha in (1, .37, .5):
                for scale in (1, 4, 16):
                    with self.subTest(alpha=alpha, scale=scale, source=source):
                        self.assertEqual(pixels(source, 'f', alpha, scale),
                                         pixels(result['commands'], 'f*', alpha, scale))


if __name__ == '__main__':
    unittest.main()
