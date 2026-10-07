import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from PIL import Image
from figure_rebuild.linear_gradient import validate_linear_gradient, svg_linear_gradient, gradient_axis, native_path_frame, verify_native_gradient, apply_gradient_angle_precision
from figure_rebuild.export_svg import export
from figure_rebuild.validate import digest, validate


def style():
    return {'fill_gradient': {'type': 'linear', 'angle': 90,
                             'stops': [{'offset': 0, 'color': '#123456'},
                                       {'offset': 1, 'color': '#FEdcBA'}]}}


class LinearGradientTests(unittest.TestCase):
    def test_axis_directions_and_quantized_stops(self):
        for angle, expected in ((0, (0, 50, 100, 50)),
                                (90, (50, 0, 50, 100)),
                                (180, (100, 50, 0, 50)),
                                (270, (50, 100, 50, 0))):
            s = style(); s['fill_gradient']['angle'] = angle
            s['fill_gradient']['stops'].insert(1, {'offset': .123456, 'color': '#112233', 'opacity': .4})
            defs = ET.Element('defs')
            self.assertEqual(svg_linear_gradient(defs, 'sample', s, {'x': 0, 'y': 0, 'width': 100, 'height': 100}), 'url(#sample)')
            self.assertEqual(tuple(float(defs[0].get(k)) for k in ('x1', 'y1', 'x2', 'y2')), expected)
            self.assertEqual(defs[0][1].get('offset'), '0.12346')
            self.assertEqual(defs[0][1].get('stop-opacity'), '0.4')

    def test_unsupported_modes_and_ambiguous_fill_rejected(self):
        for key, value in (('type', 'radial'), ('angle', -1), ('angle', 360), ('angle', True), ('angle', []),
                           ('stops', []), ('stops', None)):
            s = style(); s['fill_gradient'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_linear_gradient(s)
        for kind in ('text', 'image', 'connector'):
            with self.assertRaises(ValueError): validate_linear_gradient(style(), kind)
        s = style(); s['fill'] = '#FFFFFF'
        with self.assertRaises(ValueError): validate_linear_gradient(s)

    def test_stop_loss_and_precision_collisions_rejected(self):
        for offset in (-1, 1.1, .9999999, float('nan'), True):
            s = style(); s['fill_gradient']['stops'].insert(1, {'offset': offset, 'color': '#112233'})
            with self.subTest(offset=offset), self.assertRaises(ValueError): validate_linear_gradient(s)
        for key, value in (('color', 'red'), ('color', '#123'), ('opacity', -1), ('opacity', []),
                           ('opacity', float('inf')), ('offset', .01)):
            s = style(); s['fill_gradient']['stops'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): validate_linear_gradient(s)

    def test_absent_gradient_leaves_existing_paint_unchanged(self):
        self.assertIsNone(validate_linear_gradient({'fill': '#123456'}))

    def test_diagonal_axis_keeps_physical_angle_on_nonsquare_box(self):
        import math
        box = {'x': 10, 'y': 20, 'width': 200, 'height': 80}
        x1, y1, x2, y2 = gradient_axis(box, 30)
        self.assertAlmostEqual((y2-y1)/(x2-x1), math.tan(math.pi/6))
        length2 = (x2-x1)**2 + (y2-y1)**2
        at = lambda x, y: ((x-x1)*(x2-x1)+(y-y1)*(y2-y1))/length2
        self.assertAlmostEqual(at(10, 20), 0)
        self.assertAlmostEqual(at(210, 100), 1)
        s = style(); s['fill_gradient']['angle'] = 30
        defs = ET.Element('defs'); svg_linear_gradient(defs, 'oblique', s, box)
        self.assertEqual(defs[0].get('gradientUnits'), 'userSpaceOnUse')
        self.assertEqual(float(defs[0].get('x1')), x1)
        with self.assertRaises(ValueError): svg_linear_gradient(ET.Element('defs'), 'oblique', s)

    def test_curved_fill_uses_native_control_frame_not_svg_extrema(self):
        commands = [{'moveTo': {'x': 20, 'y': 180}},
                    {'cubicTo': {'x1': 20, 'y1': 20, 'x2': 180, 'y2': 20, 'x': 180, 'y': 180}},
                    {'close': {}}]
        frame = native_path_frame(commands)
        self.assertEqual(frame, {'x': 20, 'y': 20, 'width': 160, 'height': 160})
        self.assertEqual(gradient_axis(frame, 90), (100, 20, 100, 180))
        # The actual curve starts at y=60 at its apex, not y=20. A y=100
        # sample must nevertheless be halfway along the *native frame* axis.
        defs = ET.Element('defs'); svg_linear_gradient(defs, 'curve', style(), frame)
        self.assertEqual(defs[0].get('gradientUnits'), 'userSpaceOnUse')
        self.assertEqual(float(defs[0].get('y1')), 20)

    def test_exporter_fill_mutations_fail_closed(self):
        import copy
        a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        p = 'http://schemas.openxmlformats.org/presentationml/2006/main'
        shape = ET.fromstring(f'''<p:sp xmlns:p="{p}" xmlns:a="{a}"><p:spPr><a:custGeom/>
            <a:gradFill><a:gsLst><a:gs pos="0"><a:srgbClr val="123456"/></a:gs>
            <a:gs pos="100000"><a:srgbClr val="FEDCBA"/></a:gs></a:gsLst>
            <a:lin ang="5400000"/></a:gradFill></p:spPr></p:sp>''')
        obj = {'id': 'probe', 'kind': 'path', 'style': style()}
        self.assertTrue(verify_native_gradient(shape, obj)['native_fill_verified'])
        mutations = [(f'.//{{{a}}}lin', 'ang', '0'), (f'.//{{{a}}}lin', 'scaled', '1'),
                     (f'.//{{{a}}}gs', 'pos', '1'), (f'.//{{{a}}}srgbClr', 'val', 'FFFFFF')]
        for path, key, value in mutations:
            candidate = copy.deepcopy(shape); candidate.find(path).set(key, value)
            with self.subTest(key=key), self.assertRaises(ValueError): verify_native_gradient(candidate, obj)
        obj['style']['opacity'] = .5
        with self.assertRaises(ValueError): verify_native_gradient(shape, obj)
        obj['style'].pop('opacity')
        candidate = copy.deepcopy(shape); candidate.find(f'.//{{{a}}}gradFill').tag = f'{{{a}}}solidFill'
        with self.assertRaises(ValueError): verify_native_gradient(candidate, obj)

    def test_native_angle_float_truncation_is_corrected_with_receipt(self):
        a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        p = 'http://schemas.openxmlformats.org/presentationml/2006/main'
        element = ET.fromstring(f'<p:sp xmlns:p="{p}" xmlns:a="{a}"><p:spPr><a:gradFill><a:lin ang="9000108"/></a:gradFill></p:spPr></p:sp>')
        obj = {'id': 'angle', 'kind': 'path', 'style': style()}
        obj['style']['fill_gradient']['angle'] = 150.001812
        receipt = apply_gradient_angle_precision(element, obj)
        self.assertEqual(receipt['requested_angle_units'], 9000109)
        self.assertEqual(receipt['correction_units'], 1)
        self.assertEqual(element.find(f'.//{{{a}}}lin').get('ang'), '9000109')
        self.assertIsNone(apply_gradient_angle_precision(element, obj))
        element.find(f'.//{{{a}}}lin').set('ang', '9000000')
        self.assertIsNone(apply_gradient_angle_precision(element, obj))
        self.assertEqual(element.find(f'.//{{{a}}}lin').get('ang'), '9000000')

    def test_full_validation_and_svg_preserve_path_mask_and_unique_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (100, 100), 'white').save(root/'source.png')
            commands = [{'moveTo': {'x': 10, 'y': 10}}, {'lineTo': {'x': 90, 'y': 10}},
                        {'lineTo': {'x': 50, 'y': 90}}, {'close': {}}]
            obj = {'id': 'fr-gradient-0', 'kind': 'path', 'commands': commands,
                   'style': {**style(), 'opacity': .7}}
            manifest = {'schema_version': 1, 'id': 'gradient-probe', 'revision': 1,
                        'source': {'kind': 'generated_diagram', 'path': 'source.png',
                                   'sha256': digest(root/'source.png'), 'width': 100, 'height': 100},
                        'canvas': {'width': 100, 'height': 100},
                        'recognition': {'provider': 'calling_host', 'status': 'reviewed'},
                        'objects': [obj]}
            report = validate(manifest, root)
            self.assertEqual(report['status'], 'PASS', report)
            self.assertFalse(any('no visible paint' in w for w in report['warnings']))
            path = root/'manifest.json'; path.write_text(json.dumps(manifest))
            export(path, root/'master.svg', None, family='unused-path-only')
            svg = ET.parse(root/'master.svg').getroot()
            ns = {'s': 'http://www.w3.org/2000/svg'}
            gradient, native_path = svg.find('.//s:linearGradient', ns), svg.find('s:path', ns)
            self.assertNotEqual(gradient.get('id'), obj['id'])
            self.assertEqual(native_path.get('fill'), 'url(#'+gradient.get('id')+')')
            self.assertEqual(native_path.get('opacity'), '0.7')
            self.assertEqual(native_path.get('d'), 'M 10 10 L 90 10 L 50 90 Z')
            self.assertFalse(svg.findall('.//s:image', ns))
            self.assertEqual(obj['commands'], commands)


if __name__ == '__main__':
    unittest.main()
