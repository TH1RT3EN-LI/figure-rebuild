import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from PIL import Image

MODULE = Path(__file__).resolve().parents[2] / 'tools/figure_rebuild/formula_render.py'
spec = importlib.util.spec_from_file_location('figure_formula_render', MODULE)
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)


class FormulaSafety(unittest.TestCase):
    def test_valid_nested_math_and_literal_escape(self):
        expression = r'\Pi_c(\mathbf G_{ij}\circ\Pi_c^{-1}(\mathbf p_i,\mathbf d_i))'
        self.assertEqual(f.validate_expression(expression, confirmed=True), expression)
        self.assertEqual(f.validate_expression(r'\left\{\frac{x_i}{\sqrt{2}}\right\}', confirmed=True), r'\left\{\frac{x_i}{\sqrt{2}}\right\}')

    def test_requires_host_confirmation(self):
        for confirmed in (False, None, 1, 'yes'):
            with self.assertRaisesRegex(ValueError, 'confirmed'):
                f.validate_expression('x', confirmed=confirmed)

    def test_refuses_tex_programs_file_access_and_escape_bypasses(self):
        expressions = [r'\input{secret}', r'\write18{touch file}', r'\def\x{x}',
                       r'\csname input\endcsname', r'\catcode13=1', r'\special{file}',
                       r'^^5cinput{secret}', r'x% comment', r'$x$', r'\usepackage{shellesc}',
                       r'\newcommand{\x}{x}', r'\openout1=secret', r'\immediate\write1{x}',
                       r'\read1 to\x', r'\includegraphics{image}', r'\begin{document}x',
                       r'\unicode{x}', 'x' + '\\' + '\n' + 'input{secret}', r'\href{url}{x}',
                       r'\loop x \repeat']
        for expression in expressions:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    f.validate_expression(expression, confirmed=True)

    def test_bounded_length_and_balanced_groups(self):
        for expression in ('x' * 4097, '{x', 'x}', '{' * 33 + 'x' + '}' * 33, '\\', 'x;'):
            with self.subTest(expression=expression[:20]):
                with self.assertRaises(ValueError):
                    f.validate_expression(expression, confirmed=True)

    def test_engine_flags_never_enable_shell_escape(self):
        command = f.engine_command(Path('/bin/pdflatex'), 'pdflatex', Path('/tmp/x.tex'), Path('/tmp'))
        self.assertIn('-no-shell-escape', command)
        self.assertNotIn('-shell-escape', command)
        tectonic = f.engine_command(Path('/bin/tectonic'), 'tectonic', Path('/tmp/x.tex'), Path('/tmp'))
        self.assertIn('--untrusted', tectonic)
        self.assertNotIn('--shell-escape', tectonic)

    def test_all_bad_batch_expressions_checked_before_files_written(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'never-created'
            with self.assertRaises(ValueError):
                f.render_batch({'formulas': [{'id': 'good', 'latex': 'x', 'confirmed': True},
                                            {'id': 'bad', 'latex': r'\input{x}', 'confirmed': True}]},
                               output_dir=output, font_manifest='/no/such/registry')
            self.assertFalse(output.exists())

    def test_registry_does_not_silently_accept_hash_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'cmr10.pfb').write_bytes(b'changed-font')
            (root / 'manifest.json').write_text(json.dumps({'family': 'Computer Modern', 'files': [
                {'file': 'cmr10.pfb', 'sha256': '0' * 64}]}))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                f.resolve_math_fonts(root / 'manifest.json')

    def test_font_manifest_cannot_escape_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in ('../font.pfb', '/tmp/font.pfb'):
                with self.assertRaisesRegex(ValueError, 'below'):
                    f._child(root, path)

    def test_metrics_allow_tex_compact_spacing(self):
        metrics = f._metrics('FRMETRICS width=23.44849ptheight=11.856ptdepth=4.72115pt')
        self.assertAlmostEqual(metrics['height_pt'], 11.856)

    def test_fixed_optical_design_is_explicit_and_bounded(self):
        source = f._source(r'\mathbf C_{ij}', 35.2, '#000000', 1, design_size=10)
        self.assertIn(r'\DeclareFontShape{OT1}{cmr}{bx}{n}{<->cmbx10}{}', source)
        self.assertIn(r'\DeclareFontShape{OML}{cmm}{m}{it}{<->cmmi10}{}', source)
        self.assertNotIn('FR_DESIGN', source)
        for invalid in (True, 12, '10', -1):
            with self.assertRaises(ValueError):
                f._design_source(invalid)

    def test_controlled_pdf_stroke_is_bounded_and_not_expression_tex(self):
        self.assertEqual(f._stroke_source(0, 'tectonic'), ('', ''))
        opening, closing = f._stroke_source(.25, 'tectonic')
        self.assertIn('2 Tr 0.18750000 w', opening)
        self.assertEqual(closing, r'\special{pdf:literal direct Q}')
        self.assertIn(r'\pdfliteral direct', f._stroke_source(.15, 'pdflatex')[0])
        for invalid in (True, -1, 2.01, float('nan'), '0.25'):
            with self.assertRaises(ValueError):
                f._stroke_source(invalid, 'tectonic')
        with self.assertRaises(ValueError):
            f.validate_expression(opening + 'x' + closing, confirmed=True)

    def test_transparent_border_trim_and_clockwise_rotation(self):
        image = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
        image.putpixel((2, 1), (10, 20, 30, 255)); image.putpixel((2, 2), (20, 30, 40, 128))
        result, box = f._tight_rgba(image, 90)
        self.assertEqual(box, (2, 1, 3, 3))
        self.assertEqual(result.size, (2, 1))
        self.assertEqual(result.getpixel((1, 0)), (10, 20, 30, 255))
        self.assertEqual(result.getpixel((0, 0)), (20, 30, 40, 128))

    def test_unexpected_pdf_font_is_an_error(self):
        report = 'header\n----\nABCDEF+Arial Type 1 yes yes\n'
        with self.assertRaisesRegex(ValueError, 'refusing fallback'):
            f._embedded_fonts(report)

    def test_explicit_otf_font_decorations_do_not_permit_another_family(self):
        report = 'header\n----\nABCDEF+MathJax_Main-Bold-Identity-H CID yes yes\n'
        self.assertEqual(f._embedded_base('ABCDEF+MathJax_Main-Bold-Identity-H'), 'MathJax_Main-Bold')
        with self.assertRaises(ValueError):
            f._embedded_fonts(report)
        self.assertEqual(len(f._embedded_fonts(report, ['MathJax_Main-Bold'])), 1)

    def test_otf_registry_rejects_unverified_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'font.otf').write_bytes(b'wrong-font')
            (root / 'registry.json').write_text(json.dumps({'fonts': [
                {'id': 'MathJax_Main-Regular', 'path': 'font.otf', 'sha256': '0' * 64,
                 'postscript_name': 'MathJax_Main-Regular', 'face_index': 0}]}))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                f.resolve_alphabet_fonts(root / 'registry.json')


