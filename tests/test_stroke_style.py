"""Stroke geometry is opt-in and must survive native export without paint drift."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from figure_rebuild.stroke_style import A, P, NS, apply_stroke_style, validate_stroke_style, svg_stroke_attributes, verify_hairline_xml, verify_native_hairlines
from figure_rebuild.export_svg import export


class StrokeStyleTests(unittest.TestCase):
    def setUp(self):
        self.shape = ET.fromstring(f'''<p:sp xmlns:p="{P}" xmlns:a="{A}">
          <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm>
          <a:custGeom><a:pathLst><a:path w="100" h="100"><a:moveTo><a:pt x="0" y="0"/></a:moveTo><a:lnTo><a:pt x="100" y="100"/></a:lnTo></a:path></a:pathLst></a:custGeom>
          <a:ln w="95250" cap="flat"><a:solidFill><a:srgbClr val="123456"><a:alpha val="50000"/></a:srgbClr></a:solidFill><a:prstDash val="solid"/><a:round/><a:headEnd type="none"/><a:tailEnd type="none"/><a:extLst/></a:ln>
          </p:spPr></p:sp>''')
        self.obj = {'id': 'line', 'kind': 'path', 'style': {'stroke': '#123456', 'stroke_width': 10}}

    def test_unspecified_stroke_is_an_exact_noop(self):
        original = ET.tostring(self.shape)
        self.assertIsNone(apply_stroke_style(self.shape, self.obj))
        self.assertEqual(ET.tostring(self.shape), original)
        self.assertEqual(svg_stroke_attributes(self.obj['style']), {})

    def test_explicit_hairline_restores_paint_zero_width_and_native_stroke_state(self):
        line = self.shape.find('p:spPr/a:ln', NS)
        line.remove(line.find('a:solidFill', NS)); line.insert(0, ET.Element(f'{{{A}}}noFill'))
        path = self.shape.find('p:spPr/a:custGeom/a:pathLst/a:path', NS); path.set('stroke', '0')
        points = [ET.tostring(c) for c in path]
        style = {'fill': 'none', 'stroke': '#abcdef', 'stroke_width': 0, 'stroke_hairline': True,
                 'opacity': .5, 'stroke_linecap': 'butt', 'stroke_linejoin': 'miter'}
        report = apply_stroke_style(self.shape, {**self.obj, 'style': style})
        self.assertEqual(line.get('w'), '0'); self.assertEqual(path.get('stroke'), '1')
        self.assertEqual(line.find('a:solidFill/a:srgbClr', NS).get('val'), 'ABCDEF')
        self.assertEqual(line.find('a:solidFill/a:srgbClr/a:alpha', NS).get('val'), '50000')
        self.assertEqual(points, [ET.tostring(c) for c in path])
        self.assertTrue(report['native_zero_width_written']); self.assertEqual(report['pdf_linewidth_and_pixel_verification'], 'required')
        self.assertEqual(svg_stroke_attributes(style)['data-figure-rebuild-device-hairline'], 'native-zero-width')

    def test_invalid_hairline_never_mutates_native_shape(self):
        style = {'stroke': '#123456', 'stroke_width': 0, 'stroke_hairline': True}
        bad = [dict(style, stroke_hairline=v) for v in (False, 1, 'true', None)]
        bad += [dict(style, stroke_width=v) for v in (True, '0', None, 1)]
        bad += [dict(style, stroke=v) for v in ('none', '#GGGGGG')]
        bad += [dict(style, opacity=v) for v in (True, 0, .000001, float('nan'), 10**1000)]
        bad += [dict(style, fill='magenta')]
        original = ET.tostring(self.shape)
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                apply_stroke_style(self.shape, {**self.obj, 'style': candidate})
            self.assertEqual(ET.tostring(self.shape), original)

    def test_actual_hairline_width_color_alpha_and_stroke_are_verified_independently(self):
        style = {'stroke':'#123456','stroke_width':0,'stroke_hairline':True,'opacity':.5}
        obj={**self.obj,'style':style};props=self.shape.find('p:spPr',NS)
        props.insert(1,ET.Element(f'{{{A}}}noFill'))
        apply_stroke_style(self.shape,obj)
        self.assertEqual(verify_hairline_xml(self.shape,obj)['native_line_width_emu'],0)
        self.assertFalse(verify_hairline_xml(self.shape,obj)['pdf_linewidth_verified'])
        for failure in ('width','color','alpha','stroke','effect'):
            changed=copy.deepcopy(self.shape);line=changed.find('p:spPr/a:ln',NS)
            if failure=='width':line.set('w','1')
            elif failure=='color':line.find('a:solidFill/a:srgbClr',NS).set('val','654321')
            elif failure=='alpha':line.find('a:solidFill/a:srgbClr/a:alpha',NS).set('val','100000')
            elif failure=='stroke':changed.find('p:spPr/a:custGeom/a:pathLst/a:path',NS).set('stroke','0')
            else:ET.SubElement(changed.find('p:spPr',NS),f'{{{A}}}effectLst')
            with self.subTest(failure=failure),self.assertRaises(ValueError):verify_hairline_xml(changed,obj)

    def test_hairline_on_filled_path_retains_fill_bytes_and_control_coordinates(self):
        props=self.shape.find('p:spPr',NS);fill=ET.Element(f'{{{A}}}solidFill');ET.SubElement(fill,f'{{{A}}}srgbClr',{'val':'92E285'});props.insert(1,fill)
        before=ET.tostring(fill);geometry=ET.tostring(props.find('a:custGeom',NS))
        obj={**self.obj,'style':{'fill':'#92E285','stroke':'#000000','stroke_width':0,'stroke_hairline':True}}
        apply_stroke_style(self.shape,obj)
        self.assertEqual(ET.tostring(fill),before)
        path=props.find('a:custGeom/a:pathLst/a:path',NS);path.attrib.pop('stroke')
        self.assertEqual(ET.tostring(props.find('a:custGeom',NS)),geometry)
        path.set('stroke','1');self.assertEqual(verify_hairline_xml(self.shape,obj)['native_line_width_emu'],0)

    def test_actual_packaged_hairline_is_read_back_and_changed_width_is_refused(self):
        if __package__:
            from .test_package import fixture,slide,write_archive
        else:
            from test_package import fixture,slide,write_archive
        props=self.shape.find('p:spPr',NS);props.insert(1,ET.Element(f'{{{A}}}noFill'))
        nv=ET.Element(f'{{{P}}}nvSpPr');ET.SubElement(nv,f'{{{P}}}cNvPr',{'id':'2','name':'line'});self.shape.insert(0,nv)
        obj={**self.obj,'style':{'stroke':'#123456','stroke_width':0,'stroke_hairline':True}};apply_stroke_style(self.shape,obj)
        with tempfile.TemporaryDirectory() as directory:
            ppt=Path(directory)/'final.pptx';files=fixture([(1001,'ppt/slides/odd.xml',slide([self.shape]))]);write_archive(ppt,files)
            before=ppt.read_bytes();records=verify_native_hairlines(ppt,{'objects':[obj]})
            self.assertEqual(records[0]['native_line_width_emu'],0);self.assertEqual(ppt.read_bytes(),before)
            self.shape.find('p:spPr/a:ln',NS).set('w','1')
            files=fixture([(1001,'ppt/slides/odd.xml',slide([self.shape]))]);write_archive(ppt,files)
            with self.assertRaisesRegex(ValueError,'width'):verify_native_hairlines(ppt,{'objects':[obj]})

    def test_ordinary_zero_width_does_not_acquire_hairline_paint(self):
        original=ET.tostring(self.shape);obj={**self.obj,'style':{'stroke':'#123456','stroke_width':0}}
        self.assertIsNone(apply_stroke_style(self.shape,obj));self.assertIsNone(verify_hairline_xml(self.shape,obj))
        self.assertEqual(ET.tostring(self.shape),original)

    def test_native_cap_mapping_preserves_all_other_content(self):
        for cap, native in [('butt', 'flat'), ('round', 'rnd'), ('square', 'sq')]:
            shape = copy.deepcopy(self.shape)
            apply_stroke_style(shape, {**self.obj, 'style': {'stroke_linecap': cap}})
            shape.find('p:spPr/a:ln', NS).set('cap', 'flat')
            self.assertEqual(ET.tostring(shape), ET.tostring(self.shape))
            self.assertEqual(apply_stroke_style(shape, {**self.obj, 'style': {'stroke_linecap': cap}})['native_cap'], native)

    def test_each_join_replaces_previous_join_in_schema_order(self):
        original_path = ET.tostring(self.shape.find('p:spPr/a:custGeom', NS))
        for join in ['bevel', 'miter', 'round']:
            report = apply_stroke_style(self.shape, {**self.obj, 'style': {'stroke_linejoin': join}})
            line = self.shape.find('p:spPr/a:ln', NS)
            self.assertEqual([e.tag.rsplit('}', 1)[-1] for e in line], ['solidFill', 'prstDash', join, 'headEnd', 'tailEnd', 'extLst'])
            self.assertEqual(line.get('w'), '95250')
            self.assertEqual(line.find('a:solidFill/a:srgbClr/a:alpha', NS).get('val'), '50000')
            self.assertEqual(ET.tostring(self.shape.find('p:spPr/a:custGeom', NS)), original_path)
            if join == 'miter':
                self.assertEqual(line.find('a:miter', NS).get('lim'), '400000')
                self.assertEqual(report['native_miter_limit_units'], 400000)

    def test_explicit_miter_limit_uses_positive_percentage_half_up(self):
        style = {'stroke_linejoin': 'miter', 'stroke_miterlimit': 2.123445}
        report = apply_stroke_style(self.shape, {**self.obj, 'style': style})
        self.assertEqual(report['native_miter_limit_units'], 212345)
        self.assertEqual(svg_stroke_attributes(style), {'stroke-linejoin': 'miter', 'stroke-miterlimit': '2.123445'})

    def test_invalid_values_and_ineffective_limits_fail_before_mutation(self):
        cases = [{'stroke_linecap': x} for x in ['flat', '', [], None, True]]
        cases += [{'stroke_linejoin': x} for x in ['arcs', '', {}, None, True]]
        cases += [{'stroke_linejoin': 'miter', 'stroke_miterlimit': x} for x in [0, -1, float('nan'), float('inf'), True, None, 10**1000, 21474.83648, .000001]]
        cases += [{'stroke_miterlimit': 4}, {'stroke_linejoin': 'round', 'stroke_miterlimit': 4}]
        original = ET.tostring(self.shape)
        for style in cases:
            with self.subTest(style=style), self.assertRaises(ValueError):
                apply_stroke_style(self.shape, {**self.obj, 'style': style})
            self.assertEqual(ET.tostring(self.shape), original)

    def test_nonpath_or_missing_native_line_cannot_silently_discard_request(self):
        with self.assertRaisesRegex(ValueError, 'require a path'):
            validate_stroke_style({'stroke_linecap': 'round'}, 'image')
        self.shape.find('p:spPr', NS).remove(self.shape.find('p:spPr/a:ln', NS))
        with self.assertRaisesRegex(ValueError, 'authored native line'):
            apply_stroke_style(self.shape, {**self.obj, 'style': {'stroke_linecap': 'round'}})

    def test_svg_exports_exact_declared_stroke_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            m = {'canvas': {'width': 100, 'height': 100}, 'objects': [{**self.obj,
                'commands': [{'moveTo': {'x': 10, 'y': 20}}, {'lineTo': {'x': 50, 'y': 80}}],
                'style': {**self.obj['style'], 'stroke_linecap': 'square', 'stroke_linejoin': 'miter', 'stroke_miterlimit': 8}}]}
            (root/'manifest.json').write_text(json.dumps(m))
            export(root/'manifest.json', root/'output.svg', 'unused', family='Arial')
            attrs = ET.parse(root/'output.svg').getroot().find('{http://www.w3.org/2000/svg}path').attrib
            self.assertEqual({k: attrs[k] for k in ('stroke-linecap','stroke-linejoin','stroke-miterlimit')},
                             {'stroke-linecap': 'square', 'stroke-linejoin': 'miter', 'stroke-miterlimit': '8'})

    def test_manifest_rejects_unsupported_style_before_build_allocates_output(self):
        from argparse import Namespace
        from unittest.mock import patch
        from PIL import Image
        from figure_rebuild import cli
        from figure_rebuild.validate import validate
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (100, 100), 'white').save(root/'source.png')
            path = {**self.obj, 'commands': [{'moveTo': {'x': 20, 'y': 30}}, {'lineTo': {'x': 70, 'y': 60}}]}
            m = {'schema_version': 1, 'id': 'stroke-test', 'revision': 1,
                 'canvas': {'width': 100, 'height': 100},
                 'source': {'kind': 'generated_diagram', 'path': 'source.png', 'sha256': cli.digest(root/'source.png'), 'width': 100, 'height': 100},
                 'recognition': {'provider': 'calling_host', 'status': 'reviewed'}, 'objects': [path]}
            path['style'] = {'stroke': '#000000', 'stroke_width': 10, 'stroke_linecap': 'square', 'stroke_linejoin': 'miter', 'stroke_miterlimit': 8}
            self.assertEqual(validate(m, root)['status'], 'PASS')
            args = Namespace(manifest=str(root/'manifest.json'), output=None, base=None, base_sha256=None,
                             slide_id=None, placement=None, replace_id=None, marker_already_started=True)
            for style in [{'stroke_linecap': 'flat'}, {'stroke_linejoin': 'arcs'}, {'stroke_linejoin': 'round', 'stroke_miterlimit': 4}]:
                path['style'] = {'stroke': '#000000', 'stroke_width': 10, **style}
                report = validate(m, root)
                self.assertEqual(report['status'], 'FAIL')
                self.assertTrue(any('stroke_' in message for message in report['errors']))
                m['recognition'].update(reviewed_revision=1, reviewed_digest=cli.content_digest(m))
                (root/'manifest.json').write_text(json.dumps(m))
                with patch.object(cli, 'runtime') as runtime, self.assertRaises(ValueError):
                    cli.build(args)
                runtime.assert_not_called()
                self.assertFalse((root/'build').exists())

    def test_receipt_never_confuses_native_xml_with_renderer_verification(self):
        report = apply_stroke_style(self.shape, {**self.obj, 'style': {'stroke_linecap': 'round'}})
        self.assertTrue(report['native_geometry_written'])
        self.assertTrue(report['visual_verification_required'])
        self.assertEqual(report['preview_renderer_support'], 'not_verified_or_unsupported')


if __name__ == '__main__':
    unittest.main()
