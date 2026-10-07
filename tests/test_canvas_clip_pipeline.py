"""Canvas exceptions must survive immutable snapshots and stop unsafe placement."""
import copy
import hashlib
import json
from pathlib import Path
from argparse import Namespace
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from figure_rebuild import cli, output_review as review, package

if __package__:
    from . import test_package as packages
    from . import test_postprocess_winding as winding
else:
    import test_package as packages
    import test_postprocess_winding as winding


class CanvasClipPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_base_and_placement_stop_before_runtime_and_run_allocation(self):
        manifest = {'id': 'clipped', 'revision': 1, 'source_canvas_clip': {'version': 1}}
        for base, placement in [('base.pptx', None), (None, [0, 0, 100, 100])]:
            args = Namespace(manifest=str(self.root/'manifest.json'), base=base,
                             placement=placement, output=None)
            with patch.object(cli, 'load_and_validate', return_value=(manifest, {})), \
                    patch.object(cli, 'verify_review'), patch.object(cli, 'runtime') as runtime:
                with self.assertRaisesRegex(ValueError, 'standalone'):
                    cli.build(args)
                runtime.assert_not_called()
            self.assertFalse((self.root/'build').exists())

    def test_source_pdf_and_svg_are_frozen_and_hash_conflicts_rejected(self):
        manifest = {'source': {'path': 'source.png'}, 'objects': [], 'source_canvas_clip': {}}
        for name, raw in [('source.png', b'original png'), ('source.pdf', b'original pdf'), ('source.svg', b'original svg')]:
            (self.root/name).write_bytes(raw)
            record = {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
            if name == 'source.png': manifest['source'] = record
            else: manifest['source_canvas_clip']['source_'+name.rsplit('.', 1)[1]] = record
        run = self.root/'run'; run.mkdir()
        with patch('figure_rebuild.scene_compile.compile_scene', return_value=({}, {'hash_files': []})):
            frozen = cli.freeze_assets(self.root, run, manifest)
        for name in ('source.png', 'source.pdf', 'source.svg'):
            self.assertEqual((frozen/name).read_bytes(), (self.root/name).read_bytes())
        (self.root/'source.pdf').write_bytes(b'later change')
        self.assertEqual((frozen/'source.pdf').read_bytes(), b'original pdf')
        bad = copy.deepcopy(manifest)
        bad['source_canvas_clip']['source_pdf'] = {'path': 'source.png', 'sha256': '0'*64}
        another = self.root/'another'; another.mkdir()
        with self.assertRaisesRegex(ValueError, 'Conflicting checksums'):
            cli.freeze_assets(self.root, another, bad)

    def test_merge_rejects_canvas_marker_even_on_nested_shape(self):
        base = self.root/'base.pptx'
        packages.write_archive(base, packages.fixture([(256, 'ppt/slides/slide1.xml', packages.slide([packages.shape(2, 'base')]))]))
        for index, description in enumerate(('source_id=g; source_canvas_clip_required=true',
                                              'source_id=g;source_canvas_clip_required=true;',
                                              'source_id=g;  source_canvas_clip_required=true  ')):
            shape = packages.shape(3, 'glyph')
            shape.find('p:nvSpPr/p:cNvPr', package.NS).set('descr', description)
            root = packages.slide([packages.group(2, 'group', [shape])])
            overlay = self.root/f'overlay-{index}.pptx'
            packages.write_archive(overlay, packages.fixture([(300, 'ppt/slides/slide9.xml', root)]))
            output = self.root/f'output-{index}.pptx'
            with self.assertRaisesRegex(package.PackageError, 'original slide canvas clipping'):
                package.merge_overlay(base=base, overlay=overlay, output=output, slide_id=256,
                                      base_sha256=package.sha256(base.read_bytes()))
            self.assertFalse(output.exists())

    def test_selected_line_glyph_is_restored_and_marked_without_winding_rewrite(self):
        from figure_rebuild import postprocess
        shape, obj, entry = winding.example(winding.rect(-10, 10, 30, 30), placement=(0, 0, 1000, 1000))
        slide = ET.Element(f'{{{winding.P}}}sld')
        tree = ET.SubElement(ET.SubElement(slide, f'{{{winding.P}}}cSld'), f'{{{winding.P}}}spTree')
        ET.SubElement(tree, f'{{{winding.P}}}grpSpPr'); tree.append(shape)
        source, output = self.root/'input.pptx', self.root/'output.pptx'
        with ZipFile(source, 'w') as archive:
            archive.writestr('ppt/slides/slide1.xml', ET.tostring(slide))
            archive.writestr('unchanged.bin', b'original bytes')
        manifest, mapping, receipt = self.root/'manifest.json', self.root/'map.json', self.root/'receipt.json'
        self.put(manifest, {'canvas': {'width': 1000, 'height': 1000}, 'objects': [obj], 'source_canvas_clip': {'version': 1}})
        original = manifest.read_bytes()
        self.put(mapping, {'placement': [0, 0, 1000, 1000], 'objects': [{'id': obj['id'], 'kind': 'path', 'box': entry['source_box']}]})
        proof = {'object_ids': [obj['id']], 'status': 'VERIFIED_SOURCE_VIEWPORT_GLYPHS'}
        with patch('figure_rebuild.source_canvas_clip.verify_source_canvas_clip', return_value=proof), \
                patch('figure_rebuild.native_winding.normalize_native_polygon_fill', side_effect=AssertionError('No derived winding geometry allowed')):
            postprocess.process(source, output, manifest, receipt, object_map=mapping, asset_root=self.root)
        self.assertEqual(manifest.read_bytes(), original)
        with ZipFile(output) as archive:
            root = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
            self.assertEqual(archive.read('unchanged.bin'), b'original bytes')
        path = root.find('.//a:path', postprocess.NS)
        self.assertEqual(len(path), len(obj['commands']))
        self.assertEqual([node.tag.rsplit('}', 1)[-1] for node in path], ['moveTo', 'lnTo', 'lnTo', 'lnTo', 'close'])
        self.assertIn('source_canvas_clip_required=true', root.find('.//p:cNvPr', postprocess.NS).get('descr'))
        report = json.loads(receipt.read_text())
        self.assertEqual(report['source_canvas_clip'], proof)
        self.assertEqual(report['native_winding_fills'][0]['reason_code'], 'keep_original_canvas_clip_geometry')

    def test_canvas_dependent_base_keeps_its_viewport_when_regular_overlay_is_added(self):
        base_shape = packages.shape(2, 'original-glyph')
        base_shape.find('p:nvSpPr/p:cNvPr', package.NS).set(
            'descr', 'source_id=g; source_canvas_clip_required=true')
        base = self.root/'base.pptx'
        overlay = self.root/'ordinary.pptx'
        output = self.root/'combined.pptx'
        packages.write_archive(base, packages.fixture([
            (256, 'ppt/slides/slide1.xml', packages.slide([base_shape]))]))
        packages.write_archive(overlay, packages.fixture([
            (300, 'ppt/slides/slide9.xml', packages.slide([packages.shape(3, 'new')]))]))
        package.merge_overlay(base=base, overlay=overlay, output=output, slide_id=256,
                              base_sha256=package.sha256(base.read_bytes()))
        with ZipFile(base) as original, ZipFile(output) as merged:
            old = ET.fromstring(original.read('ppt/presentation.xml'))
            new = ET.fromstring(merged.read('ppt/presentation.xml'))
            self.assertEqual(ET.tostring(old.find('p:sldSz', package.NS)),
                             ET.tostring(new.find('p:sldSz', package.NS)))
            slide = ET.fromstring(merged.read('ppt/slides/slide1.xml'))
        retained = slide.find('p:cSld/p:spTree/p:sp', package.NS)
        self.assertEqual(ET.tostring(retained), ET.tostring(base_shape))

    def proof_fixture(self):
        run = self.root/'run'; assets = run/'assets'; assets.mkdir(parents=True)
        declaration = {}
        for role, content in [('source_pdf', b'PDF'), ('source_svg', b'SVG')]:
            path = assets/role; path.write_bytes(content)
            declaration[role] = {'path': role, 'sha256': cli.digest(path)}
        manifest = {'source_canvas_clip': declaration}
        resolved = copy.deepcopy(manifest)
        scene = run/'resolved-scene.json'; self.put(scene, resolved)
        pptx = run/'output.pptx'; pptx.write_bytes(b'actual pptx')
        source_proof = {'schema_version': 1, 'deterministic_source_proof': 'fresh'}
        native_proof = {'schema_version': 1, 'deterministic_native_proof': 'fresh'}
        sp, np = run/'source-canvas-clip.json', run/'native-canvas-clip.json'
        self.put(sp, source_proof); self.put(np, native_proof)
        paths = {'resolved_scene': scene, 'pptx': pptx}
        expected = {'schema_version': 1, 'source_receipt': review._binding(sp),
                    'native_receipt': review._binding(np), 'resolved_scene': review._binding(scene),
                    'pptx': review._binding(pptx)}
        config = {'source_canvas_clip_provenance_version': 1}
        audit = {'source_canvas_clip': expected}
        return run, config, paths, manifest, resolved, audit, source_proof, native_proof

    def test_review_recomputes_both_proofs_and_binds_original_evidence(self):
        run, config, paths, manifest, resolved, audit, source, native = self.proof_fixture()
        with patch('figure_rebuild.source_canvas_clip.verify_source_canvas_clip', return_value=source) as source_call, \
                patch('figure_rebuild.source_canvas_clip.verify_native_canvas_clip', return_value=native) as native_call:
            actual = review._canvas_clip_provenance(run, config, paths, manifest, resolved, audit)
            source_call.assert_called_once_with(resolved, run/'assets')
            native_call.assert_called_once_with(paths['pptx'], resolved, source)
        self.assertEqual(actual, audit['source_canvas_clip'])
        self.assertTrue({'source_canvas_clip', 'native_canvas_clip', 'canvas_source_pdf', 'canvas_source_svg'} <= paths.keys())

    def test_refreshed_hash_cannot_certify_forged_source_or_native_receipt(self):
        run, config, paths, manifest, resolved, audit, source, native = self.proof_fixture()
        for name, correct, role in [('source-canvas-clip.json', source, 'source_receipt'),
                                    ('native-canvas-clip.json', native, 'native_receipt')]:
            self.put(run/name, {**correct, 'schema_version': True})
            audit['source_canvas_clip'][role] = review._binding(run/name)
            with patch('figure_rebuild.source_canvas_clip.verify_source_canvas_clip', return_value=source), \
                    patch('figure_rebuild.source_canvas_clip.verify_native_canvas_clip', return_value=native):
                with self.assertRaisesRegex(ValueError, 'proof is stale'):
                    review._canvas_clip_provenance(run, config, copy.deepcopy(paths), manifest, resolved, audit)
            self.put(run/name, correct); audit['source_canvas_clip'][role] = review._binding(run/name)

    def test_changed_source_descriptor_or_base_cannot_reuse_standalone_proof(self):
        run, config, paths, manifest, resolved, audit, source, native = self.proof_fixture()
        cases = [(dict(config, base={'path': 'base.pptx'}), resolved),
                 (config, {'source_canvas_clip': {}}),
                 ({'source_canvas_clip_provenance_version': True}, resolved)]
        for changed_config, changed_resolved in cases:
            with patch('figure_rebuild.source_canvas_clip.verify_source_canvas_clip') as call:
                with self.assertRaises(ValueError):
                    review._canvas_clip_provenance(run, changed_config, paths, manifest, changed_resolved, audit)
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
