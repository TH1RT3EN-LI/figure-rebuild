import base64
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import artifact_image_preview as preview

try:
    import pymupdf
except ImportError:
    pymupdf = None


@unittest.skipIf(pymupdf is None, 'optional PyMuPDF source dependency is unavailable')
class ImagePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.pptx = Path(self.temp.name) / 'native.pptx'
        p, a, r = (preview.NS[k] for k in ('p', 'a', 'r'))
        self.pic = '''<p:pic><p:nvPicPr><p:cNvPr id="2" name="source-image"/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="ImageRel"/><a:srcRect l="0" t="0" r="0" b="0"/><a:stretch/></p:blipFill>
        <p:spPr><a:xfrm><a:off x="97631" y="192881"/><a:ext cx="95250" cy="95250"/></a:xfrm>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'''
        self.xml = f'<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{self.pic}' \
                   '<p:sp><p:nvSpPr><p:cNvPr id="3" name="foreground"/></p:nvSpPr><p:spPr/></p:sp></p:spTree></p:cSld></p:sld>'
        self.rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="ImageRel" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="/ppt/media/image.png"/></Relationships>'
        self.manifest = {'source': {'path': '/nonexistent-reference.png'}, 'objects': [
            {'id': 'source-image', 'kind': 'image'}, {'id': 'foreground', 'kind': 'path'}]}
        source = Image.new('RGBA', (80, 80), (0, 0, 255, 0))
        for x in range(0, 80, 3):
            for y in range(80): source.putpixel((x, y), (255, 0, 0, 255))
        buffer = io.BytesIO(); source.save(buffer, format='PNG'); self.media = buffer.getvalue()

    def write(self, xml=None, rels=None, media=None):
        with ZipFile(self.pptx, 'w') as z:
            z.writestr('ppt/slides/slide1.xml', xml or self.xml)
            z.writestr('ppt/slides/_rels/slide1.xml.rels', rels or self.rels)
            z.writestr('ppt/media/image.png', self.media if media is None else media)

    def test_actual_media_fractional_device_grid_alpha_order_and_no_reference(self):
        self.write(); original = self.pptx.read_bytes()
        d = preview.prepare_image_preview(self.pptx, self.manifest)
        self.assertEqual(d['unsupported'], []); self.assertEqual(self.pptx.read_bytes(), original)
        self.assertEqual(d['paint_order'], [{'id': 'source-image', 'type': 'image'}, {'id': 'foreground', 'type': 'shape'}])
        self.assertFalse(d['reference_pixels_used']); self.assertFalse(d['native_delivery_modified'])
        o, = d['objects']; self.assertEqual(o['native_media_dimensions'], [80, 80])
        for p in o['previews']:
            png = Image.open(io.BytesIO(base64.b64decode(p['png_base64']))).convert('RGBA')
            self.assertEqual(png.size, (p['width'], p['height']))
            rgba = png.getpixel((png.width//2, png.height//2))
            # The declared PDF sampler filters color and mask independently.
            # It can dim RGB as well as alpha; this is not premultiplied Skia
            # equivalence. Hidden blue storage must not become blue ink.
            self.assertTrue(60 < rgba[0] <= 255, rgba)
            self.assertEqual(rgba[1:3], (0,0)); self.assertTrue(60 < rgba[3] < 125, rgba)
            self.assertEqual(p['position']['width'] * p['scale'], p['width'])
            self.assertEqual(p['position']['left'] * p['scale'], int(p['position']['left'] * p['scale']))
        self.assertEqual(d, preview.prepare_image_preview(self.pptx, self.manifest))

    def test_native_media_and_geometry_changes_are_recomputed(self):
        self.write(); first = preview.prepare_image_preview(self.pptx, self.manifest)
        self.write(self.xml.replace('x="97631"', 'x="99999"'))
        changed = preview.prepare_image_preview(self.pptx, self.manifest)
        self.assertNotEqual(first['objects'][0]['previews'], changed['objects'][0]['previews'])
        green = Image.new('RGBA', (80,80), (0,255,0,255)); b = io.BytesIO(); green.save(b, format='PNG')
        self.write(media=b.getvalue()); changed = preview.prepare_image_preview(self.pptx, self.manifest)
        self.assertNotEqual(first['objects'][0]['native_media_sha256'], changed['objects'][0]['native_media_sha256'])

    def test_crop_rotation_border_effect_tile_and_external_link_remain_unsupported(self):
        variations = [self.xml.replace('l="0"', 'l="1"'), self.xml.replace('<a:xfrm>', '<a:xfrm rot="60000">'),
                      self.xml.replace('<p:spPr><a:xfrm>', '<p:spPr><a:effectLst/><a:xfrm>'),
                      self.xml.replace('<a:stretch/>', '<a:tile/>'),
                      self.xml.replace('</p:spPr></p:pic>', '<a:ln><a:solidFill/></a:ln></p:spPr></p:pic>')]
        for xml in variations:
            with self.subTest(xml=xml):
                self.write(xml); d = preview.prepare_image_preview(self.pptx, self.manifest)
                self.assertEqual(d['objects'], []); self.assertEqual(len(d['unsupported']), 1)
        self.write(rels=self.rels.replace('Target="', 'TargetMode="External" Target="'))
        self.assertEqual(preview.prepare_image_preview(self.pptx, self.manifest)['objects'], [])

    def test_missing_duplicate_native_identity_and_child_groups_fail_closed(self):
        for xml in [self.xml.replace('name="source-image"', 'name="different"'),
                    self.xml.replace('name="foreground"', 'name="source-image"'),
                    self.xml.replace(self.pic, '<p:grpSp>'+self.pic+'</p:grpSp>')]:
            self.write(xml)
            with self.assertRaises(ValueError): preview.prepare_image_preview(self.pptx, self.manifest)
        self.write(rels=self.rels.replace('</Relationships>', self.rels.split('>',1)[1]))
        with self.assertRaisesRegex(ValueError, 'relationship'): preview.prepare_image_preview(self.pptx, self.manifest)

    def test_budgets_entities_duplicate_zip_members_and_encoded_profile(self):
        self.write()
        with patch.object(preview, 'MAX_SURFACE_PIXELS', 1):
            self.assertTrue(preview.prepare_image_preview(self.pptx, self.manifest)['unsupported'])
        with patch.object(preview, 'MAX_COMBINED_PIXELS', 1):
            with patch.object(pymupdf, 'open', side_effect=AssertionError('allocation before planning')):
                with self.assertRaisesRegex(ValueError, 'Combined'): preview.prepare_image_preview(self.pptx, self.manifest)
        with patch.object(preview, 'MAX_DEFINITION_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'definition byte'): preview.prepare_image_preview(self.pptx, self.manifest)
        self.write('<!DOCTYPE p:sld [<!ENTITY e "bad">]>'+self.xml)
        with self.assertRaisesRegex(ValueError, 'DTD/entity'): preview.prepare_image_preview(self.pptx, self.manifest)
        self.write()
        with ZipFile(self.pptx, 'a') as z: z.writestr('ppt/media/image.png', self.media)
        with self.assertRaisesRegex(ValueError, 'duplicate member'): preview.prepare_image_preview(self.pptx, self.manifest)
        profiled = io.BytesIO(); Image.new('RGB', (8,8)).save(profiled, format='PNG', icc_profile=b'not-an-sRGB-proof')
        self.write(media=profiled.getvalue())
        self.assertTrue(preview.prepare_image_preview(self.pptx, self.manifest)['unsupported'])

    def test_relative_media_relationship_and_negative_native_position(self):
        self.write(self.xml.replace('x="97631"', 'x="-2381"'), self.rels.replace('/ppt/media/image.png', '../media/image.png'))
        d = preview.prepare_image_preview(self.pptx, self.manifest)
        self.assertEqual(d['unsupported'], []); self.assertEqual(d['objects'][0]['previews'][0]['position']['left'], -1)


if __name__ == '__main__': unittest.main()
