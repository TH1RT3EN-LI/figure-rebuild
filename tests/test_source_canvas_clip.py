"""Strict source viewport declarations and native-command tamper boundaries."""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile
from xml.etree import ElementTree as ET

from figure_rebuild.source_canvas_clip import (
    SourceCanvasClipError, verify_source_canvas_clip, verify_native_canvas_clip,
    _possibly_intersects, _native_context, main,
)
from figure_rebuild.validate import validate

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_fixture(root, *, clipped=False, render_mode=0):
    from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths
    document=fitz.open();page=document.new_page(width=180,height=100)
    page.insert_text((10,45),'Office',fontname='helv',fontsize=32,render_mode=render_mode)
    if clipped:
        for xref in page.get_contents():
            document.update_stream(xref,b'q 20 20 40 40 re W n\n'+document.xref_stream(xref)+b'\nQ')
    document.save(root/'original.pdf');document.close()
    roi=[15,20,85,42];scale=2;irect=[30,40,170,84]
    with fitz.open(root/'original.pdf') as document:
        svg=document[0].get_svg_image(text_as_path=True)
    (root/'original.svg').write_text(svg)
    with fitz.open(root/'original.pdf') as document:
        document[0].get_pixmap(matrix=fitz.Matrix(scale,scale),clip=fitz.Rect(roi),alpha=False).save(root/'source.png')
    parsed=extract_outlined_svg(svg);ids=[p.source_id for p in parsed.paints if p.kind=='glyph']
    authored=parsed
    if clipped:
        # Model an untrusted author who discards inner clips. The production
        # replay must reject this from the *actual* original PDF context.
        from dataclasses import replace
        authored=replace(parsed,paints=tuple(replace(p,clips=()) for p in parsed.paints))
    objects=outline_paths(authored,glyph_mode='outline',paint_ids=ids,transform=(2,0,0,2,-30,-40)).objects
    cfg={'version':1,'mode':'standalone_slide_canvas','source_pdf':{'path':'original.pdf','sha256':digest(root/'original.pdf'),'page':1},
         'source_svg':{'path':'original.svg','sha256':digest(root/'original.svg')},
         'raster':{'pymupdf_version':fitz.VersionBind,'scale':2,'roi_pdf_points':roi,'irect':irect,'alpha':False,'colorspace':'DeviceRGB'},
         'objects':[{'object_id':o['id'],'source_paint_id':o['id']} for o in objects]}
    return {'schema_version':1,'id':'canvas-test','revision':1,'canvas':{'width':140,'height':44,'background':'#FFFFFF'},
            'source':{'kind':'user_original','path':'source.png','sha256':digest(root/'source.png'),'width':140,'height':44},
            'recognition':{'status':'reviewed','provider':'svg_import','unresolved':[]},'objects':objects,'source_canvas_clip':cfg}


