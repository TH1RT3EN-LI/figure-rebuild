"""Literal and source-length preservation through native character runs."""
import copy
import unittest
from xml.etree import ElementTree as ET
from figure_rebuild.semantic_ooxml import apply_text_layout
from figure_rebuild.text_spacing import (A, P, NS, validate_character_spacing,
    prepare_native_character_spacing, apply_prepared_character_spacing)


def label(text='A B'):
    root = ET.fromstring(f'<p:sp xmlns:p="{P}" xmlns:a="{A}"><p:txBody>'
        '<a:bodyPr wrap="none" tIns="12"/><a:lstStyle/><a:p><a:pPr>'
        '<a:defRPr sz="1800"/></a:pPr><a:r><a:rPr sz="1800" b="0">'
        '<a:solidFill><a:srgbClr val="234567"/></a:solidFill>'
        '<a:latin typeface="Audited Source"/></a:rPr><a:t/></a:r></a:p>'
        '</p:txBody></p:sp>')
    root.find('.//a:t', NS).text = text
    return root


class CharacterSpacingTests(unittest.TestCase):
    def obj(self, **kw):
        return {'id': 'source-label', 'kind': 'text', 'text': 'A B',
                'character_spacing': [.125, -.125], **kw}

    def test_text_spaces_style_and_insets_survive_scaled_native_runs(self):
        root = label(); color = ET.tostring(root.find('.//a:solidFill', NS))
        audit = apply_text_layout(root, self.obj(), {}, .5)['character_spacing']
        runs = root.findall('.//a:r', NS)
        self.assertEqual([x.find('a:t', NS).text for x in runs], ['A', ' ', 'B'])
        self.assertEqual([x.find('a:rPr', NS).get('spc') for x in runs], ['5', '-5', '0'])
        self.assertEqual(root.find('.//a:bodyPr', NS).get('tIns'), '12')
        for run in runs:
            rp = run.find('a:rPr', NS)
            self.assertEqual(rp.get('sz'), '1800')
            self.assertEqual(rp.get('kern'), '400000')
            self.assertEqual(rp.find('a:latin', NS).get('typeface'), 'Audited Source')
            self.assertEqual(ET.tostring(rp.find('a:solidFill', NS)), color)
        self.assertEqual(runs[1].find('a:t', NS).get('{http://www.w3.org/XML/1998/namespace}space'), 'preserve')
        self.assertFalse(audit['native_application_positions_verified'])

    def test_half_point_rounding_is_symmetric_and_input_is_not_rewritten(self):
        root = label(); obj = self.obj(character_spacing=[.02, -.02]); before = copy.deepcopy(obj)
        prepared = prepare_native_character_spacing(root, obj, 1)
        self.assertEqual(len(root.findall('.//a:r', NS)), 1)
        audit = apply_prepared_character_spacing(prepared)
        self.assertEqual(audit['native_spacing_hundredths_pt'], [2, -2, 0])
        self.assertEqual(obj, before)

    def test_absent_spacing_does_not_change_existing_typography(self):
        root = label(); before = ET.tostring(root)
        self.assertIsNone(prepare_native_character_spacing(root, {'kind': 'text'}, 1))
        apply_text_layout(root, {'id': 'existing', 'kind': 'text'}, {}, 1)
        self.assertEqual(ET.tostring(root), before)

    def test_complex_scripts_or_layouts_and_malformed_lengths_are_rejected(self):
        changes = [{'text': 'A\nB'}, {'text': 'e\u0301B'}, {'text': '   '}, {'text': '中文'},
                   {'wrap': 'square'}, {'alignment': 'center'}, {'kind': 'path'},
                   {'character_spacing': [0]}, {'character_spacing': '0,0'},
                   {'character_spacing': [True, 0]}, {'character_spacing': [float('nan'), 0]},
                   {'character_spacing': [10**1000, 0]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_character_spacing(self.obj(**change))

    def test_native_mismatch_or_overflow_leaves_the_original_unchanged(self):
        for root, obj, scale in [(label('Changed'), self.obj(), 1), (label(), self.obj(), 1e100),
                                  (label(), self.obj(), True), (label(), self.obj(), float('inf'))]:
            before = ET.tostring(root)
            with self.assertRaises(ValueError):
                apply_text_layout(root, obj, {}, scale)
            self.assertEqual(ET.tostring(root), before)
        root = label(); ET.SubElement(root.find('.//a:p', NS), f'{{{A}}}br'); before = ET.tostring(root)
        with self.assertRaises(ValueError):
            apply_text_layout(root, self.obj(), {}, 1)
        self.assertEqual(ET.tostring(root), before)


if __name__ == '__main__':
    unittest.main()
