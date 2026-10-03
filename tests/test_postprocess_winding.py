"""Native polygon fill identity, paint, topology and final integer boundaries."""
from copy import deepcopy
from fractions import Fraction as Q
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as E
from zipfile import ZipFile

from figure_rebuild import native_winding as n, postprocess

P, A = n.NS['p'], n.NS['a']


def ring(points):
    return [{'moveTo': {'x': points[0][0], 'y': points[0][1]}}] + [
        {'lineTo': {'x': x, 'y': y}} for x, y in points[1:]] + [{'close': {}}]


def rect(x0, y0, x1, y1, reverse=False):
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return ring(list(reversed(points)) if reverse else points)


def example(commands=None, opacity=.37, placement=(.125, -.25, 500, 500), off_delta=0):
    commands = commands or (rect(10, 10, 30, 20) + ring([(30, 15), (26, 5), (40, 15), (26, 25)]))
    obj = {'id': 'source-paint', 'kind': 'path', 'commands': commands,
           'style': {'fill': '#7f7f7f', 'opacity': opacity, 'stroke': 'none', 'stroke_width': 0}}
    pts = [v for c in commands for k, v in c.items() if k in ('moveTo', 'lineTo')]
    xs, ys = [v['x'] for v in pts], [v['y'] for v in pts]
    box = dict(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys))
    scale = Q(placement[2])/1000
    ideal = [(Q(placement[0])+Q(box['x'])*scale)*9525,
             (Q(placement[1])+Q(box['y'])*scale)*9525,
             Q(box['width'])*scale*9525, Q(box['height'])*scale*9525]
    frame = [round(v) for v in ideal]; frame[0] += off_delta
    shape = E.Element(f'{{{P}}}sp')
    nv = E.SubElement(shape, f'{{{P}}}nvSpPr')
    E.SubElement(nv, f'{{{P}}}cNvPr', id='2', name=obj['id'])
    props = E.SubElement(shape, f'{{{P}}}spPr')
    transform = E.SubElement(props, f'{{{A}}}xfrm')
    E.SubElement(transform, f'{{{A}}}off', x=str(frame[0]), y=str(frame[1]))
    E.SubElement(transform, f'{{{A}}}ext', cx=str(frame[2]), cy=str(frame[3]))
    paths = E.SubElement(E.SubElement(props, f'{{{A}}}custGeom'), f'{{{A}}}pathLst')
    path = E.SubElement(paths, f'{{{A}}}path', w=str(frame[2]), h=str(frame[3]))
    # A dummy original geometry lets rejection tests detect *any* mutation.
    for op, point in [('moveTo', (0, 0)), ('lnTo', (frame[2], 0)), ('lnTo', (0, frame[3]))]:
        E.SubElement(E.SubElement(path, f'{{{A}}}{op}'), f'{{{A}}}pt', x=str(point[0]), y=str(point[1]))
    E.SubElement(path, f'{{{A}}}close')
    color = E.SubElement(E.SubElement(props, f'{{{A}}}solidFill'), f'{{{A}}}srgbClr', val='7F7F7F')
    E.SubElement(color, f'{{{A}}}alpha', val=str(round(opacity*100000)))
    E.SubElement(E.SubElement(props, f'{{{A}}}ln', w='0'), f'{{{A}}}noFill')
    entry = {'kind': 'path', 'source_box': box,
             'source_to_slide': {'translate_x': placement[0], 'translate_y': placement[1],
                                 'scale_numerator': placement[2], 'scale_denominator': 1000}}
    return shape, obj, entry