def native_fixture(root,manifest,mutate=None):
    ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
    q=lambda n: '{'+ns[n.split(':')[0]]+'}'+n.split(':')[1]
    presentation=ET.Element(q('p:presentation'));ids=ET.SubElement(presentation,q('p:sldIdLst'));slide_id=ET.SubElement(ids,q('p:sldId'),id='256')
    slide_id.set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id','rId1')
    ET.SubElement(presentation,q('p:sldSz'),cx=str(manifest['canvas']['width']*9525),cy=str(manifest['canvas']['height']*9525))
    slide=ET.Element(q('p:sld'));content=ET.SubElement(slide,q('p:cSld'))
    ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(content,q('p:bg')),q('p:bgPr')),q('a:solidFill')),q('a:srgbClr'),val='FFFFFF')
    tree=ET.SubElement(content,q('p:spTree'))
    ET.SubElement(ET.SubElement(tree,q('p:grpSpPr')),q('a:xfrm'))
    for index,obj in enumerate(manifest['objects']):
        shape=ET.SubElement(tree,q('p:sp'));identity=ET.SubElement(ET.SubElement(shape,q('p:nvSpPr')),q('p:cNvPr'),id=str(index+2),name=obj['id'],descr='source_id='+obj['id']+'; source_canvas_clip_required=true')
        pr=ET.SubElement(shape,q('p:spPr'));points=[]
        for command in obj['commands']:
            op,v=next(iter(command.items()))
            if op in ('moveTo','lineTo'):points.append((v['x'],v['y']))
            elif op=='cubicTo':points += [(v[kx],v[ky]) for kx,ky in [('x1','y1'),('x2','y2'),('x','y')]]
        x0,y0=min(x for x,y in points),min(y for x,y in points);w,h=max(.01,max(x for x,y in points)-x0),max(.01,max(y for x,y in points)-y0)
        xf=ET.SubElement(pr,q('a:xfrm'));ET.SubElement(xf,q('a:off'),x=str(math.floor(x0*9525+.5)),y=str(math.floor(y0*9525+.5)));ET.SubElement(xf,q('a:ext'),cx=str(math.floor(w*9525+.5)),cy=str(math.floor(h*9525+.5)))
        fill=ET.SubElement(ET.SubElement(pr,q('a:solidFill')),q('a:srgbClr'),val=obj['style']['fill'][1:]);ET.SubElement(fill,q('a:alpha'),val=str(math.floor(obj['style']['opacity']*100000+.5)))
        ET.SubElement(ET.SubElement(pr,q('a:ln'),w='0'),q('a:noFill'))
        path=ET.SubElement(ET.SubElement(ET.SubElement(pr,q('a:custGeom')),q('a:pathLst')),q('a:path'),w=str(math.floor(w*9525+.5)),h=str(math.floor(h*9525+.5)))
        for command in obj['commands']:
            op,v=next(iter(command.items()));node=ET.SubElement(path,q('a:'+{'moveTo':'moveTo','lineTo':'lnTo','cubicTo':'cubicBezTo','close':'close'}[op]))
            xy=[] if op=='close' else [(v['x'],v['y'])] if op!='cubicTo' else [(v[kx],v[ky]) for kx,ky in [('x1','y1'),('x2','y2'),('x','y')]]
            for x,y in xy:ET.SubElement(node,q('a:pt'),x=str(math.floor((x-x0)*9525+.5)),y=str(math.floor((y-y0)*9525+.5)))
    if mutate:mutate(presentation,slide,ns)
    target=root/'fixture.pptx'
    with zipfile.ZipFile(target,'w') as archive:
        archive.writestr('ppt/presentation.xml',ET.tostring(presentation));archive.writestr('ppt/slides/slide1.xml',ET.tostring(slide))
        archive.writestr('ppt/_rels/presentation.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>')
    return target


