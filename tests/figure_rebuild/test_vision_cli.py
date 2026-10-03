"""Exercise public diagnostic commands and non-overwriting publication."""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from PIL import Image, ImageDraw

ENTRY = Path(__file__).resolve().parents[2]/'tools/figure_rebuild/cli.py'
spec = importlib.util.spec_from_file_location('figure_vision_cli', ENTRY)
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
VISION = bool(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'))


class DiagnosticPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_complete_report_and_preview_are_published_without_overwrite(self):
        preview = self.root/'staged.png'; preview.write_bytes(b'synthetic preview')
        report, target = self.root/'report.json', self.root/'preview.png'
        cli.publish_diagnostic({'status': 'proposal'}, report, preview, target)
        self.assertEqual(json.loads(report.read_text()), {'status': 'proposal'})
        self.assertEqual(target.read_bytes(), preview.read_bytes())
        with self.assertRaises(FileExistsError): cli.publish_diagnostic({'changed': True}, report)
        self.assertEqual(json.loads(report.read_text()), {'status': 'proposal'})

    def test_preview_collision_rolls_back_owned_report_and_keeps_existing_bytes(self):
        preview = self.root/'staged.png'; preview.write_bytes(b'new')
        report, target = self.root/'report.json', self.root/'preview.png'
        target.write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            cli.publish_diagnostic({'status': 'proposal'}, report, preview, target)
        self.assertFalse(report.exists()); self.assertEqual(target.read_bytes(), b'old')
        self.assertFalse(list(self.root.glob('.report.json-*')))

    def test_invalid_json_never_creates_outputs(self):
        report = self.root/'invalid.json'
        with self.assertRaises(ValueError): cli.publish_diagnostic({'bad': float('nan')}, report)
        self.assertFalse(report.exists())

    @unittest.skipUnless(VISION, 'Optional vision dependencies not installed')
    def test_crop_command_retains_source_and_outputs_source_bound_proposal(self):
        source, output, preview = self.root/'source.png', self.root/'crop.json', self.root/'crop.png'
        image = Image.new('RGB', (90, 60), 'white')
        draw = ImageDraw.Draw(image); draw.rectangle((20, 15, 40, 35), fill='black')
        draw.point((50, 40), fill='black'); image.save(source)
        original = source.read_bytes()
        args = Namespace(input=str(source), output=str(output), preview=str(preview),
                         region=[10, 10, 60, 40], background=None, tolerance=18, padding=1, min_area=1)
        with redirect_stdout(io.StringIO()): cli.refine_crop_command(args)
        report = json.loads(output.read_text())
        self.assertEqual(report['proposed_region'], [19, 14, 33, 28])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(report['source']['sha256'], cli.digest(source))
        with Image.open(preview) as result: self.assertEqual(result.size, image.size)
        with self.assertRaises(ValueError): cli.refine_crop_command(args)

    def test_public_commands_refuse_input_replacement_before_computation(self):
        source = self.root/'source.png'; Image.new('RGB', (20, 20), 'white').save(source)
        original = source.read_bytes()
        args = Namespace(input=str(source), output=str(source), preview=None,
                         region=[0, 0, 20, 20], background=None, tolerance=18, padding=1, min_area=1)
        with self.assertRaisesRegex(ValueError, 'source image'): cli.refine_crop_command(args)
        args = Namespace(reference=str(source), rebuilt=str(source), output=str(source), max_shift=32)
        with self.assertRaisesRegex(ValueError, 'input image'): cli.diagnose_command(args)
        self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(VISION, 'Optional vision dependencies not installed')
    def test_diagnose_command_keeps_both_inputs_and_binds_report_hashes(self):
        source, target, output = self.root/'source.png', self.root/'target.png', self.root/'geometry.json'
        image = Image.new('RGB', (160, 120), 'white'); draw = ImageDraw.Draw(image)
        draw.rectangle((30, 25, 80, 50), fill='black'); draw.ellipse((90, 60, 112, 89), fill='navy')
        image.save(source)
        shifted = Image.new('RGB', image.size, 'white'); shifted.paste(image, (4, -3)); shifted.save(target)
        originals = source.read_bytes(), target.read_bytes()
        args = Namespace(reference=str(source), rebuilt=str(target), output=str(output), max_shift=32)
        with redirect_stdout(io.StringIO()): self.assertEqual(cli.diagnose_command(args), 0)
        report = json.loads(output.read_text())
        self.assertNotEqual(report['status'], 'unavailable')
        self.assertEqual(report['inputs']['reference']['sha256'], cli.digest(source))
        self.assertEqual((source.read_bytes(), target.read_bytes()), originals)

    def test_vision_probe_uses_requested_interpreter(self):
        result = cli.vision_capabilities(sys.executable)
        self.assertEqual(result['status'], 'available' if VISION else 'unavailable')

    def test_different_canvas_sizes_return_failure_with_report(self):
        source, target, output = self.root/'source.png', self.root/'target.png', self.root/'geometry.json'
        Image.new('RGB', (100, 80), 'white').save(source)
        Image.new('RGB', (120, 80), 'white').save(target)
        args = Namespace(reference=str(source), rebuilt=str(target), output=str(output), max_shift=32)
        with redirect_stdout(io.StringIO()): self.assertEqual(cli.diagnose_command(args), 1)
        report = json.loads(output.read_text())
        self.assertEqual(report['status'], 'failure')
        self.assertEqual(report['reason'], 'different_canvas_sizes')


if __name__ == '__main__': unittest.main()
