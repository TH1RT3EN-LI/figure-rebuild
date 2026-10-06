import base64
import copy
from fractions import Fraction
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import artifact_image_preview as preview
from figure_rebuild import artifact_shared_image_preview as shared

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


@unittest.skipUnless(fitz, 'optional PyMuPDF source dependency')
class SharedImagePreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'native.pptx'
        self.windows = [[10, 5, 30, 25], [50, 5, 80, 30]]
        self.request = {'schema_version': 1, 'groups': [{'objects': [
            {'id': f'icon-{i}', 'window': w} for i, w in enumerate(self.windows)]}]}
        self.manifest = {'canvas': {'width': 120, 'height': 90}, 'source': {'path': '/missing-reference.png'},
                         'objects': [{'id': f'icon-{i}', 'kind': 'image'} for i in range(2)]}
        image = Image.new('RGBA', (128, 80), (255, 255, 255, 255))
        for x in range(128):
            for y in range(80):
                image.putpixel((x, y), ((x * 37 + y * 11) % 256, (x * 13 + y * 41) % 256, (x * 19 + y * 7) % 256, 255))
        buffer = io.BytesIO(); image.save(buffer, format='PNG'); self.media = buffer.getvalue()
        p, a, r = (preview.NS[k] for k in ('p', 'a', 'r'))
        self.ns = f'xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"'
        self.pics = []
        for i, (x0, y0, x1, y1) in enumerate(self.windows):
            crop = [round(Fraction(v * 100000, axis)) for v, axis in zip((x0, y0, 128 - x1, 80 - y1), (128, 80, 128, 80))]
            # Independently specified original full-media geometry, rounded to
            # native EMUs separately for the visible origin and extent.
            n = [round(v * 9525) for v in (2.35 + .7 * x0, 8.25 + .7 * y0, .7 * (x1 - x0), .7 * (y1 - y0))]
            self.pics.append(f'''<p:pic><p:nvPicPr><p:cNvPr id="{i+2}" name="icon-{i}"/></p:nvPicPr>
                <p:blipFill><a:blip r:embed="r{i}"/><a:srcRect l="{crop[0]}" t="{crop[1]}" r="{crop[2]}" b="{crop[3]}"/><a:stretch/></p:blipFill>
                <p:spPr><a:xfrm><a:off x="{n[0]}" y="{n[1]}"/><a:ext cx="{n[2]}" cy="{n[3]}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>''')
        self.xml = f'<p:sld {self.ns}><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>'+''.join(self.pics)+'</p:spTree></p:cSld></p:sld>'
        self.rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(
            f'<Relationship Id="r{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="/ppt/media/{i}.png"/>' for i in range(2))+'</Relationships>'

    def write(self, *, xml=None, media=None, second=None, presentation=None):
        with ZipFile(self.path, 'w') as z:
            z.writestr('ppt/slides/slide1.xml', xml or self.xml)
            z.writestr('ppt/slides/_rels/slide1.xml.rels', self.rels)
            z.writestr('ppt/presentation.xml', presentation or f'<p:presentation {self.ns}><p:sldSz cx="1143000" cy="857250"/></p:presentation>')
            z.writestr('ppt/media/0.png', media or self.media)
            z.writestr('ppt/media/1.png', second or media or self.media)

    def prepare(self, request=None):
        return preview.prepare_image_preview(self.path, self.manifest, version=3, shared_grid=request or self.request)

    def test_complete_media_grid_matches_independent_original_transform_at_three_scales(self):
        self.write(); original = self.path.read_bytes(); result = self.prepare()
        self.assertEqual(result, self.prepare()); self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(result['reference_pixels_used']); self.assertFalse(result['source_pixel_equivalence'])
        self.assertEqual(result['paint_order'], [{'id': 'icon-0', 'type': 'image'}, {'id': 'icon-1', 'type': 'image'}])
        with fitz.open() as doc:
            page = doc.new_page(width=120, height=90)
            xr = page.insert_image(fitz.Rect(2.35, 8.25, 2.35 + 128 * .7, 8.25 + 80 * .7), stream=self.media, keep_proportion=False)
            doc.xref_set_key(xr, 'Interpolate', 'false')
            for scale in (1, 2, 4):
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
                full = Image.frombytes('RGBA', (pix.width, pix.height), pix.samples)
                for obj in result['objects']:
                    item = next(p for p in obj['previews'] if p['scale'] == scale)
                    image = Image.open(io.BytesIO(base64.b64decode(item['png_base64']))).convert('RGBA')
                    p = item['position']; x, y = round(p['left'] * scale), round(p['top'] * scale)
                    self.assertEqual(image.tobytes(), full.crop((x, y, x + image.width, y + image.height)).tobytes())
                    self.assertEqual(image.getchannel('A').getextrema(), (255, 255))

    def test_explicit_window_must_reencode_actual_crop_and_cover_native_identities(self):
        self.write()
        changes = [lambda r: r['groups'][0]['objects'][0]['window'].__setitem__(0, 11),
                   lambda r: r['groups'][0]['objects'].pop(),
                   lambda r: r['groups'][0]['objects'].append(copy.deepcopy(r['groups'][0]['objects'][0])),
                   lambda r: r['groups'][0]['objects'][0]['window'].__setitem__(0, True),
                   lambda r: r['groups'][0]['objects'][0]['window'].__setitem__(2, 129)]
        for change in changes:
            r = copy.deepcopy(self.request); change(r)
            with self.subTest(request=r), self.assertRaises(ValueError): self.prepare(r)
        for version in (1, 2):
            with self.assertRaisesRegex(ValueError, 'explicit version'): preview.prepare_image_preview(self.path, self.manifest, version=version, shared_grid=self.request)
        with self.assertRaisesRegex(ValueError, 'version'): preview.prepare_image_preview(self.path, self.manifest, version=3)

    def test_changed_native_extent_cannot_be_replaced_by_declared_shared_grid(self):
        self.write(xml=self.xml.replace('cx="133350"', 'cx="133400"'))
        with self.assertRaisesRegex(ValueError, 'incompatible'): self.prepare()

    def test_media_identity_is_actual_bytes_and_changed_color_is_recomputed(self):
        self.write(); before = self.prepare()
        buffer = io.BytesIO(); Image.new('RGBA', (128, 80), (0, 255, 0, 255)).save(buffer, format='PNG')
        self.write(second=buffer.getvalue())
        with self.assertRaisesRegex(ValueError, 'different actual image bytes'): self.prepare()
        self.write(media=buffer.getvalue()); after = self.prepare()
        self.assertNotEqual(before['objects'][0]['previews'], after['objects'][0]['previews'])

    def test_transparent_and_profiled_media_are_not_admitted_as_opaque_windows(self):
        image = Image.open(io.BytesIO(self.media)).convert('RGBA'); image.putpixel((15, 10), (255, 0, 255, 0))
        buffer = io.BytesIO(); image.save(buffer, format='PNG'); self.write(media=buffer.getvalue())
        with self.assertRaisesRegex(ValueError, 'not opaque'): self.prepare()
        buffer = io.BytesIO(); image.save(buffer, format='PNG', icc_profile=b'unknown-profile'); self.write(media=buffer.getvalue())
        with self.assertRaisesRegex(ValueError, 'color profile'): self.prepare()

    def test_budget_and_native_canvas_mismatch_fail_before_mupdf_render_allocation(self):
        self.write()
        with patch.object(fitz, 'open', side_effect=AssertionError('allocation before planning')):
            with patch.object(preview, 'MAX_COMBINED_PIXELS', 1), self.assertRaisesRegex(ValueError, 'Combined'): self.prepare()
            with patch.object(preview, 'MAX_SURFACE_PIXELS', 1), self.assertRaisesRegex(ValueError, 'surface'): self.prepare()
            with patch.object(shared, 'MAX_PAIR_CONSTRAINTS', 1), self.assertRaisesRegex(ValueError, 'pair constraint'): self.prepare()
        self.write(presentation=f'<p:presentation {self.ns}><p:sldSz cx="1143001" cy="857250"/></p:presentation>')
        with self.assertRaisesRegex(ValueError, 'native slide dimensions'): self.prepare()

    def test_nonplain_native_state_retains_existing_refusal(self):
        for xml in (self.xml.replace('<a:xfrm>', '<a:xfrm rot="60000">'),
                    self.xml.replace('<a:stretch/>', '<a:tile/>'),
                    self.xml.replace('<p:spPr>', '<p:spPr><a:effectLst/>')):
            self.write(xml=xml)
            with self.subTest(xml=xml), self.assertRaisesRegex(ValueError, 'Unsupported declared'): self.prepare()


if __name__ == '__main__': unittest.main()
