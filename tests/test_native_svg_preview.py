import copy
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from figure_rebuild import native_svg_preview as native

try:
    import pymupdf
except ImportError:
    pymupdf = None


def fixture():
    p, a = native.NS['p'], native.NS['a']
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    rel = 'http://schemas.openxmlformats.org/package/2006/relationships'
    ns = f'xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"'
    outer = '<a:moveTo><a:pt x="10" y="10"/></a:moveTo><a:lnTo><a:pt x="60" y="10"/></a:lnTo><a:lnTo><a:pt x="60" y="60"/></a:lnTo><a:lnTo><a:pt x="10" y="60"/></a:lnTo><a:close/>'
    inner = '<a:moveTo><a:pt x="25" y="25"/></a:moveTo><a:lnTo><a:pt x="45" y="25"/></a:lnTo><a:lnTo><a:pt x="45" y="45"/></a:lnTo><a:lnTo><a:pt x="25" y="45"/></a:lnTo><a:close/>'
    def shape(name, commands, fill, line):
        return f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="{name}"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="762000"/></a:xfrm><a:custGeom><a:pathLst><a:path w="100" h="80">{commands}</a:path></a:pathLst></a:custGeom>{fill}{line}</p:spPr></p:sp>'
    ring = shape('ring', outer+inner, '<a:solidFill><a:srgbClr val="FF0000"/></a:solidFill>', '<a:ln><a:noFill/></a:ln>')
    curve = shape('curve', '<a:moveTo><a:pt x="70" y="10"/></a:moveTo><a:cubicBezTo><a:pt x="90" y="10"/><a:pt x="70" y="60"/><a:pt x="90" y="60"/></a:cubicBezTo>', '<a:noFill/>', '<a:ln w="38100" cap="sq"><a:solidFill><a:srgbClr val="0000FF"><a:alpha val="50000"/></a:srgbClr></a:solidFill><a:prstDash val="solid"/><a:miter lim="1000000"/></a:ln>')
    files = {'ppt/slides/slide1.xml': f'<p:sld {ns}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{ring}{curve}</p:spTree></p:cSld></p:sld>',
             'ppt/slides/_rels/slide1.xml.rels': f'<Relationships xmlns="{rel}"/>',
             'ppt/presentation.xml': f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="slide"/></p:sldIdLst><p:sldSz cx="952500" cy="762000"/></p:presentation>',
             'ppt/_rels/presentation.xml.rels': f'<Relationships xmlns="{rel}"><Relationship Id="slide" Type="{r}/slide" Target="slides/slide1.xml"/></Relationships>'}
    manifest = {'canvas': {'width': 100, 'height': 80, 'background': '#FFFFFF'},
                'source': {'path': '/unavailable-original.png'}, 'objects': [{'id': 'ring', 'kind': 'path'}, {'id': 'curve', 'kind': 'path'}]}
    return files, manifest


class NativeSvgTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.ppt = Path(temp.name)/'native.pptx'; self.files, self.manifest = fixture(); self.write()

    def write(self, files=None):
        with ZipFile(self.ppt, 'w') as z:
            for name, data in (files or self.files).items(): z.writestr(name, data)

    def prepare(self):
        return native.prepare_native_svg(self.ppt, self.manifest)

    @unittest.skipUnless(pymupdf, 'optional PyMuPDF source dependency')
    def test_native_evenodd_hole_cubic_cap_miter_alpha_and_all_three_grids(self):
        before = self.ppt.read_bytes(); d = self.prepare(); out, receipt = native.sample_native_svg(d)
        self.assertEqual(before, self.ppt.read_bytes()); self.assertEqual(d, self.prepare())
        self.assertIn('stroke-linecap="square"', d['svg']); self.assertIn('stroke-miterlimit="10"', d['svg'])
        self.assertEqual([r['id'] for r in d['objects']], ['ring', 'curve'])
        for scale in (1, 2, 4):
            image = Image.open(io.BytesIO(out[scale])).convert('RGB')
            self.assertEqual(image.size, (100*scale,80*scale)); self.assertEqual(image.getpixel((15*scale,15*scale)), (255,0,0))
            self.assertEqual(image.getpixel((35*scale,35*scale)), (255,255,255))
            self.assertEqual(hashlib.sha256(out[scale]).hexdigest(), receipt['samples'][[1,2,4].index(scale)]['png_sha256'])
        self.assertFalse(d['reference_pixels_used']); self.assertFalse(d['source_pixel_equivalence'])

    @unittest.skipUnless(pymupdf, 'optional PyMuPDF source dependency')
    def test_actual_native_control_color_alpha_and_order_changes_change_definition_or_pixels(self):
        d = self.prepare(); baseline, _ = native.sample_native_svg(d)
        for before, after in [('x="60"', 'x="63"'), ('FF0000', '00FF00'), ('50000', '25000'), ('1000000', '250000')]:
            changed = copy.deepcopy(self.files); changed['ppt/slides/slide1.xml'] = changed['ppt/slides/slide1.xml'].replace(before, after)
            self.write(changed); new = self.prepare(); self.assertNotEqual(d, new)
            pixels, _ = native.sample_native_svg(new)
            if before != '1000000': self.assertNotEqual(baseline[4], pixels[4])
        self.manifest['objects'].reverse(); self.write()
        with self.assertRaisesRegex(ValueError, 'identity/order'): self.prepare()

    def test_actual_active_slide_relationship_and_background_are_required(self):
        for file, before, after, message in [
            ('ppt/_rels/presentation.xml.rels','slides/slide1.xml','slides/slide2.xml','relationship identity'),
            ('ppt/slides/slide1.xml','val="FFFFFF"','val="EEEEEE"','background differs'),
            ('ppt/presentation.xml','cx="952500"','cx="952501"','canvas identity')]:
            changed = copy.deepcopy(self.files); changed[file] = changed[file].replace(before, after); self.write(changed)
            with self.assertRaisesRegex(ValueError, message): self.prepare()

    def test_nonpath_source_groups_formulas_effects_rotation_and_dashes_refuse(self):
        for before, after in [('<a:xfrm>', '<a:xfrm rot="60000">'), ('<a:custGeom>', '<a:custGeom><a:gdLst/>'),
                              ('<a:ln w=', '<a:effectLst/><a:ln w='), ('val="solid"','val="dash"'),
                              ('<a:miter lim="1000000"/>','<a:miter lim="1000000"/><a:headEnd type="triangle"/>')]:
            changed=copy.deepcopy(self.files);changed['ppt/slides/slide1.xml']=changed['ppt/slides/slide1.xml'].replace(before,after);self.write(changed)
            with self.assertRaises(ValueError): self.prepare()
        self.write(); self.manifest['objects'][0]['kind']='text'
        with self.assertRaisesRegex(ValueError,'path-only'):self.prepare()

    def test_entity_duplicate_members_and_geometry_budgets_fail_before_decode(self):
        changed=copy.deepcopy(self.files);changed['ppt/slides/slide1.xml']='<!DOCTYPE p:sld [<!ENTITY e "bad">]>'+changed['ppt/slides/slide1.xml'];self.write(changed)
        with self.assertRaisesRegex(ValueError,'DTD/entity'):self.prepare()
        self.write()
        with patch.object(native,'MAX_COMMANDS',1), self.assertRaisesRegex(ValueError,'command budget'):self.prepare()
        with ZipFile(self.ppt,'a')as z:z.writestr('ppt/slides/slide1.xml',self.files['ppt/slides/slide1.xml'])
        with self.assertRaisesRegex(ValueError,'duplicate member'):self.prepare()

    @unittest.skipUnless(pymupdf, 'optional PyMuPDF source dependency')
    def test_sample_rejects_unbounded_or_external_svg_even_with_rebound_hash(self):
        d=self.prepare()
        for old,new in [('fill="#FF0000"','fill="url(https://invalid.example/paint.svg)"'),
                        ('<path ','<image href="https://invalid.example/pic.png" '),
                        ('stroke-opacity="0.5"','stroke-opacity="nan"')]:
            changed=copy.deepcopy(d);changed['svg']=changed['svg'].replace(old,new);changed['svg_sha256']=hashlib.sha256(changed['svg'].encode()).hexdigest()
            with patch.object(pymupdf,'open',side_effect=AssertionError('source decode must not run')), self.assertRaises(ValueError):
                native.sample_native_svg(changed)


if __name__ == '__main__':unittest.main()
