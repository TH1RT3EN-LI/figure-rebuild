"""Original integer curve events, complete accounting and integer topology."""
import copy
import json
from pathlib import Path
from fractions import Fraction as F
import unittest
from unittest.mock import patch

from figure_rebuild import pdf_cubic_transverse_winding as T

FIXTURES=json.loads((Path(__file__).parent/'fixtures/cubic-transverse-native.json').read_text())

def commands(value):
    return [(v[0],*[tuple(F(x) for x in p) for p in v[1:]]) for v in value]

R=[('M',(0,0)),('C',(1,-1),(3,-1),(4,0)),('L',(4,4)),('L',(0,4)),('Z',)]
def rect(x,y,w,h):return [('M',(x,y)),('L',(x+w,y)),('L',(x+w,y+h)),('L',(x,y+h)),('Z',)]


class TransverseTests(unittest.TestCase):
    def test_four_original_native_cases_and_complete_accounting(self):
        for case in FIXTURES['cases']:
            with self.subTest(case=case['id']):
                source=commands(case['commands']);before=copy.deepcopy(source)
                result=T.normalize_transverse_line_cubic_fill(source,max_operations=1_000_000)
                self.assertEqual(result['commands'],commands(case['expected_integer_commands']))
                self.assertEqual(source,before)
                proof=result['proof']
                self.assertEqual(proof['final_integer_topology'],proof['source_boundary_topology'])
                self.assertEqual(len(proof['final_integer_topology']['orientation']),case['loop_count'])
                self.assertEqual(len(proof['events']),2)
                self.assertEqual(len(proof['source_fragment_pieces']),len(proof['complete_source_fragment_decisions']))
                self.assertTrue(all(F(x)<F(1,2) for x in proof['maximum_total_new_control_error_local']))
                self.assertIn(-2,{r['exact_winding_label'] for r in proof['source_signed_support_quotient_faces']['face_boundary_cycles']})
                self.assertGreater(proof['phase_work_units_before_receipt']['integer_encoding'],0)
                verified=T.verify_transverse_line_cubic_integer_encoding(source,result['commands'])
                self.assertTrue(verified['source_recomputed'])
                self.assertTrue(verified['actual_integer_candidate_reproved'])

    def test_minimal_real_counterexample_reflection_rotation_and_negative_coordinates(self):
        source=commands(FIXTURES['minimal_commands'])
        for a,b,c,d,e,f in [(1,0,0,1,-100000,-100000),(0,-1,1,0,5000,7000),(-1,0,0,1,0,0)]:
            transformed=[(v[0],*[(a*x+c*y+e,b*x+d*y+f) for x,y in v[1:]]) for v in source]
            result=T.normalize_transverse_line_cubic_fill(transformed)
            self.assertTrue(T.verify_transverse_line_cubic_integer_encoding(transformed,result['commands'])['verified'])

    def test_rejects_true_multiple_events_LL_crossing_tangency_and_CC(self):
        for value,code in [(R+rect(1,-2,2,4),'event_budget'),(R+rect(2,2,4,4),'LL_domain')]:
            with self.assertRaises(T.Unsupported) as error:T.normalize_transverse_line_cubic_fill(value)
            self.assertEqual(error.exception.code,code)
        curve={'id':'C','points':[(F(0),F(0)),(F(4),F(-4)),(F(12),F(-4)),(F(16),F(0))]}
        line={'id':'L','points':[(F(-4),F(-3)),(F(20),F(-3))]}
        with self.assertRaises(T.Unsupported):T.event(curve,line,T.Arithmetic(100000),80)
        source=commands(FIXTURES['cases'][0]['commands'])
        with self.assertRaises(T.Unsupported) as error:T.normalize_transverse_line_cubic_fill(source+source)
        self.assertEqual(error.exception.code,'CC_domain')

    def test_numeric_closed_input_and_budgets(self):
        source=commands(FIXTURES['cases'][0]['commands'])
        for value in [[('Z',),*source],source[:-1],[('M',(True,0)),*source[1:]],
                      [('M',(float('nan'),0)),*source[1:]],[('M',(F(1,2),0)),*source[1:]],
                      [('M',(2**32,0)),*source[1:]]]:
            with self.assertRaises(T.Unsupported):T.normalize_transverse_line_cubic_fill(value)
        for options in [{'root_steps':True},{'root_steps':100},{'max_operations':True},
                        {'max_coordinate_error':(True,F(1,2))},{'max_input_segments':129},
                        {'max_commands':513},{'max_atomic_edges':2049},{'max_operations':8_000_001}]:
            with self.assertRaises(ValueError):T.normalize_transverse_line_cubic_fill(source,**options)
        for options in [{'max_operations':1},{'root_steps':16},{'max_coordinate_error':0}]:
            with self.assertRaises(T.Unsupported):T.normalize_transverse_line_cubic_fill(source,**options)

    def test_final_actual_candidate_corruption_cannot_reuse_proof(self):
        source=commands(FIXTURES['cases'][0]['commands']);result=T.normalize_transverse_line_cubic_fill(source)
        changed=copy.deepcopy(result['commands']);i=next(i for i,c in enumerate(changed) if c[0]=='C');c=changed[i]
        changed[i]=('C',(c[1][0]+1,c[1][1]),*c[2:])
        with self.assertRaises(T.Unsupported) as error:T.verify_transverse_line_cubic_integer_encoding(source,changed)
        self.assertEqual(error.exception.code,'encoding_identity')

    def test_budget_exhausts_during_encoding_and_later_candidate_parse(self):
        source=commands(FIXTURES['cases'][0]['commands']);entered=[];original=T.encode
        def observe(*args):entered.append(args[4].used);return original(*args)
        with patch.object(T,'encode',side_effect=observe):
            result=T.normalize_transverse_line_cubic_fill(source)
            start=entered[-1]
            with self.assertRaises(T.Unsupported):T.normalize_transverse_line_cubic_fill(source,max_operations=start+1)
            self.assertEqual(entered,[start,start])
        with self.assertRaises(T.Unsupported):
            T.verify_transverse_line_cubic_integer_encoding(source,result['commands'],max_operations=result['proof']['exact_operations_including_receipt'])

    def test_final_curve_gate_rejects_crossings_and_cusp_independently(self):
        limits=T.C._limits(128,512,2048,1_000_000,80)
        T.K._candidate_rings(R,limits,T.Arithmetic(1_000_000),depth=1,integer=True)
        for value in [R[:-1]+[('L',(5,-2)),('Z',)],R+R,[('M',(0,0)),('C',(4,4),(-4,4),(0,0)),('Z',)]]:
            with self.assertRaises(T.Unsupported):T.K._candidate_rings(value,limits,T.Arithmetic(1_000_000),depth=1,integer=True)

    def test_event_at_exact_dyadic_root_has_one_shared_identity(self):
        curve={'id':'C','points':[(F(-4),F(-3)),(F(-1),F(-1)),(F(1),F(1)),(F(4),F(3))]}
        line={'id':'L','points':[(F(-5),F(0)),(F(5),F(0))]}
        result=T.event(curve,line,T.Arithmetic(100000),80)
        self.assertEqual(result['parameter'],(F(1,2),F(1,2)))
        self.assertEqual(result['controls']['left'][-1],result['controls']['right'][0])

    def test_classifier_selects_once_or_rejects_unknown(self):
        limits=T.C._limits(128,512,2048,1_000_000,80)
        got=T._classify_with_budget(commands(FIXTURES['cases'][0]['commands']),limits,T.Arithmetic(1_000_000))
        self.assertEqual(got['selected_proof_mode'],'transverse_line')
        self.assertFalse(got['normalizer_attempted_during_classification'])
        self.assertEqual(T._classify_with_budget(R,limits,T.Arithmetic(1_000_000))['selected_proof_mode'],'endpoint_contact')

if __name__=='__main__':unittest.main()
