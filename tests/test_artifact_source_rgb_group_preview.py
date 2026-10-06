import base64
import copy
import hashlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import artifact_image_preview as preview
from figure_rebuild.artifact_source_image_preview import RGB_GROUP_POLICY, source_request_assets
from figure_rebuild.pdf_image_render import render_native_pdf_image, render_native_pdf_image_target_grid
if __package__:
    from . import test_pdf_image_group_sampling as group_fixture
else:
    import test_pdf_image_group_sampling as group_fixture
fitz = group_fixture.fitz


@unittest.skipUnless(fitz, 'optional PyMuPDF source dependency')
class SourceRgbGroupPreviewTests(unittest.TestCase):
    def setUp(self):
        doc, page, form, self.source, profile = group_fixture.NativeRgbGroupSamplingTests.fixture(self, mixed=False)
        self.root = self.source.parent; self.ppt = self.root/'native.pptx'; self.form = form
        self.request = dict(schema_version=1, source_pdf=dict(path=self.source.name, sha256=hashlib.sha256(self.source.read_bytes()).hexdigest()),
            page_index=0, source_transform=[1,0,0,1,0,0], user_clip_pdf=[0,0,120,100], group_sampling_policy=RGB_GROUP_POLICY,
            objects=[dict(id='picture',paint_seqnos=[0],source_bounds=[20,10,80,70],delivery_sampling_scale=4)])
        self.manifest = dict(canvas=dict(width=120,height=100),source=dict(path='/unread-reference.png'),objects=[dict(id='picture',kind='image')])
        with fitz.open(self.source)as d:
            p=d[0];self.media=render_native_pdf_image(p,p.get_bboxlog(),0,source_transform=[1,0,0,1,0,0],
                source_bounds=[20,10,80,70],user_clip_pdf=[0,0,120,100],native_sampling_scale=4,allow_native_rgb_group_sampling=True)['asset_bytes']
        p,a,r=(preview.NS[k]for k in ('p','a','r'));ns=f'xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"'
        self.xml=f'<p:sld {ns}><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/><p:pic><p:nvPicPr><p:cNvPr id="2" name="picture"/></p:nvPicPr><p:blipFill><a:blip r:embed="image"/><a:srcRect l="0" t="0" r="0" b="0"/><a:stretch/></p:blipFill><p:spPr><a:xfrm><a:off x="190500" y="95250"/><a:ext cx="571500" cy="571500"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic></p:spTree></p:cSld></p:sld>'
        self.presentation=f'<p:presentation {ns}><p:sldSz cx="1143000" cy="952500"/></p:presentation>'
        self.rel=f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="image" Type="{r}/image" Target="../media/photo.png"/></Relationships>'
        self.write()

    def write(self, xml=None, media=None):
        with ZipFile(self.ppt,'w')as z:
            z.writestr('ppt/slides/slide1.xml',xml or self.xml);z.writestr('ppt/slides/_rels/slide1.xml.rels',self.rel)
            z.writestr('ppt/presentation.xml',self.presentation);z.writestr('ppt/media/photo.png',media or self.media)

    def prepare(self, request=None):
        return preview.prepare_image_preview(self.ppt,self.manifest,version=5,source_rgb_group_sampling=request or self.request,asset_root=self.root)

    def test_actual_ICC_child_mask_and_three_grids_equal_independent_full_original_group(self):
        before=self.ppt.read_bytes();d=self.prepare();self.assertEqual(d,self.prepare());self.assertEqual(before,self.ppt.read_bytes())
        self.assertEqual(d['schema_version'],5);self.assertTrue(d['shared_group_split_unverified']);self.assertFalse(d['exact_group_decomposition_claimed']);self.assertIsNone(d['rgb_alpha_error_bound'])
        proof=d['source_sampling_proofs'][0];self.assertTrue(proof['delivery_media_replayed_byte_exactly'])
        for item in d['objects'][0]['previews']:
            scale=item['scale'];image=Image.open(io.BytesIO(base64.b64decode(item['png_base64']))).convert('RGBA')
            with fitz.open(self.source)as doc:
                pix=doc[0].get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=True)
                whole=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGBA').crop((20*scale,10*scale,80*scale,70*scale))
            self.assertEqual(image.tobytes(),whole.tobytes())
        replay=proof['target_grid_replays'][0];self.assertTrue(replay['single_image_target_grid']);self.assertTrue(replay['global_device_grid'])
        self.assertEqual(len(replay['groups']),2);self.assertIn('ICCBased',replay['groups'][1]['native_colorspace']['name'])
        self.assertIsNotNone(replay['attached_mask']);self.assertTrue(replay['native_color_mask_filtering_and_interpolation_retained'])

    def test_v4_and_legacy_sampling_admission_are_unchanged(self):
        request=copy.deepcopy(self.request);del request['group_sampling_policy']
        with self.assertRaisesRegex(ValueError,'group-free'):
            preview.prepare_image_preview(self.ppt,self.manifest,version=4,source_sampling=request,asset_root=self.root)
        with fitz.open(self.source)as doc:
            with self.assertRaisesRegex(ValueError,'4 or 8'):
                render_native_pdf_image(doc[0],doc[0].get_bboxlog(),0,source_transform=[1,0,0,1,0,0],source_bounds=[20,10,80,70],user_clip_pdf=[0,0,120,100],native_sampling_scale=1)

    def test_unsupported_group_changes_refuse_even_with_rebound_source_checksum(self):
        with fitz.open(self.source)as doc:
            doc.xref_set_key(self.form,'Group/K','true');self.source.write_bytes(doc.tobytes())
        request=copy.deepcopy(self.request);request['source_pdf']['sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'Unsupported PDF group'):self.prepare(request)

    def test_actual_media_crop_and_origin_cannot_be_replaced_by_source_sampling(self):
        image=Image.open(io.BytesIO(self.media)).convert('RGBA');image.putpixel((10,10),(0,255,0,255));buf=io.BytesIO();image.save(buf,format='PNG');self.write(media=buf.getvalue())
        with self.assertRaisesRegex(ValueError,'does not reproduce'):self.prepare()
        for before,after in [('l="0"','l="1"'),('x="190500"','x="190501"')]:
            self.write(xml=self.xml.replace(before,after))
            with self.assertRaisesRegex(ValueError,'native frame or source crop'):self.prepare()

    def test_policy_single_paint_resource_and_path_guards_precede_source_open(self):
        for changes in [dict(group_sampling_policy='arbitrary-groups'),dict(extra=True)]:
            request=copy.deepcopy(self.request);request.update(changes)
            with self.assertRaises(ValueError):source_request_assets(request,rgb_groups=True)
        request=copy.deepcopy(self.request);request['objects'][0]['paint_seqnos']=[0,1]
        with self.assertRaises(ValueError):source_request_assets(request,rgb_groups=True)
        with patch.object(fitz,'open',side_effect=AssertionError('source open must not run')):
            with patch.object(preview,'MAX_COMBINED_PIXELS',1),self.assertRaisesRegex(ValueError,'Combined'):self.prepare()
            request=copy.deepcopy(self.request);request['source_pdf']['path']='../source.pdf'
            with self.assertRaisesRegex(ValueError,'inside'):self.prepare(request)
        with self.assertRaisesRegex(ValueError,'explicit version 5'):
            preview.prepare_image_preview(self.ppt,self.manifest,version=4,source_rgb_group_sampling=self.request,asset_root=self.root)


if __name__ == '__main__':unittest.main()