@unittest.skipUnless(os.environ.get('FIGURE_REBUILD_TEX_ENGINE') and os.environ.get('FIGURE_REBUILD_MATH_FONTS')
                     and os.environ.get('FIGURE_REBUILD_ALPHABET_FONTS'),
                     'Configure Tectonic, CM fonts and registered MathJax OTF alphabets for integration tests')
class RealOpenTypeMathRendering(unittest.TestCase):
    def test_real_tex_uses_registered_mathjax_alphabets_without_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            report = f.render_formula(r'\mathbf G_{ij}', asset_id='mathjax', output_dir=temp,
                                      engine=os.environ['FIGURE_REBUILD_TEX_ENGINE'],
                                      font_manifest=os.environ['FIGURE_REBUILD_MATH_FONTS'],
                                      alphabet_font_manifest=os.environ['FIGURE_REBUILD_ALPHABET_FONTS'],
                                      confirmed=True, font_size_px=35.2)
            fonts = {f._embedded_base(name) for name in report['embedded_fonts']}
            self.assertEqual(fonts, {'MathJax_Main-Bold', 'MathJax_Math-Italic'})
            self.assertEqual(report['engine'], 'tectonic')
            self.assertEqual(report['stroke_width_px'], 0)
            self.assertFalse(report['reference_crop_used'])
            self.assertGreaterEqual(report['actual_sampling_scale'], 8)
            self.assertIn('MathJax_Main-Bold.otf', report['registered_alphabet_font_dependencies'])
            self.assertIn('MathJax_Math-Italic.otf', report['registered_alphabet_font_dependencies'])
            self.assertTrue(Path(report['assets']['pdf']['path']).read_bytes().startswith(b'%PDF'))


