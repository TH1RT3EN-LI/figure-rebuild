"""Independent semantic-to-materialized scene integration regressions."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from figure_rebuild.formula_render import _font_metadata_digest, _source
from figure_rebuild.scene_compile import compile_scene, validate_delivery_sampling


from figure_rebuild.paths import python_environment


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sample_pdf():
    document = b'%PDF-1.4\n'
    offsets = []
    for index, value in enumerate((b'<< /Type /Catalog /Pages 2 0 R >>',
                                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 5] >>'), 1):
        offsets.append(len(document))
        document += f'{index} 0 obj\n'.encode() + value + b'\nendobj\n'
    startxref = len(document)
    document += b'xref\n0 4\n0000000000 65535 f \n'
    document += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    document += b'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + str(startxref).encode() + b'\n%%EOF\n'
    return document


def synthetic_formula(job, identity='math'):
    """Source-free asset fixture; provenance metadata has no private fonts."""
    output = job / 'assets/math'; output.mkdir(parents=True, exist_ok=True)
    fonts = {'family': 'Computer Modern', 'registry': '/unavailable/public-font-registry.json',
             'registry_sha256': '1' * 64, 'font_manifest': '/unavailable/cm.json',
             'font_manifest_sha256': '2' * 64,
             'files': [{'name': name, 'sha256': '3' * 64, 'path': '/unavailable/' + name}
                       for name in ('cmr10.tfm', 'cmr10.pfb')]}
    audit = {'schema_version': '2', 'kind': 'generated_latex', 'asset_id': identity,
             'latex': r'\mathrm{x}', 'transcription_confirmed': True, 'reference_crop_used': False,
             'engine': 'tectonic', 'engine_version': 'synthetic integration fixture',
             'engine_sha256': '4' * 64, 'shell_escape': False,
             'font_size_px': 16, 'color': '#000000', 'padding_pt': 12 * 72.27 / 768,
             'design_size': None, 'stroke_width_px': 0, 'rotation_deg': 0, 'dpi': 768,
             'png_pixels': [80, 40], 'natural_display_size_px': [10, 5], 'min_sampling_scale': 8,
             'alpha_bbox_px': [14, 12, 94, 52], 'rendered_page_pixels': [100, 60],
             'tex_box': {'width_pt': 7.527, 'height_pt': 3.01125, 'depth_pt': 0},
             'baseline_origin_ink_unrotated_px': [-2, 32], 'baseline_ink_unrotated_px': 32,
             'font_registry': fonts, 'alphabet_font_registry': None,
             'registered_font_dependencies': ['cmr10.tfm', 'cmr10.pfb'],
             'registered_alphabet_font_dependencies': [], 'embedded_fonts': ['FIXTUR+CMR10'],
             'asset_path_base': 'audit_directory', 'vector_effective_viewbox': [0, 0, 10, 5],
             'vector_geometry': {'embeddedfont_outlines': True, 'external_references': False,
                                 'viewbox': [0, 0, 10, 5], 'rotation_deg': 0}}
    audit['font_metadata_sha256'] = _font_metadata_digest(fonts, None)
    (output / f'{identity}.tex').write_text(_source(audit['latex'], 16, '#000000', audit['padding_pt'], engine_kind='tectonic'))
    (output / f'{identity}.pdf').write_bytes(sample_pdf())
    Image.new('RGBA', (80, 40), (0, 0, 0, 255)).save(output / f'{identity}.png')
    (output / f'{identity}.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 5"><path d="M0 0H10V5H0Z"/></svg>')
    (output / f'{identity}.log').write_text('FRMETRICS width=7.527ptheight=3.01125ptdepth=0pt\n')
    (output / f'{identity}.dependencies.txt').write_text('cmr10.tfm cmr10.pfb\n')
    audit['assets'] = {extension: {'path': f'{identity}.{extension}', 'sha256': sha(output / f'{identity}.{extension}')}
                       for extension in ('tex', 'pdf', 'png', 'svg', 'log', 'dependencies.txt')}
    path = output / f'{identity}.json'; path.write_text(json.dumps(audit))
    return {'id': identity, 'kind': 'formula', 'audit': path.relative_to(job).as_posix(),
            'audit_sha256': sha(path), 'box': {'x': 5, 'y': 6, 'width': 10, 'height': 5}}


def rectangle(identity, x, y, width, height):
    return {'id': identity, 'kind': 'path',
            'commands': [{'moveTo': {'x': x, 'y': y}}, {'lineTo': {'x': x + width, 'y': y}},
                         {'lineTo': {'x': x + width, 'y': y + height}}, {'lineTo': {'x': x, 'y': y + height}}, {'close': {}}],
            'style': {'fill': '#EEEEEE', 'stroke': '#222222', 'stroke_width': 1}}


class SceneCompileIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.job = self.root / 'job'; self.job.mkdir()
        formula = synthetic_formula(self.job)
        self.manifest = {'schema_version': 1, 'id': 'compile-fixture', 'revision': 1,
                         'canvas': {'width': 800, 'height': 500},
                         'objects': [rectangle('module-a', 100, 100, 100, 60), formula,
                                     rectangle('module-b', 400, 200, 120, 80),
                                     {'id': 'flow', 'kind': 'connector',
                                      'from': {'id': 'module-a', 'site': 'right'}, 'to': {'id': 'module-b', 'site': 'left'},
                                      'route': 'elbow', 'arrow': {'start': 'none', 'end': 'triangle'},
                                      'style': {'fill': 'none', 'stroke': '#222222', 'stroke_width': 2}}]}

    def objects(self, scene):
        return {obj['id']: obj for obj in scene['objects']}

    def test_formula_records_hash_files_and_source_stay_stable_without_cycles(self):
        original = copy.deepcopy(self.manifest)
        materialized, audit = compile_scene(self.manifest, self.job)
        self.assertEqual(self.manifest, original)
        self.assertEqual([item['id'] for item in materialized['objects']], [item['id'] for item in original['objects']])
        self.assertEqual(len(audit['hash_files']), 7)
        formula = self.objects(materialized)['math']
        self.assertEqual(formula['kind'], 'image')
        self.assertEqual(formula['source_kind'], 'formula')
        self.assertFalse(formula['editable'])
        self.assertEqual(formula['sha256'], sha(self.job / formula['path']))
        self.assertEqual(formula['formula_asset']['id'], 'math')
        # json.dumps catches accidental obj -> formula_asset -> obj cycles.
        self.assertEqual(json.loads(json.dumps(materialized)), materialized)
        self.assertEqual(json.loads(json.dumps(audit)), audit)
        for record in audit['hash_files']:
            self.assertFalse(Path(record['path']).is_absolute())
            self.assertEqual(sha(self.job / record['path']), record['sha256'])

    def test_connector_materialization_preserves_full_stable_relationship_record(self):
        materialized, audit = compile_scene(self.manifest, self.job)
        flow = self.objects(materialized)['flow']; record = audit['connections'][0]
        self.assertEqual(flow['kind'], 'path')
        self.assertEqual(flow['source_kind'], 'connector')
        self.assertEqual(flow['connection_record'], record)
        self.assertEqual(flow['commands'], record['commands'])
        self.assertEqual(record['from']['id'], 'module-a')
        self.assertEqual(record['to']['id'], 'module-b')
        self.assertEqual(record['from']['point'], {'x': 200, 'y': 130})
        self.assertEqual(record['to']['point'], {'x': 400, 'y': 240})
        self.assertEqual(record['arrow'], {'start': 'none', 'end': 'triangle'})

    def test_frozen_formula_assets_are_consumed_after_live_originals_change(self):
        frozen = self.root / 'frozen'; shutil.copytree(self.job, frozen)
        (self.job / 'assets/math/math.png').write_bytes(b'changed live asset')
        scene, audit = compile_scene(self.manifest, self.job, asset_root=frozen)
        self.assertEqual(self.objects(scene)['math']['box']['width'], 10)
        for record in audit['hash_files']:
            self.assertEqual(sha(frozen / record['path']), record['sha256'])
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            compile_scene(self.manifest, self.job)

    def test_formula_attachment_moves_only_position_and_records_final_frame(self):
        formula = self.objects(self.manifest)['math']
        formula['attach_to'] = {'id': 'module-a', 'site': 'center', 'offset': {'x': 0, 'y': 0}}
        original = copy.deepcopy(self.manifest)
        scene, audit = compile_scene(self.manifest, self.job)
        actual = self.objects(scene)['math']
        self.assertEqual(self.manifest, original)
        self.assertEqual(actual['box'], {'x': 145, 'y': 127.5, 'width': 10, 'height': 5})
        self.assertNotIn('attach_to', actual)
        self.assertEqual(actual['source_attachment'], formula['attach_to'])
        sampling = min(80 / actual['box']['width'], 40 / actual['box']['height'])
        self.assertEqual(sampling, 8)
        record = audit['formulas'][0]
        self.assertEqual(record['placement']['sampling_scale'], sampling)
        self.assertEqual(record['placement']['box'], actual['box'])
        self.assertEqual(actual['formula_asset']['placement']['box'], actual['box'])
        baseline = record['placement']['baseline_anchor']
        self.assertEqual(baseline, {'x': actual['box']['x'] - .25, 'y': actual['box']['y'] + 4})

    def test_out_of_order_formula_attachment_chain_resolves_after_formula_fitting(self):
        parent = self.objects(self.manifest)['math']
        parent['box'] = {'x': 5, 'y': 6, 'width': 12, 'height': 5}  # contain -> 10x5
        parent['attach_to'] = {'id': 'module-a', 'site': 'center', 'offset': {'x': 0, 'y': 0}}
        child = copy.deepcopy(parent); child['id'] = 'math-child'
        child['attach_to'] = {'id': 'math', 'site': 'right', 'offset': {'x': 3, 'y': 0}}
        self.manifest['objects'].insert(0, child)
        scene, audit = compile_scene(self.manifest, self.job)
        objects = self.objects(scene)
        self.assertEqual(objects['math']['box'], {'x': 145, 'y': 127.5, 'width': 10, 'height': 5})
        self.assertEqual(objects['math-child']['box'], {'x': 153, 'y': 127.5, 'width': 10, 'height': 5})
        self.assertEqual(len(audit['hash_files']), 7)  # reused audit remains deduplicated
        by_id = {record['id']: record for record in audit['formulas']}
        for identity in ('math', 'math-child'):
            self.assertEqual(by_id[identity]['placement']['box'], objects[identity]['box'])
            self.assertGreaterEqual(by_id[identity]['placement']['sampling_scale'], 8)
        self.assertEqual(json.loads(json.dumps(scene)), scene)

    def test_baseline_formula_attachment_preserves_declared_baseline_semantics(self):
        formula = self.objects(self.manifest)['math']
        formula.pop('box')
        formula['baseline_anchor'] = {'x': 25, 'y': 35}
        formula['attach_to'] = {'id': 'module-a', 'site': 'center', 'offset': {'x': 3, 'y': 7}}
        before = copy.deepcopy(self.manifest)
        scene, audit = compile_scene(self.manifest, self.job)
        actual = self.objects(scene)['math']; record = audit['formulas'][0]
        self.assertEqual(self.manifest, before)
        self.assertEqual(record['placement']['attachment_position_kind'], 'baseline_anchor')
        self.assertEqual(record['placement']['baseline_anchor'], {'x': 153, 'y': 137})
        self.assertEqual(actual['box'], {'x': 153.25, 'y': 133, 'width': 10, 'height': 5})
        self.assertEqual(actual['source_requested_attachment'], formula['attach_to'])
        self.assertEqual(record['placement']['sampling_scale'], 8)

    def test_oversized_formula_rejects_before_attachment_can_hide_the_sampling_failure(self):
        formula = self.objects(self.manifest)['math']
        formula['box'] = {'x': 5, 'y': 6, 'width': 20, 'height': 10}
        formula['attach_to'] = {'id': 'module-a', 'site': 'center', 'offset': {'x': 0, 'y': 0}}
        before = copy.deepcopy(self.manifest)
        with self.assertRaisesRegex(ValueError, 'sampling'):
            compile_scene(self.manifest, self.job)
        self.assertEqual(self.manifest, before)

    def test_module_cli_matches_package_compilation_from_another_directory(self):
        path = self.job / 'manifest.json'; path.write_text(json.dumps(self.manifest))
        output = self.root / 'compiled.json'; audit_path = self.root / 'audit.json'
        result = subprocess.run([sys.executable, '-m', 'figure_rebuild.scene_compile',
                                 '--manifest', str(path), '--job', str(self.job),
                                 '--output', str(output), '--audit', str(audit_path)],
                                cwd=self.root, capture_output=True, text=True, env=python_environment())
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_scene, expected_audit = compile_scene(self.manifest, self.job)
        self.assertEqual(json.loads(output.read_text()), expected_scene)
        self.assertEqual(json.loads(audit_path.read_text()), expected_audit)


if __name__ == '__main__':
    unittest.main()

class DeliverySampling(unittest.TestCase):
    def test_enlarged_slide_placement_cannot_weaken_png_fallback(self):
        audit={'formulas':[{'id':'equation','placement':{'sampling_scale':8.2,'min_sampling_scale':8}}]}
        self.assertAlmostEqual(validate_delivery_sampling(audit,.5)[0]['sampling_scale'],16.4)
        with self.assertRaisesRegex(ValueError,'after slide placement'):
            validate_delivery_sampling(audit,2)
        for scale in (0,-1,float('inf'),True):
            with self.assertRaises(ValueError):validate_delivery_sampling(audit,scale)

    def test_materialized_metadata_cannot_bypass_formula_verification(self):
        with self.assertRaisesRegex(ValueError,'diagnostic'):
            compile_scene({'objects':[{'id':'equation','kind':'image','source_kind':'formula','formula_asset':{}}]},'.')
