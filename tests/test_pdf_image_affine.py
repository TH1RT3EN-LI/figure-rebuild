"""Preserve raster occurrence matrices without substituting vector arrows."""
import hashlib
import io
from decimal import Decimal
from pathlib import Path
import re
import tempfile
import unittest

from PIL import Image
from figure_rebuild.pdf_images import extract_pdf_images, UnsupportedPdfImageError

try:
    import pymupdf
except ImportError:
    pymupdf = None


@unittest.skipIf(pymupdf is None, 'optional source dependency missing')
class PdfAffineImageTests(unittest.TestCase):
    def fixture(self, matrix, *, clipped=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)/'affine.pdf'
        image = Image.new('RGBA', (24, 32), (255, 0, 0, 255))
        image.paste((0, 255, 0, 128), (12, 0, 24, 32))
        image.paste((0, 0, 255, 255), (0, 16, 12, 32))
        image.paste((0, 0, 0, 0), (8, 12, 16, 20))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        with pymupdf.open() as document:
            sheet = document.new_page(width=180, height=180)
            sheet.insert_image(pymupdf.Rect(10, 10, 80, 80), stream=stream.getvalue())
            xref = sheet.get_contents()[-1]
            content = re.sub(rb'[-+\d.eE ]+ cm',
                             (' '.join(format(Decimal(str(v)), 'f') for v in matrix)+' cm').encode(),
                             document.xref_stream(xref))
            if clipped:
                content = b'q 15 165 m 150 165 l 15 20 l h W n\n'+content+b'\nQ'
            document.update_stream(xref, content)
            document.save(path)
        return path

    def test_affine_rotation_shear_reflection_and_alpha_match_native_source(self):
        for matrix in ([75, 28, 16, 90, 20, 30], [-75, 28, 16, 90, 100, 30],
                       [0, 75, -90, 0, 130, 30], [75, .0008, .0011, 90, 20, 30]):
            for clipped in (False, True):
                with self.subTest(matrix=matrix, clipped=clipped):
                    path = self.fixture(matrix, clipped=clipped)
                    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                    with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
                        extract_pdf_images(path)
                    record, = extract_pdf_images(path, allow_affine_rasterization=True)
                    asset = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
                    frame = record['box']
                    receipt = record['provenance']['clip_rasterization']
                    self.assertTrue(receipt['derived_affine_rasterization'])
                    self.assertFalse(receipt['affine_matrix_decomposed'])
                    self.assertFalse(receipt['source_path_or_text_paints_rasterized'])
                    self.assertEqual(record['asset_source_box'], frame)
                    self.assertEqual(record['crop'], dict(left=0., top=0., right=0., bottom=0.))
                    with pymupdf.open(path) as document:
                        # Pixel centers well inside flat-color regions avoid an
                        # ambiguous comparison of two boundary sampling grids.
                        reference = document[0].get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=True)
                        reference = Image.open(io.BytesIO(reference.tobytes('png'))).convert('RGBA')
                    checked = 0
                    for y in range(int(frame['y'])+3, int(frame['y']+frame['height'])-3, 7):
                        for x in range(int(frame['x'])+3, int(frame['x']+frame['width'])-3, 7):
                            neighborhood = [reference.getpixel((x*4+dx, y*4+dy))
                                            for dx in (-3, 0, 3) for dy in (-3, 0, 3)]
                            if len(set(neighborhood)) != 1:
                                continue
                            px = int((x-frame['x'])/frame['width']*asset.width)
                            py = int((y-frame['y'])/frame['height']*asset.height)
                            actual, expected = asset.getpixel((px, py)), neighborhood[0]
                            self.assertLessEqual(max(abs(a-b) for a, b in zip(actual, expected)), 2,
                                                 (matrix, clipped, x, y, actual, expected))
                            checked += 1
                    self.assertGreater(checked, 40)
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_sha)

    def test_affine_permission_is_explicit_boolean(self):
        path = self.fixture([75, 28, 16, 90, 20, 30])
        for flag in (1, 'yes', None):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'boolean'):
                extract_pdf_images(path, allow_affine_rasterization=flag)

    def test_small_nonzero_shear_is_never_silently_axis_aligned(self):
        # A small coefficient is still an authored transform. The default must
        # not replace it by axis stretching; the opt-in keeps the actual CTM.
        path = self.fixture([75, .0000001, 0, 90, 20, 30])
        with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
            extract_pdf_images(path)
        record, = extract_pdf_images(path, allow_affine_rasterization=True)
        receipt = record['provenance']['clip_rasterization']
        self.assertTrue(receipt['derived_affine_rasterization'])
        self.assertNotEqual(receipt['exact_source_unit_transform'][1], 0)
        self.assertFalse(receipt['affine_matrix_decomposed'])
        axis_path = self.fixture([75, 0, 0, 90, 20, 30])
        for mapping in ([[1, .0000001, 0], [0, 1, 0]], [[1, 0, 0], [.0000001, 1, 0]]):
            for allow in (False, True):
                with self.subTest(mapping=mapping, allow=allow):
                    with self.assertRaisesRegex(UnsupportedPdfImageError, 'Rotated or skewed'):
                        extract_pdf_images(axis_path, source_transform=mapping,
                                           allow_affine_rasterization=allow)

    def test_negative_anisotropic_source_mapping_reflects_only_final_asset(self):
        path = self.fixture([75, 28, 16, 90, 20, 30])
        mapping = [[-2, 0, 350], [0, 3, -15]]
        record, = extract_pdf_images(path, source_transform=mapping, allow_affine_rasterization=True)
        asset = Image.open(io.BytesIO(record['asset_bytes'])).convert('RGBA')
        frame = record['box']
        with pymupdf.open(path) as document:
            reference = document[0].get_pixmap(alpha=True)
            reference = Image.open(io.BytesIO(reference.tobytes('png'))).convert('RGBA')
        checked = 0
        for x in range(25, 109, 7):
            for y in range(40, 147, 7):
                neighborhood = [reference.getpixel((x+dx, y+dy))
                                for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
                if len(set(neighborhood)) != 1:
                    continue
                px = int((350-2*x-frame['x'])/frame['width']*asset.width)
                py = int((3*y-15-frame['y'])/frame['height']*asset.height)
                if 0 <= px < asset.width and 0 <= py < asset.height:
                    self.assertLessEqual(max(abs(a-b) for a, b in zip(asset.getpixel((px, py)), neighborhood[0])), 2)
                    checked += 1
        self.assertGreater(checked, 40)
        self.assertEqual(record['provenance']['derived_asset_final_axis_flips'],
                         {'horizontal': True, 'vertical': False})


if __name__ == '__main__':
    unittest.main()
