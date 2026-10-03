"""Explicit image fit requests must not fall back silently during validation."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from figure_rebuild.validate import digest, validate


class ImageFitValidationTests(unittest.TestCase):
    def test_supported_and_invalid_image_fit_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (200, 100), 'white').save(root / 'source.png')
            original_sha = digest(root / 'source.png')
            obj = {'id': 'image', 'kind': 'image', 'path': 'source.png', 'sha256': original_sha,
                   'editable': False, 'box': {'x': 10, 'y': 10, 'width': 20, 'height': 80}}
            manifest = {'schema_version': 1, 'id': 'image-fit', 'revision': 1,
                        'source': {'kind': 'user_original', 'path': 'source.png', 'sha256': original_sha,
                                   'width': 200, 'height': 100},
                        'canvas': {'width': 200, 'height': 100},
                        'recognition': {'provider': 'calling_host', 'status': 'reviewed'}, 'objects': [obj]}
            self.assertEqual(validate(manifest, root)['status'], 'PASS')
            for fit in ('contain', 'stretch'):
                obj['fit'] = fit
                with self.subTest(fit=fit):
                    self.assertEqual(validate(manifest, root)['status'], 'PASS')
            for fit in ('cover', 'fill', '', None, [], {}, True, 0):
                obj['fit'] = fit
                with self.subTest(fit=fit):
                    report = validate(manifest, root)
                    self.assertEqual(report['status'], 'FAIL')
                    self.assertTrue(any('Invalid image fit' in message for message in report['errors']))
            self.assertEqual(digest(root / 'source.png'), original_sha)


if __name__ == '__main__':
    unittest.main()
