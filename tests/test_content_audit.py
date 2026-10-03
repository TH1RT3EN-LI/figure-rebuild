"""Source-grounded regressions: no inferred spelling, topology or acceptance."""
import copy
import unittest

from figure_rebuild.content_audit import audit_source_content
from figure_rebuild.scene_compile import compile_scene


SOURCE_SHA = 'a' * 64
REGION = {'x': 10, 'y': 10, 'width': 180, 'height': 80}


def text(identity='label', content='fsih', x=10, y=10):
    return {'id': identity, 'kind': 'text', 'text': content, 'font_size': 12,
            'box': {'x': x, 'y': y, 'width': 70, 'height': 20},
            'style': {'fill': '#000000'}}


def literal(identity='reading', ids=None, content='fsih', status='confirmed', **extra):
    return {'id': identity, 'status': status, 'object_ids': ids or ['label'],
            'text': content, 'source_region': copy.deepcopy(REGION), **extra}


def scene(objects=None, literals=None, connections=None):
    return {'schema_version': 1, 'id': 'test', 'revision': 1,
            'source': {'sha256': SOURCE_SHA}, 'canvas': {'width': 300, 'height': 200},
            'recognition': {'status': 'reviewed', 'unresolved': []},
            'objects': objects if objects is not None else [text()],
            'source_evidence': {'schema_version': 1, 'source_sha256': SOURCE_SHA,
                                'literals': literals if literals is not None else [literal()],
                                'connections': connections or []}}


def rectangle(identity, x):
    return {'id': identity, 'kind': 'path',
            'commands': [{'moveTo': {'x': x, 'y': 50}},
                         {'lineTo': {'x': x + 50, 'y': 50}},
                         {'lineTo': {'x': x + 50, 'y': 80}},
                         {'lineTo': {'x': x, 'y': 80}}, {'close': {}}],
            'style': {'fill': '#FFFFFF', 'stroke': '#000000', 'stroke_width': 1}}


def connected_scene():
    edge = {'id': 'flow', 'kind': 'connector', 'from': {'id': 'a', 'site': 'right'},
            'to': {'id': 'b', 'site': 'left'}, 'route': 'straight',
            'arrow': {'start': 'none', 'end': 'triangle'},
            'style': {'fill': 'none', 'stroke': '#000000', 'stroke_width': 2}}
    evidence = {'id': 'flow-reading', 'status': 'confirmed', 'object_id': 'flow',
                'source_region': copy.deepcopy(REGION),
                **{key: copy.deepcopy(edge[key]) for key in ('from', 'to', 'arrow')}}
    authored = scene([rectangle('a', 20), rectangle('b', 180), edge], [], [evidence])
    return compile_scene(authored, '.')[0]


