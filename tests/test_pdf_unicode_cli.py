"""Command boundary controls for independently proved PDF Unicode repair."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from figure_rebuild import cli


class PdfUnicodeCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'; self.config.mkdir()
        self.manifest = self.config / 'unicode.json'
        self.source = self.root / 'source.pdf'; self.source.write_bytes(b'original PDF')
        self.font = self.root / 'candidate.ttf'; self.font.write_bytes(b'original font')
        self.output = self.root / 'repaired.pdf'
        self.receipt = self.root / 'receipt.json'
        self.data = {'schema_version': 1,
                     'source': {'path': '../source.pdf', 'sha256': 'a' * 64},
                     'fonts': [{'font_xref': 14, 'embedded_program_sha256': 'b' * 64,
                                'candidate': {'path': '../candidate.ttf', 'sha256': 'c' * 64}}]}
        self.manifest.write_text(json.dumps(self.data))

    def invoke(self):
        arguments = ['figure-rebuild', 'repair-pdf-unicode', '--manifest', str(self.manifest),
                     '--output', str(self.output), '--receipt', str(self.receipt)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', arguments), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def backend(self, source, output, fonts):
        Path(output).write_bytes(b'new verified PDF')
        return {'status': 'PASS', 'repaired_mappings': [{'cid': 2, 'unicode': 0x1D465}]}

    def test_relative_inputs_preserve_declared_bindings_and_write_receipt(self):
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode', side_effect=self.backend) as api:
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 0, stderr)
        source, output, fonts = api.call_args.args
        self.assertEqual(source, {'path': str(self.source), 'sha256': 'a' * 64})
        self.assertEqual(output, self.output)
        self.assertEqual(fonts[0]['candidate'], {'path': str(self.font), 'sha256': 'c' * 64})
        self.assertEqual(fonts[0]['embedded_program_sha256'], 'b' * 64)
        self.assertEqual(json.loads(stdout), {'pdf': str(self.output), 'receipt': str(self.receipt)})
        self.assertEqual(json.loads(self.receipt.read_text())['repaired_mappings'][0]['unicode'], 0x1D465)
        self.assertEqual(self.source.read_bytes(), b'original PDF')

    def test_ambiguous_or_unbounded_manifest_never_calls_backend(self):
        cases = ['null', '[]', '{}', json.dumps({**self.data, 'schema_version': True}),
                 json.dumps({**self.data, 'schema_version': 1.0}),
                 json.dumps({**self.data, 'unexpected': {}}),
                 json.dumps(self.data).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                 json.dumps(self.data).replace('"font_xref": 14', '"font_xref": NaN'),
                 '{"schema_version":1,"source":{"path":"a","path":"b"},"fonts":[]}',
                 '[' * 1500 + ']' * 1500,
                 ' ' * 1048577]
        for raw in cases:
            with self.subTest(raw=raw[:70]):
                self.manifest.write_text(raw)
                with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode') as api:
                    code, stdout, stderr = self.invoke()
                self.assertEqual(code, 1); self.assertEqual(stdout, ''); self.assertTrue(stderr)
                api.assert_not_called(); self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_existing_destination_and_symlink_are_preserved(self):
        for destination in (self.output, self.receipt):
            with self.subTest(destination=destination):
                destination.write_bytes(b'keep')
                with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode') as api:
                    code, _, _ = self.invoke()
                self.assertEqual(code, 1); api.assert_not_called()
                self.assertEqual(destination.read_bytes(), b'keep'); destination.unlink()
        self.output.symlink_to(self.root / 'missing-target')
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode') as api:
            code, _, _ = self.invoke()
        self.assertEqual(code, 1); api.assert_not_called(); self.assertTrue(self.output.is_symlink())

    def test_output_and_receipt_cannot_alias_each_other(self):
        self.receipt = self.output
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode') as api:
            code, _, _ = self.invoke()
        self.assertEqual(code, 1); api.assert_not_called(); self.assertFalse(self.output.exists())

    def test_backend_refusal_does_not_publish_receipt(self):
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode', side_effect=ValueError('ambiguous exact cmap')):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1); self.assertEqual(stdout, ''); self.assertIn('ambiguous exact cmap', stderr)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_missing_optional_dependency_has_actionable_failure(self):
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode', side_effect=ImportError('no PyMuPDF')):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1); self.assertEqual(stdout, '')
        self.assertIn('figure-rebuild[source]', stderr)
        self.assertNotIn('Traceback', stderr)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())

    def test_receipt_write_failure_removes_only_new_output(self):
        original_open = Path.open
        def failing_open(path, *args, **kwargs):
            if path == self.receipt:
                raise OSError('receipt unavailable')
            return original_open(path, *args, **kwargs)
        with mock.patch('figure_rebuild.pdf_unicode.repair_pdf_unicode', side_effect=self.backend), \
                mock.patch.object(Path, 'open', failing_open):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1); self.assertEqual(stdout, ''); self.assertIn('receipt unavailable', stderr)
        self.assertFalse(self.output.exists()); self.assertFalse(self.receipt.exists())
        self.assertEqual(self.source.read_bytes(), b'original PDF')


if __name__ == '__main__':
    unittest.main()
