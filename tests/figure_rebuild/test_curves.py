"""Exact DrawingML cubic preservation; holes, transforms, SVG and safety guards."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree as ET
import zipfile
from PIL import Image, ImageFont

TOOLS = Path(__file__).resolve().parents[2] / 'tools/figure_rebuild'

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

v = module('curve_validate', 'validate.py')
postprocess = module('curve_postprocess', 'postprocess.py')
package = module('curve_package', 'package.py')
svg = module('curve_svg', 'export_svg.py')
NS = postprocess.NS
P, A = NS['p'], NS['a']

def ring():
    # Opposite winding of the inner subpath is an actual editable hole.
    k = .5522847498307936
    result = []
    for rx, ry, direction in [(40, 30, 1), (20, 15, -1)]:
        result.append({'moveTo': {'x': 50 + rx, 'y': 50}})
        s = direction
        for x1, y1, x2, y2, x, y in [
            (50+rx, 50+s*k*ry, 50+k*rx, 50+s*ry, 50, 50+s*ry),
            (50-k*rx, 50+s*ry, 50-rx, 50+s*k*ry, 50-rx, 50),
            (50-rx, 50-s*k*ry, 50-k*rx, 50-s*ry, 50, 50-s*ry),
            (50+k*rx, 50-s*ry, 50+rx, 50-s*k*ry, 50+rx, 50)]:
            result.append({'cubicTo': dict(x1=x1, y1=y1, x2=x2, y2=y2, x=x, y=y)})
        result.append({'close': {}})
    return result

class CubicPaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        Image.new('RGB', (200, 100), 'white').save(self.root/'source.png')
        self.obj = {'id': 'ring-with-hole', 'kind': 'path', 'commands': ring(),
                    'style': {'fill': '#AA8833', 'stroke': '#222222', 'stroke_width': 1.5}}
        self.manifest = {'schema_version': 1, 'id': 'curve-test', 'revision': 1,
            'canvas': {'width': 200, 'height': 100},
            'source': {'path': 'source.png', 'sha256': v.digest(self.root/'source.png'), 'kind': 'user_original', 'width': 200, 'height': 100},
            'recognition': {'provider': 'calling_host', 'status': 'reviewed'}, 'objects': [self.obj]}
        self.manifest_path = self.root/'manifest.json'
        self.mapping = self.root/'object-map.json'
        self.source = self.root/'author-intermediate.pptx'
        self.output = self.root/'native-curves.pptx'
        self.receipt = self.root/'editability.json'
        self.box = dict(x=10, y=20, width=80, height=60)
        self.mapping.write_text(json.dumps({'placement': [35, 15, 100, 50],
            'objects': [{'id': self.obj['id'], 'kind': 'path', 'box': self.box}]}))
        self.page = ET.Element(f'{{{P}}}sld')
        tree = ET.SubElement(ET.SubElement(self.page, f'{{{P}}}cSld'), f'{{{P}}}spTree')
        shape = ET.SubElement(tree, f'{{{P}}}sp')
        nv = ET.SubElement(shape, f'{{{P}}}nvSpPr')
        ET.SubElement(nv, f'{{{P}}}cNvPr', id='2', name=self.obj['id'])
        ET.SubElement(nv, f'{{{P}}}cNvSpPr'); ET.SubElement(nv, f'{{{P}}}nvPr')
        sppr = ET.SubElement(shape, f'{{{P}}}spPr')
        self.transform = ET.SubElement(sppr, f'{{{A}}}xfrm')
        ET.SubElement(self.transform, f'{{{A}}}off', x=str(40*9525), y=str(25*9525))
        ET.SubElement(self.transform, f'{{{A}}}ext', cx=str(40*9525), cy=str(30*9525))
        geom = ET.SubElement(sppr, f'{{{A}}}custGeom')
        paths = ET.SubElement(geom, f'{{{A}}}pathLst')
        path = ET.SubElement(paths, f'{{{A}}}path', w='80', h='60', stroke='1')
        ET.SubElement(ET.SubElement(path, f'{{{A}}}moveTo'), f'{{{A}}}pt', x='80', y='30')
        ET.SubElement(ET.SubElement(path, f'{{{A}}}lnTo'), f'{{{A}}}pt', x='0', y='30')

    def process(self, object_map=True):
        self.manifest_path.write_text(json.dumps(self.manifest))
        with zipfile.ZipFile(self.source, 'w') as archive:
            archive.writestr('ppt/slides/slide1.xml', ET.tostring(self.page))
        with mock.patch.dict(sys.modules, {'package': package}):
            return postprocess.process(self.source, self.output, self.manifest_path, self.receipt,
                object_map=self.mapping if object_map else None)

    def test_exact_cubics_and_opposite_winding_subpaths_survive_native_export(self):
        before = ET.tostring(self.transform)
        report = self.process()
        self.assertEqual(report['native_cubic_segment_count'], 8)
        self.assertEqual(report['native_cubic_paths'][0]['subpath_count'], 2)
        self.assertEqual(report['native_cubic_paths'][0]['path_dimensions'], [80*9525, 60*9525])
        self.assertTrue(report['fully_native'])
        with zipfile.ZipFile(self.output) as archive:
            page = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        self.assertEqual(ET.tostring(page.find('.//a:xfrm', NS)), before)
        path = page.find('.//a:path', NS)
        self.assertEqual(len(path.findall('a:cubicBezTo', NS)), 8)
        self.assertEqual(len(path.findall('a:moveTo', NS)), 2)
        self.assertEqual(len(path.findall('a:close', NS)), 2)
        self.assertEqual(path.findall('a:moveTo/a:pt', NS)[0].attrib, {'x': str(80*9525), 'y': str(30*9525)})
        first = path.find('a:cubicBezTo', NS)
        self.assertEqual(len(first), 3)
        self.assertEqual(first[2].attrib, {'x': str(40*9525), 'y': str(60*9525)})
        self.assertEqual(page.find('.//p:cNvPr', NS).get('name'), self.obj['id'])

    def test_cubics_without_mapping_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, 'requires mapped path frame'): self.process(False)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_changed_frame_orientation_or_controls_are_rejected_before_output(self):
        for problem in ('frame', 'orientation', 'control'):
            with self.subTest(problem=problem):
                self.transform.find('a:off', NS).set('x', str(40*9525 + (3 if problem == 'frame' else 0)))
                self.transform.set('flipH', 'true' if problem == 'orientation' else 'false')
                self.obj['commands'][1]['cubicTo']['x1'] = 91 if problem == 'control' else 90
                with self.assertRaises(ValueError): self.process()
                self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_svg_preserves_true_curve_commands_and_compound_subpaths(self):
        self.manifest_path.write_text(json.dumps(self.manifest))
        output = self.root/'master.svg'
        svg.export(self.manifest_path, output, 'unused', family='Configured Font')
        root = ET.parse(output).getroot()
        path = root.find('{'+svg.SVG+'}path')
        self.assertEqual(path.get('d').count('C '), 8)
        self.assertEqual(path.get('d').count('M '), 2)
        self.assertEqual(path.get('d').count('Z'), 2)

    def test_mixed_native_line_and_cubic_commands_use_valid_drawingml_tags(self):
        self.obj['commands'].insert(1, {'lineTo': {'x': 89, 'y': 50}})
        self.process()
        with zipfile.ZipFile(self.output) as archive:
            page = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        path = page.find('.//a:path', NS)
        self.assertEqual(len(path.findall('a:lnTo', NS)), 1)
        self.assertEqual(len(path.findall('a:lineTo', NS)), 0)
        self.assertEqual(len(path.findall('a:cubicBezTo', NS)), 8)

    def test_validator_accepts_cubics_and_rejects_bad_controls(self):
        self.assertEqual(v.validate(self.manifest, self.root)['status'], 'PASS')
        for bad_value in (float('nan'), -1, 201, True):
            bad = copy.deepcopy(self.manifest)
            bad['objects'][0]['commands'][1]['cubicTo']['x1'] = bad_value
            self.assertEqual(v.validate(bad, self.root)['status'], 'FAIL')
        for commands in ([{'cubicTo': self.obj['commands'][1]['cubicTo']}, {'close': {}}],
                         [{'moveTo': {'x': 2, 'y': 3}}, {'cubicTo': {'x': 4, 'y': 5}}]):
            bad = copy.deepcopy(self.manifest); bad['objects'][0]['commands'] = commands
            self.assertEqual(v.validate(bad, self.root)['status'], 'FAIL')

    def test_per_object_svg_font_is_configured_and_unknown_family_fails(self):
        self.manifest['objects'].append({'id': 'label', 'kind': 'text', 'text': 'ConvGRU', 'font_size': 18,
            'font_family': 'Reference Serif', 'box': {'x': 20, 'y': 35, 'width': 130, 'height': 30}})
        self.manifest_path.write_text(json.dumps(self.manifest))
        font = ImageFont.load_default()
        audit = [{'family': 'Default Sans', 'role': 'regular', 'renderer': '/prepared/default.ttf'},
                 {'family': 'Reference Serif', 'role': 'regular', 'renderer': '/prepared/serif.ttf'}]
        with mock.patch.object(svg.ImageFont, 'truetype', return_value=font) as load:
            svg.export(self.manifest_path, self.root/'text.svg', 'unused', family='Default Sans', font_audit=audit)
            load.assert_called_once_with('/prepared/serif.ttf', 18)
        node = ET.parse(self.root/'text.svg').getroot().find('{'+svg.SVG+'}text')
        self.assertEqual(node.get('font-family'), 'Reference Serif')
        with self.assertRaisesRegex(ValueError, 'No configured SVG font face'):
            svg.export(self.manifest_path, self.root/'unknown.svg', 'unused', family='Default Sans')

    def test_font_family_validation_rejects_css_injection_and_control_characters(self):
        text = {'id': 'label', 'kind': 'text', 'text': 'ConvGRU', 'font_size': 18,
                'box': {'x': 20, 'y': 35, 'width': 130, 'height': 30}}
        self.manifest['objects'].append(text)
        for family in ('', ' ', None, 4, 'bad\nfont', 'bad"font', "bad'font", 'bad\\font'):
            text['font_family'] = family
            self.assertEqual(v.validate(self.manifest, self.root)['status'], 'FAIL')
        text['font_family'] = 'Reference Serif'
        self.assertEqual(v.validate(self.manifest, self.root)['status'], 'PASS')

if __name__ == '__main__': unittest.main()
