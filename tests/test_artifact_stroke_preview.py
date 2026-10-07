import copy
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as E
from zipfile import ZipFile

from figure_rebuild import artifact_stroke_preview as preview


class StrokePreviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.pptx = Path(self.temp.name) / 'native.pptx'
        self.xml = f'''<p:sld xmlns:p="{preview.NS['p']}" xmlns:a="{preview.NS['a']}"><p:cSld><p:spTree>
        <p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="source-stroke"/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="381000" y="381000"/><a:ext cx="952500" cy="762000"/></a:xfrm>
        <a:custGeom><a:pathLst><a:path w="1000" h="800"><a:moveTo><a:pt x="0" y="800"/></a:moveTo><a:cubicBezTo><a:pt x="200" y="0"/><a:pt x="800" y="0"/><a:pt x="1000" y="800"/></a:cubicBezTo></a:path></a:pathLst></a:custGeom>
        <a:noFill/><a:ln w="114300" cap="rnd"><a:solidFill><a:srgbClr val="FF0000"><a:alpha val="50000"/></a:srgbClr></a:solidFill><a:prstDash val="solid"/><a:round/></a:ln></p:spPr></p:sp>
        </p:spTree></p:cSld></p:sld>'''
        self.manifest = {'objects': [{'id': 'source-stroke', 'kind': 'path', 'style': {
            'fill': 'none', 'stroke': '#ff0000', 'stroke_width': 12,
            'stroke_linecap': 'round', 'stroke_linejoin': 'round'}}]}

    def write(self, xml=None):
        with ZipFile(self.pptx, 'w') as z: z.writestr('ppt/slides/slide1.xml', xml or self.xml)

    def test_actual_native_cubic_alpha_padding_and_delivery_no_mutation(self):
        self.write(); before = self.pptx.read_bytes()
        d = preview.prepare_stroke_preview(self.pptx, self.manifest); self.assertEqual(d['unsupported'], [])
        self.assertEqual(self.pptx.read_bytes(), before)
        o = d['objects'][0]; svg = E.fromstring(o['svg']); p = list(svg)[0]
        self.assertEqual(p.attrib['d'], 'M0 80 C20 0 80 0 100 80')
        self.assertEqual(p.attrib['stroke-opacity'], '0.5'); self.assertEqual(p.attrib['stroke-linecap'], 'round')
        self.assertEqual(o['position'], {'left': 33, 'top': 33, 'width': 114, 'height': 94})
        self.assertFalse(d['source_pixel_equivalence']); self.assertEqual(d, preview.prepare_stroke_preview(self.pptx, self.manifest))

    def test_actual_square_bevel_and_miter_limit_not_an_importer_default(self):
        m = copy.deepcopy(self.manifest)
        m['objects'][0]['style'].update(stroke_linecap='square', stroke_linejoin='miter', stroke_miterlimit=2.5)
        self.write(self.xml.replace('cap="rnd"', 'cap="sq"').replace('<a:round/>', '<a:miter lim="250000"/>'))
        p = E.fromstring(preview.prepare_stroke_preview(self.pptx, m)['objects'][0]['svg'])[0]
        self.assertEqual((p.get('stroke-linecap'), p.get('stroke-linejoin'), p.get('stroke-miterlimit')), ('square', 'miter', '2.5'))
        m['objects'][0]['style']['stroke_linejoin'] = 'bevel'; del m['objects'][0]['style']['stroke_miterlimit']
        self.write(self.xml.replace('cap="rnd"', 'cap="sq"').replace('<a:round/>', '<a:bevel/>'))
        self.assertEqual(E.fromstring(preview.prepare_stroke_preview(self.pptx, m)['objects'][0]['svg'])[0].get('stroke-linejoin'), 'bevel')

    def test_changed_native_cap_or_miter_is_rejected_even_if_structurally_valid(self):
        self.write(self.xml.replace('cap="rnd"', 'cap="flat"'))
        with self.assertRaisesRegex(ValueError, 'Native cap disagrees'): preview.prepare_stroke_preview(self.pptx, self.manifest)
        m = copy.deepcopy(self.manifest); m['objects'][0]['style'].update(stroke_linejoin='miter', stroke_miterlimit=4)
        self.write(self.xml.replace('<a:round/>', '<a:miter lim="250000"/>'))
        with self.assertRaisesRegex(ValueError, 'Native miter disagrees'): preview.prepare_stroke_preview(self.pptx, m)

    def test_filled_rotated_dashed_effectful_and_arrow_end_paths_stay_explicitly_unsupported(self):
        for xml in [self.xml.replace('<a:noFill/>', '<a:solidFill/>'), self.xml.replace('<a:xfrm>', '<a:xfrm rot="60000">'),
                    self.xml.replace('val="solid"', 'val="dash"'), self.xml.replace('<a:noFill/>', '<a:noFill/><a:effectLst/>'),
                    self.xml.replace('<a:round/>', '<a:round/><a:headEnd type="triangle"/>')]:
            with self.subTest(xml=xml):
                self.write(xml); d = preview.prepare_stroke_preview(self.pptx, self.manifest)
                self.assertEqual(d['objects'], []); self.assertEqual(len(d['unsupported']), 1)

    def test_undeclared_paths_are_not_selected_and_duplicate_ids_are_rejected(self):
        self.write(); m = copy.deepcopy(self.manifest)
        del m['objects'][0]['style']['stroke_linecap']; del m['objects'][0]['style']['stroke_linejoin']
        self.assertEqual(preview.prepare_stroke_preview(self.pptx, m)['objects'], [])
        m['objects'] *= 2
        with self.assertRaisesRegex(ValueError, 'identities'): preview.prepare_stroke_preview(self.pptx, m)

    def test_native_and_svg_budgets_and_xml_entity_rejection(self):
        for xml in [self.xml.replace('x="0"', 'x="2147483648"'), self.xml.replace('cx="952500"', 'cx="2147483647"')]:
            self.write(xml); self.assertTrue(preview.prepare_stroke_preview(self.pptx, self.manifest)['unsupported'])
        self.write('<!DOCTYPE p:sld [<!ENTITY e "bad">]>' + self.xml)
        with self.assertRaisesRegex(ValueError, 'DTD/entity'): preview.prepare_stroke_preview(self.pptx, self.manifest)
        self.write()
        with ZipFile(self.pptx, 'a') as z: z.writestr('ppt/slides/slide1.xml', self.xml)
        with self.assertRaisesRegex(ValueError, 'duplicate member'): preview.prepare_stroke_preview(self.pptx, self.manifest)

    def test_shape_hash_is_independent_of_process_namespace_prefix_registration(self):
        self.write(); first = preview.prepare_stroke_preview(self.pptx, self.manifest)
        E.register_namespace('a', preview.NS['a']); E.register_namespace('p', preview.NS['p'])
        self.assertEqual(first, preview.prepare_stroke_preview(self.pptx, self.manifest))
