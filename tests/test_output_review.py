"""Postbuild review must describe the exact inspected and delivered bytes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from zipfile import ZipFile
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

    def add_image_preview_provenance(self, version=1, picture_crop=None):
        from figure_rebuild.artifact_image_preview import prepare_image_preview
        from figure_rebuild.artifact_image_preview import NS
        pptx = self.run / 'validated-output/reconstruction.pptx'
        pic, rel, media = '', '', None
        if picture_crop:
            from PIL import Image
            from io import BytesIO
            image = BytesIO(); Image.new('RGB', (20,20), (0,255,0)).save(image, format='PNG'); media = image.getvalue()
            pic = ('<p:pic><p:nvPicPr><p:cNvPr id="2" name="photo"/></p:nvPicPr>'
                   '<p:blipFill><a:blip r:embed="image"/><a:srcRect '+picture_crop+'/>'
                   '<a:stretch/></p:blipFill><p:spPr><a:xfrm><a:off x="9525" y="9525"/>'
                   '<a:ext cx="95250" cy="95250"/></a:xfrm><a:prstGeom prst="rect">'
                   '<a:avLst/></a:prstGeom></p:spPr></p:pic>')
            rel = '<Relationship Id="image" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/photo.png"/>'
            scene = json.loads((self.run / 'resolved-scene.json').read_text()); scene['objects'] = [{'id':'photo','kind':'image'}]
            self.write_json(self.run / 'resolved-scene.json', scene)
        with ZipFile(pptx, 'w') as z:
            z.writestr('ppt/slides/slide1.xml', f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}" xmlns:r="{NS["r"]}"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{pic}</p:spTree></p:cSld></p:sld>')
            z.writestr('ppt/slides/_rels/slide1.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rel+'</Relationships>')
            if media is not None: z.writestr('ppt/media/photo.png', media)
        delivery = json.loads((self.run / 'delivery.json').read_text())
        Path(delivery['output']).write_bytes(pptx.read_bytes())
        delivery['sha256'] = digest(pptx.read_bytes()); self.write_json(self.run / 'delivery.json', delivery)
        self.add_preview_provenance()
        config = json.loads((self.run / 'build-config.json').read_text())
        config['artifact_image_preview_version'] = version
        self.write_json(self.run / 'build-config.json', config)
        d = prepare_image_preview(self.run / 'validated-output/reconstruction.pptx',
                                  json.loads((self.run / 'resolved-scene.json').read_text()), version=version)
        definition = self.run / 'artifact-image-preview.json'; self.write_json(definition, d)
        audit = json.loads((self.run / 'render-audit.json').read_text())
        audit['evidence']['image_preview_definition'] = review._binding(definition)
        audit['artifact_image_preview'] = {'schema_version': version, 'policy': d['policy'],
            'preview_only': True, 'native_delivery_modified': False, 'reference_pixels_used': False,
            'renderer': d['renderer'], 'renderer_version': d['renderer_version'], 'mupdf_version': d['mupdf_version'],
            'applications': [{'scale': s, 'applied_object_ids': [o['id']for o in d['objects']], 'complete_mixed_paint_order_preserved': True} for s in (1,2,4)],
            'unsupported': [], 'source_pixel_equivalence': False, 'application_playback_verified': False}
        self.write_json(self.run / 'render-audit.json', audit)
        delivery = json.loads((self.run / 'delivery.json').read_text())
        delivery['render_audit_sha256'] = digest((self.run / 'render-audit.json').read_bytes())
        self.write_json(self.run / 'delivery.json', delivery)
        return audit

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_binds_actual_definition_with_user_and_playback_pending(self):
        self.add_image_preview_provenance(); record = review.prepare_output_review(self.run)
        self.assertIn('image_preview_definition', record['bindings'])
        self.assertFalse(record['model_review']['performed'])
        self.assertEqual(record['user_acceptance'], {'status': 'pending'})

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_version2_binds_its_own_policy_and_rejects_downgrade(self):
        self.add_image_preview_provenance(version=2)
        self.assertFalse(review.prepare_output_review(self.run)['model_review']['performed'])
        config = json.loads((self.run / 'build-config.json').read_text()); config['artifact_image_preview_version'] = 1
        self.write_json(self.run / 'build-config.json', config)
        with self.assertRaisesRegex(ValueError, 'definition disagrees'): review.prepare_output_review(self.run)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_fractional_crop_is_replayed_and_forgery_rejected(self):
        audit = self.add_image_preview_provenance(version=2, picture_crop='l="12345" t="0" r="23456" b="0"')
        record = review.prepare_output_review(self.run)
        self.assertFalse(record['model_review']['performed'])
        path = self.run / 'artifact-image-preview.json'; d = json.loads(path.read_text())
        self.assertEqual(d['objects'][0]['native_source_window_exact'], ['2469/20000','0','2392/3125','1'])
        d['objects'][0]['native_source_crop_units']['l'] = 0
        self.write_json(path, d); audit['evidence']['image_preview_definition'] = review._binding(path)
        self.write_json(self.run / 'render-audit.json', audit)
        with self.assertRaisesRegex(ValueError, 'definition disagrees'): review.prepare_output_review(self.run)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_forged_definition_rejected_even_after_its_hash_is_updated(self):
        audit = self.add_image_preview_provenance(); path = self.run / 'artifact-image-preview.json'
        d = json.loads(path.read_text()); d['paint_order'] = [{'id': 'fake', 'type': 'image'}]
        self.write_json(path, d); audit['evidence']['image_preview_definition'] = review._binding(path)
        self.write_json(self.run / 'render-audit.json', audit)
        with self.assertRaisesRegex(ValueError, 'definition disagrees'): review.prepare_output_review(self.run)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_order_and_reference_playback_claims_are_not_accepted(self):
        good = self.add_image_preview_provenance()
        for change in (lambda a: a['applications'].reverse(), lambda a: a.update(reference_pixels_used=True),
                       lambda a: a.update(application_playback_verified=True), lambda a: a.update(source_pixel_equivalence=True)):
            audit = copy.deepcopy(good); change(audit['artifact_image_preview'])
            self.write_json(self.run / 'render-audit.json', audit)
            with self.assertRaisesRegex(ValueError, 'receipt disagrees'): review.prepare_output_review(self.run)

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'), 'optional PyMuPDF source dependency is unavailable')
    def test_picture_preview_unrequested_bad_or_incompatible_version_is_rejected(self):
        self.add_image_preview_provenance(); config = json.loads((self.run / 'build-config.json').read_text())
        for value in (None, True, 2, 3):
            edited = copy.deepcopy(config)
            if value is None: edited.pop('artifact_image_preview_version')
            else: edited['artifact_image_preview_version'] = value
            self.write_json(self.run / 'build-config.json', edited)
            with self.assertRaisesRegex(ValueError, 'picture preview'): review.prepare_output_review(self.run)

    def test_unrequested_pdf_derivation_cannot_be_added_to_artifact_or_legacy_delivery(self):
        for provenance in (False, True):
            if provenance:
                audit = self.add_preview_provenance()
                audit['pdf_alpha_derivation'] = {'policy': 'binary-alpha-white-matte-v1'}
                self.write_json(self.run / 'render-audit.json', audit)
            delivery = json.loads((self.run / 'delivery.json').read_text())
            delivery['pdf_alpha_derivation'] = {'policy': 'binary-alpha-white-matte-v1'}
            self.write_json(self.run / 'delivery.json', delivery)
            with self.subTest(provenance=provenance), self.assertRaisesRegex(ValueError, 'unrequested|not requested'):
                review.prepare_output_review(self.run)

    def add_preview_provenance(self):
        from PIL import Image
        config = json.loads((self.run / 'build-config.json').read_text())
        config.update(preview_backend='artifact', preview_provenance_version=1)
        self.write_json(self.run / 'build-config.json', config)
        previews = {}
        for role, filename, scale in [('preview_1x', 'preview-1x.png', 1), ('preview_2x', 'preview-2x.png', 2),
                                      ('preview_4x', 'preview-4x.png', 4), ('preview_smooth_1x', 'preview-smooth-1x.png', 1)]:
            path = self.run / filename
            Image.new('RGB', (20*scale, 10*scale), 'white').save(path)
            previews[role] = {**review._binding(path), 'width': 20*scale, 'height': 10*scale, 'scale': scale}
        previews['preview_smooth_1x']['derivation'] = {'source_role': 'preview_4x', 'source_sha256': previews['preview_4x']['sha256'],
                                                     'kernel': 'lanczos3', 'target_size': [20, 10], 'is_raw_preview': False}
        self.write_json(self.run / 'font-audit.json', {'test': 'registered font fixture'})
        audit = {'schema_version': 1, 'preview_backend': 'artifact', 'renderer': 'Codex Artifact Tool',
                 'input_pptx': review._binding(self.run / 'validated-output/reconstruction.pptx'),
                 'application_playback_verified': False, 'previews': previews,
                 'evidence': {'font_audit': review._binding(self.run / 'font-audit.json')}}
        self.write_json(self.run / 'render-audit.json', audit)
        delivery = json.loads((self.run / 'delivery.json').read_text())
        delivery.update(preview_backend='artifact', render_audit_sha256=review._binding(self.run / 'render-audit.json')['sha256'])
        self.write_json(self.run / 'delivery.json', delivery)
        return audit

    def add_stroke_preview_provenance(self):
        from figure_rebuild.artifact_stroke_preview import NS, prepare_stroke_preview
        pptx = self.run / 'validated-output/reconstruction.pptx'
        with ZipFile(pptx, 'w') as z:
            z.writestr('ppt/slides/slide1.xml', f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/></p:spTree></p:cSld></p:sld>')
        delivery = json.loads((self.run / 'delivery.json').read_text())
        Path(delivery['output']).write_bytes(pptx.read_bytes())
        delivery['sha256'] = digest(pptx.read_bytes())
        self.write_json(self.run / 'delivery.json', delivery)
        audit = self.add_preview_provenance()
        config = json.loads((self.run / 'build-config.json').read_text())
        config['artifact_stroke_preview_version'] = 1
        self.write_json(self.run / 'build-config.json', config)
        definition = prepare_stroke_preview(pptx, json.loads((self.run / 'resolved-scene.json').read_text()))
        path = self.run / 'artifact-stroke-preview.json'
        self.write_json(path, definition)
        audit['evidence']['stroke_preview_definition'] = review._binding(path)
        audit['artifact_stroke_preview'] = {'schema_version': 1, 'policy': definition['policy'],
            'preview_only': True, 'native_delivery_modified': False, 'applied_object_ids': [],
            'unsupported': [], 'complete_mixed_paint_order_preserved': True,
            'source_pixel_equivalence': False, 'application_playback_verified': False}
        self.save_stroke_preview_provenance(audit)
        return audit, definition

    def save_stroke_preview_provenance(self, audit):
        self.write_json(self.run / 'render-audit.json', audit)
        delivery = json.loads((self.run / 'delivery.json').read_text())
        delivery['render_audit_sha256'] = review._binding(self.run / 'render-audit.json')['sha256']
        self.write_json(self.run / 'delivery.json', delivery)

    def test_stroke_preview_definition_is_replayed_from_actual_native_bytes(self):
        audit, definition = self.add_stroke_preview_provenance()
        record = self.observed()
        review.verify_output_review(self.run, record)
        for field, value in [('native_slide_xml_sha256', '0' * 64), ('objects', [{'id': 'forged-stroke'}]),
                             ('preview_only', False), ('source_pixel_equivalence', True)]:
            changed = copy.deepcopy(definition); changed[field] = value
            path = self.run / 'artifact-stroke-preview.json'; self.write_json(path, changed)
            audit['evidence']['stroke_preview_definition'] = review._binding(path)
            self.save_stroke_preview_provenance(audit)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'actual delivered PPTX'):
                review.prepare_output_review(self.run)
        self.write_json(path, definition)
        audit['evidence']['stroke_preview_definition'] = review._binding(path)
        # A newly hashed and structurally valid native package also invalidates an old definition.
        pptx = self.run / 'validated-output/reconstruction.pptx'
        with ZipFile(pptx) as z: xml = z.read('ppt/slides/slide1.xml')
        with ZipFile(pptx, 'w') as z: z.writestr('ppt/slides/slide1.xml', xml.replace(b'<p:cSld>', b'<p:cSld name="changed">'))
        delivery = json.loads((self.run / 'delivery.json').read_text()); Path(delivery['output']).write_bytes(pptx.read_bytes())
        delivery['sha256'] = digest(pptx.read_bytes()); self.write_json(self.run / 'delivery.json', delivery)
        audit['input_pptx'] = review._binding(pptx); self.save_stroke_preview_provenance(audit)
        with self.assertRaisesRegex(ValueError, 'actual delivered PPTX'): review.prepare_output_review(self.run)

    def test_stroke_preview_application_receipt_cannot_grant_order_or_playback_without_evidence(self):
        audit, _ = self.add_stroke_preview_provenance()
        for field, value in [('applied_object_ids', ['forged-stroke']), ('complete_mixed_paint_order_preserved', False),
                             ('native_delivery_modified', True), ('application_playback_verified', True)]:
            changed = copy.deepcopy(audit); changed['artifact_stroke_preview'][field] = value
            self.save_stroke_preview_provenance(changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'application/order receipt'):
                review.prepare_output_review(self.run)

    def test_stroke_preview_requires_complete_and_requested_backend_provenance(self):
        audit, _ = self.add_stroke_preview_provenance()
        config = json.loads((self.run / 'build-config.json').read_text())
        changed = copy.deepcopy(audit); del changed['evidence']['stroke_preview_definition']
        self.save_stroke_preview_provenance(changed)
        with self.assertRaisesRegex(ValueError, 'definition is missing'): review.prepare_output_review(self.run)
        self.save_stroke_preview_provenance(audit)
        for change in [{'artifact_stroke_preview_version': True}, {'base': 'other.pptx'},
                       {'preview_backend': 'libreoffice'}, {'artifact_stroke_preview_version': None}]:
            self.write_json(self.run / 'build-config.json', {**config, **change})
            with self.subTest(change=change), self.assertRaises(ValueError): review.prepare_output_review(self.run)
        stripped = {k: v for k, v in config.items() if k not in ('preview_backend', 'preview_provenance_version', 'artifact_stroke_preview_version')}
        self.write_json(self.run / 'build-config.json', stripped)
        with self.assertRaisesRegex(ValueError, 'Unrequested'): review.prepare_output_review(self.run)

    def add_diagnostic_provenance(self, objects=None, *, declared_literal=False, source_inventory=False):
        from figure_rebuild.scene_compile import compile_scene
        manifest = json.loads((self.run / 'manifest-snapshot.json').read_text())
        manifest['canvas'] = {'width': 200, 'height': 100}
        manifest['objects'] = objects or []
        if source_inventory:
            root = self.run / 'assets'
            reference = root / 'source/reference.json'
            self.write_json(reference, manifest)
            artifact = root / 'source/original.svg'
            artifact.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><path d="M1 1L10 10"/></svg>')
            inventory = {'schema_version': 1, 'source_sha256': manifest['source']['sha256'],
                'reference_manifest': {'path': 'source/reference.json', 'sha256': review._binding(reference)['sha256']},
                'source_artifacts': [{'path': 'source/original.svg', 'sha256': review._binding(artifact)['sha256']}],
                'components': [{'id': 'reviewed-source-graphic', 'category': 'graphic',
                    'source_region': {'x': 0, 'y': 0, 'width': 200, 'height': 100},
                    'object_ids': [o['id'] for o in manifest['objects']], 'representation': 'native_geometry',
                    'reading_status': 'verified', 'limitations': []}], 'unresolved': []}
            file = root / 'source/inventory.json'; self.write_json(file, inventory)
            manifest['source_inventory'] = {'path': 'source/inventory.json', 'sha256': review._binding(file)['sha256']}
        if declared_literal:
            manifest['source_evidence'] = {'schema_version': 1, 'source_sha256': manifest['source']['sha256'],
                'literals': [{'id': 'source-reading', 'status': 'confirmed', 'object_ids': ['label'], 'text': 'ABC',
                              'source_region': {'x': 10, 'y': 10, 'width': 40, 'height': 20}}], 'connections': []}
        resolved, semantic = compile_scene(manifest, self.run, asset_root=self.run / 'assets')
        self.write_json(self.run / 'manifest-snapshot.json', manifest)
        self.write_json(self.run / 'resolved-scene.json', resolved)
        source = semantic['source_content']
        # Complete synthetic writer-format evidence. Real-font writer outputs
        # are exercised separately in test_output_review_text_fit.py.
        measured = []
        for obj in resolved['objects']:
            if obj['kind'] != 'text':
                continue
            size = obj['font_size']; box = obj['box']; lines = obj['text'].split('\n')
            measured.append({'id': obj['id'], 'box': box, 'layout': {
                'lines': lines, 'line_count': len(lines), 'required_width': max(map(len, lines)) * size * .5,
                'required_height': size * 1.2 * len(lines), 'line_height': size * 1.2,
                'ascent': size * .8, 'descent': size * .2, 'native_baseline_ascent': size * .84,
                'default_native_baseline_ascent': size * .84, 'baseline_basis': 'font_metrics_and_native_leading',
                'line_height_basis': 'default_measured_leading',
                'measurement_basis': 'registered font; approximate PPT line layout; actual preview still required',
                'content_box': box.copy(), 'insets': {k: 0 for k in ('left', 'right', 'top', 'bottom')}}})
        counts = {'resolved_objects': len(resolved['objects']), 'live_text_objects': len(measured),
                  'measured_live_text_objects': len(measured),
                  'unmeasured_path_objects': sum(obj['kind'] == 'path' for obj in resolved['objects']),
                  'unmeasured_image_objects': sum(obj['kind'] == 'image' for obj in resolved['objects'])}
        text_fit = {'schema_version': 1, 'status': 'PASS' if measured else 'NOT_APPLICABLE',
                    'scope': 'measured_live_text_only', 'counts': counts, 'objects': measured,
                    'visual_verification_required': True}
        for filename, data in [('source-content-audit.json', source), ('semantic-audit.json', semantic), ('text-fit.json', text_fit)]:
            self.write_json(self.run / filename, data)
        if source_inventory:
            self.write_json(self.run / 'source-inventory-audit.json', semantic['source_inventory'])
        audit = self.add_preview_provenance()
        config = json.loads((self.run / 'build-config.json').read_text())
        config['diagnostic_provenance_version'] = 1
        self.write_json(self.run / 'build-config.json', config)
        coverage = {'schema_version': 1, 'inputs': {
            role: review._binding(self.run / filename) for role, filename in [
                ('source', 'assets/source/original.png'), ('manifest', 'manifest-snapshot.json'),
                ('resolved_scene', 'resolved-scene.json'), ('pptx', 'validated-output/reconstruction.pptx')]},
            'reports': {
                'source_content_audit': {'artifact': review._binding(self.run / 'source-content-audit.json'),
                    'status': source['status'], 'scope': 'declared_invariants_only', 'counts': source['coverage'] | {}},
                'semantic_audit': {'artifact': review._binding(self.run / 'semantic-audit.json'),
                    'status': 'RECORDED' if semantic['formulas'] or semantic['connections'] else 'NOT_PROVIDED',
                    'scope': 'declared_formula_connection_records_only',
                    'counts': {'formula_records': len(semantic['formulas']), 'connection_records': len(semantic['connections'])}},
                'text_fit': {'artifact': review._binding(self.run / 'text-fit.json'), 'status': text_fit['status'],
                             'scope': text_fit['scope'], 'counts': counts}},
            'semantic_recognition_performed': False, 'source_fidelity_evaluated': False, 'visual_acceptance': 'pending'}
        coverage['reports']['source_content_audit']['counts'].pop('scope')
        if source_inventory:
            inventory = semantic['source_inventory']
            coverage['reports']['source_inventory'] = {'artifact': review._binding(self.run / 'source-inventory-audit.json'),
                'status': inventory['status'], 'scope': inventory['scope'], 'counts': inventory['coverage']}
        audit['diagnostic_coverage'] = coverage
        self.save_diagnostic_audit(audit)
        return audit

    def add_inventory_fixture(self):
        return self.add_diagnostic_provenance([{'id': 'source-operator', 'kind': 'path',
            'commands': [{'moveTo': {'x': 1, 'y': 1}}, {'lineTo': {'x': 10, 'y': 10}}]}], source_inventory=True)

    def test_source_inventory_report_is_bound_and_remains_scoped(self):
        self.add_inventory_fixture(); record = self.observed()
        self.assertIn('source_inventory_audit', record['bindings'])
        report = record['diagnostic_coverage']['reports']['source_inventory']
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['counts']['objects_compared'], 1)
        self.assertFalse(record['diagnostic_coverage']['semantic_recognition_performed'])
        review.verify_output_review(self.run, record)

    def test_changed_source_inventory_sidecars_and_frozen_evidence_fail(self):
        self.add_inventory_fixture(); record = self.observed()
        for relative in ('source-inventory-audit.json', 'assets/source/original.svg', 'assets/source/reference.json'):
            file = self.run / relative; original = file.read_bytes(); file.write_bytes(b'{}')
            try:
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    review.verify_output_review(self.run, record)
            finally:
                file.write_bytes(original)

    def test_source_bound_resolved_geometry_and_diagnostic_provenance_cannot_be_bypassed(self):
        self.add_inventory_fixture()
        resolved = json.loads((self.run / 'resolved-scene.json').read_text())
        resolved['objects'][0]['commands'][1]['lineTo']['x'] += 1
        self.write_json(self.run / 'resolved-scene.json', resolved)
        with self.assertRaisesRegex(ValueError, 'source-bound authoring'): review.prepare_output_review(self.run)
        config = json.loads((self.run / 'build-config.json').read_text()); config.pop('diagnostic_provenance_version')
        self.write_json(self.run / 'build-config.json', config)
        with self.assertRaisesRegex(ValueError, 'requires diagnostic provenance'): review.prepare_output_review(self.run)

    def save_diagnostic_audit(self, audit):
        self.write_json(self.run / 'render-audit.json', audit)
        delivery = json.loads((self.run / 'delivery.json').read_text())
        delivery['render_audit_sha256'] = review._binding(self.run / 'render-audit.json')['sha256']
        delivery['diagnostic_coverage'] = audit['diagnostic_coverage']
        self.write_json(self.run / 'delivery.json', delivery)

    def test_zero_live_text_is_not_applicable_and_not_semantic_approval(self):
        self.add_diagnostic_provenance([{'id': 'outline', 'kind': 'path',
            'commands': [{'moveTo': {'x': 1, 'y': 1}}, {'lineTo': {'x': 10, 'y': 10}}]}])
        record = self.observed()
        coverage = record['diagnostic_coverage']
        self.assertEqual(coverage['reports']['text_fit']['status'], 'NOT_APPLICABLE')
        self.assertEqual(coverage['reports']['text_fit']['counts']['unmeasured_path_objects'], 1)
        self.assertEqual(coverage['reports']['source_content_audit']['status'], 'NOT_PROVIDED')
        self.assertEqual(coverage['reports']['semantic_audit']['status'], 'NOT_PROVIDED')
        self.assertFalse(coverage['semantic_recognition_performed'])
        self.assertTrue({'source_content_audit', 'semantic_audit', 'text_fit'} <= set(record['bindings']))
        review.verify_output_review(self.run, record)

    def test_live_text_and_declared_source_constraint_retain_limited_scope(self):
        self.add_diagnostic_provenance([{'id': 'label', 'kind': 'text', 'text': 'ABC', 'font_size': 12,
            'box': {'x': 10, 'y': 10, 'width': 40, 'height': 20}}], declared_literal=True)
        record = self.observed()
        reports = record['diagnostic_coverage']['reports']
        self.assertEqual(reports['text_fit']['status'], 'PASS')
        self.assertEqual(reports['text_fit']['counts']['measured_live_text_objects'], 1)
        self.assertEqual(reports['source_content_audit']['status'], 'PASS')
        self.assertEqual(reports['source_content_audit']['counts']['literals_checked'], 1)
        self.assertEqual(reports['source_content_audit']['scope'], 'declared_invariants_only')
        review.verify_output_review(self.run, record)

    def test_diagnostic_sidecar_changes_missing_files_and_review_scope_fail(self):
        self.add_diagnostic_provenance()
        record = self.observed()
        for role in ('source_content_audit', 'semantic_audit', 'text_fit'):
            path = Path(record['bindings'][role]['path']); original = path.read_bytes()
            for data in (b'{}', None):
                if data is None: path.unlink()
                else: path.write_bytes(data)
                with self.subTest(role=role, data=data), self.assertRaises(ValueError):
                    review.verify_output_review(self.run, record)
                path.write_bytes(original)
        for field, value in [('semantic_recognition_performed', True), ('source_fidelity_evaluated', True), ('visual_acceptance', 'accepted')]:
            altered = copy.deepcopy(record); altered['diagnostic_coverage'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'coverage'):
                review.verify_output_review(self.run, altered)
        del record['diagnostic_coverage']
        with self.assertRaisesRegex(ValueError, 'coverage'): review.verify_output_review(self.run, record)

    def test_rebound_diagnostics_must_still_match_scene_and_measured_ids(self):
        original = self.add_diagnostic_provenance()
        original_text = json.loads((self.run / 'text-fit.json').read_text())
        for kind in ('zero_pass', 'measured_id', 'integer_type', 'scope'):
            audit = copy.deepcopy(original); text_fit = copy.deepcopy(original_text)
            if kind == 'zero_pass': text_fit['status'] = 'PASS'
            elif kind == 'measured_id': text_fit['objects'] = [{'id': 'invented-label'}]
            elif kind == 'integer_type': text_fit['counts']['resolved_objects'] = False
            else: text_fit['scope'] = 'all_visible_text'
            self.write_json(self.run / 'text-fit.json', text_fit)
            report = audit['diagnostic_coverage']['reports']['text_fit']
            report.update(artifact=review._binding(self.run / 'text-fit.json'), status=text_fit['status'], scope=text_fit['scope'], counts=text_fit['counts'])
            self.save_diagnostic_audit(audit)
            with self.subTest(kind=kind), self.assertRaises(ValueError): review.prepare_output_review(self.run)
        self.write_json(self.run / 'text-fit.json', original_text)
        audit = copy.deepcopy(original)
        source = json.loads((self.run / 'source-content-audit.json').read_text()); source['status'] = 'PASS'
        self.write_json(self.run / 'source-content-audit.json', source)
        semantic = json.loads((self.run / 'semantic-audit.json').read_text()); semantic['source_content'] = source
        self.write_json(self.run / 'semantic-audit.json', semantic)
        for role, filename in [('source_content_audit', 'source-content-audit.json'), ('semantic_audit', 'semantic-audit.json')]:
            audit['diagnostic_coverage']['reports'][role]['artifact'] = review._binding(self.run / filename)
        audit['diagnostic_coverage']['reports']['source_content_audit']['status'] = 'PASS'
        self.save_diagnostic_audit(audit)
        with self.assertRaisesRegex(ValueError, 'declared constraints'): review.prepare_output_review(self.run)

    def test_rebound_text_measurements_need_data_and_internal_fit_consistency(self):
        audit = self.add_diagnostic_provenance([{'id': 'label', 'kind': 'text', 'text': 'ABC', 'font_size': 12,
            'box': {'x': 10, 'y': 10, 'width': 40, 'height': 20}}])
        original = json.loads((self.run / 'text-fit.json').read_text())
        for mutation in ('id_only', 'overflow', 'nan', 'bool_count', 'wrong_text'):
            data = copy.deepcopy(original)
            row = data['objects'][0]
            if mutation == 'id_only': data['objects'] = [{'id': 'label'}]
            elif mutation == 'overflow': row['layout']['required_width'] = 1000
            elif mutation == 'nan': row['layout']['required_width'] = float('nan')
            elif mutation == 'bool_count': row['layout']['line_count'] = True
            else: row['layout']['lines'] = ['changed']
            self.write_json(self.run / 'text-fit.json', data)
            rebound = copy.deepcopy(audit)
            rebound['diagnostic_coverage']['reports']['text_fit']['artifact'] = review._binding(self.run / 'text-fit.json')
            self.save_diagnostic_audit(rebound)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'Text-fit|text-fit.json'):
                review.prepare_output_review(self.run)

    def test_legacy_runs_keep_their_original_binding_contract(self):
        before = review.prepare_output_review(self.run)
        for filename in ('source-content-audit.json', 'semantic-audit.json', 'text-fit.json'):
            self.write_json(self.run / filename, {'legacy': 'not automatically rebound'})
        after = review.prepare_output_review(self.run)
        self.assertEqual(before, after)
        self.assertNotIn('diagnostic_coverage', after)

    def test_new_build_review_binds_renderer_without_granting_playback_verification(self):
        self.add_preview_provenance()
        record = self.observed()
        self.assertIn('render_audit', record['bindings'])
        self.assertIn('font_audit', record['bindings'])
        result = review.verify_output_review(self.run, record)
        self.assertEqual(result['native_application_verification'], 'not_verified')

    def test_renderer_provenance_swaps_and_missing_scale_fail_even_before_review(self):
        audit = self.add_preview_provenance()
        original = copy.deepcopy(audit)
        for change in ('backend', 'pptx', 'scale', 'acceptance'):
            audit = copy.deepcopy(original)
            if change == 'backend': audit['preview_backend'] = 'libreoffice'
            elif change == 'pptx': audit['input_pptx']['sha256'] = '0'*64
            elif change == 'scale': del audit['previews']['preview_4x']
            else: audit['application_playback_verified'] = True
            self.write_json(self.run / 'render-audit.json', audit)
            with self.subTest(change=change), self.assertRaises(ValueError): review.prepare_output_review(self.run)
        (self.run / 'render-audit.json').unlink()
        with self.assertRaises(ValueError): review.prepare_output_review(self.run)

    def test_registered_font_evidence_change_invalidates_new_review(self):
        self.add_preview_provenance()
        record = self.observed()
        (self.run / 'font-audit.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'): review.verify_output_review(self.run, record)

    def test_smooth_preview_derivation_cannot_be_relabelled_or_rebound(self):
        original = self.add_preview_provenance()
        for field, value in [('source_role', 'preview_1x'), ('source_sha256', '0'*64), ('kernel', 'nearest'),
                             ('target_size', [40, 20]), ('is_raw_preview', True)]:
            audit = copy.deepcopy(original)
            audit['previews']['preview_smooth_1x']['derivation'][field] = value
            self.write_json(self.run / 'render-audit.json', audit)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'derivation'):
                review.prepare_output_review(self.run)

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
