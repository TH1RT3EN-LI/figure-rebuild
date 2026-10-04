"""The SVG aide preserves source baselines when live text is rotated."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from figure_rebuild.export_svg import export


class SourceBaselineSvgTests(unittest.TestCase):
    def test_explicit_character_advances_preserve_literal_spaces_and_source_pivot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'canvas': {'width': 300, 'height': 200}, 'objects': [
                {'id': 'spaced', 'kind': 'text', 'text': 'A B', 'font_size': 10,
                 'anchor': {'x': 50, 'y': 80}, 'rotation': 90, 'character_spacing': [.125, -.25]}]}))
            with patch('figure_rebuild.export_svg.ImageFont.truetype') as font:
                font.return_value.getmetrics.return_value = (8, 2)
                export(manifest, root / 'master.svg', 'face', family='Source Family')
            ns = {'s': 'http://www.w3.org/2000/svg'}
            text = ET.parse(root / 'master.svg').getroot().find('s:text', ns)
            self.assertEqual(''.join(text.itertext()), 'A B')
            self.assertEqual(text.get('transform'), 'rotate(90 50 80)')
            self.assertEqual(text.get('font-kerning'), 'none')
            self.assertEqual([s.get('dx') for s in text.findall('s:tspan', ns)], ['0', '0.125', '-0.25'])

    def test_rotated_baseline_anchor_and_explicit_frame_use_their_own_pivots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            objects = [
                {'id': 'vertical-label', 'kind': 'text', 'text': 'Patch Merging',
                 'font_size': 10, 'anchor': {'x': 50, 'y': 80}, 'rotation': -90},
                {'id': 'frame-label', 'kind': 'text', 'text': 'Other label',
                 'font_size': 10, 'box': {'x': 100, 'y': 40, 'width': 60, 'height': 20},
                 'rotation': 90},
            ]
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'canvas': {'width': 300, 'height': 200},
                                            'objects': objects}))
            with patch('figure_rebuild.export_svg.ImageFont.truetype') as font:
                font.return_value.getmetrics.return_value = (8, 2)
                export(manifest, root / 'master.svg', 'explicit-test-face', family='Source Family')
            nodes = ET.parse(root / 'master.svg').getroot()
            ns = {'s': 'http://www.w3.org/2000/svg'}
            source = nodes.find("s:text[@id='vertical-label']", ns)
            self.assertEqual(source.text, 'Patch Merging')
            self.assertEqual((source.get('x'), source.get('y')), ('50', '80'))
            self.assertEqual(source.get('transform'), 'rotate(-90 50 80)')
            boxed = nodes.find("s:text[@id='frame-label']", ns)
            self.assertEqual(boxed.get('transform'), 'rotate(90 130.0 50.0)')


if __name__ == '__main__':
    unittest.main()