@unittest.skipIf(fitz is None,'PyMuPDF is optional')
class SourceCanvasClipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.m=make_fixture(self.root)

    def test_real_pdf_source_replay_admits_only_explicit_original_glyphs(self):
        proof=verify_source_canvas_clip(self.m,self.root)
        self.assertEqual(proof,verify_source_canvas_clip(self.m,self.root))
        self.assertTrue(proof['object_ids']);self.assertEqual(proof['irect'],[30,40,170,84])
        self.assertEqual(validate(self.m,self.root)['status'],'PASS')
        self.assertEqual(proof['semantic_fidelity'],'NOT_PROVIDED')
        old=copy.deepcopy(self.m);del old['source_canvas_clip']
        self.assertEqual(validate(old,self.root)['status'],'FAIL')
        self.assertIsNone(verify_source_canvas_clip(old,self.root))

    def test_only_selected_objects_receive_bounds_exception(self):
        m=copy.deepcopy(self.m);m['source_canvas_clip']['objects']=m['source_canvas_clip']['objects'][1:]
        self.assertEqual(validate(m,self.root)['status'],'FAIL')

    def test_modified_original_glyph_command_and_style_are_refused(self):
        for field in ['commands','style']:
            m=copy.deepcopy(self.m)
            if field=='commands':m['objects'][0]['commands'][0]['moveTo']['x']+=.001
            else:m['objects'][0]['style']['opacity']=.9
            with self.subTest(field=field),self.assertRaisesRegex(SourceCanvasClipError,'commands or style'):
                verify_source_canvas_clip(m,self.root)

    def test_boolean_numeric_alias_is_not_original_source_style(self):
        for field, value in [('opacity', True), ('stroke_width', False)]:
            m=copy.deepcopy(self.m);m['objects'][0]['style'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(SourceCanvasClipError,'commands or style'):
                verify_source_canvas_clip(m,self.root)
        for field in ['canvas','source']:
            m=copy.deepcopy(self.m);m[field]['width']=float(m[field]['width'])
            with self.subTest(field=field),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,self.root)

    def test_pixel_and_png_byte_budgets_precede_rasterization(self):
        m=copy.deepcopy(self.m);m['canvas']['width']=m['canvas']['height']=10000
        m['source_canvas_clip']['raster'].update(roi_pdf_points=[0,0,5000,5000],irect=[0,0,10000,10000])
        with mock.patch('pymupdf.open') as opened,self.assertRaisesRegex(SourceCanvasClipError,'pixel replay budget'):
            verify_source_canvas_clip(m,self.root)
        opened.assert_not_called()
        with (self.root/'source.png').open('wb') as stream:stream.truncate(64*1024*1024+1)
        with mock.patch('pymupdf.open') as opened,self.assertRaisesRegex(SourceCanvasClipError,'byte budget'):
            verify_source_canvas_clip(self.m,self.root)
        opened.assert_not_called()

    def test_declaration_page_version_scale_and_exact_pixel_grid_reject(self):
        changes=[('version',True),('version',2),('mode','base_region')]
        for key,value in changes:
            m=copy.deepcopy(self.m);m['source_canvas_clip'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,self.root)
        for key,value in [('scale',True),('scale',0),('scale',float('inf')),('irect',[30.,40,170,84]),('irect',[31,40,170,84]),('roi_pdf_points',[15.1,20,85,42]),('alpha',True),('colorspace','DeviceCMYK'),('pymupdf_version','wrong')]:
            m=copy.deepcopy(self.m);m['source_canvas_clip']['raster'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,self.root)
        for value in [True,0,2,1.0]:
            m=copy.deepcopy(self.m);m['source_canvas_clip']['source_pdf']['page']=value
            with self.subTest(page=value),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,self.root)

    def test_hash_and_original_pixel_binding_are_not_caller_receipts(self):
        for key in ['source_pdf','source_svg']:
            m=copy.deepcopy(self.m);m['source_canvas_clip'][key]['sha256']='0'*64
            with self.subTest(key=key),self.assertRaisesRegex(SourceCanvasClipError,'hash changed'):verify_source_canvas_clip(m,self.root)
        from PIL import Image
        with Image.open(self.root/'source.png') as im:
            im.putpixel((0,0),(1,2,3));im.save(self.root/'source.png')
        m=copy.deepcopy(self.m);m['source']['sha256']=digest(self.root/'source.png')
        with self.assertRaisesRegex(SourceCanvasClipError,'pixels differ'):verify_source_canvas_clip(m,self.root)

    def test_forged_svg_and_rehashed_pdf_do_not_match_fresh_source(self):
        path=self.root/'original.svg';path.write_text(path.read_text().replace('<defs>','<defs>\n'))
        m=copy.deepcopy(self.m);m['source_canvas_clip']['source_svg']['sha256']=digest(path)
        with self.assertRaisesRegex(SourceCanvasClipError,'fresh outlined source'):verify_source_canvas_clip(m,self.root)

    def test_selection_identity_order_and_unknown_declaration_fields(self):
        for mutation in ['missing','duplicate','reverse_order','unknown_field']:
            m=copy.deepcopy(self.m)
            if mutation=='missing':m['source_canvas_clip']['objects'][0]['source_paint_id']='absent'
            elif mutation=='duplicate':m['source_canvas_clip']['objects'].append(m['source_canvas_clip']['objects'][0])
            elif mutation=='reverse_order':m['objects'][0]['z_index']=1000
            else:m['source_canvas_clip']['trusted']=True
            with self.subTest(mutation=mutation),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,self.root)

    def test_path_escape_and_canvas_grid_change(self):
        m=copy.deepcopy(self.m);m['source_canvas_clip']['source_svg']['path']='../escape.svg'
        with self.assertRaises(ValueError):verify_source_canvas_clip(m,self.root)
        m=copy.deepcopy(self.m);m['canvas']['width']+=1
        with self.assertRaisesRegex(SourceCanvasClipError,'canvas differs'):verify_source_canvas_clip(m,self.root)

    def test_native_complete_controls_marker_frame_and_slide_verified(self):
        receipt=verify_source_canvas_clip(self.m,self.root);ppt=native_fixture(self.root,self.m)
        verified=verify_native_canvas_clip(ppt,self.m,receipt)
        self.assertEqual(verified['status'],'VERIFIED_NATIVE_SOURCE_VIEWPORT')
        self.assertEqual(len(verified['objects']),len(self.m['objects']))
        self.assertEqual(verified,verify_native_canvas_clip(ppt,self.m,receipt))

    def test_native_tampering_never_turns_into_valid_clip(self):
        receipt=verify_source_canvas_clip(self.m,self.root)
        def mutation(kind):
            def change(p,s,ns):
                if kind=='slide':p.find('p:sldSz',ns).set('cx','1')
                elif kind=='second_slide':ET.SubElement(p.find('p:sldIdLst',ns),'{'+ns['p']+'}sldId',id='257')
                elif kind=='marker':s.find('.//p:cNvPr',ns).set('descr','source_canvas_clip_required=trueish')
                elif kind=='frame':s.find('.//a:off',ns).set('x','999999')
                elif kind=='control':s.find('.//a:cubicBezTo/a:pt',ns).set('x','999999')
                elif kind=='closure':
                    path=s.find('.//a:pathLst/a:path',ns);path.remove(list(path)[-1])
                elif kind=='units':s.find('.//a:pathLst/a:path',ns).set('w','100')
                elif kind=='namespace':s.find('.//a:cubicBezTo',ns).tag='{urn:wrong}cubicBezTo'
                elif kind=='point_tag':s.find('.//a:cubicBezTo/a:pt',ns).tag='{'+ns['a']+'}lnTo'
                elif kind=='duplicate_fill':
                    pr=s.find('.//p:spPr',ns);pr.append(copy.deepcopy(pr.find('a:solidFill',ns)))
                elif kind=='line_fill':ET.SubElement(s.find('.//a:ln',ns),'{'+ns['a']+'}solidFill')
                elif kind=='root_transform':s.find('.//p:grpSpPr/a:xfrm',ns).set('rot','60000')
                elif kind=='background':s.find('.//p:bgPr/a:solidFill/a:srgbClr',ns).set('val','000000')
                elif kind=='boolean':s.find('.//p:spPr/a:xfrm',ns).set('flipH','yes')
                elif kind=='duplicate_tree':
                    content=s.find('p:cSld',ns);content.append(copy.deepcopy(content.find('p:spTree',ns)))
                elif kind=='relationship':p.find('p:sldIdLst/p:sldId',ns).set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id','wrong')
                elif kind=='presentation_root':p.tag='{urn:wrong}presentation'
                elif kind=='slide_root':s.tag='{urn:wrong}sld'
                elif kind=='use_bg_fill':s.find('.//p:sp',ns).set('useBgFill','1')
                elif kind=='bw_mode':s.find('.//p:spPr',ns).set('bwMode','white')
                elif kind=='alpha_child':ET.SubElement(s.find('.//p:spPr/a:solidFill/a:srgbClr/a:alpha',ns),'{'+ns['a']+'}shade',val='50000')
                elif kind=='timing':ET.SubElement(s,'{'+ns['p']+'}timing')
                elif kind=='geometry_foreign':ET.SubElement(s.find('.//a:custGeom',ns),'{urn:wrong}paint')
                elif kind=='placeholder':ET.SubElement(s.find('.//p:nvSpPr',ns),'{'+ns['p']+'}ph',type='title')
                elif kind=='missing_native_id':s.find('.//p:sp/p:nvSpPr/p:cNvPr',ns).attrib.pop('id')
                elif kind=='invalid_native_id':s.find('.//p:sp/p:nvSpPr/p:cNvPr',ns).set('id','bad')
                elif kind=='out_of_range_native_id':s.find('.//p:sp/p:nvSpPr/p:cNvPr',ns).set('id','4294967296')
                elif kind=='numeric_duplicate_native_id':
                    ids=s.findall('.//p:sp/p:nvSpPr/p:cNvPr',ns);ids[1].set('id','0'+ids[0].get('id'))
                elif kind=='missing_slide_id':p.find('p:sldIdLst/p:sldId',ns).attrib.pop('id')
                elif kind=='invalid_slide_id':p.find('p:sldIdLst/p:sldId',ns).set('id','bad')
                elif kind=='small_slide_id':p.find('p:sldIdLst/p:sldId',ns).set('id','255')
                elif kind=='overflow_slide_id':p.find('p:sldIdLst/p:sldId',ns).set('id','4294967296')
                elif kind=='signed31_slide_id_boundary':p.find('p:sldIdLst/p:sldId',ns).set('id','2147483648')
                elif kind=='slide_id_child':ET.SubElement(p.find('p:sldIdLst/p:sldId',ns),'{'+ns['p']+'}timing')
                elif kind=='fill':s.find('.//a:solidFill/a:srgbClr',ns).set('val','FF0000')
                elif kind=='opacity':s.find('.//a:solidFill/a:srgbClr/a:alpha',ns).set('val','50000')
                elif kind=='hidden':s.find('.//p:cNvPr',ns).set('hidden','1')
                elif kind=='order':
                    tree=s.find('.//p:spTree',ns);shape=tree.find('p:sp',ns);tree.remove(shape);tree.append(shape)
                elif kind=='group':
                    tree=s.find('.//p:spTree',ns);shape=tree.find('p:sp',ns);tree.remove(shape);ET.SubElement(tree,'{'+ns['p']+'}grpSp').append(shape)
            return change
        for kind in ['slide','second_slide','marker','frame','control','closure','units','group','fill','opacity','hidden','order',
                     'namespace','point_tag','duplicate_fill','line_fill','root_transform','background','boolean','duplicate_tree','relationship',
                     'presentation_root','slide_root','use_bg_fill','bw_mode','alpha_child','timing','geometry_foreign','placeholder',
                     'missing_native_id','invalid_native_id','out_of_range_native_id','numeric_duplicate_native_id',
                     'missing_slide_id','invalid_slide_id','small_slide_id','overflow_slide_id','signed31_slide_id_boundary','slide_id_child']:
            ppt=native_fixture(self.root,self.m,mutation(kind))
            with self.subTest(kind=kind),self.assertRaises(SourceCanvasClipError):verify_native_canvas_clip(ppt,self.m,receipt)

    def test_native_manifest_change_and_saved_proof_forgery_rejected(self):
        receipt=verify_source_canvas_clip(self.m,self.root);ppt=native_fixture(self.root,self.m)
        m=copy.deepcopy(self.m);m['objects'][0]['style']['fill']='#FF0000'
        with self.assertRaisesRegex(SourceCanvasClipError,'current fresh source proof'):verify_native_canvas_clip(ppt,m,receipt)
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps(self.m));saved=self.root/'receipt.json';receipt['source_rgb_samples_sha256']='0'*64;saved.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(SourceCanvasClipError,'fresh source replay'):main(['--manifest',str(manifest),'--root',str(self.root),'--pptx',str(ppt),'--source-receipt',str(saved),'--output',str(self.root/'out.json')])
        with self.assertRaisesRegex(SourceCanvasClipError,'base deck'):main(['--manifest',str(manifest),'--root',str(self.root),'--base-present','--output',str(self.root/'out.json')])

    def test_actual_pdf_internal_clip_and_stroked_text_are_not_eligible(self):
        for name,options in [('clipped',{'clipped':True}),('stroke',{'render_mode':1})]:
            root=self.root/name;root.mkdir();m=make_fixture(root,**options)
            with self.subTest(name=name),self.assertRaises(SourceCanvasClipError):verify_source_canvas_clip(m,root)

    def test_rounded_float_product_cannot_claim_integer_pixel_grid(self):
        m=copy.deepcopy(self.m);r=m['source_canvas_clip']['raster'];r['scale']=10;r['roi_pdf_points']=[.1,.1,14.1,4.5];r['irect']=[1,1,141,45]
        with self.assertRaisesRegex(SourceCanvasClipError,'exactly'):verify_source_canvas_clip(m,self.root)


