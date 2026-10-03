import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from figure_rebuild import validate as v

class ManifestChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        Image.new('RGBA',(200,100),'white').save(self.root/'reference.png')
        self.m={'schema_version':1,'id':'test-figure','revision':1,'source':{'path':'reference.png','kind':'user_original','sha256':v.digest(self.root/'reference.png'),'width':200,'height':100},'canvas':{'width':200,'height':100},'recognition':{'provider':'calling_host','status':'reviewed'},'objects':[{'id':'curve','kind':'path','commands':[{'moveTo':{'x':10,'y':10}},{'lineTo':{'x':100,'y':70}}],'style':{'fill':'none','stroke':'#333333','stroke_width':2}}]}
    def tearDown(self):self.tmp.cleanup()
    def test_original_tampering_fails(self):
        (self.root/'reference.png').write_bytes(b'changed')
        self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
    def test_pending_or_empty_recognition_is_not_a_finished_conversion(self):
        self.m['recognition']['status']='needs_review';self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
        self.m['recognition']['status']='reviewed';self.m['objects']=[];self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
    def test_path_escape_and_nonfinite_geometry_rejected(self):
        self.m['source']['path']='../reference.png';self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
        self.m['source']['path']='reference.png';self.m['objects'][0]['commands'][1]['lineTo']['x']=float('nan');self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
    def test_native_text_and_raster_are_reported_separately(self):
        self.m['objects'].append({'id':'photo','kind':'image','editable':False,'path':'reference.png','sha256':v.digest(self.root/'reference.png'),'box':{'x':100,'y':10,'width':80,'height':40}})
        report=v.validate(self.m,self.root);self.assertEqual(report['status'],'PASS');self.assertFalse(report['fully_native']);self.assertEqual(report['raster_count'],1)
        self.m['objects'][-1]['editable']=True;self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
    def test_duplicate_identity_and_uncertain_labels_fail(self):
        self.m['objects'].append(copy.deepcopy(self.m['objects'][0]));self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')
        self.m['objects'].pop();self.m['recognition']['unresolved']=['unreadable symbol'];self.assertEqual(v.validate(self.m,self.root)['status'],'FAIL')

    def assertFails(self, manifest, message=None):
        report = v.validate(manifest, self.root)
        self.assertEqual(report['status'], 'FAIL')
        if message:
            self.assertTrue(any(message in e for e in report['errors']), report['errors'])

    def test_malformed_records_return_errors_instead_of_crashing(self):
        for manifest in (None, [], '', 1):
            with self.subTest(manifest=manifest): self.assertFails(manifest, 'Manifest must be a record')
        for key, value in [('canvas', []), ('source', None), ('recognition', 'wrong'), ('objects', {}), ('revision', True), ('schema_version', True)]:
            bad = copy.deepcopy(self.m); bad[key] = value
            with self.subTest(key=key): self.assertFails(bad)
        for changes in [{'id': []}, {'kind': {}}, {'group_id': []}, {'style': []}, {'kind': 'text', 'anchor': None}, {'kind': 'text', 'text': []}, {'commands': [{'moveTo': None}, {'lineTo': []}]}]:
            bad = copy.deepcopy(self.m); bad['objects'][0].update(changes)
            with self.subTest(changes=changes): self.assertFails(bad)
        bad = copy.deepcopy(self.m); bad['recognition']['provider'] = []
        self.assertFails(bad)

    def test_nonfinite_and_unbounded_input_is_not_authorable(self):
        for value in (float('inf'), float('nan'), 10**1000, True):
            bad = copy.deepcopy(self.m); bad['canvas']['width'] = value
            with self.subTest(value=type(value).__name__): self.assertFails(bad, 'Invalid canvas width')
        bad = copy.deepcopy(self.m); bad['objects'][0]['commands'] = [{'moveTo': {'x': 1, 'y': 1}}] * 200001
        self.assertFails(bad, 'Invalid path command count')

    def test_geometry_outside_source_canvas_needs_clipping(self):
        for x, y in [(-1, 10), (201, 10), (10, -1), (10, 101)]:
            bad = copy.deepcopy(self.m); bad['objects'][0]['commands'][1]['lineTo'].update(x=x, y=y)
            with self.subTest(x=x, y=y): self.assertFails(bad, 'clipping is unsupported')
        self.assertEqual(v.validate(self.m, self.root)['status'], 'PASS')

    def text_manifest(self):
        m = copy.deepcopy(self.m)
        m['objects'].append({'id': 'label', 'kind': 'text', 'text': 'Visible text', 'font_size': 16,
                             'anchor': {'x': 20, 'y': 35}, 'style': {'fill': '#000000'}})
        return m

    def test_source_literal_mismatch_blocks_review_without_changing_the_text(self):
        m = self.text_manifest()
        m['source_evidence'] = {'schema_version': 1, 'source_sha256': m['source']['sha256'],
            'literals': [{'id': 'label-reading', 'status': 'confirmed', 'object_ids': ['label'],
                          'text': 'Source text',
                          'source_region': {'x': 10, 'y': 10, 'width': 150, 'height': 40}}]}
        result = v.validate(m, self.root, require_review=False)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['source_content']['mismatches'][0]['code'], 'literal_mismatch')
        self.assertEqual(m['objects'][-1]['text'], 'Visible text')
        m['objects'][-1]['text'] = 'Source text'
        self.assertEqual(v.validate(m, self.root)['source_content']['status'], 'PASS')

    def test_absent_source_evidence_does_not_claim_content_verification(self):
        result = v.validate(self.text_manifest(), self.root)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['source_content']['status'], 'NOT_PROVIDED')

    def test_invisible_text_and_unsupported_text_stroke_fail(self):
        for style in ({'fill': 'none'}, {'fill': '#333333', 'opacity': 0}, {'fill': '#333333', 'stroke': '#FF0000', 'stroke_width': 2}):
            bad = self.text_manifest(); bad['objects'][-1]['style'] = style
            with self.subTest(style=style): self.assertFails(bad)
        valid = self.text_manifest(); valid['objects'][-1]['style'] = {}
        self.assertEqual(v.validate(valid, self.root)['status'], 'PASS')

    def test_invalid_text_position_or_box_rotation_fails_before_renderer(self):
        bad = self.text_manifest(); bad['objects'][-1]['anchor']['x'] = -1
        self.assertFails(bad, 'clipping is unsupported')
        bad = self.text_manifest(); bad['objects'][-1]['rotation'] = 90
        self.assertFails(bad, 'requires an explicit box')
        for box in ({'x': 1, 'y': 1, 'width': 0, 'height': 10}, {'x': 1, 'y': 1, 'width': -2, 'height': 10}, {'x': 190, 'y': 30, 'width': 20, 'height': 80}):
            bad = self.text_manifest(); text = bad['objects'][-1]; text.pop('anchor'); text.update(box=box, rotation=90)
            with self.subTest(box=box): self.assertFails(bad)
        valid = self.text_manifest(); text = valid['objects'][-1]; text.pop('anchor'); text.update(box={'x': 40, 'y': 35, 'width': 60, 'height': 20}, rotation=90)
        self.assertEqual(v.validate(valid, self.root)['status'], 'PASS')

    def test_placement_is_checked_before_authoring(self):
        canvas = {'width': 1280, 'height': 720}
        self.assertEqual(v.validate_placement([100, 100, 400, 300], canvas), [100, 100, 400, 300])
        for placement in (None, [], [0, 0, 1], [0, 0, 0, 10], [0, 0, -10, 10], [-1, 0, 10, 10], [0, 0, 1281, 10], [0, 719, 10, 10], [0, 0, float('nan'), 10], [0, 0, True, 10]):
            with self.subTest(placement=placement), self.assertRaises(ValueError):
                v.validate_placement(placement, canvas)

    def test_xml_forbidden_characters_are_rejected(self):
        for ch in ('\x00', '\x01', '\ud800', '\ufffe'):
            bad = self.text_manifest(); bad['objects'][-1]['text'] = 'Label' + ch
            with self.subTest(code=ord(ch)): self.assertFails(bad, 'XML 1.0')

    def test_rotated_visible_footprint_can_fit_outside_editing_rectangle(self):
        valid = self.text_manifest(); text = valid['objects'][-1]; text.pop('anchor')
        text.update(box={'x': -30, 'y': 40, 'width': 100, 'height': 20}, rotation=90)
        self.assertEqual(v.validate(valid, self.root)['status'], 'PASS')

    def test_crop_quantization_cannot_erase_visible_area(self):
        self.m['objects'].append({'id':'photo','kind':'image','editable':False,'path':'reference.png','sha256':v.digest(self.root/'reference.png'),'box':{'x':100,'y':10,'width':80,'height':40},
                                 'crop': {'left': .49999999, 'right': .49999999, 'top': 0, 'bottom': 0}})
        self.assertFails(self.m, 'quantization')

if __name__=='__main__':unittest.main()
