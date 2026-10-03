"""True SVG formula embedding with PNG fallback and explicit native text layout."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from figure_rebuild.package import XmlDocument
from figure_rebuild.semantic_ooxml import (A, P, R, REL, CT, ASVG, NS,
                                                SVG_EXTENSION, add_formula_svg, apply_text_layout)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 4"><path d="M 0 0 L 10 4"/></svg>'
PNG = b'unchanged high-density PNG bytes, validated by the formula asset resolver'


def picture():
    element = ET.Element(f'{{{P}}}pic')
    fill = ET.SubElement(element, f'{{{P}}}blipFill')
    blip = ET.SubElement(fill, f'{{{A}}}blip', {f'{{{R}}}embed': 'rId1'})
    extensions = ET.SubElement(blip, f'{{{A}}}extLst')
    ET.SubElement(extensions, f'{{{A}}}ext', {'uri': '{kept-native-dpi-metadata}'})
    props = ET.SubElement(element, f'{{{P}}}spPr')
    ET.SubElement(props, f'{{{A}}}xfrm')
    return element


def textbox(top_inset='0', paragraphs=2):
    element = ET.Element(f'{{{P}}}sp')
    body = ET.SubElement(element, f'{{{P}}}txBody')
    ET.SubElement(body, f'{{{A}}}bodyPr', {'anchor': 't', 'tIns': top_inset, 'lIns': '1234'})
    ET.SubElement(body, f'{{{A}}}lstStyle')
    for index in range(paragraphs):
        paragraph = ET.SubElement(body, f'{{{A}}}p')
        props = ET.SubElement(paragraph, f'{{{A}}}pPr', {'algn': 'ctr'})
        leading = ET.SubElement(props, f'{{{A}}}lnSpc')
        ET.SubElement(leading, f'{{{A}}}spcPct', {'val': '120000'})
        run = ET.SubElement(paragraph, f'{{{A}}}r')
        ET.SubElement(run, f'{{{A}}}t').text = 'Content ' + str(index)
    return element


class FormulaSVGTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'formula.svg').write_bytes(SVG)
        self.obj = {'id': 'formula-alpha', 'kind': 'image', 'source_kind': 'formula',
                    'sha256': hashlib.sha256(PNG).hexdigest(), 'formula_asset': {
                        'representation': 'svg', 'svg_path': 'formula.svg', 'png_path': 'formula.png',
                        'hash_files': [{'path': 'formula.svg', 'sha256': hashlib.sha256(SVG).hexdigest()}],
                        'audit': {'reference_crop_used': False, 'rotation_deg': 0,
                                  'natural_display_size_px': [10, 4],
                                  'vector_geometry': {'embeddedfont_outlines': True,
                                                      'external_references': False, 'rotation_deg': 0}}}}
        self.payloads = {'ppt/media/original-formula.png': PNG}
        self.rels = XmlDocument.parse((f'<Relationships xmlns="{REL}"><Relationship Id="rId1" '
            f'Type="{R}/image" Target="../media/original-formula.png"/></Relationships>').encode(), 'rels')
        self.types = XmlDocument.parse((f'<Types xmlns="{CT}"><Default Extension="png" '
            'ContentType="image/png"/></Types>').encode(), 'types')
        self.element = picture()

    def add(self, element=None, obj=None):
        return add_formula_svg(self.element if element is None else element,
                               self.obj if obj is None else obj, self.payloads,
                               self.rels, self.types, self.root)

    def test_svg_embedding_keeps_png_fallback_and_existing_extensions(self):
        png_before = self.payloads.copy()
        audit = self.add()
        self.assertTrue(audit['svg_embedded'])
        self.assertEqual(self.payloads[audit['svg_member']], SVG)
        self.assertEqual(self.payloads['ppt/media/original-formula.png'], png_before['ppt/media/original-formula.png'])
        blip = self.element.find('p:blipFill/a:blip', NS)
        self.assertEqual(blip.get(f'{{{R}}}embed'), 'rId1')
        self.assertEqual(blip.find('a:extLst/a:ext', NS).get('uri'), '{kept-native-dpi-metadata}')
        svg = blip.find(f'a:extLst/a:ext[@uri="{SVG_EXTENSION}"]/asvg:svgBlip', NS)
        self.assertEqual(svg.get(f'{{{R}}}embed'), audit['svg_relationship'])
        self.assertEqual(self.rels.root[-1].get('TargetMode'), None)
        self.assertEqual(self.types.root[-1].attrib, {'Extension': 'svg', 'ContentType': 'image/svg+xml'})
        self.assertIn(ASVG.encode(), ET.tostring(self.element))

    def test_identical_svg_bytes_deduplicate_media_and_relationship(self):
        first = self.add()
        second = self.add(picture(), {**self.obj, 'id': 'formula-beta'})
        self.assertEqual(first['svg_member'], second['svg_member'])
        self.assertEqual(first['svg_relationship'], second['svg_relationship'])
        self.assertEqual(len(self.payloads), 2)
        self.assertEqual(len(self.rels.root), 2)
        self.assertEqual(len(self.types.root), 2)

    def test_png_representation_does_not_require_or_embed_svg(self):
        obj = copy.deepcopy(self.obj)
        obj['formula_asset'].update(representation='png', svg_path=None, hash_files=[])
        audit = self.add(obj=obj)
        self.assertFalse(audit['svg_embedded'])
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(len(self.rels.root), 1)

    def test_changed_svg_hash_and_missing_hash_leave_everything_unchanged(self):
        before = (self.payloads.copy(), ET.tostring(self.rels.root), ET.tostring(self.types.root), ET.tostring(self.element))
        (self.root / 'formula.svg').write_bytes(SVG + b' ')
        with self.assertRaisesRegex(ValueError, 'output hash changed'):
            self.add()
        self.assertEqual(before, (self.payloads.copy(), ET.tostring(self.rels.root), ET.tostring(self.types.root), ET.tostring(self.element)))
        self.obj['formula_asset']['hash_files'] = []
        with self.assertRaisesRegex(ValueError, 'pinned exactly once'):
            self.add()

    def test_external_svg_content_and_job_escape_are_rejected(self):
        external = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 4"><path d="M 0 0"/><use href="https://example.org/glyph.svg#x"/></svg>'
        (self.root / 'formula.svg').write_bytes(external)
        self.obj['formula_asset']['hash_files'][0]['sha256'] = hashlib.sha256(external).hexdigest()
        with self.assertRaisesRegex(ValueError, 'external references'):
            self.add()
        self.obj['formula_asset'].update(svg_path='../formula.svg', hash_files=[{'path': '../formula.svg', 'sha256': '0'*64}])
        with self.assertRaisesRegex(ValueError, 'escapes the frozen job'):
            self.add()

    def test_fallback_bytes_and_external_fallback_relationship_are_rejected(self):
        self.payloads['ppt/media/original-formula.png'] = b'changed'
        with self.assertRaisesRegex(ValueError, 'PNG fallback bytes changed'):
            self.add()
        self.payloads['ppt/media/original-formula.png'] = PNG
        self.rels.root[0].set('TargetMode', 'External')
        with self.assertRaisesRegex(ValueError, 'PNG fallback relationship is invalid'):
            self.add()

    def test_formula_rotation_must_be_baked_and_match_both_assets(self):
        audit = self.obj['formula_asset']['audit']
        audit['rotation_deg'] = 90
        with self.assertRaisesRegex(ValueError, 'audited rotation disagrees'):
            self.add()
        audit['vector_geometry']['rotation_deg'] = 90
        self.assertEqual(self.add()['rotation_baked_deg'], 90)
        self.element = picture()
        self.element.find('p:spPr/a:xfrm', NS).set('rot', '5400000')
        with self.assertRaisesRegex(ValueError, 'native frame rotation'):
            self.add()

    def test_conflicting_content_type_ink_frame_and_duplicate_extension_fail(self):
        ET.SubElement(self.types.root, f'{{{CT}}}Default', {'Extension': 'svg', 'ContentType': 'wrong/type'})
        with self.assertRaisesRegex(ValueError, 'Conflicting SVG content-type'):
            self.add()
        self.types.root.remove(self.types.root[-1])
        self.obj['formula_asset']['audit']['natural_display_size_px'] = [9, 4]
        with self.assertRaisesRegex(ValueError, 'ink frames disagree'):
            self.add()
        self.obj['formula_asset']['audit']['natural_display_size_px'] = [10, 4]
        self.add()
        with self.assertRaisesRegex(ValueError, 'extension already exists'):
            self.add()

    def test_svg_part_content_type_override_cannot_hide_a_mismatch(self):
        member = 'ppt/media/formula-' + hashlib.sha256(SVG).hexdigest() + '.svg'
        ET.SubElement(self.types.root, f'{{{CT}}}Override', {'PartName': '/' + member,
                                                          'ContentType': 'image/png'})
        with self.assertRaisesRegex(ValueError, 'Conflicting SVG media-part content type'):
            self.add()
        self.assertNotIn(member, self.payloads)


class NativeTextLayoutTests(unittest.TestCase):
    def test_percentage_spacing_preserves_paragraph_editability_and_reports_fractional_precision(self):
        element = textbox()
        original_text = [node.text for node in element.findall('.//a:t', NS)]
        mapped = {'text_layout': {'native_baseline_ascent': 20, 'baseline_adjustment_px': 4, 'line_count': 50,
            'renderer_baseline': {'model': 'artifact_presentation_v1', 'first_baseline_px': 16, 'scale': 1,
                'spacing': 'percent_of_natural_line', 'spacing_thousandths_percent': 83333,
                'natural_line_height_px': 24, 'line_height_px': 24 * 83333 / 100000}}}
        audit = apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 20}, mapped, 1)
        properties = element.findall('p:txBody/a:p/a:pPr', NS)
        self.assertTrue(all(node.find('a:lnSpc/a:spcPct', NS).get('val') == '83333' for node in properties))
        self.assertTrue(all(node.find('a:lnSpc/a:spcPts', NS) is None for node in properties))
        self.assertEqual([node.text for node in element.findall('.//a:t', NS)], original_text)
        self.assertTrue(audit['fractional_native_percent'])
        self.assertTrue(audit['native_precision_review_required'])
        self.assertAlmostEqual(audit['whole_percent_fallback_accumulated_loss_px'], 49 * 24 * .00333)
        self.assertNotIn('spacing_hundredths_pt', audit)
        before = ET.tostring(element)
        mapped['text_layout']['renderer_baseline']['line_height_px'] = 21
        with self.assertRaisesRegex(ValueError, 'percentage line height is inconsistent'):
            apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 20}, mapped, 1)
        self.assertEqual(ET.tostring(element), before)

    def test_renderer_baseline_correction_preserves_content_height_and_all_vertical_alignments(self):
        for anchor in ('t', 'ctr', 'b'):
            for adjustment in (-2.25, 3.75):
                element = textbox(top_inset='19050')
                body = element.find('p:txBody/a:bodyPr', NS)
                body.set('anchor', anchor)
                body.set('bIns', '28575')
                mapped = {'text_layout': {'native_baseline_ascent': 14,
                    'baseline_adjustment_px': adjustment,
                    'renderer_baseline': {'model': 'artifact_presentation_v1',
                                          'first_baseline_px': 14 - adjustment, 'scale': .5}}}
                audit = apply_text_layout(element, {'id': 'label', 'kind': 'text'}, mapped, .5)
                self.assertEqual(int(body.get('tIns')) + int(body.get('bIns')), 47625)
                self.assertEqual(body.get('anchor'), anchor)
                self.assertEqual(body.get('lIns'), '1234')
                self.assertTrue(audit['native_content_height_preserved'])
                self.assertTrue(audit['baseline_calibrated'])

    def test_exact_line_height_can_use_a_negative_derived_bottom_inset(self):
        element = textbox()
        mapped = {'text_layout': {'native_baseline_ascent': 20, 'baseline_adjustment_px': 4,
            'renderer_baseline': {'model': 'artifact_presentation_v1', 'first_baseline_px': 16, 'scale': 2}}}
        audit = apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 20,
                                            'baseline_offset': 20}, mapped, 2)
        body = element.find('p:txBody/a:bodyPr', NS)
        self.assertEqual(body.get('tIns'), '76200')
        self.assertEqual(body.get('bIns'), '-76200')
        self.assertEqual(audit['spacing_hundredths_pt'], 3000)

    def test_inconsistent_renderer_metrics_fail_before_mutating_spacing_or_insets(self):
        base = {'native_baseline_ascent': 20, 'baseline_adjustment_px': 4,
            'renderer_baseline': {'model': 'artifact_presentation_v1', 'first_baseline_px': 16, 'scale': 1}}
        for changed in ({'baseline_adjustment_px': 5}, {'native_baseline_ascent': 21},
                        {'renderer_baseline': {**base['renderer_baseline'], 'scale': 2}},
                        {'renderer_baseline': {**base['renderer_baseline'], 'first_baseline_px': float('nan')}}):
            element = textbox()
            before = ET.tostring(element)
            with self.assertRaises(ValueError):
                apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 20},
                                  {'text_layout': {**base, **changed}}, 1)
            self.assertEqual(ET.tostring(element), before)

    def test_explicit_line_height_updates_all_native_paragraphs(self):
        element = textbox()
        content = [node.text for node in element.findall('.//a:t', NS)]
        audit = apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 16}, {}, 2)
        self.assertEqual(audit['spacing_hundredths_pt'], 2400)
        properties = element.findall('p:txBody/a:p/a:pPr', NS)
        self.assertTrue(all(node.get('algn') == 'ctr' for node in properties))
        self.assertTrue(all(node.find('a:lnSpc/a:spcPts', NS).get('val') == '2400' for node in properties))
        self.assertTrue(all(node.find('a:lnSpc/a:spcPct', NS) is None for node in properties))
        self.assertEqual([node.text for node in element.findall('.//a:t', NS)], content)

    def test_baseline_calibration_adjusts_only_top_inset_and_reports_approximation(self):
        element = textbox(top_inset='9525')
        obj = {'id': 'label', 'kind': 'text', 'baseline_offset': 14}
        mapped = {'text_layout': {'default_native_baseline_ascent': 12, 'native_baseline_ascent': 14}}
        audit = apply_text_layout(element, obj, mapped, .5)
        body = element.find('p:txBody/a:bodyPr', NS)
        self.assertEqual(body.get('tIns'), '19050')
        self.assertEqual(body.get('lIns'), '1234')
        self.assertTrue(audit['baseline_calibrated'])
        self.assertTrue(audit['visual_verification_required'])

    def test_missing_or_inconsistent_metrics_and_negative_native_inset_fail_atomically(self):
        element = textbox()
        obj = {'id': 'label', 'kind': 'text', 'baseline_offset': 10, 'line_height': 14}
        before = ET.tostring(element)
        for mapped in ({}, {'text_layout': {'native_baseline_ascent': 10}},
                       {'text_layout': {'native_baseline_ascent': 9, 'default_native_baseline_ascent': 8}},
                       {'text_layout': {'native_baseline_ascent': 10, 'default_native_baseline_ascent': 12}}):
            with self.assertRaises(ValueError):
                apply_text_layout(element, obj, mapped, 1)
            self.assertEqual(ET.tostring(element), before)

    def test_existing_text_without_explicit_calibration_is_byte_unchanged(self):
        element = textbox()
        before = ET.tostring(element)
        audit = apply_text_layout(element, {'id': 'label', 'kind': 'text'}, None, 1)
        self.assertEqual(ET.tostring(element), before)
        self.assertFalse(audit['line_height_applied'])
        self.assertFalse(audit['baseline_calibrated'])

    def test_paragraph_properties_are_created_and_invalid_values_fail(self):
        element = textbox(paragraphs=1)
        paragraph = element.find('p:txBody/a:p', NS)
        paragraph.remove(paragraph[0])
        apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': 12}, {}, 1)
        self.assertEqual(paragraph[0].tag, f'{{{A}}}pPr')
        for value in (0, -1, float('inf'), True):
            with self.assertRaises(ValueError):
                apply_text_layout(element, {'id': 'label', 'kind': 'text', 'line_height': value}, {}, 1)


if __name__ == '__main__':
    unittest.main()