class NativeContextBoundaryTests(unittest.TestCase):
    def record(self):
        return {'source_seqno':0,'bbox_pdf_pt':[0,0,20,20],'role':'normal','page_object_allowed':True,'unresolved':[],
                'clip_ids':[],'group_ids':[],'pattern_depth':0,'active_mask_ids':[],'active_image_mask_clip_ids':[]}
    def report(self,paint,groups=None):
        return {'identity_complete':True,'groups':groups or [],'paints':[paint],'source_pdf_sha256':'a'*64,'bboxlog_sha256':'b'*64,'paint_count':1}
    def test_native_unknown_infinite_empty_and_invalid_bounds_are_conservative(self):
        for box in [[-2147483648,-2147483648,2147483520,2147483520],[20,20,10,10],[float('inf'),0,1,1]]:
            self.assertTrue(_possibly_intersects(box,[0,0,10,10]))
        self.assertFalse(_possibly_intersects([20,20,30,30],[0,0,10,10]))
        self.assertTrue(_possibly_intersects([10,10,10,10],[0,0,10,10]))
        self.assertTrue(_possibly_intersects([100,100,100,100],[0,0,10,10]))
    def test_masks_clips_patterns_and_unknown_groups_refuse_even_infinite_bbox(self):
        for key,value in [('role','active_mask_unsupported'),('page_object_allowed',False),('unresolved',['unknown']),('clip_ids',[1]),('pattern_depth',1),('active_mask_ids',[1]),('active_image_mask_clip_ids',[1]),('group_ids',[999])]:
            p=self.record();p[key]=value;p['bbox_pdf_pt']=[-2147483648,-2147483648,2147483520,2147483520]
            with self.subTest(key=key),mock.patch('figure_rebuild.pdf_paint_context.inspect_pdf_paint_context',return_value=self.report(p)),self.assertRaises(SourceCanvasClipError):_native_context('unused',1,[0,0,10,10])
    def test_mask_definition_paints_are_not_page_objects(self):
        p=self.record();p['role']='mask_definition';p['page_object_allowed']=False
        with mock.patch('figure_rebuild.pdf_paint_context.inspect_pdf_paint_context',return_value=self.report(p)):
            receipt=_native_context('unused',1,[0,0,10,10])
        self.assertEqual(receipt['mask_definition_seqnos_not_page_objects'],[0]);self.assertFalse(receipt['admitted_roi_paint_seqnos'])
