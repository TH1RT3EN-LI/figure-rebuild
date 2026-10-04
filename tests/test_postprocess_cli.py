"""Large geometry receipts must not exhaust the build subprocess's stdout."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from figure_rebuild import postprocess


class PostprocessCliTests(unittest.TestCase):
    def test_quiet_preserves_large_receipt_without_serializing_it_again(self):
        # CLIP exposed a valid compact proof exceeding16MiB. The default Node
        # stdout buffer is much smaller; raising it only postpones the failure.
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'editability.json'
            data = {'native_winding_fills': ['proof' * 3_400_000]}
            expected = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
            self.assertGreater(len(expected), 16 * 1024 * 1024)

            def process(*args, **kwargs):
                Path(args[3]).write_text(expected)
                return data

            stdout = StringIO()
            with patch.object(postprocess, 'process', side_effect=process) as called, \
                    patch.object(postprocess.json, 'dumps', side_effect=AssertionError('duplicate serialization')), \
                    redirect_stdout(stdout):
                self.assertEqual(postprocess.main([
                    '--input', 'input.pptx', '--output', 'output.pptx',
                    '--manifest', 'scene.json', '--receipt', str(receipt),
                    '--object-map', 'objects.json', '--asset-root', 'assets',
                    '--placement', '0', '0', '1026', '492', '--quiet']), 0)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(receipt.read_text(), expected)
            called.assert_called_once_with('input.pptx', 'output.pptx', 'scene.json', str(receipt),
                                           object_map='objects.json', asset_root='assets',
                                           occupied_placement=[0.0, 0.0, 1026.0, 492.0])

    def test_default_stdout_remains_the_complete_json_result(self):
        data = {'native_objects': [{'id': '标签'}], 'warnings': []}
        stdout = StringIO()
        with patch.object(postprocess, 'process', return_value=data), redirect_stdout(stdout):
            self.assertEqual(postprocess.main(['--input', 'in', '--output', 'out',
                                              '--manifest', 'scene', '--receipt', 'receipt']), 0)
        self.assertEqual(json.loads(stdout.getvalue()), data)

    def test_quiet_does_not_hide_processing_or_receipt_write_failures(self):
        with patch.object(postprocess, 'process', side_effect=OSError('receipt write failed')):
            with self.assertRaisesRegex(OSError, 'receipt write failed'):
                postprocess.main(['--input', 'in', '--output', 'out', '--manifest', 'scene',
                                  '--receipt', 'receipt', '--quiet'])


if __name__ == '__main__':
    unittest.main()
