"""Portable native-PPT fixtures exercise actual ZIP/XML/media comparisons."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib
from xml.etree import ElementTree as ET

from figure_rebuild.pptx_fidelity import audit_pptx_fidelity
from figure_rebuild.pdf_source_replay import ReplayLimits

P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
NS={'p':P,'a':A,'r':R}


def png():
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b'')


def fixture_scene(root):
    root=Path(root);(root/'assets').mkdir(exist_ok=True)
    (root/'assets/image.png').write_bytes(png());(root/'source.png').write_bytes(png())
    return {'canvas':{'width':100,'height':80,'background':'#FFFFFF'},
            'source':{'path':'source.png','sha256':hashlib.sha256(png()).hexdigest()},
            'objects':[{'id':'glyph-1','kind':'path','z_index':0,
                'commands':[{'moveTo':{'x':2.,'y':3.}},{'lineTo':{'x':8.,'y':3.}},{'lineTo':{'x':6.,'y':9.}},{'close':{}}],
                'style':{'fill':'#000000','stroke':'none','stroke_width':0,'opacity':1.0}},
                {'id':'image-1','kind':'image','z_index':1,'path':'assets/image.png','sha256':hashlib.sha256(png()).hexdigest(),
                 'box':{'x':20,'y':30,'width':10,'height':10},'crop':{'left':0.,'top':0.,'right':0.,'bottom':0.},'fit':'stretch','editable':False}]}


def make_ppt(path,scene,asset_root):
    """Construct supported OXML independently, using declared expected geometry."""
    q=lambda n:f'{{{A}}}{n}'
    snap=lambda n:int(n*9525+.5)
    slide=ET.Element(f'{{{P}}}sld');cs=ET.SubElement(slide,f'{{{P}}}cSld')
    bg=ET.SubElement(ET.SubElement(ET.SubElement(cs,f'{{{P}}}bg'),f'{{{P}}}bgPr'),q('solidFill'));ET.SubElement(bg,q('srgbClr'),val='FFFFFF')
    tree=ET.SubElement(cs,f'{{{P}}}spTree');ET.SubElement(tree,f'{{{P}}}nvGrpSpPr');ET.SubElement(ET.SubElement(tree,f'{{{P}}}grpSpPr'),q('xfrm'))
    rels=ET.Element(f'{{{REL}}}Relationships');media={}
    for ix,o in enumerate(scene['objects']):
        image=o['kind']=='image';n=ET.SubElement(tree,f'{{{P}}}'+('pic' if image else 'sp'))
        nv=ET.SubElement(n,f'{{{P}}}'+('nvPicPr' if image else 'nvSpPr'));ET.SubElement(nv,f'{{{P}}}cNvPr',id=str(ix+1),name='untrusted-name',descr='source_id='+o['id'])
        if image:
            bf=ET.SubElement(n,f'{{{P}}}blipFill');ET.SubElement(bf,q('blip'),{f'{{{R}}}embed':f'i{ix}'})
            ET.SubElement(bf,q('srcRect'),{short:str(round(o['crop'][k]*100000)) for k,short in [('left','l'),('top','t'),('right','r'),('bottom','b')]});ET.SubElement(bf,q('stretch'))
            member=f'ppt/media/image{ix}.png';media[member]=(Path(asset_root)/o['path']).read_bytes();ET.SubElement(rels,f'{{{REL}}}Relationship',Id=f'i{ix}',Type=R+'/image',Target='../media/image'+str(ix)+'.png')
            box=[o['box'][k] for k in ('x','y','width','height')]
        else:
            pairs=[]
            for cmd in o['commands']:
                for data in cmd.values():
                    pairs.extend((data[x],data[y]) for x,y in [('x1','y1'),('x2','y2'),('x','y')] if x in data)
            xs,ys=zip(*pairs);box=[min(xs),min(ys),max(.01,max(xs)-min(xs)),max(.01,max(ys)-min(ys))]
        sp=ET.SubElement(n,f'{{{P}}}spPr');xf=ET.SubElement(sp,q('xfrm'));ET.SubElement(xf,q('off'),x=str(snap(box[0])),y=str(snap(box[1])));ET.SubElement(xf,q('ext'),cx=str(snap(box[2])),cy=str(snap(box[3])))
        if image:
            ET.SubElement(ET.SubElement(sp,q('prstGeom'),prst='rect'),q('avLst'))
        else:
            shape=ET.SubElement(ET.SubElement(ET.SubElement(sp,q('custGeom')),q('pathLst')),q('path'),w=str(snap(box[2])),h=str(snap(box[3])))
            for cmd in o['commands']:
                op,data=next(iter(cmd.items()));node=ET.SubElement(shape,q({'lineTo':'lnTo','cubicTo':'cubicBezTo'}.get(op,op)))
                for x,y in [('x1','y1'),('x2','y2'),('x','y')]:
                    if x in data:ET.SubElement(node,q('pt'),x=str(snap(data[x]-box[0])),y=str(snap(data[y]-box[1])))
            fill=ET.SubElement(ET.SubElement(sp,q('solidFill')),q('srgbClr'),val='000000');ET.SubElement(fill,q('alpha'),val='100000')
            line=ET.SubElement(sp,q('ln'),w='0');ET.SubElement(line,q('noFill'));ET.SubElement(line,q('prstDash'),val='solid')
    pres=ET.Element(f'{{{P}}}presentation');ET.SubElement(ET.SubElement(pres,f'{{{P}}}sldIdLst'),f'{{{P}}}sldId',{'id':'256',f'{{{R}}}id':'s1'});ET.SubElement(pres,f'{{{P}}}sldSz',cx=str(snap(scene['canvas']['width'])),cy=str(snap(scene['canvas']['height'])))
    pr=ET.Element(f'{{{REL}}}Relationships');ET.SubElement(pr,f'{{{REL}}}Relationship',Id='s1',Type=R+'/slide',Target='slides/slide1.xml')
    files={'ppt/presentation.xml':ET.tostring(pres),'ppt/_rels/presentation.xml.rels':ET.tostring(pr),'ppt/slides/slide1.xml':ET.tostring(slide),'ppt/slides/_rels/slide1.xml.rels':ET.tostring(rels),**media}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for k,v in files.items():z.writestr(k,v)
    return files


class PptxFidelityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.scene=fixture_scene(self.root);self.ppt=self.root/'actual.pptx';self.files=make_ppt(self.ppt,self.scene,self.root)
    def audit(self,scene=None,**kw):return audit_pptx_fidelity(self.ppt,scene or self.scene,asset_root=self.root,**kw)
    def mutate(self,change):
        files=copy.deepcopy(self.files);change(files)
        with zipfile.ZipFile(self.ppt,'w',zipfile.ZIP_DEFLATED) as z:
            for k,v in files.items():z.writestr(k,v)
    def xml(self,fn):
        def edit(f):
            n=ET.fromstring(f['ppt/slides/slide1.xml']);fn(n);f['ppt/slides/slide1.xml']=ET.tostring(n)
        self.mutate(edit)
    def test_complete_geometry_media_positive(self):
        r=self.audit();self.assertEqual(r['status'],'VERIFIED_IN_DECLARED_SCOPE');self.assertEqual(r['summary']['verified_paths'],1);self.assertEqual(r['summary']['verified_images'],1)
    def test_one_emu_actual_path_mutation_fails(self):
        def change(n):
            p=n.find('.//a:path/a:moveTo/a:pt',NS);p.set('x',str(int(p.get('x'))+1))
        self.xml(change);self.assertEqual(self.audit()['status'],'FAIL')
    def test_same_image_bytes_wrong_placement_fails(self):
        def change(n):n.find('.//p:pic/p:spPr/a:xfrm/a:off',NS).set('x','1')
        self.xml(change);self.assertEqual(self.audit()['status'],'FAIL')
    def test_embedded_bytes_mutation_fails(self):
        self.mutate(lambda f:f.__setitem__('ppt/media/image1.png',png()+b'changed'))
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_unknown_effect_and_explicit_transform_unresolved(self):
        self.xml(lambda n:ET.SubElement(n.find('.//p:sp/p:spPr/a:solidFill/a:srgbClr',NS),f'{{{A}}}tint',val='30000'))
        self.assertEqual(self.audit()['status'],'UNRESOLVED')
        self.xml(lambda n:ET.SubElement(n.find('.//p:pic/p:blipFill/a:stretch',NS),f'{{{A}}}fillRect',l='1000'))
        self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_slide_relationship_cannot_redirect_audited_page(self):
        def change(f):
            r=ET.fromstring(f['ppt/_rels/presentation.xml.rels']);r[0].set('Target','slides/slide2.xml');f['ppt/_rels/presentation.xml.rels']=ET.tostring(r);f['ppt/slides/slide2.xml']=f['ppt/slides/slide1.xml']
        self.mutate(change);self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_missing_identity_never_matched_by_name(self):
        self.xml(lambda n:n.find('.//p:sp/p:nvSpPr/p:cNvPr',NS).attrib.pop('descr'))
        self.assertEqual(self.audit()['status'],'FAIL')
    def test_zip_and_xml_budgets_are_actual(self):
        self.assertEqual(self.audit(limits={'max_zip_entries':1})['status'],'UNRESOLVED')
        self.assertEqual(self.audit(limits={'max_xml_bytes':16})['status'],'UNRESOLVED')
        self.mutate(lambda f:f.__setitem__('../escape',b'x'))
        self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_inert_slide_creation_id_is_allowed_but_unknown_extension_is_not(self):
        def creation(n,uri):
            ext=ET.SubElement(ET.SubElement(n.find('p:cSld',NS),f'{{{P}}}extLst'),f'{{{P}}}ext',uri=uri)
            ET.SubElement(ext,'{http://schemas.microsoft.com/office/powerpoint/2010/main}creationId',val='1646791540')
        self.xml(lambda n:creation(n,'{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}'))
        self.assertEqual(self.audit()['status'],'VERIFIED_IN_DECLARED_SCOPE')
        self.xml(lambda n:creation(n,'unknown-extension'))
        self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_root_review_six_false_pass_attacks_closed(self):
        attacks={
            'background_alpha':lambda n:ET.SubElement(n.find('p:cSld/p:bg/p:bgPr/a:solidFill/a:srgbClr',NS),f'{{{A}}}alpha',val='0'),
            'background_shade':lambda n:ET.SubElement(n.find('p:cSld/p:bg/p:bgPr/a:solidFill/a:srgbClr',NS),f'{{{A}}}shade',val='0'),
            'foreign_command':lambda n:setattr(n.find('.//a:path/a:lnTo',NS),'tag','{urn:foreign}lnTo'),
            'foreign_point':lambda n:setattr(n.find('.//a:path/a:moveTo/a:pt',NS),'tag','{urn:foreign}pt'),
            'duplicate_tree':lambda n:n.find('p:cSld',NS).append(copy.deepcopy(n.find('p:cSld/p:spTree',NS))),
            'duplicate_background':lambda n:n.find('p:cSld',NS).append(copy.deepcopy(n.find('p:cSld/p:bg',NS))),
        }
        for name,change in attacks.items():
            with self.subTest(name=name):
                self.xml(change);self.assertNotEqual(self.audit()['status'],'VERIFIED_IN_DECLARED_SCOPE')
    def test_wrong_point_count_placeholder_and_relation_type_unknown(self):
        self.xml(lambda n:ET.SubElement(n.find('.//a:path/a:moveTo',NS),f'{{{A}}}pt',x='0',y='0'))
        self.assertEqual(self.audit()['status'],'UNRESOLVED')
        def placeholder(n):
            nv=ET.SubElement(n.find('.//p:sp/p:nvSpPr',NS),f'{{{P}}}nvPr');ET.SubElement(nv,f'{{{P}}}ph',type='title')
        self.xml(placeholder);self.assertEqual(self.audit()['status'],'UNRESOLVED')
        def relation(f):
            r=ET.fromstring(f['ppt/slides/_rels/slide1.xml.rels']);r[0].set('Type','urn:fake/image');f['ppt/slides/_rels/slide1.xml.rels']=ET.tostring(r)
        self.mutate(relation);self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_selected_shape_and_container_effect_boundaries_are_closed(self):
        attacks={
            'useBgFill':lambda n:n.find('.//p:sp',NS).set('useBgFill','1'),
            'bwMode':lambda n:n.find('.//p:sp/p:spPr',NS).set('bwMode','white'),
            'alpha_nested_shade':lambda n:ET.SubElement(n.find('.//p:sp/p:spPr/a:solidFill/a:srgbClr/a:alpha',NS),f'{{{A}}}shade',val='0'),
            'slide_timing':lambda n:ET.SubElement(n,f'{{{P}}}timing'),
            'foreign_geometry_child':lambda n:ET.SubElement(n.find('.//p:sp/p:spPr/a:custGeom',NS),'{urn:foreign}pathLst'),
        }
        for name,change in attacks.items():
            with self.subTest(name=name):
                # xml() always starts from self.files, the unchanged baseline ZIP.
                self.xml(change);self.assertEqual(self.audit()['status'],'UNRESOLVED')
    def test_invalid_budget_bool_and_nan_rejected(self):
        for limits in ({'max_zip_entries':True},{'timeout_seconds':float('nan')},
                       {'timeout_seconds':10**400}):
            with self.assertRaises(ValueError):self.audit(limits=limits)
    def test_extreme_scene_coordinates_remain_unresolved(self):
        scene=copy.deepcopy(self.scene);scene['canvas']['width']=10**400
        self.assertEqual(self.audit(scene)['status'],'UNRESOLVED')
    def test_asset_escape_is_unresolved(self):
        scene=copy.deepcopy(self.scene);scene['objects'][1]['path']='../outside.png'
        self.assertEqual(self.audit(scene)['status'],'UNRESOLVED')

if __name__=='__main__':unittest.main()