class NativeWindingTests(unittest.TestCase):
    def apply(self, shape, obj, entry, **kwargs):
        return n.normalize_native_polygon_fill(shape, obj, entry, parent_identity=True, **kwargs)

    def test_union_is_one_path_with_unchanged_alpha_frame_and_source(self):
        shape, obj, entry = example()
        source = deepcopy(obj); props = shape.find('p:spPr', n.NS)
        preserved = {c.tag: E.tostring(c) for c in props if c.tag != f'{{{A}}}custGeom'}
        result = self.apply(shape, obj, entry)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(obj, source)
        self.assertEqual(preserved, {c.tag: E.tostring(c) for c in props if c.tag != f'{{{A}}}custGeom'})
        paths = props.find('a:custGeom/a:pathLst', n.NS)
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(paths.findall('a:path/a:moveTo', n.NS)), 1)
        self.assertEqual(result['paint_verification']['native_alpha_units'], 37000)
        self.assertTrue(result['native_grid']['topology_verification']['topology_unchanged'])
        self.assertLessEqual(Q(result['native_grid']['maximum_point_error_emu_exact']), Q(1, 2))
        total = Q(result['native_grid']['maximum_total_geometry_error_emu_exact'])
        self.assertLessEqual(total, Q(1, 2)+Q(1, 1024))
        self.assertIsNone(result['rgb_alpha_error_bound'])

    def test_hole_island_and_repeat_walk_keep_single_alpha_application(self):
        commands = rect(0, 0, 30, 30) + rect(5, 5, 25, 25, True) + rect(10, 10, 20, 20)
        shape, obj, entry = example(commands)
        result = self.apply(shape, obj, entry)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['normalization']['output_ring_count'], 3)
        self.assertEqual(len(shape.findall('p:spPr/a:custGeom/a:pathLst/a:path', n.NS)), 1)
        self.assertEqual(len(shape.findall('.//a:alpha', n.NS)), 1)
        shape, obj, entry = example(rect(0, 0, 20, 20)*2)
        self.assertEqual(self.apply(shape, obj, entry)['normalization']['output_ring_count'], 1)

    def test_existing_offset_roundoff_is_compensated_including_negative_local_points(self):
        shape, obj, entry = example(rect(10, 10, 30, 30), placement=(0, 0, 1000, 1000), off_delta=1)
        result = self.apply(shape, obj, entry)
        self.assertEqual(result['status'], 'applied')
        self.assertGreater(result['native_grid']['negative_local_point_count'], 0)
        self.assertEqual(result['native_grid']['maximum_point_error_emu_exact'], '0')
        self.assertEqual(result['native_grid']['original_frame_errors_emu_exact'][0], '1')
        first = shape.find('.//a:moveTo/a:pt', n.NS)
        self.assertEqual(first.get('x'), '-1')

    def assert_preserved_rejection(self, shape, obj, entry, code=None):
        before = E.tostring(shape); result = self.apply(shape, obj, entry)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(E.tostring(shape), before)
        if code:
            self.assertEqual(result['reason_code'], code)
        self.assertTrue(result['geometry_preserved'])
        return result

    def test_grid_cannot_merge_separate_components(self):
        shape, obj, entry = example(rect(0, 0, 1, 1)+rect(1.00000001, 0, 2, 1), placement=(0, 0, 1000, 1000))
        self.assert_preserved_rejection(shape, obj, entry, 'touching_boundary')

    def test_native_style_mismatch_or_ambiguous_line_is_not_rewritten(self):
        edits = [lambda s,o: o['style'].update(stroke='#000000'),
                 lambda s,o: o['style'].update(fill_rule='evenodd'),
                 lambda s,o: s.find('.//a:alpha', n.NS).set('val', '37001'),
                 lambda s,o: s.find('.//a:srgbClr', n.NS).set('val', 'FFFFFF'),
                 lambda s,o: s.find('.//a:ln', n.NS).remove(s.find('.//a:ln/a:noFill', n.NS)),
                 lambda s,o: E.SubElement(s.find('.//a:ln', n.NS), f'{{{A}}}solidFill'),
                 lambda s,o: s.find('.//a:path', n.NS).set('fill', 'none'),
                 lambda s,o: E.SubElement(s.find('p:spPr', n.NS), f'{{{A}}}effectLst')]
        for edit in edits:
            shape, obj, entry = example(); edit(shape, obj)
            with self.subTest(edit=edit): self.assert_preserved_rejection(shape, obj, entry)

    def test_missing_mapping_or_changed_transform_stays_original(self):
        shape, obj, entry = example(); self.assert_preserved_rejection(shape, obj, None, 'missing_map')
        for attr,value in [('rot','1'),('flipH','1'),('flipV','true')]:
            shape, obj, entry = example();shape.find('.//a:xfrm', n.NS).set(attr,value)
            self.assert_preserved_rejection(shape, obj, entry, 'unsupported_transform')
        shape, obj, entry = example(off_delta=3)
        self.assert_preserved_rejection(shape, obj, entry, 'frame_mismatch')

    def test_curves_are_explicitly_not_applicable_without_any_change(self):
        shape, obj, entry = example(); obj['commands'].insert(1, {'cubicTo': {'x1': 10,'y1':10,'x2':20,'y2':20,'x':30,'y':10}})
        before = E.tostring(shape); result = self.apply(shape,obj,entry)
        self.assertEqual(result['reason_code'], 'curves_not_supported')
        self.assertEqual(result['status'], 'not_applicable')
        self.assertEqual(E.tostring(shape),before)

    def test_wrapper_input_limit_precedes_copying_or_normalization(self):
        shape, obj, entry = example(); obj['commands'] = [{'moveTo': {'x':0,'y':0}}]*1041
        with patch.object(n, '_commands', side_effect=AssertionError('Should not copy oversized input')):
            self.assert_preserved_rejection(shape,obj,entry,'budget')
        shape, obj, entry = example();obj['commands']='invalid'
        self.assert_preserved_rejection(shape,obj,entry,'budget')

    def test_rejected_proof_does_not_partially_publish_geometry(self):
        shape,obj,entry=example()
        error=n.UnsupportedPdfWindingError('budget','synthetic predicate budget exhausted')
        with patch.object(n,'verify_simple_loop_topology',side_effect=error):
            self.assert_preserved_rejection(shape,obj,entry,'budget')

    def test_parent_transform_must_be_proved_identity(self):
        shape,obj,entry=example();before=E.tostring(shape)
        result=n.normalize_native_polygon_fill(shape,obj,entry)
        self.assertEqual(result['reason_code'],'unsupported_parent_transform')
        self.assertEqual(E.tostring(shape),before)
        tree=E.Element(f'{{{P}}}spTree');props=E.SubElement(tree,f'{{{P}}}grpSpPr')
        self.assertTrue(n.root_group_is_identity(tree))
        transform=E.SubElement(props,f'{{{A}}}xfrm')
        self.assertTrue(n.root_group_is_identity(tree))  # Actual authoring export.
        for tag,attributes in [('off',{'x':'5','y':'6'}),('ext',{'cx':'10','cy':'20'}),('chOff',{'x':'5','y':'6'}),('chExt',{'cx':'10','cy':'20'})]:
            E.SubElement(transform,f'{{{A}}}{tag}',attributes)
        self.assertTrue(n.root_group_is_identity(tree))
        transform.find('a:chOff',n.NS).set('x','4')
        self.assertFalse(n.root_group_is_identity(tree))

    def test_process_publishes_receipt_without_changing_manifest_or_other_members(self):
        shape,obj,entry=example(placement=(0,0,1000,1000))
        root=E.Element(f'{{{P}}}sld');tree=E.SubElement(E.SubElement(root,f'{{{P}}}cSld'),f'{{{P}}}spTree');E.SubElement(tree,f'{{{P}}}grpSpPr');tree.append(shape)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);source=p/'input.pptx';output=p/'output.pptx';manifest=p/'manifest.json';mapping=p/'map.json';receipt=p/'receipt.json'
            with ZipFile(source,'w') as z:z.writestr('ppt/slides/slide1.xml',E.tostring(root));z.writestr('unchanged.bin',b'original bytes')
            manifest.write_text(json.dumps({'canvas':{'width':1000,'height':1000},'objects':[obj]}));before=manifest.read_bytes()
            mapping.write_text(json.dumps({'placement':[0,0,1000,1000],'objects':[{'id':obj['id'],'kind':'path','box':entry['source_box']}]}))
            result=postprocess.process(source,output,manifest,receipt,object_map=mapping)
            self.assertEqual(result['native_winding_fills'][0]['status'],'applied')
            self.assertEqual(manifest.read_bytes(),before)
            with ZipFile(output) as z:self.assertEqual(z.read('unchanged.bin'),b'original bytes')
            self.assertEqual(json.loads(receipt.read_text())['native_winding_fills'],result['native_winding_fills'])


if __name__ == '__main__':
    unittest.main()
