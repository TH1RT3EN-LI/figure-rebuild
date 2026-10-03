"""Postbuild review must describe the exact inspected and delivered bytes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from figure_rebuild import output_review as review


def digest(content):
    return hashlib.sha256(content).hexdigest()


class OutputReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.make_build('run-001')

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def make_build(self, name):
        run = self.root / name
        (run / 'assets/source').mkdir(parents=True)
        source = b'original reference image'
        (run / 'assets/source/original.png').write_bytes(source)
        manifest = {
            'id': 'fixture', 'revision': 1,
            'source': {'path': 'source/original.png', 'sha256': digest(source)},
            'objects': [],
        }
        self.write_json(run / 'manifest-snapshot.json', manifest)
        self.write_json(run / 'resolved-scene.json', manifest)
        (run / 'validated-output').mkdir()
        pptx = b'exact validated PPTX bytes'
        (run / 'validated-output/reconstruction.pptx').write_bytes(pptx)
        output = self.root / 'exports' / (name + '.pptx')
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(pptx)
        (run / 'preview-1x.png').write_bytes(b'actual 1x preview')
        (run / 'preview-2x.png').write_bytes(b'actual 2x preview')
        self.write_json(run / 'build-config.json', {
            'run': str(run), 'manifest': str(run / 'manifest-snapshot.json'),
            'asset_root': str(run / 'assets'), 'output': str(output),
        })
        self.write_json(run / 'delivery.json', {
            'output': str(output), 'sha256': digest(pptx),
            'source_sha256': digest(source), 'visual_acceptance': 'pending',
            'application_playback_verified': False,
        })
        return run

    def observed(self, run=None):
        record = review.prepare_output_review(run or self.run)
        model = record['model_review']
        model.update(performed=True, reviewer='calling-model',
                     method='Viewed reference and actual 1x/2x previews; enlarged arrowheads.',
                     status='no_observed_issue')
        model['inspected'] = [
            {'artifact': role, 'sha256': record['bindings'][role]['sha256'],
             'regions': ['full scene', 'top-right arrowheads']}
            for role in ('source', 'preview_1x', 'preview_2x')
        ]
        return record

    def finding(self, status='open'):
        return {'id': 'edge-1', 'severity': 'major', 'status': status,
                'description': 'Return arrow points the wrong way.',
                'region': 'Top-right feedback edge',
                'artifacts': ['source', 'preview_2x']}

    def test_preparation_is_not_an_inspection_or_acceptance(self):
        template = review.prepare_output_review(self.run)
        self.assertFalse(template['model_review']['performed'])
        self.assertEqual(template['user_acceptance'], {'status': 'pending'})
        self.assertEqual(template['native_application_verification'], {'status': 'not_verified'})
        with self.assertRaisesRegex(ValueError, 'actually performed'):
            review.record_output_review(self.run, template)
        self.assertFalse((self.run / 'output-review.json').exists())

    def test_clear_review_preserves_bindings_and_independent_statuses(self):
        record = self.observed()
        before = copy.deepcopy(record)
        saved = review.record_output_review(self.run, record)
        self.assertEqual(record, before)
        self.assertEqual(saved['bindings'], before['bindings'])
        self.assertIn('recorded_at', saved)
        self.assertEqual(json.loads((self.run / 'output-review.json').read_text()), saved)
        result = review.verify_output_review(self.run, saved, require_no_observed_issues=True)
        self.assertEqual(result, {'status': 'no_observed_issue', 'unresolved_findings': [],
                                 'user_acceptance': 'pending',
                                 'native_application_verification': 'not_verified'})

    def test_every_required_artifact_is_hashed_and_stale_bytes_fail(self):
        for role in ('source', 'manifest', 'resolved_scene', 'pptx',
                     'preview_1x', 'preview_2x', 'build_config', 'delivery'):
            with self.subTest(role=role):
                record = self.observed()
                path = Path(record['bindings'][role]['path'])
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                try:
                    with self.assertRaises(ValueError):
                        review.verify_output_review(self.run, record)
                finally:
                    path.write_bytes(original)

    def test_swapped_or_missing_previews_are_not_accepted(self):
        record = self.observed()
        a, b = self.run / 'preview-1x.png', self.run / 'preview-2x.png'
        first, second = a.read_bytes(), b.read_bytes()
        a.write_bytes(second)
        b.write_bytes(first)
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.verify_output_review(self.run, record)
        b.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            review.verify_output_review(self.run, record)

    def test_delivery_swap_fails_even_when_immutable_pptx_is_unchanged(self):
        record = self.observed()
        Path(record['bindings']['delivered_pptx']['path']).write_bytes(b'other export')
        with self.assertRaisesRegex(ValueError, 'do not match'):
            review.verify_output_review(self.run, record)

    def test_recording_does_not_refresh_bindings_after_inspection(self):
        record = self.observed()
        (self.run / 'preview-2x.png').write_bytes(b'new render after inspection')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.record_output_review(self.run, record)
        self.assertFalse((self.run / 'output-review.json').exists())

    def test_review_cannot_be_transplanted_to_another_identical_figure_build(self):
        record = self.observed()
        other = self.make_build('run-002')
        with self.assertRaisesRegex(ValueError, 'different build'):
            review.verify_output_review(other, record)

    def test_missing_extra_or_forged_binding_is_rejected(self):
        for change in ('missing', 'extra', 'wrong-path', 'wrong-hash'):
            with self.subTest(change=change):
                record = self.observed()
                if change == 'missing':
                    del record['bindings']['resolved_scene']
                elif change == 'extra':
                    record['bindings']['other'] = record['bindings']['source']
                elif change == 'wrong-path':
                    record['bindings']['preview_2x']['path'] = record['bindings']['preview_1x']['path']
                else:
                    record['bindings']['preview_2x']['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'stale'):
                    review.verify_output_review(self.run, record)

    def test_added_or_modified_optional_preview_invalidates_prior_scope(self):
        record = self.observed()
        path = self.run / 'preview-4x.png'
        path.write_bytes(b'extra preview')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.verify_output_review(self.run, record)
        record = self.observed()
        self.assertIn('preview_4x', record['bindings'])
        path.write_bytes(b'changed extra preview')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.verify_output_review(self.run, record)

    def test_open_findings_are_saved_but_fail_clear_review_gate(self):
        record = self.observed()
        record['model_review'].update(status='issues_found', findings=[self.finding()])
        saved = review.record_output_review(self.run, record)
        self.assertEqual(review.verify_output_review(self.run, saved)['unresolved_findings'], ['edge-1'])
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            review.verify_output_review(self.run, saved, require_no_observed_issues=True)
        saved['model_review']['status'] = 'no_observed_issue'
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            review.verify_output_review(self.run, saved)

    def test_resolved_findings_need_explanation_and_further_review_needs_reason(self):
        record = self.observed()
        record['model_review']['findings'] = [self.finding('resolved')]
        with self.assertRaisesRegex(ValueError, 'explanation'):
            review.verify_output_review(self.run, record)
        record['model_review']['findings'][0]['resolution'] = 'Reinspection shows a correct leftward head.'
        review.verify_output_review(self.run, record, require_no_observed_issues=True)
        record['model_review'].update(status='needs_further_review', findings=[])
        with self.assertRaisesRegex(ValueError, 'reason'):
            review.verify_output_review(self.run, record)
        record['model_review']['limitations'] = ['Tiny legend cannot yet be read.']
        self.assertEqual(review.verify_output_review(self.run, record)['status'], 'needs_further_review')
        with self.assertRaisesRegex(ValueError, 'further review'):
            review.verify_output_review(self.run, record, require_no_observed_issues=True)

    def test_inspection_needs_all_visual_artifacts_regions_and_exact_hashes(self):
        for change in ('missing-source', 'stale-hash', 'empty-regions', 'empty-reviewer',
                       'empty-method', 'unknown-artifact'):
            with self.subTest(change=change):
                record = self.observed()
                model = record['model_review']
                if change == 'missing-source':
                    model['inspected'].pop(0)
                elif change == 'stale-hash':
                    model['inspected'][1]['sha256'] = '0' * 64
                elif change == 'empty-regions':
                    model['inspected'][1]['regions'] = []
                elif change == 'empty-reviewer':
                    model['reviewer'] = ' '
                elif change == 'empty-method':
                    model['method'] = ''
                else:
                    model['inspected'][1]['artifact'] = 'a contact sheet from another run'
                with self.assertRaises(ValueError):
                    review.verify_output_review(self.run, record)

    def test_legacy_performed_method_and_automatic_acceptance_claims_are_rejected(self):
        with self.assertRaises(ValueError):
            review.record_output_review(self.run, {'performed': True, 'method': 'viewed previews'})
        for change in ('acceptance', 'app-claim', 'extra-acceptance', 'perfect'):
            with self.subTest(change=change):
                record = self.observed()
                if change == 'acceptance':
                    record['user_acceptance']['status'] = 'accepted'
                elif change == 'app-claim':
                    record['native_application_verification']['status'] = 'verified'
                elif change == 'extra-acceptance':
                    record['application_playback_verified'] = True
                else:
                    record['model_review']['status'] = 'perfect'
                with self.assertRaises(ValueError):
                    review.verify_output_review(self.run, record)

    def test_native_application_evidence_is_separate_hash_bound_and_not_user_acceptance(self):
        record = self.observed()
        screenshot = self.root / 'powerpoint-window.png'
        screenshot.write_bytes(b'independent native application screenshot')
        native = {
            'status': 'verified', 'application': 'PowerPoint', 'version': 'test-version',
            'reviewer': 'human-observer', 'method': 'Opened this PPTX and inspected its slide.',
            'pptx_sha256': record['bindings']['pptx']['sha256'],
            'evidence': [{'path': str(screenshot), 'sha256': digest(screenshot.read_bytes())}],
        }
        record['native_application_verification'] = native
        result = review.verify_output_review(self.run, record)
        self.assertEqual(result['native_application_verification'], 'verified')
        self.assertEqual(result['user_acceptance'], 'pending')
        native['pptx_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'different PPTX'):
            review.verify_output_review(self.run, record)
        native['pptx_sha256'] = record['bindings']['pptx']['sha256']
        native['evidence'] = [record['bindings']['preview_2x']]
        with self.assertRaisesRegex(ValueError, 'not native application'):
            review.verify_output_review(self.run, record)
        for mode in ('copy', 'hardlink'):
            duplicate = self.root / (mode + '.png')
            original = Path(record['bindings']['preview_2x']['path'])
            if mode == 'copy':
                shutil.copyfile(original, duplicate)
            else:
                os.link(original, duplicate)
            native['evidence'] = [{'path': str(duplicate), 'sha256': digest(duplicate.read_bytes())}]
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'not native application'):
                review.verify_output_review(self.run, record)
        native['evidence'] = [{'path': str(screenshot), 'sha256': digest(screenshot.read_bytes())}]
        screenshot.write_bytes(b'changed evidence')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.verify_output_review(self.run, record)

    def test_snapshot_escape_or_wrong_config_cannot_select_other_files(self):
        config_path = self.run / 'build-config.json'
        config = json.loads(config_path.read_text())
        config['manifest'] = str(self.root / 'manifest.json')
        self.write_json(config_path, config)
        with self.assertRaisesRegex(ValueError, 'immutable manifest'):
            review.prepare_output_review(self.run)

    def test_snapshot_symlink_escape_is_rejected(self):
        target = self.root / 'foreign.png'
        target.write_bytes(b'foreign preview')
        path = self.run / 'preview-2x.png'
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            review.prepare_output_review(self.run)

    def test_existing_review_cannot_be_overwritten(self):
        record = self.observed()
        review.record_output_review(self.run, record)
        destination = self.run / 'output-review.json'
        before = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            review.record_output_review(self.run, record)
        self.assertEqual(destination.read_bytes(), before)
        self.assertFalse(list(self.run.glob('.output-review.json-*')))

    def test_changed_artifact_during_staging_aborts_without_report(self):
        record = self.observed()
        real_stage = review.stage
        def changing_stage(destination, content):
            temporary = real_stage(destination, content)
            (self.run / 'preview-2x.png').write_bytes(b'changed during staging')
            return temporary
        with patch.object(review, 'stage', side_effect=changing_stage):
            with self.assertRaisesRegex(ValueError, 'stale'):
                review.record_output_review(self.run, record)
        self.assertFalse((self.run / 'output-review.json').exists())
        self.assertFalse(list(self.run.glob('.output-review.json-*')))

    def test_changed_artifact_at_publication_removes_only_new_report(self):
        record = self.observed()
        real_link = os.link
        def changing_link(source, destination):
            real_link(source, destination)
            (self.run / 'preview-2x.png').write_bytes(b'changed during publication')
        with patch.object(review.os, 'link', side_effect=changing_link):
            with self.assertRaisesRegex(ValueError, 'stale'):
                review.record_output_review(self.run, record)
        self.assertFalse((self.run / 'output-review.json').exists())

    def test_malformed_statuses_raise_value_errors(self):
        for target, field in (('model_review', 'status'), ('native_application_verification', 'status')):
            record = self.observed()
            record[target][field] = []
            with self.subTest(target=target), self.assertRaises(ValueError):
                review.verify_output_review(self.run, record)


if __name__ == '__main__':
    unittest.main()
