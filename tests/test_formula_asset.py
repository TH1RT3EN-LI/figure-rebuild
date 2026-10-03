import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

from figure_rebuild import formula_asset as a
from figure_rebuild import formula_render as r


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tiny_pdf():
    data = b'%PDF-1.4\n'
    entries = []
    for index, body in enumerate((b'<< /Type /Catalog /Pages 2 0 R >>',
                                  b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                                  b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 5] >>'), 1):
        entries.append(len(data))
        data += f'{index} 0 obj\n'.encode() + body + b'\nendobj\n'
    offset = len(data)
    data += b'xref\n0 4\n0000000000 65535 f \n'
    data += b''.join(f'{entry:010d} 00000 n \n'.encode() for entry in entries)
    data += b'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + str(offset).encode() + b'\n%%EOF\n'
    return data


def fixture(job):
    output = job / 'assets/formulas'; output.mkdir(parents=True)
    stem = 'variable'
    fonts = {'family': 'Computer Modern', 'registry': '/external/registry.json',
             'registry_sha256': 'a' * 64, 'font_manifest': '/external/cm.json',
             'font_manifest_sha256': 'b' * 64,
             'files': [{'name': name, 'path': '/external/' + name, 'sha256': 'c' * 64}
                       for name in ('cmr10.tfm', 'cmr10.pfb')]}
    audit = {'schema_version': '2', 'kind': 'generated_latex', 'asset_id': stem,
             'latex': r'\mathrm{x}', 'transcription_confirmed': True,
             'reference_crop_used': False, 'engine': 'tectonic', 'engine_version': 'Tectonic 0.17',
             'engine_sha256': 'd' * 64, 'shell_escape': False,
             'font_size_px': 16, 'color': '#000000', 'padding_pt': 12 * 72.27 / 768, 'design_size': None,
             'stroke_width_px': 0, 'rotation_deg': 0, 'dpi': 768,
             'png_pixels': [80, 40], 'natural_display_size_px': [10, 5], 'min_sampling_scale': 8,
             'baseline_origin_ink_unrotated_px': [-2, 32], 'baseline_ink_unrotated_px': 32,
             'alpha_bbox_px': [14, 12, 94, 52], 'rendered_page_pixels': [100, 60],
             'tex_box': {'width_pt': 7.527, 'height_pt': 32 * 72.27 / 768, 'depth_pt': 0},
             'font_registry': fonts,
             'alphabet_font_registry': None, 'registered_font_dependencies': ['cmr10.tfm', 'cmr10.pfb'],
             'registered_alphabet_font_dependencies': [], 'embedded_fonts': ['ABCDEF+CMR10'],
             'vector_effective_viewbox': [0, 0, 10, 5], 'asset_path_base': 'audit_directory',
             'svg_intrinsic_pixels': [80, 40], 'svg_intrinsic_rasterization_scale': 8,
             'vector_geometry': {'embeddedfont_outlines': True, 'external_references': False, 'rotation_deg': 0,
                                 'viewbox': [0, 0, 10, 5], 'intrinsic_pixels': [80, 40],
                                 'intrinsic_rasterization_scale': 8, 'intrinsic_pixel_policy': 'match_png_fallback'}}
    audit['font_metadata_sha256'] = r._font_metadata_digest(fonts, None)
    (output / f'{stem}.tex').write_text(r._source(audit['latex'], 16, '#000000', audit['padding_pt'], engine_kind='tectonic'))
    (output / f'{stem}.pdf').write_bytes(tiny_pdf())
    Image.new('RGBA', (80, 40), (0, 0, 0, 255)).save(output / f'{stem}.png')
    (output / f'{stem}.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80px" height="40px" viewBox="0 0 10 5"><path d="M0 0H10V5H0Z"/></svg>')
    (output / f'{stem}.log').write_text('FRMETRICS width=7.527ptheight=3.01125ptdepth=0pt\n')
    (output / f'{stem}.dependencies.txt').write_text('cmr10.tfm cmr10.pfb\n')
    audit['assets'] = {extension: {'path': f'{stem}.{extension}',
                                  'sha256': digest(output / f'{stem}.{extension}')}
                       for extension in ('tex', 'pdf', 'png', 'svg', 'log', 'dependencies.txt')}
    audit_path = output / f'{stem}.json'
    audit_path.write_text(json.dumps(audit))
    element = {'id': 'formula-x', 'kind': 'formula', 'audit': audit_path.relative_to(job).as_posix(),
               'audit_sha256': digest(audit_path), 'box': [12, 20, 10, 5]}
    return element, audit, audit_path


def save_audit(element, audit, path):
    path.write_text(json.dumps(audit))
    element['audit_sha256'] = digest(path)


class FrozenFormulaAssets(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.job = Path(self.temporary.name) / 'job'; self.job.mkdir()
        self.element, self.audit, self.audit_path = fixture(self.job)

    def tearDown(self):
        self.temporary.cleanup()

    def test_vector_default_and_all_outputs_are_hash_bound(self):
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['representation'], 'svg')
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [12, 20, 10, 5])))
        self.assertEqual(result['placement']['sampling_scale'], 8)
        self.assertEqual(len(result['hash_files']), 7)
        self.assertTrue(result['vector_geometry']['embeddedfont_outlines'])
        self.assertTrue(all(not Path(record['path']).is_absolute() for record in result['hash_files']))

    def test_standard_dictionary_box_matches_legacy_sequence_placement(self):
        legacy = a.resolve_formula_asset(self.element, self.job)
        self.element['box'] = {'x': 12, 'y': 20, 'width': 10, 'height': 5}
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['placement'], legacy['placement'])
        self.assertEqual(result['placement']['box'], self.element['box'])

    def test_frozen_assets_are_read_after_original_mutation(self):
        frozen = Path(self.temporary.name) / 'frozen'; shutil.copytree(self.job, frozen)
        (self.audit_path.parent / self.audit['assets']['png']['path']).write_bytes(b'broken original')
        result = a.resolve_formula_asset(self.element, self.job, asset_root=frozen)
        self.assertEqual(result['placement']['sampling_scale'], 8)
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            a.resolve_formula_asset(self.element, self.job)

    def test_audit_and_each_output_modification_fail(self):
        for extension in ('tex', 'pdf', 'png', 'svg', 'log', 'dependencies.txt'):
            path = self.audit_path.parent / self.audit['assets'][extension]['path']; original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(extension=extension), self.assertRaisesRegex(ValueError, 'hash changed'):
                a.resolve_formula_asset(self.element, self.job)
            path.write_bytes(original)
        self.audit_path.write_text(self.audit_path.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'audit hash'):
            a.resolve_formula_asset(self.element, self.job)

    def test_asset_and_audit_paths_are_confined_even_for_absolute_legacy_records(self):
        self.element['audit'] = '../outside.json'
        with self.assertRaisesRegex(ValueError, 'escapes'):
            a.resolve_formula_asset(self.element, self.job)
        self.element['audit'] = 'assets/formulas/variable.json'
        self.audit['schema_version'] = '1'
        self.element['representation'] = 'png'
        self.audit['assets']['png']['path'] = '/outside/variable.png'
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            a.resolve_formula_asset(self.element, self.job)

    def test_baseline_uses_true_tex_origin_and_em(self):
        self.element.pop('box'); self.element['baseline_anchor'] = {'x': 12, 'y': 20}
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [12.25, 16, 10, 5])))
        self.assertEqual(result['placement']['baseline_anchor'], {'x': 12, 'y': 20})
        self.element['font_size'] = 8
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [12.125, 18, 5, 2.5])))
        self.assertEqual(result['placement']['sampling_scale'], 16)

    def test_rotated_baseline_is_explicitly_rejected(self):
        self.audit['rotation_deg'] = 180; self.audit['vector_geometry']['rotation_deg'] = 180; save_audit(self.element, self.audit, self.audit_path)
        self.element.pop('box'); self.element['baseline_anchor'] = {'x': 0, 'y': 20}
        with self.assertRaisesRegex(ValueError, 'Rotated'):
            a.resolve_formula_asset(self.element, self.job)

    def test_final_fallback_sampling_rejects_enlargement_for_svg_and_png(self):
        self.element['box'] = [0, 0, 20, 10]
        for representation in ('svg', 'png'):
            self.element['representation'] = representation
            with self.subTest(representation=representation), self.assertRaisesRegex(ValueError, 'sampling'):
                a.resolve_formula_asset(self.element, self.job)
        self.element.pop('box'); self.element['baseline_anchor'] = {'x': 0, 'y': 20}; self.element['font_size'] = 32
        with self.assertRaisesRegex(ValueError, 'sampling'):
            a.resolve_formula_asset(self.element, self.job)

    def test_contain_preserves_aspect_and_explicit_em_is_not_resized(self):
        self.element['box'] = [0, 0, 12, 5]
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [1, 0, 10, 5])))
        self.element['font_size'] = 8
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [3.5, 1.25, 5, 2.5])))
        self.element['font_size'] = 20
        with self.assertRaisesRegex(ValueError, 'exceeds its box'):
            a.resolve_formula_asset(self.element, self.job)

    def test_legacy_requires_explicit_png_and_still_binds_tex_pdf_png(self):
        self.audit['schema_version'] = '1'
        self.audit['assets'] = {key: value for key, value in self.audit['assets'].items() if key in ('tex', 'pdf', 'png')}
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'explicit png'):
            a.resolve_formula_asset(self.element, self.job)
        self.element['representation'] = 'png'
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertTrue(result['legacy_audit'])
        self.assertEqual(len(result['hash_files']), 4)
        for record in self.audit['assets'].values():
            record['path'] = str(self.audit_path.parent / record['path'])
        save_audit(self.element, self.audit, self.audit_path)
        result = a.resolve_formula_asset(self.element, self.job)
        self.assertEqual(result['png_path'], 'assets/formulas/variable.png')

    def test_relative_v2_assets_work_when_the_whole_job_is_relocated(self):
        moved = Path(self.temporary.name) / 'moved-job'; shutil.copytree(self.job, moved)
        shutil.rmtree(self.job)
        result = a.resolve_formula_asset(self.element, moved)
        self.assertEqual(result['placement']['box'], dict(zip(('x', 'y', 'width', 'height'), [12, 20, 10, 5])))

    def test_rebound_audit_cannot_invent_baseline_metrics(self):
        self.audit['baseline_origin_ink_unrotated_px'][1] += 1
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'baseline differs'):
            a.resolve_formula_asset(self.element, self.job)
        self.audit['baseline_origin_ink_unrotated_px'][1] -= 1
        self.audit['tex_box']['height_pt'] += 1
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'actual engine log'):
            a.resolve_formula_asset(self.element, self.job)

    def test_expression_and_font_metadata_are_validated(self):
        self.element['latex'] = 'y'
        with self.assertRaisesRegex(ValueError, 'transcription disagrees'):
            a.resolve_formula_asset(self.element, self.job)
        self.element.pop('latex')
        self.audit['font_registry']['files'][0]['sha256'] = 'e' * 64
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'metadata hash'):
            a.resolve_formula_asset(self.element, self.job)

    def test_rehashed_tex_and_dependency_corruption_is_rejected(self):
        path = self.audit_path.parent / self.audit['assets']['tex']['path']; path.write_text(r'\input{file}')
        self.audit['assets']['tex']['sha256'] = digest(path); save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'TeX no longer matches'):
            a.resolve_formula_asset(self.element, self.job)
        path.write_text(r._source(self.audit['latex'], 16, '#000000', self.audit['padding_pt'], engine_kind='tectonic'))
        self.audit['assets']['tex']['sha256'] = digest(path)
        dependencies = self.audit_path.parent / self.audit['assets']['dependencies.txt']['path']; dependencies.write_text('no registered fonts')
        self.audit['assets']['dependencies.txt']['sha256'] = digest(dependencies)
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'dependency is missing'):
            a.resolve_formula_asset(self.element, self.job)

    def test_rehashed_png_pdf_and_svg_corruption_is_rejected(self):
        for extension, content in (('png', b'broken png'), ('pdf', b'%PDF-1.4\nbroken'),
                                   ('svg', b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 5"><text>x</text></svg>')):
            path = self.audit_path.parent / self.audit['assets'][extension]['path']; original = path.read_bytes()
            path.write_bytes(content); self.audit['assets'][extension]['sha256'] = digest(path)
            save_audit(self.element, self.audit, self.audit_path)
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                a.resolve_formula_asset(self.element, self.job)
            path.write_bytes(original); self.audit['assets'][extension]['sha256'] = digest(path)
        save_audit(self.element, self.audit, self.audit_path)

    def test_rehashed_low_intrinsic_svg_cannot_claim_high_density_metadata(self):
        path = self.audit_path.parent / self.audit['assets']['svg']['path']
        path.write_text(path.read_text().replace('width="80px" height="40px"', 'width="10px" height="5px"'))
        self.audit['assets']['svg']['sha256'] = digest(path)
        save_audit(self.element, self.audit, self.audit_path)
        with self.assertRaisesRegex(ValueError, 'intrinsic dimensions disagree'):
            a.resolve_formula_asset(self.element, self.job)

    def test_both_or_neither_placement_is_rejected(self):
        self.element['baseline_anchor'] = {'x': 0, 'y': 20}
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            a.resolve_formula_asset(self.element, self.job)
        self.element.pop('baseline_anchor'); self.element.pop('box')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            a.resolve_formula_asset(self.element, self.job)


@unittest.skipUnless(os.environ.get('FIGURE_REBUILD_TEX_ENGINE') and os.environ.get('FIGURE_REBUILD_MATH_FONTS'),
                     'Configure real TeX and audited CM registry for integration tests')
class RealFrozenFormulaAssets(unittest.TestCase):
    def test_real_formula_vector_and_fallback_are_bound_and_portable(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / 'job'; job.mkdir()
            audit = r.render_formula(r'\mathbf p^{*}_{ij}', asset_id='point', output_dir=job / 'assets',
                                     engine=os.environ['FIGURE_REBUILD_TEX_ENGINE'],
                                     font_manifest=os.environ['FIGURE_REBUILD_MATH_FONTS'], confirmed=True,
                                     font_size_px=24)
            audit_path = job / 'assets/point.json'
            element = {'kind': 'formula', 'audit': 'assets/point.json', 'audit_sha256': digest(audit_path),
                       'baseline_anchor': {'x': 25, 'y': 35}}
            resolved = a.resolve_formula_asset(element, job)
            self.assertGreaterEqual(resolved['placement']['sampling_scale'], 8)
            self.assertTrue(resolved['vector_geometry']['embeddedfont_outlines'])
            self.assertEqual(resolved['vector_geometry']['intrinsic_pixels'], audit['png_pixels'])
            self.assertGreaterEqual(resolved['vector_geometry']['intrinsic_rasterization_scale'] + 1e-7, 8)
            self.assertEqual(audit['vector_effective_viewbox'][2:], audit['natural_display_size_px'])
            frozen = Path(temporary) / 'snapshot'; shutil.copytree(job, frozen)
            shutil.rmtree(job / 'assets')
            copied = a.resolve_formula_asset(element, job, asset_root=frozen)
            self.assertEqual(copied['placement'], resolved['placement'])


if __name__ == '__main__':
    unittest.main()
