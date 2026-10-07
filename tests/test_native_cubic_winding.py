"""Native context and producer binding: no caller proof authorizes a rewrite."""
from copy import deepcopy
from fractions import Fraction as F
import math
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from figure_rebuild import native_cubic_winding as w
from figure_rebuild.pdf_cubic_winding import normalize_isolated_cubic_fill

P,A=w.P,w.A
ROUND=[('M',(0,0)),('C',(1,-1),(3,-1),(4,0)),('L',(4,4)),('L',(0,4)),('Z',)]
OVERLAP=ROUND+[('M',(2,2)),('L',(7,3)),('L',(3,7)),('Z',)]


def fixture(commands=OVERLAP, opacity=.5, placement=(0,0,1000,1000)):
    scene=[]
    for c in commands:
        op={'M':'moveTo','L':'lineTo','C':'cubicTo','Z':'close'}[c[0]]
        values={} if c[0]=='Z' else dict(zip(('x1','y1','x2','y2','x','y') if c[0]=='C' else ('x','y'),[v for p in c[1:] for v in p]))
        scene.append({op:values})
    obj={'id':'curve','kind':'path','commands':scene,'style':{'fill':'#336699','stroke':'none','stroke_width':0,'opacity':opacity}}
    _,box=w._source_commands(obj,512)
    manifest={'canvas':{'width':1000,'height':1000},'objects':[obj]}
    mapping={'placement':list(placement),'objects':[{'id':'curve','kind':'path','box':box,'editable':True}]}
    root=ET.Element(P+'sld');content=ET.SubElement(root,P+'cSld');tree=ET.SubElement(content,P+'spTree')
    nv=ET.SubElement(tree,P+'nvGrpSpPr');ET.SubElement(nv,P+'cNvPr',{'id':'1','name':''});ET.SubElement(nv,P+'cNvGrpSpPr');ET.SubElement(nv,P+'nvPr')
    grp=ET.SubElement(tree,P+'grpSpPr');ET.SubElement(grp,A+'xfrm')
    shape=ET.SubElement(tree,P+'sp');nv=ET.SubElement(shape,P+'nvSpPr');ET.SubElement(nv,P+'cNvPr',{'id':'2','name':'curve','descr':'source_id=curve'});ET.SubElement(nv,P+'cNvSpPr');ET.SubElement(nv,P+'nvPr')
    props=ET.SubElement(shape,P+'spPr');xf=ET.SubElement(props,A+'xfrm',{'rot':'0'})
    scale=placement[2]/1000
    xy=[(placement[0]+box['x']*scale)*9525,(placement[1]+box['y']*scale)*9525]
    wh=[box['width']*scale*9525,box['height']*scale*9525]
    ET.SubElement(xf,A+'off',dict(zip(('x','y'),map(lambda v:str(math.floor(v+.5)),xy))))
    ET.SubElement(xf,A+'ext',dict(zip(('cx','cy'),map(lambda v:str(math.floor(v+.5)),wh))))
    geom=ET.SubElement(props,A+'custGeom');paths=ET.SubElement(geom,A+'pathLst');path=ET.SubElement(paths,A+'path',{'w':str(max(1,math.floor(box['width']*9525+.5))),'h':str(max(1,math.floor(box['height']*9525+.5)))})
    for c in commands:
        node=ET.SubElement(path,A+{'M':'moveTo','L':'lnTo','C':'cubicBezTo','Z':'close'}[c[0]])
        for x,y in c[1:]:ET.SubElement(node,A+'pt',{'x':str(math.floor((x-box['x'])*9525+.5)),'y':str(math.floor((y-box['y'])*9525+.5))})
    solid=ET.SubElement(props,A+'solidFill');color=ET.SubElement(solid,A+'srgbClr',{'val':'336699'});ET.SubElement(color,A+'alpha',{'val':str(math.floor(opacity*100000+.5))})
    line=ET.SubElement(props,A+'ln',{'w':'0'});ET.SubElement(line,A+'noFill');ET.SubElement(line,A+'prstDash',{'val':'solid'})
    return root,manifest,mapping,list(placement)