class SourceContentAudit(unittest.TestCase):
    def codes(self, report):
        return {item['code'] for item in report['mismatches'] + report['unresolved'] + report['diagnostics']}

    def test_exact_source_literals_are_not_autocorrected(self):
        for expected, wrong in [('fsih', 'fish'), ('pos_min = -3', 'pos_min = 3'),
                                ('_build_version_info(version)', '_parse_version(version)'),
                                ('>', '→'), ('a b', 'ab'), ('fi', '\ufb01')]:
            with self.subTest(expected=expected):
                data = scene([text(content=expected)], [literal(content=expected)])
                self.assertEqual(audit_source_content(data)['status'], 'PASS')
                data['objects'][0]['text'] = wrong
                before = copy.deepcopy(data)
                report = audit_source_content(data)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['mismatches'][0]['expected'], expected)
                self.assertEqual(report['mismatches'][0]['actual'], wrong)
                self.assertEqual(data, before)

    def test_fragment_order_and_whitespace_are_explicit(self):
        data = scene([text('one', 'a '), text('two', 'b', x=90)],
                     [literal(ids=['one', 'two'], content='a b')])
        self.assertEqual(audit_source_content(data)['status'], 'PASS')
        data['source_evidence']['literals'][0]['object_ids'].reverse()
        self.assertIn('literal_mismatch', self.codes(audit_source_content(data)))

    def test_only_final_objects_are_checked(self):
        data = scene()
        data['manifest_snapshot'] = {'objects': copy.deepcopy(data['objects'])}
        data['objects'][0]['text'] = 'fish'
        self.assertIn('literal_mismatch', self.codes(audit_source_content(data)))

    def test_hidden_old_literal_cannot_prove_visible_replacement(self):
        data = scene()
        data['objects'][0]['style']['opacity'] = 0
        data['objects'].append(text('replacement', 'fish'))
        self.assertIn('target_not_visible', self.codes(audit_source_content(data)))

    def test_outlines_with_untrusted_source_text_do_not_satisfy_literal(self):
        obj = rectangle('label', 20)
        obj['source_text'] = 'fsih'
        report = audit_source_content(scene([obj]))
        self.assertIn('literal_requires_live_text', self.codes(report))

    def test_missing_objects_and_duplicate_ids_fail(self):
        for data, code in [(scene([]), 'missing_object'),
                           (scene([text(), text()]), 'duplicate_object_id'),
                           (scene(literals=[literal(), literal()]), 'duplicate_evidence_id'),
                           (scene(literals=[literal(ids=['label', 'label'])]), 'invalid_evidence_targets')]:
            with self.subTest(code=code):
                self.assertIn(code, self.codes(audit_source_content(data)))

    def test_missing_or_stale_source_binding_fails(self):
        for checksum in [None, '', 'b' * 64, 'A' * 64, 123]:
            data = scene(); data['source_evidence']['source_sha256'] = checksum
            self.assertIn('source_sha_mismatch', self.codes(audit_source_content(data)))

    def test_invalid_evidence_fields_and_statuses_fail_closed(self):
        mutations = [('status', 'accepted'), ('status', True), ('text', ''), ('text', None),
                     ('expected', 'guess'), ('object_ids', 'label'), ('reason', []), ('resolves', [False])]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                data = scene(); data['source_evidence']['literals'][0][key] = value
                self.assertEqual(audit_source_content(data)['status'], 'FAIL')
        for evidence in [None, [], {'schema_version': True}, {'schema_version': 1, 'typo': []}]:
            data = scene(); data['source_evidence'] = evidence
            self.assertEqual(audit_source_content(data)['status'], 'FAIL')

    def test_invalid_source_regions_fail_closed(self):
        for region in [None, [], {}, {'x': 10, 'y': 10, 'width': 0, 'height': 10},
                       {'x': -1, 'y': 10, 'width': 20, 'height': 10},
                       {'x': 290, 'y': 10, 'width': 20, 'height': 10},
                       {'x': 0, 'y': 0, 'width': float('nan'), 'height': 10},
                       {'x': True, 'y': 0, 'width': 10, 'height': 10}]:
            data = scene(); data['source_evidence']['literals'][0]['source_region'] = region
            self.assertIn('invalid_source_region', self.codes(audit_source_content(data)))

    def test_absent_evidence_is_not_content_acceptance(self):
        data = scene(); data.pop('source_evidence')
        report = audit_source_content(data)
        self.assertEqual(report['status'], 'NOT_PROVIDED')
        self.assertFalse(report['history_checked'])
        self.assertEqual(report['structural_completion'], 'not_evaluated')
        self.assertEqual(report['model_review'], 'not_evaluated')
        self.assertEqual(report['user_acceptance'], 'pending')

    def test_unknowns_are_not_cleared_by_recognition_review(self):
        data = scene(literals=[literal(status='unresolved')])
        report = audit_source_content(data)
        self.assertIn('source_reading_unresolved', self.codes(report))
        self.assertEqual(report['status'], 'FAIL')
        data['recognition']['unresolved'] = ['unreadable API']
        self.assertIn('recognition_unresolved', self.codes(audit_source_content(data)))

    def test_removed_legacy_unknown_requires_matching_explicit_resolution(self):
        old = scene(); old['recognition']['unresolved'] = [{'id': 'code-reading', 'reason': 'uncertain minus'}]
        current = scene()
        self.assertIn('unknown_removed_without_evidence', self.codes(audit_source_content(current, previous_manifest=old)))
        current['source_evidence']['literals'][0]['resolves'] = ['code-reading']
        self.assertEqual(audit_source_content(current, previous_manifest=old)['status'], 'PASS')
        current['objects'][0]['text'] = 'fish'
        self.assertIn('unknown_removed_without_evidence', self.codes(audit_source_content(current, previous_manifest=old)))

    def test_removed_source_unknown_requires_explicit_resolution_even_same_id(self):
        old = scene(literals=[literal(status='unresolved')]); current = scene()
        self.assertIn('unknown_removed_without_evidence', self.codes(audit_source_content(current, previous_manifest=old)))
        current['source_evidence']['literals'][0]['resolves'] = ['reading']
        self.assertEqual(audit_source_content(current, previous_manifest=old)['status'], 'PASS')
        current['source_evidence']['literals'] = []
        self.assertIn('unknown_removed_without_evidence', self.codes(audit_source_content(current, previous_manifest=old)))

    def test_resolution_with_wrong_sha_or_invalid_region_does_not_clear_unknown(self):
        old = scene(); old['recognition']['unresolved'] = ['unreadable']
        for mutation in ('sha', 'region'):
            current = scene(literals=[literal(resolves=['unreadable'])])
            if mutation == 'sha': current['source_evidence']['source_sha256'] = 'b' * 64
            else: current['source_evidence']['literals'][0]['source_region']['x'] = -1
            self.assertIn('unknown_removed_without_evidence', self.codes(audit_source_content(current, previous_manifest=old)))

    def test_unrelated_reading_cannot_clear_typed_unknown(self):
        old = scene(literals=[literal(status='unresolved')])
        current = scene(literals=[literal(resolves=['reading'])])
        current['source_evidence']['literals'][0]['source_region'] = {
            'x': 200, 'y': 120, 'width': 50, 'height': 30}
        self.assertIn('unknown_removed_without_evidence',
                      self.codes(audit_source_content(current, previous_manifest=old)))
        current = scene([text('unrelated')], [literal(ids=['unrelated'], resolves=['reading'])])
        self.assertIn('unknown_removed_without_evidence',
                      self.codes(audit_source_content(current, previous_manifest=old)))
        current = scene(literals=[literal(resolves=['reading'])])
        self.assertEqual(audit_source_content(current, previous_manifest=old)['status'], 'PASS')

    def test_duplicate_text_is_diagnostic_and_never_deleted(self):
        data = scene([text(), text('copy', x=10.5)])
        before = copy.deepcopy(data); report = audit_source_content(data)
        self.assertEqual(report['status'], 'REVIEW')
        self.assertIn('possible_duplicate_live_text', self.codes(report))
        self.assertEqual(report['diagnostics'][0]['automatic_action'], 'none')
        self.assertEqual(data, before)
        data['objects'][1]['box']['x'] = 100
        self.assertEqual(audit_source_content(data)['status'], 'PASS')

    def test_final_resolved_attachment_positions_drive_duplicate_check(self):
        data = scene([rectangle('node', 100), text(), text('copy', x=200)], [])
        for obj in data['objects'][1:]:
            obj['attach_to'] = {'id': 'node', 'site': 'center', 'offset': {'x': 0, 'y': 0}}
        resolved, _ = compile_scene(data, '.')
        self.assertIn('possible_duplicate_live_text', self.codes(audit_source_content(resolved)))

    def test_confirmed_topology_uses_materialized_connection(self):
        data = connected_scene(); before = copy.deepcopy(data)
        report = audit_source_content(data)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['coverage']['connections_checked'], 1)
        self.assertEqual(data, before)

    def test_reversed_direction_and_changed_arrow_fail(self):
        for mutation in ('direction', 'arrow'):
            data = connected_scene(); actual = data['objects'][-1]['connection_record']
            if mutation == 'direction': actual['from'], actual['to'] = actual['to'], actual['from']
            else: actual['arrow']['end'] = 'none'
            self.assertIn('connection_mismatch', self.codes(audit_source_content(data)))

    def test_metadata_cannot_hide_mutated_actual_commands(self):
        data = connected_scene()
        data['objects'][-1]['commands'][-1]['lineTo']['x'] += 20
        self.assertIn('connection_materialization_mismatch', self.codes(audit_source_content(data)))

    def test_matching_stale_commands_and_record_do_not_hide_moved_endpoint(self):
        data = connected_scene()
        for command in data['objects'][1]['commands']:
            for point in command.values():
                if 'x' in point: point['x'] += 10
        self.assertIn('connection_geometry_mismatch', self.codes(audit_source_content(data)))

    def test_changed_paint_and_plain_arrow_path_cannot_pass_topology(self):
        data = connected_scene(); data['objects'][-1]['style']['stroke_width'] = 0
        self.assertIn('connection_materialization_mismatch', self.codes(audit_source_content(data)))
        data = connected_scene(); data['objects'][-1].pop('connection_record')
        self.assertIn('connection_requires_materialized_record', self.codes(audit_source_content(data)))

    def test_previous_different_source_cannot_be_silently_compared(self):
        old = scene(); old['source']['sha256'] = 'b' * 64
        self.assertIn('previous_source_sha_mismatch', self.codes(audit_source_content(scene(), previous_manifest=old)))

    def test_malformed_previous_unknown_cannot_disappear_as_pass(self):
        for mutation in ('id', 'status', 'source_sha256'):
            old = scene(literals=[literal(status='unresolved')])
            if mutation == 'id': old['source_evidence']['literals'][0].pop('id')
            elif mutation == 'status': old['source_evidence']['literals'][0]['status'] = 'forgotten'
            else: old['source_evidence']['source_sha256'] = 'b' * 64
            self.assertIn('invalid_previous_evidence', self.codes(audit_source_content(scene(), previous_manifest=old)))

    def test_hostile_numeric_bounds_and_endpoint_types_return_reports(self):
        data = scene(); data['canvas']['width'] = 10 ** 400
        self.assertIn('invalid_source_region', self.codes(audit_source_content(data)))
        data = scene(); data['objects'][0]['box']['x'] = 1e308
        report = audit_source_content(data, duplicate_tolerance=1e-9)
        self.assertIn('duplicate_position_out_of_range', self.codes(report))
        data = connected_scene(); data['source_evidence']['connections'][0]['from']['site'] = []
        self.assertIn('invalid_expected_connection', self.codes(audit_source_content(data)))

    def test_hidden_endpoint_cannot_satisfy_source_connection(self):
        data = connected_scene(); data['objects'][0]['style']['opacity'] = 0
        self.assertIn('connection_target_not_visible', self.codes(audit_source_content(data)))


if __name__ == '__main__':
    unittest.main()
