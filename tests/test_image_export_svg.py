"""The vector aide must use the same full-byte crop and frame semantics as PPT."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

from figure_rebuild.export_svg import export


class ImageSvgPlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Image.new('RGB', (120, 60), 'red').save(self.root / 'asset.png')
        self.original = (self.root / 'asset.png').read_bytes()
        self.object = {'id': 'cropped', 'kind': 'image', 'path': 'asset.png',
                       'box': {'x': 10, 'y': 20, 'width': 160, 'height': 100},
                       'crop': {'left': .25, 'top': 0, 'right': .25, 'bottom': 0}}

    def tearDown(self):
        self.temp.cleanup()

    def render(self, objects):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'canvas': {'width': 400, 'height': 200}, 'objects': objects}))
        output = self.root / 'source.svg'
        export(manifest, output, 'unused-font-path', family='Arial')
        return ET.parse(output).getroot()

    def image(self, root, object_id):
        panel = root.find("{http://www.w3.org/2000/svg}svg[@id='%s']" % object_id)
        self.assertIsNotNone(panel)
        embedded = panel.find('{http://www.w3.org/2000/svg}image')
        self.assertEqual(base64.b64decode(embedded.get('href').split(',', 1)[1]), self.original)
        self.assertEqual(panel.get('overflow'), 'hidden')
        return panel

    def test_contain_crop_uses_fitted_viewport_so_removed_pixels_cannot_fill_margins(self):
        panel = self.image(self.render([self.object]), 'cropped')
        self.assertEqual([float(panel.get(k)) for k in ('x', 'y', 'width', 'height')], [40, 20, 100, 100])
        self.assertEqual([float(v) for v in panel.get('viewBox').split()], [30, 0, 60, 60])

    def test_explicit_stretch_uses_exact_box_and_preserves_source_bytes(self):
        panel = self.image(self.render([{**self.object, 'fit': 'stretch'}]), 'cropped')
        self.assertEqual([float(panel.get(k)) for k in ('x', 'y', 'width', 'height')], [10, 20, 160, 100])
        self.assertEqual(panel.get('preserveAspectRatio'), 'none')
        self.assertEqual([float(v) for v in panel.get('viewBox').split()], [30, 0, 60, 60])
        self.assertEqual((self.root / 'asset.png').read_bytes(), self.original)

    def test_repeated_image_bytes_can_have_independent_crop_and_fit(self):
        second = {**self.object, 'id': 'second', 'fit': 'stretch',
                  'crop': {'left': .5, 'top': .25, 'right': 0, 'bottom': .25}}
        root = self.render([self.object, second])
        first = self.image(root, 'cropped'); other = self.image(root, 'second')
        self.assertNotEqual(first.get('viewBox'), other.get('viewBox'))
        self.assertEqual(float(first.get('width')), 100)
        self.assertEqual(float(other.get('width')), 160)

    def test_unsupported_fit_cannot_silently_choose_contain(self):
        for fit in ('cover', None, [], True):
            with self.subTest(fit=fit), self.assertRaisesRegex(ValueError, 'Invalid image fit'):
                self.render([{**self.object, 'fit': fit}])


if __name__ == '__main__':
    unittest.main()
