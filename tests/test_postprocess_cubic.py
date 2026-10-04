"""Exercise cubic restoration and guarded fill rewriting through real ZIP output."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from figure_rebuild import postprocess

NS = postprocess.NS
P, A = ('{' + NS[k] + '}' for k in ('p', 'a'))


def fixture(overlap=True):
    commands = [
        {'moveTo': {'x': 10.0, 'y': 10.0}},
        {'cubicTo': {'x1': 11.0, 'y1': 9.0, 'x2': 13.0, 'y2': 9.0, 'x': 14.0, 'y': 10.0}},
        {'lineTo': {'x': 14.0, 'y': 14.0}},
        {'lineTo': {'x': 10.0, 'y': 14.0}}, {'close': {}}]
    if overlap:
        commands += [{'moveTo': {'x': 12.0, 'y': 12.0}},
                     {'lineTo': {'x': 17.0, 'y': 13.0}},
                     {'lineTo': {'x': 13.0, 'y': 17.0}}, {'close': {}}]
    box = {'x': 10, 'y': 9, 'width': 7 if overlap else 4, 'height': 8 if overlap else 5}
    obj = {'id': 'curved-fill', 'kind': 'path', 'commands': commands,
           'style': {'fill': '#7f7f7f', 'opacity': .37, 'stroke': 'none', 'stroke_width': 0}}
    manifest = {'canvas': {'width': 100, 'height': 100}, 'objects': [obj]}
    mapping = {'placement': [0, 0, 100, 100],
               'objects': [{'id': obj['id'], 'kind': 'path', 'box': box, 'editable': True}]}
    root = ET.Element(P + 'sld')
    tree = ET.SubElement(ET.SubElement(root, P + 'cSld'), P + 'spTree')
    nv = ET.SubElement(tree, P + 'nvGrpSpPr')
    ET.SubElement(nv, P + 'cNvPr', id='1', name='root')
    ET.SubElement(nv, P + 'cNvGrpSpPr'); ET.SubElement(nv, P + 'nvPr')
    ET.SubElement(tree, P + 'grpSpPr')
    shape = ET.SubElement(tree, P + 'sp')
    nv = ET.SubElement(shape, P + 'nvSpPr')
    ET.SubElement(nv, P + 'cNvPr', id='2', name=obj['id'])
    ET.SubElement(nv, P + 'cNvSpPr'); ET.SubElement(nv, P + 'nvPr')
    props = ET.SubElement(shape, P + 'spPr')
    xf = ET.SubElement(props, A + 'xfrm')
    ET.SubElement(xf, A + 'off', x=str(box['x']*9525), y=str(box['y']*9525))
    ET.SubElement(xf, A + 'ext', cx=str(box['width']*9525), cy=str(box['height']*9525))
    paths = ET.SubElement(ET.SubElement(props, A + 'custGeom'), A + 'pathLst')
    path = ET.SubElement(paths, A + 'path', w='1', h='1')
    ET.SubElement(ET.SubElement(path, A + 'moveTo'), A + 'pt', x='0', y='0')
    ET.SubElement(ET.SubElement(path, A + 'lnTo'), A + 'pt', x='1', y='1')
    ET.SubElement(path, A + 'close')
    fill = ET.SubElement(ET.SubElement(props, A + 'solidFill'), A + 'srgbClr', val='7F7F7F')
    ET.SubElement(fill, A + 'alpha', val='37000')
    ET.SubElement(ET.SubElement(props, A + 'ln', w='0'), A + 'noFill')
    return root, shape, manifest, mapping


class CubicPostprocessTests(unittest.TestCase):
    def run_fixture(self, overlap=True, placement=(0.0, 0.0, 100.0, 100.0)):
        root, shape, manifest, mapping = fixture(overlap)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            mp, op = p/'manifest.json', p/'map.json'
            mp.write_text(json.dumps(manifest)); op.write_text(json.dumps(mapping))
            source, output, receipt = p/'input.pptx', p/'output.pptx', p/'receipt.json'
            with ZipFile(source, 'w') as archive:
                archive.writestr('ppt/slides/slide1.xml', ET.tostring(root))
                archive.writestr('unchanged.bin', b'untouched source member')
            source_bytes, manifest_bytes, mapping_bytes = source.read_bytes(), mp.read_bytes(), op.read_bytes()
            restored = deepcopy(shape)
            postprocess._restore_cubic_path(restored, manifest['objects'][0], postprocess._mapped_frames(op, manifest))
            result = postprocess.process(source, output, mp, receipt, object_map=op,
                                         occupied_placement=placement)
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(mp.read_bytes(), manifest_bytes)
            self.assertEqual(op.read_bytes(), mapping_bytes)
            self.assertEqual(json.loads(receipt.read_text()), result)
            with ZipFile(output) as archive:
                actual = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
                self.assertEqual(archive.read('unchanged.bin'), b'untouched source member')
            return result, actual.find('.//p:sp', NS), restored

    def test_restoration_precedes_proved_rewrite_and_keeps_integer_curves(self):
        result, actual, restored = self.run_fixture()
        row = result['native_winding_fills'][0]
        self.assertEqual(row['status'], 'applied', row)
        self.assertEqual(result['native_cubic_segment_count'], 1)
        for tag in ['xfrm', 'solidFill', 'ln']:
            self.assertEqual(ET.tostring(actual.find('p:spPr/a:'+tag, NS)),
                             ET.tostring(restored.find('p:spPr/a:'+tag, NS)))
        self.assertEqual([ET.tostring(n) for n in actual.findall('.//a:cubicBezTo', NS)],
                         [ET.tostring(n) for n in restored.findall('.//a:cubicBezTo', NS)])
        self.assertEqual(len(actual.findall('.//a:path', NS)), 1)
        self.assertEqual(len(actual.findall('.//a:moveTo', NS)), 1)

    def test_missing_explicit_placement_keeps_post_restoration_geometry(self):
        result, actual, restored = self.run_fixture(placement=None)
        row = result['native_winding_fills'][0]
        self.assertEqual(row['status'], 'rejected', row)
        self.assertEqual(row['reason_code'], 'source_binding')
        self.assertEqual(ET.tostring(actual.find('p:spPr', NS)), ET.tostring(restored.find('p:spPr', NS)))

    def test_correct_single_curve_fill_does_not_get_rewritten(self):
        result, actual, restored = self.run_fixture(overlap=False)
        row = result['native_winding_fills'][0]
        self.assertEqual(row['status'], 'not_applicable', row)
        self.assertEqual(row['reason_code'], 'no_proven_fill_rule_mismatch')
        self.assertEqual(ET.tostring(actual.find('p:spPr', NS)), ET.tostring(restored.find('p:spPr', NS)))

    def test_map_cannot_authorize_different_occupied_placement(self):
        result, actual, restored = self.run_fixture(placement=(1, 0, 100, 100))
        row = result['native_winding_fills'][0]
        self.assertEqual(row['status'], 'rejected', row)
        self.assertEqual(row['reason_code'], 'source_binding')
        self.assertEqual(ET.tostring(actual.find('p:spPr', NS)), ET.tostring(restored.find('p:spPr', NS)))


if __name__ == '__main__':
    unittest.main()
