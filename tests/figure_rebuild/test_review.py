"""Content-bound review, immutable revisions, and concurrent run reservations."""
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ENTRY = Path(__file__).resolve().parents[2] / 'tools/figure_rebuild/review.py'
spec = importlib.util.spec_from_file_location('figure_review_tests', ENTRY)
review = importlib.util.module_from_spec(spec); spec.loader.exec_module(review)


class ReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {'schema_version': 1, 'id': 'test', 'revision': 1,
                         'source': {'path': 'source.png', 'sha256': 'f'*64},
                         'canvas': {'width': 100, 'height': 100},
                         'recognition': {'provider': 'calling_host', 'status': 'reviewed', 'notes': 'approximate'},
                         'objects': [{'id': 'line', 'kind': 'path', 'commands': []}]}
        self.bind(self.manifest)

    def bind(self, manifest):
        manifest['recognition']['reviewed_revision'] = manifest['revision']
        manifest['recognition']['reviewed_digest'] = review.content_digest(manifest)

    def snapshot(self, manifest, number):
        run = self.root / f'run-{number:03d}'; run.mkdir()
        (run / 'manifest-snapshot.json').write_text(json.dumps(manifest))

    def test_only_review_metadata_is_excluded_from_content_digest(self):
        expected = review.content_digest(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed['recognition'].update(review_note='different', reviewed_at='later', acceptance='pending')
        self.assertEqual(review.content_digest(changed), expected)
        for field, value in [('notes', 'changed provenance'), ('provider', 'svg_import'), ('unresolved', ['unreadable'])]:
            changed = copy.deepcopy(self.manifest); changed['recognition'][field] = value
            with self.subTest(field=field): self.assertNotEqual(review.content_digest(changed), expected)

    def test_content_and_revision_changes_invalidate_review(self):
        self.assertEqual(review.verify_review(self.manifest),review.content_digest(self.manifest))
        for key, value in [('revision', 2), ('objects', []), ('canvas', {'width': 101, 'height': 100})]:
            changed = copy.deepcopy(self.manifest); changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'stale'):
                review.verify_review(changed)

    def test_repeating_an_original_digest_allowed_even_after_a_newer_revision(self):
        self.snapshot(self.manifest,1)
        next_version=copy.deepcopy(self.manifest);next_version['revision']=2;next_version['objects']=[]
        self.bind(next_version);self.snapshot(next_version,2)
        self.assertEqual(review.check_revision_history(self.root,self.manifest),review.content_digest(self.manifest))

    def test_modified_or_nonincremented_revision_rejected(self):
        self.snapshot(self.manifest,1)
        changed=copy.deepcopy(self.manifest);changed['objects']=[];self.bind(changed)
        with self.assertRaisesRegex(ValueError,'increment revision'):review.check_revision_history(self.root,changed)
        changed['revision']=3;self.bind(changed)
        self.assertEqual(review.check_revision_history(self.root,changed),review.content_digest(changed))
        self.snapshot(changed,2)
        old=copy.deepcopy(self.manifest);old['revision']=2;old['canvas']['width']=102;self.bind(old)
        with self.assertRaisesRegex(ValueError,'greater'):review.check_revision_history(self.root,old)

    def test_malformed_snapshot_history_is_rejected(self):
        run=self.root/'run-001';run.mkdir();(run/'manifest-snapshot.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'Cannot trust'):review.check_revision_history(self.root,self.manifest)

    def test_changed_bound_snapshot_cannot_rewrite_revision_history(self):
        self.snapshot(self.manifest,1)
        path=self.root/'run-001/manifest-snapshot.json'
        altered=json.loads(path.read_text());altered['canvas']['width']=101
        path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError,'Cannot trust.*stale'):
            review.check_revision_history(self.root,self.manifest)

    def test_atomic_mkdir_allocations_are_unique_under_concurrency(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            runs=list(pool.map(lambda _:review.allocate_run(self.root),range(30)))
        self.assertEqual(len({p.name for p in runs}),30)
        self.assertTrue(all(p.is_dir() for p in runs))

    def test_revision_decision_and_snapshot_reservation_are_serialized(self):
        changed=copy.deepcopy(self.manifest);changed['objects']=[];self.bind(changed)
        def reserve(manifest):
            try:
                with review.allocation_lock(self.root):
                    review.check_revision_history(self.root,manifest)
                    run=review.allocate_run(self.root)
                    (run/'manifest-snapshot.json').write_text(json.dumps(manifest))
                return 'reserved'
            except ValueError:return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(reserve,[self.manifest,changed]))
        self.assertCountEqual(outcomes,['reserved','rejected'])

    def test_allocation_lock_releases_after_exception(self):
        with self.assertRaises(RuntimeError):
            with review.allocation_lock(self.root):raise RuntimeError('stopped')
        with review.allocation_lock(self.root,timeout=.2):review.allocate_run(self.root)


if __name__ == '__main__': unittest.main()
