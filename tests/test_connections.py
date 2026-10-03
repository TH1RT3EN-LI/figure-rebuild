"""Relationship topology, stable local edits, and file transaction regressions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from figure_rebuild.connections import (
    apply_scene_patch, attachment_records, object_bounds, path_bounds,
    resolve_scene, validate_connections, write_patch,
)
from figure_rebuild.review import content_digest
from test_scene_compile import synthetic_formula


def rectangle(oid, x, y, width, height):
    return {'id': oid, 'kind': 'path', 'z_index': 1,
            'commands': [{'moveTo': {'x': x, 'y': y}},
                         {'lineTo': {'x': x+width, 'y': y}},
                         {'lineTo': {'x': x+width, 'y': y+height}},
                         {'lineTo': {'x': x, 'y': y+height}}, {'close': {}}],
            'style': {'fill': '#EAEAEA', 'stroke': '#333333', 'stroke_width': 2}}


def label(oid, target, x=110, y=110, width=80, height=40):
    return {'id': oid, 'kind': 'text', 'z_index': 2, 'text': 'Visible label',
            'font_size': 18, 'box': {'x': x, 'y': y, 'width': width, 'height': height},
            'alignment': 'center', 'vertical_alignment': 'middle',
            'style': {'fill': '#000000'},
            'attach_to': {'id': target, 'site': 'center', 'offset': {'x': 0, 'y': 0}}}


def connector(oid='flow', start='module-a', end='module-b', route='elbow'):
    return {'id': oid, 'kind': 'connector', 'z_index': 3,
            'from': {'id': start, 'site': 'right'}, 'to': {'id': end, 'site': 'left'},
            'route': route, 'arrow': {'start': 'none', 'end': 'stealth'},
            'style': {'fill': 'none', 'stroke': '#222222', 'stroke_width': 2}}


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / 'source.png'
        Image.new('RGB', (800, 500), 'white').save(source)
        self.scene = {'schema_version': 1, 'id': 'connection-fixture', 'revision': 3,
                      'canvas': {'width': 800, 'height': 500},
                      'source': {'path': 'source.png', 'kind': 'user_original', 'width': 800,
                                 'height': 500, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
                      'recognition': {'provider': 'calling_host', 'status': 'reviewed',
                                      'unresolved': [], 'reviewed_at': 'fixture', 'acceptance': 'accepted'},
                      'objects': [rectangle('module-a', 100, 100, 100, 60),
                                  label('module-label', 'module-a'),
                                  rectangle('module-b', 400, 200, 120, 80), connector()]}
        self.scene['recognition']['reviewed_digest'] = content_digest(self.scene)
        self.scene['recognition']['reviewed_revision'] = 3

    def patch(self, operations, scene=None):
        scene = scene or self.scene
        return {'base_revision': scene['revision'], 'base_digest': content_digest(scene),
                'reason': 'User spacing correction', 'operations': operations}

    def objects(self, scene):
        return {obj['id']: obj for obj in scene['objects']}

    def test_resolution_uses_stable_sites_and_preserves_input_order_and_style(self):
        before = copy.deepcopy(self.scene)
        resolved, records = resolve_scene(self.scene)
        self.assertEqual(self.scene, before)
        self.assertEqual([obj['id'] for obj in resolved['objects']], [obj['id'] for obj in self.scene['objects']])
        record = records[0]
        self.assertEqual(record['from']['site_index'], 3)
        self.assertEqual(record['to']['site_index'], 1)
        self.assertEqual(record['points'], [{'x': 200, 'y': 130}, {'x': 300, 'y': 130},
                                           {'x': 300, 'y': 240}, {'x': 400, 'y': 240}])
        flow = self.objects(resolved)['flow']
        self.assertEqual(flow['kind'], 'path')
        self.assertEqual(flow['source_kind'], 'connector')
        self.assertEqual(flow['connection_record'], record)
        self.assertEqual(flow['style'], self.objects(before)['flow']['style'])
        self.assertEqual(attachment_records(self.scene)[0]['target_id'], 'module-a')

    def test_move_module_updates_attached_label_and_connector_only(self):
        before = copy.deepcopy(self.scene)
        operations = [{'op': 'translate', 'id': 'module-a', 'dx': 25, 'dy': 10}]
        result, audit = apply_scene_patch(self.scene, self.patch(operations), self.root)
        self.assertEqual(self.scene, before)
        objects = self.objects(result)
        self.assertEqual(path_bounds(objects['module-a'])['x'], 125)
        self.assertEqual(objects['module-label']['box']['x'], 135)
        self.assertEqual(objects['module-label']['box']['y'], 120)
        self.assertEqual(objects['module-b'], self.objects(before)['module-b'])
        self.assertEqual(objects['flow'], self.objects(before)['flow'])
        self.assertEqual(audit['connections_after'][0]['from']['point'], {'x': 225, 'y': 140})
        self.assertEqual(set(audit['affected_ids']), {'module-a', 'module-label', 'flow'})
        self.assertEqual(result['revision'], 4)
        self.assertEqual(result['recognition']['status'], 'needs_review')
        self.assertNotIn('acceptance', result['recognition'])
        self.assertNotIn('reviewed_digest', result['recognition'])

    def test_labels_follow_an_out_of_order_chain_without_double_translation(self):
        child = label('child-label', 'module-label', 120, 120, 60, 20)
        self.scene['objects'].insert(0, child)
        result, _ = apply_scene_patch(self.scene, self.patch([
            {'op': 'translate', 'id': 'module-a', 'dx': 30, 'dy': 12}]), self.root)
        objects = self.objects(result)
        self.assertEqual(objects['child-label']['box'], {'x': 150, 'y': 132, 'width': 60, 'height': 20})

    def test_direct_attached_label_edit_updates_offset_for_later_module_move(self):
        result, _ = apply_scene_patch(self.scene, self.patch([
            {'op': 'translate', 'id': 'module-label', 'dx': 7, 'dy': -3},
            {'op': 'translate', 'id': 'module-a', 'dx': 30, 'dy': 12}]), self.root)
        obj = self.objects(result)['module-label']
        self.assertEqual(obj['attach_to']['offset'], {'x': 7, 'y': -3})
        self.assertEqual(obj['box']['x'], 147)
        self.assertEqual(obj['box']['y'], 119)

    def test_baseline_attachment_is_resolved_as_a_baseline(self):
        obj = self.scene['objects'][1]
        obj.pop('box'); obj['anchor'] = {'x': 150, 'y': 130}
        obj['vertical_alignment'] = 'top'; obj['attach_to']['offset'] = {'x': 0, 'y': 6}
        result, _ = apply_scene_patch(self.scene, self.patch([
            {'op': 'translate', 'id': 'module-a', 'dx': 10, 'dy': 4}]), self.root)
        self.assertEqual(self.objects(result)['module-label']['anchor'], {'x': 160, 'y': 140})

    def baseline_formula(self):
        # Reuse the frozen, source-free integration fixture. Neither a private
        # system font nor a LaTeX engine is needed to test scene movement.
        obj = synthetic_formula(self.root, 'formula-baseline')
        obj.pop('box'); obj['baseline_anchor'] = {'x': 150, 'y': 130}
        self.scene['objects'].insert(1,obj)
        return obj

    def test_formula_baseline_translate_preserves_asset_and_updates_only_anchor(self):
        obj = self.baseline_formula(); before = copy.deepcopy(obj)
        result, audit = apply_scene_patch(self.scene, self.patch([
            {'op':'translate','id':obj['id'],'dx':7,'dy':-3}]),self.root)
        moved=self.objects(result)[obj['id']]
        self.assertEqual(moved['baseline_anchor'],{'x':157,'y':127})
        before.pop('baseline_anchor'); remainder=copy.deepcopy(moved);remainder.pop('baseline_anchor')
        self.assertEqual(before,remainder)
        self.assertEqual(audit['affected_ids'],[obj['id']])

    def test_formula_baseline_attachment_and_child_chain_follow_module(self):
        obj=self.baseline_formula()
        obj['attach_to']={'id':'module-a','site':'center','offset':{'x':3,'y':7}}
        child=label('formula-child',obj['id'],width=20,height=10)
        self.scene['objects'].insert(0,child)
        result,_=apply_scene_patch(self.scene,self.patch([
            {'op':'translate','id':'module-a','dx':10,'dy':4}]),self.root)
        objects=self.objects(result)
        self.assertEqual(objects[obj['id']]['baseline_anchor'],{'x':163,'y':141})
        self.assertNotIn('anchor',objects[obj['id']])
        self.assertEqual(objects['formula-child']['box'],{'x':153,'y':136,'width':20,'height':10})
        resolved,_=resolve_scene(result)
        self.assertEqual(self.objects(resolved)[obj['id']]['attachment_record']['position_kind'],'baseline_anchor')

    def test_baseline_formula_and_zero_area_path_cannot_be_native_endpoint(self):
        obj=self.baseline_formula()
        self.scene['objects'][-1]['from']={'id':obj['id'],'site':'right'}
        self.assertTrue(validate_connections(self.scene))
        self.scene['objects'][1]=rectangle(obj['id'],50,50,0,30)
        self.assertTrue(any('positive-area' in error for error in validate_connections(self.scene)))

    def test_attachment_cycles_and_dangling_targets_reject_without_mutation(self):
        for change in ('cycle', 'missing'):
            bad = copy.deepcopy(self.scene)
            if change == 'cycle':
                child = label('child', 'module-label'); bad['objects'].append(child)
                bad['objects'][1]['attach_to']['id'] = 'child'
            else:
                bad['objects'][1]['attach_to']['id'] = 'missing'
            before = copy.deepcopy(bad)
            self.assertTrue(validate_connections(bad))
            with self.assertRaises(ValueError):
                resolve_scene(bad)
            self.assertEqual(bad, before)

    def test_connector_flow_cycles_are_valid_but_connector_targets_are_not(self):
        self.scene['objects'].append(connector('return-flow', 'module-b', 'module-a', 'straight'))
        self.assertEqual(validate_connections(self.scene), [])
        self.assertEqual(len(resolve_scene(self.scene)[1]), 2)
        self.scene['objects'][-1]['to']['id'] = 'flow'
        self.assertTrue(validate_connections(self.scene))

    def test_unknown_sites_arrows_and_dangling_connector_reject(self):
        for field, value in [('site', 'outside'), ('id', 'missing')]:
            bad = copy.deepcopy(self.scene); bad['objects'][-1]['from'][field] = value
            with self.assertRaises(ValueError):
                resolve_scene(bad)
        bad = copy.deepcopy(self.scene); bad['objects'][-1]['arrow']['end'] = 'custom-guess'
        self.assertTrue(validate_connections(bad))

    def test_module_label_connector_and_manifest_locks_block_indirect_motion(self):
        for target in ('module-a', 'module-label', 'flow'):
            for mode in ('object', 'manifest'):
                bad = copy.deepcopy(self.scene)
                if mode == 'object':
                    self.objects(bad)[target]['locked'] = True
                else:
                    bad['locks'] = [{'id': target}]
                before = copy.deepcopy(bad)
                with self.assertRaisesRegex(ValueError, 'locked'):
                    apply_scene_patch(bad, self.patch([
                        {'op': 'translate', 'id': 'module-a', 'dx': 10, 'dy': 0}], bad), self.root)
                self.assertEqual(bad, before)

    def test_stale_revision_or_digest_cannot_change_state(self):
        patch = self.patch([{'op': 'translate', 'id': 'module-a', 'dx': 10, 'dy': 0}])
        before = copy.deepcopy(self.scene)
        for field, value in [('base_revision', 2), ('base_digest', '0'*64), ('base_revision', True)]:
            bad = dict(patch, **{field: value})
            with self.assertRaisesRegex(ValueError, 'Stale'):
                apply_scene_patch(self.scene, bad, self.root)
            self.assertEqual(self.scene, before)

    def test_setbox_resizes_path_controls_and_reanchors_label_without_stroke_change(self):
        patch = self.patch([{'op': 'setbox', 'id': 'module-a',
                             'box': {'x': 120, 'y': 90, 'width': 160, 'height': 80}}])
        result, _ = apply_scene_patch(self.scene, patch, self.root)
        objects = self.objects(result)
        self.assertEqual(path_bounds(objects['module-a']), {'x': 120, 'y': 90, 'width': 160, 'height': 80})
        self.assertEqual(objects['module-a']['style'], self.objects(self.scene)['module-a']['style'])
        self.assertEqual(objects['module-label']['box']['x'], 160)
        self.assertEqual(objects['module-label']['box']['y'], 110)
        self.assertEqual(resolve_scene(result)[1][0]['from']['point'], {'x': 280, 'y': 130})

    def test_cubic_bounds_use_actual_extrema_and_translation_keeps_controls(self):
        path = {'id': 'cubic', 'kind': 'path', 'commands': [
            {'moveTo': {'x': 20, 'y': 20}},
            {'cubicTo': {'x1': 20, 'y1': 120, 'x2': 120, 'y2': 120, 'x': 120, 'y': 20}}]}
        self.assertEqual(path_bounds(path), {'x': 20, 'y': 20, 'width': 100, 'height': 75})
        self.scene['objects'][0] = dict(path, id='module-a', style={'stroke': '#222222', 'stroke_width': 2})
        result, _ = apply_scene_patch(self.scene, self.patch([
            {'op': 'translate', 'id': 'module-a', 'dx': 10, 'dy': 5}]), self.root)
        command = self.objects(result)['module-a']['commands'][1]['cubicTo']
        self.assertEqual(command, {'x1': 30, 'y1': 125, 'x2': 130, 'y2': 125, 'x': 130, 'y': 25})

    def test_sequential_attached_setbox_uses_current_module_position(self):
        result, _ = apply_scene_patch(self.scene, self.patch([
            {'op': 'translate', 'id': 'module-a', 'dx': 30, 'dy': 0},
            {'op': 'setbox', 'id': 'module-label', 'box': {'x': 170, 'y': 130, 'width': 60, 'height': 20}},
            {'op': 'translate', 'id': 'module-a', 'dx': 10, 'dy': 0}]), self.root)
        self.assertEqual(self.objects(result)['module-label']['box'],
                         {'x': 180, 'y': 130, 'width': 60, 'height': 20})

    def test_invalid_operation_nonfinite_noop_and_outside_canvas_are_atomic(self):
        invalid = [{'op': 'translate', 'id': 'module-a', 'dx': float('nan'), 'dy': 0},
                   {'op': 'translate', 'id': 'module-a', 'dx': 10**1000, 'dy': 0},
                   {'op': 'translate', 'id': 'module-a', 'dx': 0, 'dy': 0},
                   {'op': 'translate', 'id': 'module-a', 'dx': 900, 'dy': 0},
                   {'op': 'translate', 'id': 'flow', 'dx': 10, 'dy': 0},
                   {'op': 'replace', 'id': 'module-a', 'field': 'style', 'value': {}},
                   {'op': 'translate', 'id': '0', 'dx': 10, 'dy': 0}]
        before = copy.deepcopy(self.scene)
        for operation in invalid:
            with self.subTest(operation=operation['op']):
                with self.assertRaises(ValueError):
                    apply_scene_patch(self.scene, self.patch([operation]), self.root)
                self.assertEqual(self.scene, before)

    def test_transaction_keeps_exact_base_bytes_relative_assets_and_audit(self):
        source = self.root / 'manifest.json'
        original = (json.dumps(self.scene, indent=3)+'\n').encode()
        source.write_bytes(original)
        output = self.root / 'manifest-r4.json'
        patch = self.patch([{'op': 'translate', 'id': 'module-a', 'dx': 15, 'dy': 0}])
        audit = write_patch(source, patch, output)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(Path(audit['snapshot']).read_bytes(), original)
        result = json.loads(output.read_text())
        self.assertEqual(result['source']['path'], 'source.png')
        self.assertEqual(result['revision'], 4)
        saved_audit = json.loads((Path(audit['snapshot']).parent / 'patch-audit.json').read_text())
        self.assertEqual(saved_audit['base_revision'], 3)
        self.assertEqual(saved_audit['base_digest'], patch['base_digest'])
        self.assertEqual(saved_audit['reason'], patch['reason'])

    def test_stale_patch_and_existing_output_write_no_snapshot(self):
        source = self.root / 'manifest.json'; source.write_text(json.dumps(self.scene))
        patch = self.patch([{'op': 'translate', 'id': 'module-a', 'dx': 15, 'dy': 0}])
        output = self.root / 'manifest-r4.json'
        bad = dict(patch, base_digest='0'*64)
        with self.assertRaises(ValueError):
            write_patch(source, bad, output)
        self.assertFalse(output.exists())
        self.assertFalse((self.root/'history').exists())
        output.write_text('existing user result')
        with self.assertRaises(ValueError):
            write_patch(source, patch, output)
        self.assertEqual(output.read_text(), 'existing user result')
        self.assertFalse((self.root/'history').exists())

    def test_output_cannot_move_assets_to_a_new_job_or_overwrite_input(self):
        source = self.root/'manifest.json'; source.write_text(json.dumps(self.scene))
        patch = self.patch([{'op': 'translate', 'id': 'module-a', 'dx': 15, 'dy': 0}])
        for output in (source, self.root/'another-job'/'manifest.json'):
            with self.assertRaises(ValueError):
                write_patch(source, patch, output)
        self.assertFalse((self.root/'history').exists())

    def test_endpoint_rejects_anchor_only_text_and_image_attachment_is_allowed(self):
        anchored = self.scene['objects'][1]
        anchored.pop('box'); anchored['anchor'] = {'x': 150, 'y': 130}
        self.scene['objects'][-1]['from']['id'] = 'module-label'
        self.assertTrue(validate_connections(self.scene))
        self.scene['objects'][-1]['from']['id'] = 'module-a'
        image = label('formula-label', 'module-a')
        image.update(kind='image', source_kind='formula', path='source.png', editable=False,
                     sha256=self.scene['source']['sha256'], style={})
        for key in ('text', 'font_size', 'alignment', 'vertical_alignment'):
            image.pop(key)
        self.scene['objects'].append(image)
        resolved, _ = resolve_scene(self.scene)
        self.assertEqual(self.objects(resolved)['formula-label']['attachment_record']['target_id'], 'module-a')


if __name__ == '__main__':
    unittest.main()
