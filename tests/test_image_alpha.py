"""Visible pixels, placement, refusal domains and immutable source assets."""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from figure_rebuild.image_alpha import derive_opaque_rect_image, derive_rgba_border_image


class ImageAssetFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.png'

    def fixture(self, edit=None, **save_options):
        image = Image.new('RGBA', (17, 11), (221, 13, 179, 0))
        for y in range(2, 10):
            for x in range(3, 15):
                image.putpixel((x, y), (x * 13, y * 21, (x + y) * 7, 255))
        if edit:
            edit(image)
        image.save(self.source, **save_options)
        return image, {'id': 'picture', 'kind': 'image', 'editable': False,
                       'path': 'source.png', 'sha256': sha256(self.source.read_bytes()).hexdigest(),
                       'fit': 'stretch', 'box': {'x': -12.375, 'y': 8.125, 'width': 203.75, 'height': 91.5}}


class OpaqueRectangleCropTests(ImageAssetFixture, unittest.TestCase):
    def test_visible_rgb_and_every_pixel_center_keep_their_canvas_position(self):
        image, obj = self.fixture()
        before = deepcopy(obj)
        original = self.source.read_bytes()
        result, receipt = derive_opaque_rect_image(obj, self.root, 'derived/cropped.png')
        self.assertEqual(obj, before)
        self.assertEqual(self.source.read_bytes(), original)
        with Image.open(self.root / result['path']) as derived:
            self.assertEqual(derived.mode, 'RGB')
            self.assertEqual(derived.size, (12, 8))
            self.assertEqual(derived.tobytes(), image.crop((3, 2, 15, 10)).convert('RGB').tobytes())
        for axis, extent, offset, count, old_count in [('x', 'width', 3, 12, 17), ('y', 'height', 2, 8, 11)]:
            for i in range(count):
                old = Fraction(obj['box'][axis]) + Fraction(obj['box'][extent]) * (Fraction(i) + offset + Fraction(1, 2)) / old_count
                new = Fraction(result['box'][axis]) + Fraction(result['box'][extent]) * (Fraction(i) + Fraction(1, 2)) / count
                self.assertLessEqual(abs(old - new), Fraction(1, 1_000_000_000))
        self.assertFalse(receipt['source_PDF_or_filtering_equivalence_claimed'])

    def test_density_metadata_is_preserved(self):
        _, obj = self.fixture(dpi=(120, 144))
        result, _ = derive_opaque_rect_image(obj, self.root, 'crop.png')
        with Image.open(self.source) as source, Image.open(self.root / result['path']) as derived:
            self.assertEqual(source.info, derived.info)

    def test_partial_alpha_holes_and_disconnected_opaque_pixels_are_rejected(self):
        for value, position in [(128, (5, 5)), (0, (5, 5)), (255, (0, 0))]:
            with self.subTest(value=value, position=position):
                _, obj = self.fixture(lambda image: image.putpixel(position, (50, 80, 90, value)))
                with self.assertRaises(ValueError):
                    derive_opaque_rect_image(obj, self.root, 'crop.png')
                self.assertFalse((self.root / 'crop.png').exists())

    def test_fully_opaque_and_fully_transparent_are_not_silently_changed(self):
        for alpha in (0, 255):
            _, obj = self.fixture(lambda image: image.putalpha(alpha))
            with self.assertRaises(ValueError):
                derive_opaque_rect_image(obj, self.root, 'crop.png')

    def test_contain_rotation_crop_and_painted_images_are_rejected(self):
        _, obj = self.fixture()
        for change in [{'fit': 'contain'}, {'rotation': 2}, {'crop': {'left': .1, 'top': 0, 'right': 0, 'bottom': 0}},
                       {'style': {'opacity': .5}}, {'style': {'stroke': '#000000'}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                derive_opaque_rect_image({**obj, **change}, self.root, 'crop.png')

    def test_bad_hash_and_outside_paths_do_not_write(self):
        _, obj = self.fixture()
        for modified, destination in [({**obj, 'sha256': '0' * 64}, 'crop.png'),
                                      (obj, '../escape.png'), (obj, str(self.root / 'absolute.png')),
                                      ({**obj, 'path': '../source.png'}, 'crop.png')]:
            with self.assertRaises(ValueError):
                derive_opaque_rect_image(modified, self.root, destination)
        self.assertFalse((self.root / 'crop.png').exists())

    def test_existing_asset_is_never_overwritten(self):
        _, obj = self.fixture()
        (self.root / 'crop.png').write_bytes(b'previous evidence')
        with self.assertRaises(ValueError):
            derive_opaque_rect_image(obj, self.root, 'crop.png')
        self.assertEqual((self.root / 'crop.png').read_bytes(), b'previous evidence')

    def test_derived_filename_must_match_the_png_encoding(self):
        _, obj = self.fixture()
        with self.assertRaises(ValueError):
            derive_opaque_rect_image(obj, self.root, 'crop.jpg')
        self.assertFalse((self.root / 'crop.jpg').exists())

    def test_profiles_require_separate_preservation(self):
        _, obj = self.fixture(icc_profile=b'untested color profile')
        with self.assertRaises(ValueError):
            derive_opaque_rect_image(obj, self.root, 'crop.png')

    def test_resource_and_numeric_limits_are_not_relaxed(self):
        _, obj = self.fixture()
        with patch('figure_rebuild.image_alpha.MAX_PIXELS', 180), self.assertRaises(ValueError):
            derive_opaque_rect_image(obj, self.root, 'crop.png')
        for value in (True, float('inf'), -1, 10 ** 400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                derive_opaque_rect_image({**obj, 'box': {**obj['box'], 'width': value}}, self.root, 'crop.png')


class RGBABorderCropTests(ImageAssetFixture, unittest.TestCase):
    def test_partial_alpha_holes_and_hidden_rgb_are_kept_without_quantization(self):
        def modify(image):
            for i, alpha in enumerate([0, 1, 128, 253, 254]):
                image.putpixel((5+i, 5), (201+i, 9+i, 77-i, alpha))
        image, obj = self.fixture(modify, dpi=(120, 144))
        original, before = self.source.read_bytes(), deepcopy(obj)
        result, receipt = derive_rgba_border_image(obj, self.root, 'rgba.png')
        self.assertEqual(obj, before)
        self.assertEqual(self.source.read_bytes(), original)
        with Image.open(self.source) as source, Image.open(self.root/result['path']) as derived:
            self.assertEqual(derived.mode, 'RGBA')
            self.assertEqual(derived.size, (12, 8))
            self.assertEqual(derived.info, source.info)
            self.assertEqual(derived.tobytes(), image.crop((3, 2, 15, 10)).tobytes())
        self.assertEqual(receipt['source_alpha_values'], [0, 1, 128, 253, 254, 255])
        self.assertTrue(receipt['all_retained_RGBA_bytes_identical'])
        self.assertFalse(receipt['opaque_rectangle_proved'])
        self.assertFalse(receipt['source_PDF_or_filtering_equivalence_claimed'])
        self.assertEqual(receipt['policy'], 'exact-rgba-transparent-border-trim-v1')

    def test_disconnected_nonopaque_support_keeps_every_pixel_center(self):
        def modify(image):
            image.putalpha(0)
            for point, alpha in [((3, 2), 1), ((14, 9), 254), ((7, 7), 128)]:
                image.putpixel(point, (111, 31, 201, alpha))
        image, obj = self.fixture(modify)
        result, receipt = derive_rgba_border_image(obj, self.root, 'rgba.png')
        with Image.open(self.root/result['path']) as derived:
            self.assertEqual(derived.tobytes(), image.crop((3, 2, 15, 10)).tobytes())
        for axis, extent, offset, count, old_count in [('x', 'width', 3, 12, 17), ('y', 'height', 2, 8, 11)]:
            for i in range(count):
                old = Fraction(obj['box'][axis]) + Fraction(obj['box'][extent]) * (Fraction(i)+offset+Fraction(1, 2))/old_count
                new = Fraction(result['box'][axis]) + Fraction(result['box'][extent]) * (Fraction(i)+Fraction(1, 2))/count
                self.assertLessEqual(abs(new-old), Fraction(1, 1_000_000_000))
        self.assertEqual(receipt['nonzero_alpha_pixel_cell_bounds'], [3, 2, 15, 10])

    def test_no_border_and_empty_support_are_refused_before_writing(self):
        for alpha in [0, 1, 254, 255]:
            _, obj = self.fixture(lambda image: image.putalpha(alpha))
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                derive_rgba_border_image(obj, self.root, 'rgba.png')
            self.assertFalse((self.root/'rgba.png').exists())

    def test_rgba_policy_keeps_the_shared_hash_context_and_resource_guards(self):
        _, obj = self.fixture()
        for change in [{'fit': 'contain'}, {'rotation': 1}, {'sha256': '0'*64},
                       {'crop': {'left': .1, 'right': 0, 'top': 0, 'bottom': 0}},
                       {'style': {'opacity': .5}}, {'path': '../source.png'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                derive_rgba_border_image({**obj, **change}, self.root, 'rgba.png')
        with patch('figure_rebuild.image_alpha.MAX_PIXELS', 180), self.assertRaises(ValueError):
            derive_rgba_border_image(obj, self.root, 'rgba.png')
        with self.assertRaises(ValueError):
            derive_rgba_border_image(obj, self.root, '../rgba.png')
        self.assertFalse((self.root/'rgba.png').exists())