def find(root,path):
    node=root.find(path,{'p':P[1:-1],'a':A[1:-1]});assert node is not None,path;return node


class NativeCubicWindingTests(unittest.TestCase):
    def run_case(self,parts,expected='applied',**kwargs):
        root,m,mp,place=parts;before=ET.tostring(root);mc,pc=deepcopy(m),deepcopy(mp)
        receipt=w.normalize_native_cubic_fill(root,object_id='curve',manifest=m,object_map=mp,placement=place,**kwargs)
        self.assertEqual(receipt['status'],expected,receipt)
        self.assertEqual(m,mc);self.assertEqual(mp,pc)
        if expected!='applied':self.assertEqual(ET.tostring(root),before)
        return receipt

    def reject(self,code,mutate,**kwargs):
        with self.subTest(matrix=code):
            parts=fixture();mutate(*parts);return self.run_case(parts,'rejected',**kwargs)

    def test_positive_one_compound_alpha_and_separate_error_accounts(self):
        parts=fixture();old=deepcopy(find(parts[0],'.//p:spPr'));r=self.run_case(parts)
        new=find(parts[0],'.//p:spPr')
        for tag in ('xfrm','solidFill','ln'):
            self.assertEqual(ET.tostring(old.find(A+tag)),ET.tostring(new.find(A+tag)))
        self.assertTrue(r['mismatch_witnesses']);self.assertEqual(r['compound_paint_count'],1)
        self.assertEqual(r['paint_verification']['native_alpha_units'],50000)
        self.assertFalse(r['existing_source_error_in_additional_half_emu_bound'])
        self.assertTrue(r['final_geometry_verification']['geometry']['C_controls_and_protected_endpoints_identical'])
        self.assertEqual(len(new.findall('.//'+A+'path')),1)
        self.assertEqual(r['source_native_binding']['original_pdf_to_scene_error'],'NOT_RECOMPUTED_BY_THIS_WRAPPER')

    def test_noop_correct_curved_fill_keeps_original_xml(self):
        r=self.run_case(fixture(ROUND),'not_applicable')
        self.assertEqual(r['reason_code'],'no_proven_fill_rule_mismatch')

    def test_contact_mode_preserves_whole_curve_and_old_default_refusal(self):
        # The inner rectangle meets the genuine C endpoint at(4,0), and its
        # interior overlaps the rounded contour. This requires both the new
        # contact proof and a real even-winding witness before any rewrite.
        commands=ROUND+[('M',(2,0)),('L',(4,0)),('L',(4,4)),('L',(2,4)),('Z',)]
        old=self.run_case(fixture(commands),'rejected')
        self.assertEqual(old['reason_code'],'curve_hull_isolation')
        parts=fixture(commands);shape=deepcopy(find(parts[0],'.//p:spPr'))
        r=self.run_case(parts,proof_mode='endpoint_contact',proof_split_depth=1)
        self.assertEqual(r['policy'],w.CONTACT_POLICY)
        self.assertEqual(r['proof_mode'],'endpoint_contact')
        self.assertTrue(r['mismatch_witnesses'])
        actual=find(parts[0],'.//p:spPr')
        for tag in ('xfrm','solidFill','ln'):
            self.assertEqual(ET.tostring(shape.find(A+tag)),ET.tostring(actual.find(A+tag)))
        self.assertEqual([ET.tostring(n) for n in shape.findall('.//'+A+'cubicBezTo')],
                         [ET.tostring(n) for n in actual.findall('.//'+A+'cubicBezTo')])
        self.assertEqual(len(actual.findall('.//'+A+'path')),1)

    def test_contact_mode_still_needs_a_fill_mismatch(self):
        r=self.run_case(fixture(ROUND),'not_applicable',proof_mode='endpoint_contact')
        self.assertEqual(r['reason_code'],'no_proven_fill_rule_mismatch')

    def test_contact_mode_and_depth_are_closed_typed_options(self):
        for mode,depth in [('unknown',None),(True,None),('isolated',1),
                           ('endpoint_contact',True),('endpoint_contact',2)]:
            with self.subTest(mode=mode,depth=depth):
                self.run_case(fixture(),'rejected',proof_mode=mode,proof_split_depth=depth)

    def test_contact_shared_budget_includes_final_reproof_and_receipt(self):
        r=self.run_case(fixture(),proof_mode='endpoint_contact')
        total=r['exact_predicate_operations_including_context']
        phases=(r['normalization']['exact_predicate_operations']+
                r['final_geometry_verification']['exact_predicate_operations_including_decode'])
        self.assertGreater(total,phases)
        rejected=self.run_case(fixture(),'rejected',proof_mode='endpoint_contact',
                               max_operations=total-1)
        self.assertEqual(rejected['reason_code'],'budget')

    def test_contact_final_verifier_recomputes_source_after_corrupted_candidate(self):
        normal=w._normalize_contact_with_budget
        def corrupt(*args,**kwargs):
            result=normal(*args,**kwargs)
            i=next(i for i,c in enumerate(result['commands']) if c[0]=='C')
            c=result['commands'][i]
            result['commands'][i]=('C',(c[1][0]+1,c[1][1]),*c[2:])
            return result
        with patch.object(w,'_normalize_contact_with_budget',side_effect=corrupt):
            self.run_case(fixture(),'rejected',proof_mode='endpoint_contact')

    def test_contact_actual_interior_crossing_remains_unchanged(self):
        first=[('M',(-4,-4)),('C',(-2,-2),(2,2),(4,4)),('L',(-4,4)),('Z',)]
        second=[('M',(-4,4)),('C',(-2,2),(2,-2),(4,-4)),('L',(-4,-4)),('Z',)]
        outer=[('M',(-10,-10)),('L',(10,-10)),('L',(10,10)),('L',(-10,10)),('Z',)]
        for source in (first+second,outer*2+first+second):
            with self.subTest(covered=len(source)>len(first+second)):
                r=self.run_case(fixture(source),'rejected',proof_mode='endpoint_contact')
                self.assertEqual(r['reason_code'],'contact_domain')

    def test_I01_I05_source_and_actual_identities(self):
        self.reject('I01',lambda r,m,mp,p:m['objects'].append(deepcopy(m['objects'][0])))
        self.reject('I02',lambda r,m,mp,p:mp['objects'].append(deepcopy(mp['objects'][0])))
        self.reject('I03',lambda r,m,mp,p:find(r,'.//p:spTree').append(deepcopy(find(r,'.//p:sp'))))
        self.reject('I04',lambda r,m,mp,p:find(r,'.//p:spTree').remove(find(r,'.//p:sp')))
        self.reject('I05',lambda r,m,mp,p:find(r,'.//p:sp/p:nvSpPr/p:cNvPr').set('name','other'))

    def test_I06_source_canvas_selected_or_marked_preserved(self):
        for marker in (False,True):
            parts=fixture();r,m,mp,p=parts
            if marker:find(r,'.//p:sp/p:nvSpPr/p:cNvPr').set('descr','source_canvas_clip_required=true')
            else:m['source_canvas_clip']={'objects':[{'object_id':'curve'}]}
            got=self.run_case(parts,'not_applicable');self.assertEqual(got['reason_code'],'keep_original_canvas_clip_geometry')

    def test_I07_I10_context_and_singleton_guards(self):
        def nest(r,m,mp,p):
            tree=find(r,'.//p:spTree');shape=find(r,'.//p:sp');tree.remove(shape);ET.SubElement(tree,P+'grpSp').append(shape)
        self.reject('I07',nest)
        self.reject('I08',lambda r,m,mp,p:find(r,'.//p:grpSpPr/a:xfrm').set('rot','1'))
        self.reject('I08-effects',lambda r,m,mp,p:ET.SubElement(find(r,'.//p:grpSpPr'),A+'effectLst'))
        self.reject('I09',lambda r,m,mp,p:find(r,'.//p:sp/p:nvSpPr/p:cNvPr').set('hidden','1'))
        self.reject('I09-animation',lambda r,m,mp,p:ET.SubElement(r,P+'timing'))
        self.reject('I10',lambda r,m,mp,p:find(r,'.//p:sp').append(deepcopy(find(r,'.//p:spPr'))))

    def test_P01_P06_native_paint_and_scene_types(self):
        self.reject('P01',lambda r,m,mp,p:find(r,'.//p:spPr').append(deepcopy(find(r,'.//a:solidFill'))))
        self.reject('P02',lambda r,m,mp,p:setattr(find(r,'.//a:solidFill'),'tag','{urn:wrong}solidFill'))
        self.reject('P03',lambda r,m,mp,p:find(r,'.//a:srgbClr').set('val','FF0000'))
        self.reject('P03-tint',lambda r,m,mp,p:ET.SubElement(find(r,'.//a:srgbClr'),A+'tint',{'val':'50000'}))
        self.reject('P04',lambda r,m,mp,p:find(r,'.//a:srgbClr').append(deepcopy(find(r,'.//a:alpha'))))
        self.reject('P04-child',lambda r,m,mp,p:ET.SubElement(find(r,'.//a:alpha'),A+'shade',{'val':'0'}))
        self.reject('P05',lambda r,m,mp,p:find(r,'.//a:alpha').set('val','50001'))
        for value in (True,float('nan'),float('inf')):
            self.reject('P06',lambda r,m,mp,p:m['objects'][0]['style'].update(opacity=value))

    def test_P08_P10_stroke_effect_zero_alpha_and_unknown_source_context(self):
        self.reject('P08',lambda r,m,mp,p:ET.SubElement(find(r,'.//a:ln'),A+'solidFill'))
        self.reject('P09',lambda r,m,mp,p:ET.SubElement(find(r,'.//p:spPr'),A+'effectLst'))
        self.reject('P10',lambda r,m,mp,p:m['objects'][0]['style'].update(opacity=0))
        self.reject('source-mask',lambda r,m,mp,p:m['objects'][0].update(mask='unknown'))

    def test_B01_B03_complete_initial_integer_identity(self):
        def delta(r,m,mp,p):
            pt=find(r,'.//a:cubicBezTo/a:pt');pt.set('x',str(int(pt.get('x'))+1))
        self.reject('B01',delta)
        def swap(r,m,mp,p):
            c=find(r,'.//a:cubicBezTo');a,b=deepcopy(c[0]),deepcopy(c[1]);c.remove(c[0]);c.remove(c[0]);c.insert(0,a);c.insert(0,b)
        self.reject('B02',swap)
        self.reject('B02-P0',lambda r,m,mp,p:find(r,'.//a:moveTo/a:pt').set('x','1'))
        self.reject('B03',lambda r,m,mp,p:ET.SubElement(find(r,'.//a:path'),A+'close'))

    def test_B04_B12_map_and_rounding_forgery(self):
        self.reject('B04',lambda r,m,mp,p:mp['objects'][0]['box'].update(x=.001))
        self.reject('B05',lambda r,m,mp,p:mp['placement'].__setitem__(0,1))
        def doubled(r,m,mp,p):
            path=find(r,'.//a:path')
            for k in ('w','h'):path.set(k,str(int(path.get(k))*2))
            for pt in path.findall('.//'+A+'pt'):
                for k in ('x','y'):pt.set(k,str(int(pt.get(k))*2))
        self.reject('B06',doubled)
        self.reject('B07',lambda r,m,mp,p:find(r,'.//a:path').set('w','2'))
        self.reject('B08',lambda r,m,mp,p:m['objects'][0]['commands'][1]['cubicTo'].update(x1=1.001))
        self.reject('B09',lambda r,m,mp,p:mp['objects'][0]['box'].update(y=0,height=7))
        self.reject('B10',lambda r,m,mp,p:find(r,'.//a:pt').set('x','-1'))
        def nonuniform(r,m,mp,p):p[3]+=1e-8;mp['placement']=list(p)
        self.reject('B11',nonuniform)
        self.reject('B12',lambda r,m,mp,p:mp['objects'][0]['box'].update(x=True))

    def test_X01_X05_native_xml_profile(self):
        self.reject('X01',lambda r,m,mp,p:setattr(find(r,'.//a:pt'),'tag','{urn:wrong}pt'))
        for v in ('wd2','1.0','+1','01','-0','2147483648'):
            self.reject('X01-X03',lambda r,m,mp,p:find(r,'.//a:pt').set('x',v))
        self.reject('X02',lambda r,m,mp,p:find(r,'.//a:pt').set('effect','1'))
        self.reject('X02-child',lambda r,m,mp,p:ET.SubElement(find(r,'.//a:pt'),A+'pt'))
        self.reject('X04',lambda r,m,mp,p:find(r,'.//a:pathLst').append(deepcopy(find(r,'.//a:path'))))
        self.reject('X05',lambda r,m,mp,p:find(r,'.//a:path').set('unknown','1'))

    def test_X06_X10_frame_and_negative_global_coordinates(self):
        def move(r,m,mp,p):
            off=find(r,'.//p:spPr/a:xfrm/a:off');off.set('x',str(int(off.get('x'))+3))
        self.reject('X06',move)
        parts=fixture();off=find(parts[0],'.//p:spPr/a:xfrm/a:off');off.set('x',str(int(off.get('x'))+1))
        got=self.run_case(parts);self.assertEqual(got['source_native_binding']['initial_frame_error_emu_exact'][0],'1')
        self.reject('X08',lambda r,m,mp,p:find(r,'.//p:spPr/a:xfrm').set('flipH','yes'))
        self.run_case(fixture(placement=(-100,-50,1000,1000)))
        self.run_case(fixture(placement=(-100,-50,500,500)))

    def test_T01_T05_candidate_proof_and_protected_control_forgery(self):
        normal=w.normalize_isolated_cubic_fill
        def corrupted(*args,**kwargs):
            result=normal(*args,**kwargs)
            for i,c in enumerate(result['commands']):
                if c[0]=='C':result['commands'][i]=(c[0],(c[1][0]+1,c[1][1]),*c[2:]);break
            return result
        with patch.object(w,'normalize_isolated_cubic_fill',side_effect=corrupted):self.run_case(fixture(),'rejected')
        def wrong_vertex(*args,**kwargs):
            result=normal(*args,**kwargs);c=result['commands'][0];result['commands'][0]=(c[0],(c[1][0]+1,c[1][1]));return result
        with patch.object(w,'normalize_isolated_cubic_fill',side_effect=wrong_vertex):self.run_case(fixture(),'rejected')
        def missing(*args,**kwargs):
            result=normal(*args,**kwargs);result['commands']=[c for c in result['commands'] if c[0]!='C'];return result
        with patch.object(w,'normalize_isolated_cubic_fill',side_effect=missing):self.run_case(fixture(),'rejected')

    def test_T06_T09_budget_final_verification_and_transaction(self):
        for kwargs in ({'max_operations':1},{'max_input_segments':1},{'max_commands':2},{'max_atomic_edges':1},{'max_tree_nodes':1},{'max_xml_text_bytes':1}):
            self.run_case(fixture(),'rejected',**kwargs)
        with patch.object(w,'verify_native_cubic_path_encoding',side_effect=w.UnsupportedPdfCubicWindingError('encoding_topology','mutated final grid')):
            self.run_case(fixture(),'rejected')
        # An independently raised final encoding/half-slide error never commits.
        with patch.object(w,'verify_native_cubic_path_encoding',side_effect=w.UnsupportedPdfCubicWindingError('encoding_error','half slide bound')):
            self.run_case(fixture(),'rejected')

    def test_T10_T13_empty_candidate_repeat_and_error_report(self):
        normal=w.normalize_isolated_cubic_fill
        def empty(*args,**kwargs):
            r=normal(*args,**kwargs);r['commands']=[];return r
        with patch.object(w,'normalize_isolated_cubic_fill',side_effect=empty):self.run_case(fixture(),'rejected')
        parts=fixture();self.run_case(parts);self.run_case(parts,'rejected')
        parts=fixture();find(parts[0],'.//p:spPr/a:xfrm/a:off').set('x','2');r=self.run_case(parts)
        self.assertGreater(F(r['source_native_binding']['maximum_scene_to_initial_native_control_error_emu_exact'][0]),F(1,2))
        self.assertFalse(r['existing_source_error_in_additional_half_emu_bound'])

    def test_R04_R05_ordinary_objects_do_not_enter_new_geometry_path(self):
        parts=fixture();parts[1]['objects'][0]['kind']='text';self.run_case(parts,'not_applicable')
        parts=fixture();parts[1]['objects'][0]['kind']='image';self.run_case(parts,'not_applicable')
        polygon=[('M',(0,0)),('L',(4,0)),('L',(4,4)),('Z',)]
        self.run_case(fixture(polygon),'not_applicable')

    def test_malformed_canvas_rows_and_unbounded_map_metadata_reject(self):
        for rows in (['curve'],[{'id':'curve'}],[{'object_id':True}],
                     [{'object_id':'curve','behavior':'unknown'}],
                     [{'object_id':'curve'},{'object_id':'curve'}]):
            self.reject('malformed-canvas',lambda r,m,mp,p:m.update(source_canvas_clip={'objects':rows}))
        nested=[]
        for _ in range(1500):nested=[nested]
        # Avoid copying hostile nested metadata in the test harness; production
        # rejects its type before recursive serialization or equality traversal.
        for key in ('group_id','editable'):
            root,m,mp,place=fixture();mp['objects'][0][key]=nested;before=ET.tostring(root)
            got=w.normalize_native_cubic_fill(root,object_id='curve',manifest=m,object_map=mp,placement=place)
            self.assertEqual(got['status'],'rejected',got);self.assertEqual(ET.tostring(root),before)

    def test_json_int_float_aliases_are_exact_numeric_not_boolean_aliases(self):
        parts=list(fixture());parts[3]=[float(v) for v in parts[3]]
        for key,value in list(parts[2]['objects'][0]['box'].items()):parts[2]['objects'][0]['box'][key]=float(value)
        self.run_case(parts)
        self.reject('placement-bool',lambda r,m,mp,p:mp['placement'].__setitem__(0,False))
        self.reject('placement-NaN',lambda r,m,mp,p:mp['placement'].__setitem__(0,float('nan')))
        self.reject('box-inexact',lambda r,m,mp,p:mp['objects'][0]['box'].update(width=7.000000000000001))

    def test_duck_typed_or_subclassed_root_cannot_authorize_real_child_mutation(self):
        root,m,mp,place=fixture();before=ET.tostring(root)
        class Fake:
            tag=root.tag;attrib=root.attrib;text=root.text;tail=root.tail
            def __iter__(self):return iter(root)
            def __len__(self):return len(root)
        class ElementSubclass(ET.Element):pass
        subclass=ElementSubclass(root.tag)
        for child in root:subclass.append(child)
        for candidate in (Fake(),subclass):
            got=w.normalize_native_cubic_fill(candidate,object_id='curve',manifest=m,object_map=mp,placement=place)
            self.assertEqual(got['status'],'rejected',got);self.assertEqual(ET.tostring(root),before)
        class HookedDict(dict):
            def keys(self):raise AssertionError('Attribute hooks must not execute')
            def values(self):raise AssertionError('Attribute hooks must not execute')
        root.attrib=HookedDict(root.attrib)
        got=w.normalize_native_cubic_fill(root,object_id='curve',manifest=m,object_map=mp,placement=place)
        self.assertEqual(got['status'],'rejected',got)
        root.attrib=dict(root.attrib)
        self.assertEqual(ET.tostring(root),before)


if __name__=='__main__':unittest.main()
