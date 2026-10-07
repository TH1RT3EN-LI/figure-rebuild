"""Public command input, delegation and failure status; no external papers."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import venv

from figure_rebuild import cli
from figure_rebuild.source_fidelity import (
    PdfSourceDescriptor, SourceReplayPolicy, ReplayLimits, replay_pdf_source,
)
if __package__:
    from .test_pdf_source_replay import fitz, source_fixture
    from .test_pptx_fidelity import make_ppt
else:
    from test_pdf_source_replay import fitz, source_fixture
    from test_pptx_fidelity import make_ppt


class SourceFidelityCliInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'; self.config.mkdir()
        self.descriptor = self.config / 'source.json'
        self.source = {'pdf_path': '../source.pdf', 'pdf_sha256': '0' * 64,
                       'page': 1, 'region': [0, 0, 10, 10], 'scale': 2}
        self.descriptor.write_text(json.dumps(self.source))
        self.evidence = self.root / 'evidence'
        self.args = ['verify-source-fidelity', '--source-descriptor', str(self.descriptor),
                     '--manifest', str(self.root / 'manifest.json'),
                     '--resolved-scene', str(self.root / 'resolved.json'),
                     '--asset-root', str(self.root / 'assets'),
                     '--pptx', str(self.root / 'actual.pptx'),
                     '--evidence-dir', str(self.evidence)]

    def invoke(self, extra=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', ['figure-rebuild', *self.args, *extra]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_exact_typed_delegation_and_both_path_bases(self):
        self.source.update(reference_png_path='../reference.png', reference_png_sha256='1' * 64)
        self.descriptor.write_text(json.dumps(self.source))
        policy = self.config / 'policy.json'; policy.write_text('{"max_clip_overhang": 0}')
        limits = self.config / 'limits.json'; limits.write_text('{"max_commands": 123}')
        for key, value in [('--manifest', 'relative-manifest.json'), ('--evidence-dir', 'new-evidence')]:
            self.args[self.args.index(key) + 1] = value
        report = {'status': 'VERIFIED_IN_DECLARED_SCOPE', 'semantic_recognition': 'NOT_PROVIDED',
                  'visual_acceptance': 'NOT_EVALUATED'}
        with mock.patch('figure_rebuild.source_fidelity.audit_source_fidelity', return_value=report) as api:
            code, output, error = self.invoke(['--policy', str(policy), '--limits', str(limits)])
        self.assertEqual(code, 0, error); self.assertEqual(json.loads(output), report)
        source = api.call_args.args[0]; kw = api.call_args.kwargs
        self.assertIsInstance(source, PdfSourceDescriptor)
        self.assertEqual(source.pdf_path, str(self.root / 'source.pdf'))
        self.assertEqual(source.reference_png_path, str(self.root / 'reference.png'))
        self.assertEqual(kw['manifest_path'], Path.cwd() / 'relative-manifest.json')
        self.assertEqual(kw['evidence_dir'], Path.cwd() / 'new-evidence')
        self.assertEqual(kw['policy'], SourceReplayPolicy(max_clip_overhang=0))
        self.assertEqual(kw['limits'], ReplayLimits(max_commands=123))
        self.assertFalse(self.evidence.exists())

    def test_only_exact_verified_status_exits_zero(self):
        for status in ['FAIL', 'UNRESOLVED', 'NOT_PROVIDED', 'PASS', 'unknown', None]:
            with self.subTest(status=status), mock.patch(
                    'figure_rebuild.source_fidelity.audit_source_fidelity', return_value={'status': status}):
                code, output, error = self.invoke()
            self.assertEqual(code, 1); self.assertEqual(json.loads(output)['status'], status)

    def test_strict_json_rejects_unknown_missing_duplicates_nonfinite_and_nonrecords(self):
        cases = ['null', '[]', 'true', '{}', '{"unexpected":1}',
                 '{"page":1,"page":2}', '{"region":{"x":1,"x":2}}',
                 '{"page":NaN}', '{"page":Infinity}', '{"page":1e400}',
                 '{"page":' + '1' + '0' * 400 + '}', '[' * 1500 + ']' * 1500]
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                self.descriptor.write_text(raw)
                with mock.patch('figure_rebuild.source_fidelity.audit_source_fidelity') as api:
                    code, output, error = self.invoke()
                self.assertEqual(code, 1); self.assertEqual(output, ''); self.assertTrue(error)
                api.assert_not_called(); self.assertFalse(self.evidence.exists())

    def test_descriptor_paths_cannot_be_empty_boolean_or_null(self):
        for key, value in [('pdf_path', ''), ('pdf_path', True), ('pdf_path', None),
                           ('reference_png_path', ''), ('reference_png_path', False)]:
            with self.subTest(key=key, value=value):
                self.descriptor.write_text(json.dumps({**self.source, key: value}))
                with mock.patch('figure_rebuild.source_fidelity.audit_source_fidelity') as api:
                    code, output, error = self.invoke()
                self.assertEqual(code, 1); api.assert_not_called()

    def test_api_numeric_types_reject_boolean_and_float_integer_before_evidence(self):
        for key, value in [('page', True), ('page', 1.0), ('scale', False),
                           ('region', [False, 0, 10, 10]),
                           ('region', [0, 0, 1e308, 1e308])]:
            with self.subTest(key=key, value=value):
                self.descriptor.write_text(json.dumps({**self.source, key: value}))
                code, output, error = self.invoke()
                self.assertEqual(code, 1); self.assertEqual(output, '')
                self.assertFalse(self.evidence.exists()); self.assertNotIn('Traceback', error)

    def test_policy_and_limits_are_strict_and_use_typed_api_validation(self):
        cases = [('--policy', {'unknown': 1}), ('--limits', {'unknown': 1}),
                 ('--policy', {'native_occurrence_rendering': 1}),
                 ('--policy', {'max_clip_overhang': True}),
                 ('--policy', {'transformations': {}}),
                 ('--limits', {'max_commands': True}), ('--limits', {'max_commands': 1.0}),
                 ('--limits', {'timeout_seconds': False}), ('--limits', [])]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                path = self.config / 'optional.json'; path.write_text(json.dumps(value))
                code, output, error = self.invoke([flag, str(path)])
                self.assertEqual(code, 1); self.assertFalse(self.evidence.exists())
                self.assertNotIn('Traceback', error)

    def test_json_byte_limit_and_encoding_are_enforced(self):
        for raw in [b' ' * 1048577, json.dumps(self.source).encode('utf-16')]:
            self.descriptor.write_bytes(raw)
            with mock.patch('figure_rebuild.source_fidelity.audit_source_fidelity') as api:
                code, output, error = self.invoke()
            self.assertEqual(code, 1); api.assert_not_called()

    def test_existing_evidence_is_not_overwritten(self):
        self.evidence.mkdir(); marker = self.evidence / 'keep'; marker.write_text('original')
        code, output, error = self.invoke()
        self.assertEqual(code, 1); self.assertEqual(output, '')
        self.assertEqual(marker.read_text(), 'original')
        self.assertEqual(list(self.evidence.iterdir()), [marker])

    def test_report_publication_failure_cannot_print_verified(self):
        with mock.patch('figure_rebuild.source_fidelity._save',
                        side_effect=OSError('injected final report write failure')):
            code, output, error = self.invoke()
        self.assertEqual(code, 1); self.assertEqual(output, '')
        self.assertIn('write failure', error)

    def test_missing_required_argument_is_parser_error(self):
        self.args = ['verify-source-fidelity']
        with self.assertRaises(SystemExit) as caught: self.invoke()
        self.assertEqual(caught.exception.code, 2)

    def test_real_no_optional_dependency_cli_is_unresolved(self):
        # A new isolated interpreter keeps dependencies absent in the worker too.
        envdir = self.root / 'no-source-deps'; venv.EnvBuilder(with_pip=False).create(envdir)
        (self.root / 'source.pdf').write_bytes(b'%PDF-1.4\n%fixture\n')
        self.source['pdf_sha256'] = hashlib.sha256((self.root / 'source.pdf').read_bytes()).hexdigest()
        self.descriptor.write_text(json.dumps(self.source))
        for name in ('manifest.json', 'resolved.json'): (self.root / name).write_text('{}')
        env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]), PYTHONNOUSERSITE='1')
        run = subprocess.run([str(envdir / 'bin/python'), '-m', 'figure_rebuild', *self.args],
                             cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 1, run.stderr)
        report = json.loads(run.stdout); self.assertEqual(report['status'], 'UNRESOLVED')
        self.assertIn('ModuleNotFoundError', json.dumps(report))
        self.assertIn('pymupdf', json.dumps(report))
        self.assertEqual(json.loads((self.evidence / 'source-fidelity.json').read_text()), report)
        self.assertEqual(report['semantic_recognition'], 'NOT_PROVIDED')


@unittest.skipIf(fitz is None, 'optional PyMuPDF unavailable')
class SourceFidelityCliActual(unittest.TestCase):
    def test_real_generated_pdf_to_ppt_cli_binding_and_failure(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); source = source_fixture(root)
            assets = root / 'source-replay'
            replay = replay_pdf_source(source, evidence_dir=assets)
            self.assertEqual(replay['status'], 'VERIFIED_IN_DECLARED_SCOPE', replay)
            expected = json.loads((assets / 'expected-scene.json').read_text())
            scene = {'canvas': expected['canvas'], 'objects': expected['objects'],
                     'source': {'path': 'fresh-source.png', 'sha256': expected['source_sha256']}}
            manifest = root / 'manifest.json'; manifest.write_text(json.dumps(scene))
            ppt = root / 'actual.pptx'; make_ppt(ppt, scene, assets)
            config = root / 'config'; config.mkdir()
            descriptor = config / 'source.json'
            descriptor.write_text(json.dumps({**asdict(source), 'pdf_path': '../' + Path(source.pdf_path).name,
                                             'reference_png_path': '../source-replay/fresh-source.png',
                                             'reference_png_sha256': expected['source_sha256']}))
            env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).resolve().parents[1]))
            for label in ('positive', 'wrong-source-digest'):
                if label != 'positive':
                    data = json.loads(descriptor.read_text()); data['pdf_sha256'] = '0' * 64
                    descriptor.write_text(json.dumps(data))
                evidence = root / label
                run = subprocess.run([sys.executable, '-m', 'figure_rebuild', 'verify-source-fidelity',
                    '--source-descriptor', 'config/source.json', '--manifest', 'manifest.json',
                    '--resolved-scene', 'manifest.json', '--asset-root', 'source-replay',
                    '--pptx', 'actual.pptx', '--evidence-dir', label], cwd=root, env=env,
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0 if label == 'positive' else 1, run.stderr)
                report = json.loads(run.stdout)
                self.assertEqual(report, json.loads((evidence / 'source-fidelity.json').read_text()))
                self.assertEqual(report['status'], 'VERIFIED_IN_DECLARED_SCOPE' if label == 'positive' else 'FAIL')
                self.assertEqual(report['semantic_recognition'], 'NOT_PROVIDED')
                self.assertEqual(report['visual_acceptance'], 'NOT_EVALUATED')
                if label == 'positive':
                    self.assertEqual(report['bindings']['actual_pptx']['sha256'], hashlib.sha256(ppt.read_bytes()).hexdigest())
                    self.assertEqual(report['bindings']['manifest']['sha256'], hashlib.sha256(manifest.read_bytes()).hexdigest())


if __name__ == '__main__': unittest.main()
