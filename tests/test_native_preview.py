"""Native rendering must bind final bytes and fail on substitution or stale output."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from figure_rebuild import native_preview as native
from tests.test_package import fixture, slide, write_archive

try:
    import pymupdf
except ImportError:
    pymupdf = None


class NativeProfileTests(unittest.TestCase):
    def test_profile_is_explicit_and_rejects_unknown_or_owned_options(self):
        valid = {'command': [sys.executable], 'fc_match': sys.executable}
        self.assertEqual(native.validate_profile(valid, Path.cwd())['timeout_seconds'], 120)
        cases = [dict(valid, typo=True), dict(valid, command='soffice'),
                 dict(valid, command=[sys.executable, '-env:UserInstallation=file:///tmp/shared']),
                 dict(valid, timeout_seconds=True), dict(valid, timeout_seconds=float('nan')),
                 dict(valid, environment={'FONTCONFIG_FILE': '/tmp/wrong'}),
                 dict(valid, fc_match='/missing/fc-match')]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                native.validate_profile(value, Path.cwd())
        for value in ('auto', None, True, []):
            with self.assertRaises(ValueError):
                native.validate_backend(value)

    def test_timeout_kills_owned_process_and_leaves_failed_command_evidence(self):
        commands = []
        with self.assertRaisesRegex(ValueError, 'timed out'):
            native._execute([sys.executable, '-c', 'import time; time.sleep(5)'], {}, .05, commands)
        self.assertTrue(commands[0]['timed_out'])
        self.assertNotEqual(commands[0]['returncode'], 0)


@unittest.skipUnless(pymupdf, 'optional PyMuPDF dependency')
class NativeRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name)
        (self.run / 'validated-output').mkdir()
        self.pptx = self.run / 'validated-output/reconstruction.pptx'
        self.files = fixture([(1001, 'ppt/slides/odd.xml', slide([]))], size=(200 * 9525, 100 * 9525))
        write_archive(self.pptx, self.files)
        (self.run / 'fonts').mkdir()
        font = self.run / 'fonts/regular.ttf'
        font.write_bytes(b'font bytes: real Fontconfig checked by external integration')
        self.audit = [{'family': 'Test Family', 'role': 'regular', 'bold': False, 'italic': False,
                       'renderer': str(font), 'renderer_sha256': native.binding(font)['sha256'],
                       'postscript_names': ['TestFamily-Regular']}]
        bold = self.run / 'fonts/bold.ttf'
        bold.write_bytes(b'independent registered bold font bytes')
        self.audit.append({'family': 'Test Family', 'role': 'bold', 'bold': True, 'italic': False,
                           'renderer': str(bold), 'renderer_sha256': native.binding(bold)['sha256'],
                           'postscript_names': ['TestFamily-Bold']})
        (self.run / 'font-audit.json').write_text(json.dumps(self.audit))
        self.config = {'run': str(self.run), 'preview_backend': 'libreoffice',
                       'runtime': {'native_preview': {'command': [sys.executable], 'fc_match': sys.executable}}}
        self.mode = 'ok'

    def command(self, command, environment, timeout, commands):
        output = ''
        if '-f' in command:
            selected = self.run / ('native-preview/fonts/1.ttf' if 'weight=200' in command[-1] else 'native-preview/fonts/0.ttf')
            if self.mode == 'substitute':
                selected = self.run / 'fonts/regular.ttf'
            output = str(selected) + '\n0\n'
        elif '--version' in command:
            output = 'LibreOffice 26.2.5.2 test\n'
        elif '--convert-to' in command:
            export = command[command.index('--convert-to') + 1]
            options = json.loads(export.split(':', 2)[2])
            if export.startswith('pdf:'):
                self.assertIs(options['UseLosslessCompression']['value'], True)
                self.assertIs(options['ReduceImageResolution']['value'], False)
                if self.mode != 'no_pdf':
                    with pymupdf.open() as document:
                        for _ in range(2 if self.mode == 'two_pages' else 1):
                            page = document.new_page(width=150, height=75.01 if self.mode == 'rounding' else 75)
                            page.draw_line((20, 30), (80, 30), width=6)
                        document.save(self.run / 'native-preview/pdf/reconstruction.pdf')
                if self.mode == 'mutate':
                    self.pptx.write_bytes(b'changed final PPTX')
            else:
                self.assertTrue(export.startswith('png:impress_png_Export:'))
                self.assertEqual(options['PixelWidth']['type'], 'long')
                self.assertIs(options['Translucent']['value'], False)
                width, height = (int(options[k]['value']) for k in ('PixelWidth', 'PixelHeight'))
                if self.mode == 'wrong_png_size': width += 1
                if self.mode != 'no_png':
                    Image.new('RGB', (width, height), 'orange').save(Path(command[command.index('--outdir') + 1]) / 'reconstruction.png')
        commands.append({'argv': command, 'environment_overrides': environment, 'environment_unset': ['FONTCONFIG_SYSROOT'], 'returncode': 0, 'stdout': output, 'stderr': ''})
        return output

    def render(self):
        with patch.object(native, '_execute', side_effect=self.command):
            return native.render(self.config)

    def test_final_ppt_pdf_font_and_all_scales_are_bound_without_acceptance(self):
        before = self.pptx.read_bytes()
        result = self.render()
        self.assertEqual(before, self.pptx.read_bytes())
        self.assertEqual(result['native_render']['slide_id'], 1001)
        self.assertFalse(result['application_playback_verified'])
        self.assertEqual(result['native_render']['input_before'], result['native_render']['input_after'])
        for scale in (1, 2, 4):
            with Image.open(self.run / f'preview-{scale}x.png') as img:
                self.assertEqual(img.size, (200 * scale, 100 * scale))
            self.assertEqual((self.run / f'preview-{scale}x.png').read_bytes(),
                             (self.run / f'native-preview/png-{scale}x/reconstruction.png').read_bytes())
        for item in result['evidence'].values():
            self.assertEqual(item, native.binding(item['path']))
        self.assertIn('<reset-dirs', (self.run / 'native-preview/fonts.conf').read_text())

    def test_pdf_page_rounding_does_not_affect_direct_png_dimensions(self):
        self.mode = 'rounding'
        result = self.render()
        self.assertAlmostEqual(result['native_render']['page_rect_points'][3], 75.01, places=3)
        self.assertEqual(result['raw_preview_format'], 'impress_png_Export')
        self.assertNotIn('rasterizer', result)
        self.assertEqual(result['native_render']['pixel_dimensions']['4'], [800, 400])

    def test_missing_pdf_success_exit_is_failure(self):
        self.mode = 'no_pdf'
        with self.assertRaises(OSError):
            self.render()
        self.assertFalse((self.run / 'preview-1x.png').exists())

    def test_missing_png_success_exit_is_failure(self):
        self.mode = 'no_png'
        with self.assertRaises(OSError):
            self.render()

    def test_wrong_png_dimensions_are_rejected_without_resizing(self):
        self.mode = 'wrong_png_size'
        with self.assertRaisesRegex(ValueError, 'pixel dimensions'):
            self.render()
        self.assertFalse((self.run / 'preview-1x.png').exists())

    def test_wrong_page_count_is_failure(self):
        self.mode = 'two_pages'
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.render()

    def test_final_ppt_mutation_is_failure(self):
        self.mode = 'mutate'
        with self.assertRaisesRegex(ValueError, 'changed during'):
            self.render()

    def test_same_font_bytes_from_unregistered_path_are_rejected(self):
        self.mode = 'substitute'
        with self.assertRaisesRegex(ValueError, 'substituted'):
            self.render()

    def test_stale_output_or_multislide_or_base_is_rejected(self):
        for mode in ('stale', 'base', 'multislide'):
            with self.subTest(mode=mode):
                config = copy.deepcopy(self.config)
                if mode == 'stale':
                    (self.run / 'preview-1x.png').write_bytes(b'stale')
                elif mode == 'base':
                    config['base'] = {'slide_id': '1001'}
                else:
                    write_archive(self.pptx, fixture([(1001, 'ppt/slides/a.xml', slide([])), (2002, 'ppt/slides/b.xml', slide([]))]))
                with self.assertRaises(ValueError):
                    native.render(config)
                (self.run / 'preview-1x.png').unlink(missing_ok=True)
                self.assertFalse((self.run / 'native-preview').exists())

    def test_new_lossy_pdf_stream_is_rejected_but_original_jpeg_is_identified(self):
        jpeg = io.BytesIO()
        Image.new('RGB', (50, 20), 'orange').save(jpeg, format='JPEG')
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=150, height=75)
            page.insert_image(pymupdf.Rect(10, 10, 110, 50), stream=jpeg.getvalue())
            with self.assertRaisesRegex(ValueError, 'lossy'):
                native._image_evidence(self.pptx, pdf, page)
            files = {**self.files, 'ppt/media/photo.jpg': jpeg.getvalue()}
            write_archive(self.pptx, files)
            evidence = native._image_evidence(self.pptx, pdf, page)
            self.assertTrue(evidence['pdf_images'][0]['lossy_source_bytes_preserved'])
            self.assertEqual(evidence['pdf_images'][0]['pixel_dimensions'], [50, 20])

    def test_native_metadata_faults_are_rejected_against_actual_evidence(self):
        from figure_rebuild.output_review import _preview_provenance
        result = self.render()
        previews = {}
        for scale in (1, 2, 4):
            path = self.run / f'preview-{scale}x.png'
            previews[f'preview_{scale}x'] = {**native.binding(path), 'width': 200*scale, 'height': 100*scale, 'scale': scale}
        smooth = self.run / 'preview-smooth-1x.png'
        with Image.open(self.run / 'preview-4x.png') as img:
            img.resize((200, 100), Image.Resampling.LANCZOS).save(smooth)
        previews['preview_smooth_1x'] = {**native.binding(smooth), 'width': 200, 'height': 100, 'scale': 1,
            'derivation': {'source_role': 'preview_4x', 'source_sha256': previews['preview_4x']['sha256'],
                           'kernel': 'lanczos3', 'target_size': [200, 100], 'is_raw_preview': False}}
        result.update(schema_version=1, preview_backend='libreoffice', input_pptx=native.binding(self.pptx), previews=previews)
        self.config['preview_provenance_version'] = 1
        audit_path = self.run / 'render-audit.json'
        audit_path.write_text(json.dumps(result))
        _preview_provenance(self.run, self.config, {'pptx': self.pptx})
        for failure in ('fonts', 'duplicate_face', 'dpi', 'executable', 'raw_format', 'png_options', 'png_source', 'png_font', 'png_index', 'png_bytes'):
            changed = copy.deepcopy(result)
            if failure == 'fonts': changed['native_render']['pdf_fonts'] = ['DefinitelyUnregisteredFont']
            elif failure == 'duplicate_face': changed['native_render']['font_resolutions'][1] = copy.deepcopy(changed['native_render']['font_resolutions'][0])
            elif failure == 'dpi': changed['pixel_density_dpi'] = [72, 144, 288]
            elif failure == 'raw_format': changed['raw_preview_format'] = 'pdf_raster'
            elif failure == 'png_options': changed['native_render']['png_exports']['1']['options']['PixelWidth']['value'] = '199'
            elif failure == 'png_source': changed['native_render']['png_exports']['1']['input_pptx']['sha256'] = '0'*64
            elif failure == 'png_font': changed['native_render']['png_exports']['1']['fontconfig_sha256'] = '0'*64
            elif failure == 'png_index': changed['native_render']['png_exports']['1']['command_index'] = 0
            elif failure == 'png_bytes': changed['evidence']['native_png_1x'] = copy.deepcopy(changed['evidence']['native_png_2x'])
            else: changed['command_executable'] = {'path': '/other/soffice', 'sha256': '0'*64}
            audit_path.write_text(json.dumps(changed))
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                _preview_provenance(self.run, self.config, {'pptx': self.pptx})
        commands_path = self.run / 'native-preview/commands.json'
        original_commands = json.loads(commands_path.read_text())
        for failure in ('filter', 'font_environment', 'input', 'output', 'notes'):
            changed = copy.deepcopy(result)
            commands = copy.deepcopy(original_commands)
            index = changed['native_render']['png_exports']['1']['command_index']
            command = commands[index]
            if failure == 'filter':
                i = command['argv'].index('--convert-to') + 1
                command['argv'][i] = command['argv'][i].replace('impress_png_Export', 'draw_png_Export')
            elif failure == 'font_environment': command['environment_overrides']['FONTCONFIG_FILE'] = '/other/fonts.conf'
            elif failure == 'input': command['argv'][-1] = '/other.pptx'
            elif failure == 'output': command['argv'][command['argv'].index('--outdir') + 1] = '/other/output'
            else:
                pdf_command = next(c for c in commands if any(a.startswith('pdf:') for a in c['argv']))
                i = pdf_command['argv'].index('--convert-to') + 1
                pdf_command['argv'][i] = pdf_command['argv'][i].replace('"ExportNotesPages":{"type":"boolean","value":false}', '"ExportNotesPages":{"type":"boolean","value":true}')
            commands_path.write_text(json.dumps(commands))
            changed['evidence']['native_commands'] = native.binding(commands_path)
            audit_path.write_text(json.dumps(changed))
            with self.subTest(command_failure=failure), self.assertRaises(ValueError):
                _preview_provenance(self.run, self.config, {'pptx': self.pptx})


if __name__ == '__main__':
    unittest.main()
