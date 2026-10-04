"""The transverse domain must still authenticate the real restored native tree."""
from copy import deepcopy
from fractions import Fraction as F
from xml.etree import ElementTree as E
import unittest
from unittest.mock import patch

from figure_rebuild import native_cubic_winding as W
from figure_rebuild import pdf_cubic_transverse_winding as T
from figure_rebuild.native_cubic_geometry import decode_native_cubic_path,verify_native_cubic_path_encoding
from test_native_cubic_winding import fixture,find,ROUND
from test_pdf_cubic_transverse_winding import FIXTURES,commands

def transverse_fixture(placement=(0,0,1000,1000),opacity=.37):
    native=commands(FIXTURES['cases'][0]['commands'])
    source=[(c[0],*[(float(x/9525),float(y/9525)) for x,y in c[1:]]) for c in native]
    return fixture(source,opacity=opacity,placement=placement)


class NativeTransverseTests(unittest.TestCase):
    def call(self,parts,expected='applied',**options):
        root,manifest,mapping,placement=parts;before=E.tostring(root)
        m,p=deepcopy(manifest),deepcopy(mapping)
        result=W.normalize_native_cubic_fill(root,object_id='curve',manifest=manifest,object_map=mapping,
            placement=placement,max_operations=options.pop('max_operations',4_000_000),
            proof_mode=options.pop('proof_mode','transverse_line'),**options)
        self.assertEqual(result['status'],expected,result)
        self.assertEqual(manifest,m);self.assertEqual(mapping,p)
        if expected!='applied':self.assertEqual(E.tostring(root),before)
        return result

    def test_real_integer_curve_fixture_keeps_alpha_frame_and_one_compound_paint(self):
        parts=transverse_fixture();old=deepcopy(find(parts[0],'.//p:spPr'))
        result=self.call(parts)
        self.assertEqual(result['proof_mode'],'transverse_line')
        self.assertEqual(result['policy'],W.TRANSVERSE_POLICY)
        self.assertEqual(result['paint_verification']['native_alpha_units'],37000)
        new=find(parts[0],'.//p:spPr')
        for tag in ('xfrm','solidFill','ln'):
            self.assertEqual(E.tostring(old.find(W.A+tag)),E.tostring(new.find(W.A+tag)))
        self.assertEqual(len(new.findall('.//'+W.A+'path')),1)
        self.assertFalse(result['existing_source_error_in_additional_half_emu_bound'])
        final=result['final_geometry_verification']
        self.assertTrue(final['geometry']['actual_integer_candidate_reproved'])
        self.assertTrue(all(F(v)<=F(1,2) for v in final['maximum_new_coordinate_error_slide_emu']))

    def test_classifier_selects_from_actual_geometry_without_fallback(self):
        result=self.call(transverse_fixture(),proof_mode='classified_contact_or_transverse')
        self.assertEqual(result['source_domain_selection']['selected_proof_mode'],'transverse_line')
        contact=self.call(fixture(ROUND),'not_applicable',proof_mode='classified_contact_or_transverse')
        self.assertEqual(contact['proof_mode'],'endpoint_contact')
        with patch.object(T,'_normalize_with_budget',side_effect=T.Unsupported('test_failure','transverse failed')), \
             patch.object(W,'_normalize_contact_with_budget') as old:
            self.call(transverse_fixture(),'rejected',proof_mode='classified_contact_or_transverse')
            old.assert_not_called()
        with patch.object(W,'_normalize_contact_with_budget',side_effect=T.Unsupported('test_failure','contact failed')), \
             patch.object(T,'_normalize_with_budget') as new:
            self.call(fixture(ROUND),'rejected',proof_mode='classified_contact_or_transverse')
            new.assert_not_called()
        with patch.object(T,'_classify_with_budget',side_effect=T.Unsupported('classification_domain','unknown')), \
             patch.object(W,'_normalize_contact_with_budget') as old, \
             patch.object(T,'_normalize_with_budget') as new:
            self.call(transverse_fixture(),'rejected',proof_mode='classified_contact_or_transverse')
            old.assert_not_called();new.assert_not_called()

    def test_legacy_contact_domain_does_not_accept_actual_crossings(self):
        result=self.call(transverse_fixture(),'rejected',proof_mode='endpoint_contact')
        self.assertEqual(result['reason_code'],'contact_domain')
        self.call(fixture(ROUND),'rejected',proof_mode='transverse_line')

    def test_authenticated_input_guards_reject_without_partial_mutation(self):
        mutations={
            'duplicate_source':lambda r,m,mp,p:m['objects'].append(deepcopy(m['objects'][0])),
            'duplicate_map':lambda r,m,mp,p:mp['objects'].append(deepcopy(mp['objects'][0])),
            'different_point':lambda r,m,mp,p:find(r,'.//a:cubicBezTo/a:pt').set('x','17'),
            'foreign_point':lambda r,m,mp,p:setattr(find(r,'.//a:cubicBezTo/a:pt'),'tag','{wrong}pt'),
            'dimensions':lambda r,m,mp,p:find(r,'.//a:path').set('w','10'),
            'parent_transform':lambda r,m,mp,p:find(r,'.//p:grpSpPr/a:xfrm').set('rot','1'),
            'shape_frame':lambda r,m,mp,p:find(r,'.//p:spPr/a:xfrm/a:off').set('x','300'),
            'native_color':lambda r,m,mp,p:find(r,'.//a:srgbClr').set('val','FF0000'),
            'native_alpha':lambda r,m,mp,p:find(r,'.//a:alpha').set('val','37001'),
            'effect':lambda r,m,mp,p:E.SubElement(find(r,'.//p:spPr'),W.A+'effectLst'),
            'source_stroke':lambda r,m,mp,p:m['objects'][0]['style'].update(stroke='#000000'),
            'forged_source_box':lambda r,m,mp,p:mp['objects'][0]['box'].update(x=3),
            'placement':lambda r,m,mp,p:p.__setitem__(0,1),
            'malformed_canvas':lambda r,m,mp,p:m.update(source_canvas_clip={'objects':['curve']}),
            'nonfinite_opacity':lambda r,m,mp,p:m['objects'][0]['style'].update(opacity=float('nan')),
            'bool_placement':lambda r,m,mp,p:p.__setitem__(0,False),
        }
        for label,mutate in mutations.items():
            with self.subTest(label=label):
                parts=transverse_fixture();mutate(*parts);self.call(parts,'rejected',proof_mode='classified_contact_or_transverse')

    def test_canvas_selected_and_intrinsic_marker_never_enter_new_geometry(self):
        for marker in (False,True):
            parts=transverse_fixture()
            if marker:find(parts[0],'.//p:sp/p:nvSpPr/p:cNvPr').set('descr','source_canvas_clip_required=true')
            else:parts[1]['source_canvas_clip']={'objects':[{'object_id':'curve'}]}
            result=self.call(parts,'not_applicable',proof_mode='classified_contact_or_transverse')
            self.assertEqual(result['reason_code'],'keep_original_canvas_clip_geometry')

    def test_new_total_error_is_limited_in_actual_slide_units(self):
        result=self.call(transverse_fixture(placement=(0,0,500,500)))
        self.assertTrue(all(F(v)<=F(1,4) for v in result['final_geometry_verification']['maximum_new_coordinate_error_slide_emu']))
        result=self.call(transverse_fixture(placement=(0,0,3000,3000)),'rejected')
        self.assertEqual(result['reason_code'],'grid_error')

    def test_final_native_XML_verifier_rejects_corrupted_proposal(self):
        original=T._normalize_with_budget;calls=[]
        def corrupt(*args,**kwargs):
            result=original(*args,**kwargs);calls.append(1)
            if len(calls)==1:
                i=next(i for i,c in enumerate(result['commands']) if c[0]=='C');c=result['commands'][i]
                result['commands'][i]=('C',(c[1][0]+1,c[1][1]),*c[2:])
            return result
        with patch.object(T,'_normalize_with_budget',side_effect=corrupt):
            result=self.call(transverse_fixture(),'rejected')
            self.assertEqual(result['reason_code'],'encoding_identity')
            self.assertEqual(len(calls),2)

    def test_shared_budget_includes_context_classification_final_XML_and_serialization(self):
        result=self.call(transverse_fixture(),proof_mode='classified_contact_or_transverse')
        total=result['exact_predicate_operations_including_context']
        self.assertGreater(total,result['normalization']['exact_operations_including_receipt'])
        json_writer=W._json;completed_receipts=[]
        def observe(value):
            result=json_writer(value)
            if isinstance(value,dict) and value.get('status')=='applied' and 'final_geometry_verification' in value:
                completed_receipts.append(value)
            return result
        with patch.object(W,'_json',side_effect=observe):
            rejected=self.call(transverse_fixture(),'rejected',proof_mode='classified_contact_or_transverse',max_operations=total-1)
        self.assertEqual(rejected['reason_code'],'budget')
        # The proof and receipt already existed; only the last metered receipt
        # byte-block cost failed. The helper above still checks whole XML bytes.
        self.assertEqual(len(completed_receipts),1)

    def test_native_verifier_strict_XML_and_explicit_mode(self):
        parts=transverse_fixture();original=deepcopy(find(parts[0],'.//a:path'))
        self.call(parts);candidate=deepcopy(find(parts[0],'.//a:path'))
        ext=tuple(int(find(parts[0],'.//p:spPr/a:xfrm/a:ext').get(k)) for k in ('cx','cy'))
        result=verify_native_cubic_path_encoding(original,candidate,slide_extents=ext,proof_mode='transverse_line',max_operations=4_000_000)
        self.assertTrue(result['geometry']['verified'])
        for mutation in ('namespace','dimensions','extra_close'):
            value=deepcopy(candidate)
            if mutation=='namespace':value.find(W.A+'cubicBezTo/'+W.A+'pt').tag='pt'
            elif mutation=='dimensions':value.set('w',str(int(value.get('w'))+1))
            else:E.SubElement(value,W.A+'close')
            before=E.tostring(value)
            with self.assertRaises(ValueError):verify_native_cubic_path_encoding(original,value,slide_extents=ext,proof_mode='transverse_line',max_operations=4_000_000)
            self.assertEqual(E.tostring(value),before)
        for mode,depth in [('transverse_line',1),('classified_contact_or_transverse',1),(True,None)]:
            self.call(transverse_fixture(),'rejected',proof_mode=mode,proof_split_depth=depth)

if __name__=='__main__':unittest.main()