@unittest.skipUnless(os.environ.get('FIGURE_REBUILD_TEX_ENGINE') and os.environ.get('FIGURE_REBUILD_MATH_FONTS'),
                     'Configure a real TeX engine and audited Computer Modern registry for integration tests')
class RealFormulaRendering(unittest.TestCase):
    def test_pdf_fillstroke_changes_rendered_ink_and_retains_real_font(self):
        with tempfile.TemporaryDirectory() as temp:
            arguments = dict(output_dir=temp, engine=os.environ['FIGURE_REBUILD_TEX_ENGINE'],
                             font_manifest=os.environ['FIGURE_REBUILD_MATH_FONTS'], confirmed=True,
                             font_size_px=35.2)
            plain = f.render_formula(r'\mathbf G', asset_id='plain', **arguments)
            stroked = f.render_formula(r'\mathbf G', asset_id='stroked', stroke_width_px=.35, **arguments)
            with Image.open(plain['assets']['png']['path']) as image:
                plain_area = sum(index * count for index, count in enumerate(image.getchannel('A').histogram()))
            with Image.open(stroked['assets']['png']['path']) as image:
                stroke_area = sum(index * count for index, count in enumerate(image.getchannel('A').histogram()))
            self.assertGreater(stroke_area, plain_area * 1.03)
            self.assertEqual(stroked['stroke_width_px'], .35)
            self.assertEqual(stroked['embedded_fonts'], plain['embedded_fonts'])
            self.assertIn('2 Tr', Path(stroked['assets']['tex']['path']).read_text())

    def test_fixed_design_uses_only_the_registered_10pt_programs(self):
        with tempfile.TemporaryDirectory() as temp:
            report = f.render_formula(r'\mathbf C_{ij}', asset_id='fixed', output_dir=temp,
                                      engine=os.environ['FIGURE_REBUILD_TEX_ENGINE'],
                                      font_manifest=os.environ['FIGURE_REBUILD_MATH_FONTS'], confirmed=True,
                                      font_size_px=35.2, design_size=10)
            self.assertEqual(report['design_size'], 10)
            self.assertEqual({name.split('+')[-1] for name in report['embedded_fonts']}, {'CMBX10', 'CMMI10'})

    def test_generated_transparent_asset_has_audited_fonts_and_sampling(self):
        with tempfile.TemporaryDirectory() as temp:
            report = f.render_formula(r'\mathbf p^{*}_{ij}', asset_id='point', output_dir=temp,
                                      engine=os.environ['FIGURE_REBUILD_TEX_ENGINE'],
                                      font_manifest=os.environ['FIGURE_REBUILD_MATH_FONTS'], confirmed=True,
                                      font_size_px=24, rotation_deg=90, display_size_px=[24, 26])
            self.assertGreaterEqual(report['actual_sampling_scale'], 8)
            self.assertFalse(report['reference_crop_used'])
            self.assertEqual(report['rotation_deg'], 90)
            self.assertTrue(report['registered_font_dependencies'])
            for record in report['assets'].values():
                self.assertEqual(f._sha(record['path']), record['sha256'])
            with Image.open(report['assets']['png']['path']) as image:
                self.assertEqual(image.mode, 'RGBA')
                alpha = image.getchannel('A')
                self.assertEqual(alpha.getbbox(), (0, 0, image.width, image.height))
                self.assertLess(alpha.getextrema()[0], 255)
            self.assertIn(r'\mathbf p^{*}_{ij}', Path(report['assets']['tex']['path']).read_text())
            self.assertTrue(Path(report['assets']['pdf']['path']).read_bytes().startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
