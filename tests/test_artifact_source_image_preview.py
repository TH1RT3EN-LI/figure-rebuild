import base64
import copy
import hashlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import artifact_image_preview as preview
from figure_rebuild import artifact_source_image_preview as source_preview
from figure_rebuild.pdf_image_render import render_native_pdf_image, render_native_pdf_image_interval

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


@unittest.skipUnless(fitz, 'optional PyMuPDF source dependency')
class SourceImagePreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.path = self.root/'native.pptx'; self.source = self.root/'source.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=120, height=90)
            image = Image.new('RGB', (47, 43))
            for x in range(image.width):
                for y in range(image.height):
                    image.putpixel((x, y), ((x*37+y*11)%256, (x*13+y*41)%256, (x*19+y*7)%256))
            buffer = io.BytesIO(); image.save(buffer, format='PNG')
            page.insert_image(fitz.Rect(10.35, 5.25, 32.65, 30.75), stream=buffer.getvalue(), keep_proportion=False)
            # An actual CMYK resource overlaps the first image. Its native
            # conversion order is preserved within the image-only interval.
            cmyk = fitz.Pixmap(fitz.csCMYK, 5, 7, bytes([32, 180, 70, 20])*35, False)
            page.insert_image(fitz.Rect(21.125, 8.5, 40.25, 31.75), pixmap=cmyk, keep_proportion=False)
            image = Image.new('RGB', (19, 17), (190, 30, 200)); buffer = io.BytesIO(); image.save(buffer, format='PNG')
            page.insert_image(fitz.Rect(60.35, 45.25, 82.65, 70.75), stream=buffer.getvalue(), keep_proportion=False)
            page.draw_rect(fitz.Rect(22, 12, 27, 17), fill=(0, 1, 0), color=None)
            # The admitted fixture explicitly has no shared source group.
            # The separate negative tests use actual group callbacks.
            doc.xref_set_key(page.xref, 'Group', 'null')
            doc.save(self.source)
        self.request = {'schema_version': 1, 'source_pdf': {'path': 'source.pdf', 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()},
                        'page_index': 0, 'source_transform': [1,0,0,1,0,0], 'user_clip_pdf': [0,0,120,90],
                        'objects': [{'id': 'photo-0', 'paint_seqnos': [0,1], 'source_bounds': [10,5,41,32], 'delivery_sampling_scale': 4},
                                    {'id': 'photo-1', 'paint_seqnos': [2], 'source_bounds': [60,45,83,71], 'delivery_sampling_scale': 4}]}
        self.manifest = {'canvas': {'width': 120, 'height': 90}, 'source': {'path': '/missing-reference.png'},
                         'objects': [{'id': 'photo-0', 'kind': 'image'}, {'id': 'photo-1', 'kind': 'image'}]}
        self.media = [self.sample(o, 4, False)['asset_bytes'] for o in self.request['objects']]
        p, a, r = (preview.NS[k] for k in ('p','a','r')); self.ns = f'xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"'
        self.pics = []
        for i, obj in enumerate(self.request['objects']):
            x,y,x1,y1 = obj['source_bounds']; n = [v*9525 for v in (x,y,x1-x,y1-y)]
            self.pics.append(f'''<p:pic><p:nvPicPr><p:cNvPr id="{i+2}" name="photo-{i}"/></p:nvPicPr>
                <p:blipFill><a:blip r:embed="r{i}"/><a:srcRect l="0" t="0" r="0" b="0"/><a:stretch/></p:blipFill>
                <p:spPr><a:xfrm><a:off x="{n[0]}" y="{n[1]}"/><a:ext cx="{n[2]}" cy="{n[3]}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>''')
        self.xml = f'<p:sld {self.ns}><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>'+''.join(self.pics)+'</p:spTree></p:cSld></p:sld>'
        self.write()

    def sample(self, recipe, scale, global_grid):
        with fitz.open(self.source) as doc:
            page = doc[0]
            return render_native_pdf_image_interval(page, page.get_bboxlog(), recipe['paint_seqnos'],
                source_transform=[1,0,0,1,0,0], source_bounds=recipe['source_bounds'], user_clip_pdf=[0,0,120,90],
                sampling_scale=scale, global_device_grid=global_grid)

    def write(self, *, xml=None, media=None):
        with ZipFile(self.path, 'w') as z:
            z.writestr('ppt/slides/slide1.xml', xml or self.xml)
            z.writestr('ppt/slides/_rels/slide1.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(
                f'<Relationship Id="r{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="/ppt/media/{i}.png"/>' for i in range(2))+'</Relationships>')
            z.writestr('ppt/presentation.xml', f'<p:presentation {self.ns}><p:sldSz cx="1143000" cy="857250"/></p:presentation>')
            for i, data in enumerate(media or self.media): z.writestr(f'ppt/media/{i}.png', data)

    def prepare(self, request=None):
        return preview.prepare_image_preview(self.path, self.manifest, version=4,
            source_sampling=self.request if request is None else request, asset_root=self.root)

    def test_three_target_grids_match_original_image_only_context_including_cmyk(self):
        before = self.path.read_bytes(); result = self.prepare()
        self.assertEqual(result, self.prepare()); self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(result['schema_version'], 4); self.assertFalse(result['reference_pixels_used'])
        self.assertFalse(result['source_pixel_equivalence']); self.assertFalse(result['native_delivery_modified'])
        for proof in result['source_sampling_proofs']:
            self.assertTrue(proof['delivery_media_replayed_byte_exactly'])
            self.assertIsNone(proof['delivery_replay']['rgb_alpha_error_bound'])
        # Suppress only the actual independent vector stream. The independent
        # full-page renderer keeps all original image resources and contexts.
        with fitz.open(self.source) as doc:
            doc.update_stream(doc[0].get_contents()[-1], b'')
            for scale in (1,2,4):
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale,scale), alpha=True)
                full = Image.frombytes('RGBA', (pix.width,pix.height), pix.samples)
                for obj, recipe in zip(result['objects'], self.request['objects']):
                    item = next(p for p in obj['previews'] if p['scale']==scale)
                    im = Image.open(io.BytesIO(base64.b64decode(item['png_base64']))).convert('RGBA')
                    self.assertEqual(im.tobytes(), full.crop(tuple(v*scale for v in recipe['source_bounds'])).tobytes())
        first = result['source_sampling_proofs'][0]['target_grid_replays'][0]
        self.assertIsNone(first['all_selected_image_receipts'][0]['attached_mask'])
        self.assertIn('CMYK', first['all_selected_image_receipts'][1]['native_colorspace'])
        self.assertEqual(first['independent_text_path_shading_other_image_paints_forwarded'], 0)

    def test_source_reference_vector_pixels_are_not_in_image_preview(self):
        item = self.prepare()['objects'][0]['previews'][0]
        im = Image.open(io.BytesIO(base64.b64decode(item['png_base64']))).convert('RGBA')
        self.assertNotEqual(im.getpixel((14,9))[:3], (0,255,0))
        with fitz.open(self.source) as doc: self.assertEqual(doc[0].get_pixmap(alpha=False).pixel(24,14), (0,255,0))

    def test_source_checksum_and_changed_source_even_with_rebound_checksum_refuse(self):
        with fitz.open(self.source) as doc:
            doc.xref_set_key(doc[0].get_images()[0][0], 'Interpolate', 'true')
            changed = doc.tobytes()
        self.source.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'frozen checksum'): self.prepare()
        request = copy.deepcopy(self.request); request['source_pdf']['sha256'] = hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(ValueError, 'does not reproduce'): self.prepare(request)

    def test_changed_actual_media_bytes_cannot_be_hidden_by_source_recipe(self):
        image = Image.open(io.BytesIO(self.media[0])).convert('RGBA'); image.putpixel((40,40), (0,255,0,255))
        buffer=io.BytesIO(); image.save(buffer, format='PNG'); self.write(media=[buffer.getvalue(),self.media[1]])
        with self.assertRaisesRegex(ValueError, 'does not reproduce'): self.prepare()

    def test_native_crop_frame_and_effects_keep_strict_actual_picture_checks(self):
        for xml, message in [(self.xml.replace('l="0"', 'l="1"', 1), 'native frame or source crop'),
                             (self.xml.replace('x="95250"','x="95251"'), 'native frame'),
                             (self.xml.replace('<a:stretch/>','<a:tile/>',1), 'Unsupported declared'),
                             (self.xml.replace('<p:spPr>','<p:spPr><a:effectLst/>',1), 'Unsupported declared')]:
            self.write(xml=xml)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message): self.prepare()

    def test_intervals_cannot_skip_actual_nonimage_paint_or_native_identities(self):
        request=copy.deepcopy(self.request); request['objects'][1]['paint_seqnos']=[2,3]
        with self.assertRaisesRegex(ValueError, 'actual image paints'): self.prepare(request)
        for change in [lambda r:r['objects'].reverse(), lambda r:r['objects'].pop(),
                       lambda r:r['objects'][0]['paint_seqnos'].__setitem__(0, True),
                       lambda r:r['objects'][0]['source_bounds'].__setitem__(0, 10.0)]:
            request=copy.deepcopy(self.request); change(request)
            with self.subTest(request=request), self.assertRaises(ValueError): self.prepare(request)

    def test_real_source_transparency_group_refuses_without_group_policy_extension(self):
        with fitz.open(self.source) as doc:
            doc.xref_set_key(doc[0].xref, 'Group', '<</S/Transparency/CS/DeviceRGB/I true>>')
            changed=doc.tobytes()
        self.source.write_bytes(changed); request=copy.deepcopy(self.request)
        request['source_pdf']['sha256']=hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(ValueError, 'group-free'): self.prepare(request)

    def test_image_mask_which_introduces_actual_page_group_keeps_group_refusal(self):
        image=Image.new('RGBA',(47,43),(255,0,0,128)); buffer=io.BytesIO(); image.save(buffer,format='PNG')
        with fitz.open(self.source) as doc:
            page=doc[0]; page.insert_image(fitz.Rect(10,5,41,32),stream=buffer.getvalue(),keep_proportion=False)
            changed=doc.tobytes()
        self.source.write_bytes(changed); request=copy.deepcopy(self.request)
        request['source_pdf']['sha256']=hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(ValueError,'group-free'): self.prepare(request)

    def test_pixel_budget_and_path_escape_refuse_before_native_source_open(self):
        with patch.object(fitz, 'open', side_effect=AssertionError('unplanned source render')):
            with patch.object(preview, 'MAX_COMBINED_PIXELS', 1), self.assertRaisesRegex(ValueError, 'Combined'): self.prepare()
            request=copy.deepcopy(self.request); request['source_pdf']['path']='../source.pdf'
            with self.assertRaisesRegex(ValueError, 'inside'): self.prepare(request)
        with self.assertRaisesRegex(ValueError, 'immutable asset root'):
            preview.prepare_image_preview(self.path,self.manifest,version=4,source_sampling=self.request)
        for version in (1,2,3):
            with self.assertRaisesRegex(ValueError, 'explicit version 4'):
                preview.prepare_image_preview(self.path,self.manifest,version=version,source_sampling=self.request,asset_root=self.root)

    def test_legacy_single_image_contract_keeps_existing_sampling_scale_policy(self):
        with fitz.open(self.source) as doc:
            page=doc[0]
            with self.assertRaisesRegex(ValueError,'4 or 8'):
                render_native_pdf_image(page,page.get_bboxlog(),0,source_transform=[1,0,0,1,0,0],
                    source_bounds=[10,5,41,32],user_clip_pdf=[0,0,120,90],native_sampling_scale=1)


if __name__ == '__main__': unittest.main()
