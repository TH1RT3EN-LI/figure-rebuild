"""Source-bound content gates must detect valid-looking but incorrect scenes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from figure_rebuild.cli import freeze_assets
from figure_rebuild.review import content_digest, verify_review
from figure_rebuild.scene_compile import compile_scene
from figure_rebuild.source_inventory import audit_source_inventory
from figure_rebuild.validate import validate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def path(identity, commands):
    return {'id': identity, 'kind': 'path', 'commands': commands,
            'style': {'fill': 'none', 'stroke': '#000000', 'stroke_width': 1}}


class SourceInventoryChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.job = self.root / 'job'; self.job.mkdir()
        Image.new('RGB', (200, 100), 'white').save(self.job / 'source.png')
        (self.job / 'source.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
            '<text x="10" y="20">Layer1</text><path d="M20 40H30M25 35V45"/>'
            '<path d="M50 60H100M95 55L100 60L95 65"/></svg>')
        self.manifest = {'schema_version': 1, 'id': 'source-gate', 'revision': 1,
            'source': {'path': 'source.png', 'kind': 'user_original',
                       'sha256': sha(self.job / 'source.png'), 'width': 200, 'height': 100},
            'canvas': {'width': 200, 'height': 100},
            'recognition': {'provider': 'calling_host', 'status': 'reviewed', 'unresolved': []},
            'objects': [
                {'id': 'label', 'kind': 'text', 'text': 'Layer1', 'font_size': 12,
                 'box': {'x': 10, 'y': 10, 'width': 60, 'height': 20}, 'style': {'fill': '#000000'}},
                path('operator', [{'moveTo': {'x': 20, 'y': 40}}, {'lineTo': {'x': 30, 'y': 40}},
                                  {'moveTo': {'x': 25, 'y': 35}}, {'lineTo': {'x': 25, 'y': 45}}]),
                path('edge', [{'moveTo': {'x': 50, 'y': 60}}, {'lineTo': {'x': 100, 'y': 60}}]),
                path('arrowhead', [{'moveTo': {'x': 95, 'y': 55}}, {'lineTo': {'x': 100, 'y': 60}},
                                   {'lineTo': {'x': 95, 'y': 65}}]) ]}
        self.inventory = {'schema_version': 1, 'source_sha256': self.manifest['source']['sha256'],
            'reference_manifest': {}, 'source_artifacts': [self.binding('source.svg')],
            'components': [
                self.component('label-reading', 'literal', ['label'], 'live_text', reading='Layer1'),
                self.component('plus', 'math', ['operator'], 'glyph_paths',
                               limitations=['Editable outlines; semantic formula source is unavailable.']),
                self.component('forward-flow', 'connection', ['edge', 'arrowhead'], 'native_geometry')],
            'unresolved': []}
        self.bind_reference()

    def binding(self, relative):
        return {'path': relative, 'sha256': sha(self.job / relative)}

    def component(self, identity, category, ids, representation, **extra):
        return {'id': identity, 'category': category, 'object_ids': ids,
                'representation': representation, 'reading_status': 'verified',
                'source_region': {'x': 0, 'y': 0, 'width': 200, 'height': 100},
                'limitations': [], **extra}

    def write_inventory(self, raw=None):
        (self.job / 'inventory.json').write_text(json.dumps(self.inventory) if raw is None else raw)
        self.manifest['source_inventory'] = self.binding('inventory.json')

    def bind_reference(self):
        reference = copy.deepcopy(self.manifest); reference.pop('source_inventory', None)
        (self.job / 'reference.json').write_text(json.dumps(reference))
        self.inventory['reference_manifest'] = self.binding('reference.json')
        self.write_inventory()

    def audit(self, manifest=None, root=None):
        return audit_source_inventory(manifest or self.manifest, root or self.job)

    def test_positive_compares_all_objects_and_declares_outline_limit(self):
        original = copy.deepcopy(self.manifest)
        result = self.audit()
        self.assertEqual(result['status'], 'REVIEW')
        self.assertEqual(result['coverage'], {'status': 'complete', 'reference_objects': 4,
            'objects_compared': 4, 'components_checked': 3, 'named_component_objects': 4,
            'uncovered_object_ids': [], 'category_counts': {'literal': 1, 'math': 1, 'connection': 1}})
        self.assertEqual({r['path'] for r in result['hash_files']},
                         {'source.png', 'source.svg', 'inventory.json', 'reference.json'})
        self.assertFalse(result['automatic_recognition_performed'])
        self.assertFalse(result['final_application_verified'])
        self.assertEqual(result['user_acceptance'], 'pending')
        self.assertEqual(self.manifest, original)

    def test_absence_does_not_claim_source_validation(self):
        data = copy.deepcopy(self.manifest); data.pop('source_inventory')
        self.assertEqual(self.audit(data)['status'], 'NOT_PROVIDED')
        self.assertNotIn('source_inventory', compile_scene(data, self.job)[1])

    def test_literal_only_full_coverage_can_pass_with_explicit_scope(self):
        self.manifest['objects'] = self.manifest['objects'][:1]
        self.inventory['components'] = self.inventory['components'][:1]
        self.bind_reference()
        self.assertEqual(self.audit()['status'], 'PASS')
        self.assertFalse(self.audit()['automatic_recognition_performed'])

    def test_valid_geometry_does_not_make_wrong_words_symbols_or_arrows_correct(self):
        mutations = []
        data = copy.deepcopy(self.manifest); data['objects'][0]['text'] = 'Layer9'; mutations.append(data)
        data = copy.deepcopy(self.manifest); data['objects'].pop(1); mutations.append(data)
        data = copy.deepcopy(self.manifest); data['objects'][1]['commands'][0]['moveTo']['x'] += 1; mutations.append(data)
        data = copy.deepcopy(self.manifest); data['objects'][1]['style']['stroke'] = '#FF0000'; mutations.append(data)
        data = copy.deepcopy(self.manifest)
        data['objects'][3]['commands'] = [{'moveTo': {'x': 55, 'y': 55}},
            {'lineTo': {'x': 50, 'y': 60}}, {'lineTo': {'x': 55, 'y': 65}}]; mutations.append(data)
        for data in mutations:
            without_inventory = copy.deepcopy(data); without_inventory.pop('source_inventory')
            with self.subTest(objects=data['objects']):
                self.assertEqual(validate(without_inventory, self.job)['status'], 'PASS')
                self.assertEqual(validate(data, self.job)['status'], 'FAIL')
                self.assertEqual(self.audit(data)['status'], 'FAIL')

    def test_order_extra_objects_and_group_changes_fail(self):
        data = copy.deepcopy(self.manifest); data['objects'].reverse()
        self.assertEqual(self.audit(data)['status'], 'FAIL')
        data = copy.deepcopy(self.manifest); extra = copy.deepcopy(data['objects'][1]); extra['id'] = 'extra'
        data['objects'].append(extra); self.assertEqual(self.audit(data)['status'], 'FAIL')
        data = copy.deepcopy(self.manifest); data['groups'] = [{'id': 'new-group'}]
        self.assertEqual(self.audit(data)['status'], 'FAIL')

    def test_uncovered_objects_are_compared_and_coverage_is_not_complete(self):
        self.inventory['components'] = self.inventory['components'][:1]; self.write_inventory()
        result = self.audit()
        self.assertEqual(result['status'], 'REVIEW')
        self.assertEqual(result['coverage']['status'], 'partial')
        self.assertEqual(result['coverage']['uncovered_object_ids'], ['operator', 'edge', 'arrowhead'])
        data = copy.deepcopy(self.manifest); data['objects'][-1]['commands'].pop()
        self.assertEqual(self.audit(data)['status'], 'FAIL')

    def test_dense_826_objects_have_no_256_object_comparison_cutoff(self):
        operator = self.manifest['objects'][1]
        self.manifest['objects'] = [dict(copy.deepcopy(operator), id=f'symbol-{i}') for i in range(826)]
        self.inventory['components'] = [self.component('dense-math', 'math',
            [o['id'] for o in self.manifest['objects']], 'glyph_paths', limitations=['Outlined mathematics.'])]
        self.bind_reference()
        self.assertEqual(self.audit()['coverage']['objects_compared'], 826)
        data = copy.deepcopy(self.manifest); data['objects'][-1]['commands'].pop()
        result = self.audit(data)
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any(m.get('object_id') == 'symbol-825' for m in result['mismatches']))

    def test_reference_uses_scene_byte_budget_instead_of_smaller_inventory_budget(self):
        reference = copy.deepcopy(self.manifest); reference.pop('source_inventory')
        for count, expected in ((9, 'REVIEW'), (33, 'FAIL')):
            reference['notes'] = 'x' * (count * 1024**2)
            file = self.job / 'reference.json'; file.write_text(json.dumps(reference))
            self.inventory['reference_manifest'] = self.binding('reference.json'); self.write_inventory()
            with self.subTest(megabytes=count): self.assertEqual(self.audit()['status'], expected)

    def test_clearing_recognition_cannot_hide_unresolved_source_evidence(self):
        self.inventory['unresolved'] = [{'id': 'unknown-subscript', 'category': 'math',
            'source_region': {'x': 10, 'y': 10, 'width': 20, 'height': 20}, 'reason': 'Unreadable index.'}]
        self.write_inventory()
        self.assertEqual(self.manifest['recognition']['unresolved'], [])
        self.assertEqual(validate(self.manifest, self.job)['status'], 'FAIL')
        self.assertEqual(self.audit()['coverage']['status'], 'partial')
        self.inventory['unresolved'] = []; self.inventory['components'][0]['reading_status'] = 'unresolved'
        self.write_inventory(); self.assertEqual(self.audit()['status'], 'FAIL')

    def test_every_bound_source_file_is_checked(self):
        for relative in ('inventory.json', 'reference.json', 'source.svg', 'source.png'):
            file = self.job / relative; content = file.read_bytes()
            try:
                file.write_bytes(content + b' changed')
                with self.subTest(relative=relative): self.assertEqual(self.audit()['status'], 'FAIL')
            finally:
                file.write_bytes(content)

    def test_cross_source_reference_is_rejected_even_with_updated_reference_hash(self):
        file = self.job / 'reference.json'; data = json.loads(file.read_text())
        data['source']['sha256'] = 'b' * 64; file.write_text(json.dumps(data))
        self.inventory['reference_manifest'] = self.binding('reference.json'); self.write_inventory()
        self.assertEqual(self.audit()['status'], 'FAIL')

    def test_path_escape_absolute_path_and_symlink_escape_fail(self):
        outside = self.root / 'outside.svg'; outside.write_bytes((self.job / 'source.svg').read_bytes())
        (self.job / 'escaped.svg').symlink_to(outside)
        for relative in ('../outside.svg', str(outside), 'escaped.svg', 'a\\b.svg', 'C:source.svg'):
            self.inventory['source_artifacts'][0]['path'] = relative; self.write_inventory()
            with self.subTest(relative=relative): self.assertEqual(self.audit()['status'], 'FAIL')

    def test_json_duplicate_keys_nonfinite_numbers_and_nonclosed_schema_fail(self):
        original = json.dumps(self.inventory)
        for raw in (original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                    original.replace('"x": 0', '"x": NaN', 1),
                    original.replace('"x": 0', '"x": 1e309', 1),
                    original[:-1] + ', "unexpected": true}', '[]', '{}'):
            self.write_inventory(raw)
            with self.subTest(raw=raw[:40]): self.assertEqual(self.audit()['status'], 'FAIL')

    def test_malformed_component_and_fabricated_live_text_claims_fail(self):
        original = copy.deepcopy(self.inventory)
        changes = [('category', []), ('representation', 'live_text'), ('object_ids', ['absent']),
                   ('object_ids', ['operator', 'operator']), ('limitations', []),
                   ('source_region', {'x': True, 'y': 0, 'width': 1, 'height': 1})]
        for key, value in changes:
            self.inventory = copy.deepcopy(original); self.inventory['components'][1][key] = value
            self.write_inventory()
            with self.subTest(key=key, value=value): self.assertEqual(self.audit()['status'], 'FAIL')
        self.inventory = copy.deepcopy(original); self.inventory['schema_version'] = True
        self.write_inventory(); self.assertEqual(self.audit()['status'], 'FAIL')

    def test_exact_unicode_reading_does_not_normalize_distinct_encodings(self):
        self.manifest['objects'][0]['text'] = 'e\u0301'; self.inventory['components'][0]['reading'] = '\u00e9'
        self.bind_reference(); self.assertEqual(self.audit()['status'], 'FAIL')
        self.inventory['components'][0]['reading'] = 'e\u0301'; self.write_inventory()
        self.assertEqual(self.audit()['status'], 'REVIEW')

    def test_compile_and_frozen_assets_retain_inventory_reference_and_source_artifacts(self):
        _, audit = compile_scene(self.manifest, self.job)
        self.assertEqual(audit['source_inventory'], self.audit())
        self.assertEqual(audit['hash_files'], self.audit()['hash_files'])
        run = self.root / 'run'; run.mkdir(); freeze_assets(self.job, run, self.manifest)
        (self.job / 'source.svg').write_bytes(b'changed current original')
        self.assertEqual(self.audit(root=run / 'assets')['status'], 'REVIEW')
        self.assertEqual(self.audit()['status'], 'FAIL')
        rejected = self.root / 'rejected-run'; rejected.mkdir()
        with self.assertRaisesRegex(ValueError, 'Source inventory'):
            freeze_assets(self.job, rejected, self.manifest)

    def test_semantic_connector_is_compared_before_materialization(self):
        self.manifest['objects'] = [path('a', [{'moveTo': {'x': 20, 'y': 40}}, {'lineTo': {'x': 40, 'y': 60}}]),
            path('b', [{'moveTo': {'x': 120, 'y': 40}}, {'lineTo': {'x': 140, 'y': 60}}]),
            {'id': 'flow', 'kind': 'connector', 'from': {'id': 'a', 'site': 'right'},
             'to': {'id': 'b', 'site': 'left'}, 'route': 'straight',
             'arrow': {'start': 'none', 'end': 'triangle'},
             'style': {'fill': 'none', 'stroke': '#000000', 'stroke_width': 1}}]
        self.inventory['components'] = [self.component('all', 'connection', ['a', 'b', 'flow'], 'native_geometry')]
        self.bind_reference()
        result = validate(self.manifest, self.job)
        self.assertEqual(result['status'], 'PASS', result['errors'])
        self.assertEqual(result['source_inventory']['status'], 'PASS')
        self.manifest['objects'][-1]['arrow'] = {'start': 'triangle', 'end': 'none'}
        self.assertEqual(validate(self.manifest, self.job)['status'], 'FAIL')

    def test_removing_inventory_invalidates_bound_model_review(self):
        self.manifest['recognition'].update(reviewed_digest=content_digest(self.manifest), reviewed_revision=1)
        verify_review(self.manifest)
        self.manifest.pop('source_inventory')
        with self.assertRaisesRegex(ValueError, 'stale'): verify_review(self.manifest)


if __name__ == '__main__':
    unittest.main()
