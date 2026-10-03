import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw

from figure_rebuild import crop_refine as c
VISION_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('cv2', 'numpy'))
needs_vision = unittest.skipUnless(VISION_AVAILABLE, 'Optional vision dependencies are not installed')


class CropRefinement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.path = self.root / 'reference.png'

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, image):
        image.save(self.path)

    @needs_vision
    def test_disconnected_one_pixel_dot_and_label_are_retained(self):
        image = Image.new('RGB', (40, 30), 'white'); draw = ImageDraw.Draw(image)
        draw.rectangle((12, 10, 20, 15), fill='black')
        draw.point((4, 3), fill='black'); draw.point((34, 25), fill='black')
        self.save(image)
        report = c.refine_crop(self.path, [0, 0, 40, 30], padding=0)
        self.assertEqual(report['detected_content_bbox'], [4, 3, 31, 23])
        self.assertEqual(report['diagnostics']['retained_component_count'], 3)
        self.assertEqual(report['diagnostics']['excluded_foreground_pixels'], 0)
        self.assertTrue(report['review_required'])
        json.dumps(report, allow_nan=False)

    @needs_vision
    def test_transparent_black_is_not_foreground(self):
        image = Image.new('RGBA', (20, 10), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((6, 3, 8, 5), fill=(0, 0, 0, 255))
        self.save(image)
        report = c.refine_crop(self.path, [0, 0, 20, 10], padding=0)
        self.assertEqual(report['detected_content_bbox'], [6, 3, 3, 3])
        self.assertEqual(report['diagnostics']['foreground_pixels'], 9)

    @needs_vision
    def test_configurable_background_and_tolerance(self):
        image = Image.new('RGB', (20, 10), (10, 40, 80))
        ImageDraw.Draw(image).point((5, 4), fill=(10, 60, 80)); self.save(image)
        report = c.refine_crop(self.path, [0, 0, 20, 10], background='#0a2850', tolerance=18, padding=0)
        self.assertEqual(report['detected_content_bbox'], [5, 4, 1, 1])
        empty = c.refine_crop(self.path, [0, 0, 20, 10], background=[10, 40, 80], tolerance=20)
        self.assertEqual(empty['status'], 'empty')

    @needs_vision
    def test_source_bytes_unchanged_and_crop_maps_full_image_margins(self):
        image = Image.new('RGB', (100, 50), 'white')
        ImageDraw.Draw(image).rectangle((30, 15, 49, 24), fill='black'); self.save(image)
        original = self.path.read_bytes()
        report = c.refine_crop(self.path, [10, 5, 60, 30], padding=2)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(report['source']['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(report['proposed_region'], [28, 13, 24, 14])
        self.assertEqual(report['proposed_crop'], {'left': .28, 'top': .26, 'right': .48, 'bottom': .46})
        crop = report['proposed_crop']
        self.assertAlmostEqual((1 - crop['left'] - crop['right']) * 100, 24)
        self.assertAlmostEqual((1 - crop['top'] - crop['bottom']) * 50, 14)

    @needs_vision
    def test_fractional_roi_rounds_outward_and_padding_stays_inside_selected_roi(self):
        image = Image.new('RGB', (20, 10), 'white')
        ImageDraw.Draw(image).point((4, 2), fill='black'); self.save(image)
        report = c.refine_crop(self.path, [4.2, 2.4, 2.1, 2.2], padding=100)
        self.assertEqual(report['requested_region'], [4.2, 2.4, 2.1, 2.2])
        self.assertEqual(report['effective_region'], [4, 2, 3, 3])
        self.assertEqual(report['proposed_region'], [4, 2, 3, 3])

    @needs_vision
    def test_content_outside_roi_cannot_expand_proposal(self):
        image = Image.new('RGB', (20, 10), 'white'); draw = ImageDraw.Draw(image)
        draw.point((0, 0), fill='black'); draw.point((9, 4), fill='black'); self.save(image)
        report = c.refine_crop(self.path, [5, 2, 10, 6], padding=0)
        self.assertEqual(report['proposed_region'], [9, 4, 1, 1])

    @needs_vision
    def test_empty_detection_never_suggests_deletion(self):
        self.save(Image.new('RGB', (20, 10), 'white'))
        report = c.refine_crop(self.path, [0, 0, 20, 10])
        self.assertEqual(report['status'], 'empty')
        self.assertIsNone(report['detected_content_bbox'])
        self.assertIsNone(report['proposed_region'])
        self.assertIsNone(report['proposed_crop'])
        self.assertTrue(report['review_required'])
        self.assertTrue(any('not a deletion' in note for note in report['notes']))

    @needs_vision
    def test_larger_min_area_is_explicit_and_reports_excluded_pixels(self):
        image = Image.new('RGB', (20, 10), 'white'); draw = ImageDraw.Draw(image)
        draw.point((1, 1), fill='black'); draw.rectangle((8, 3, 10, 5), fill='black'); self.save(image)
        report = c.refine_crop(self.path, [0, 0, 20, 10], min_area=2, padding=0)
        self.assertEqual(report['detected_content_bbox'], [8, 3, 3, 3])
        self.assertEqual(report['diagnostics']['excluded_foreground_pixels'], 1)
        self.assertTrue(any('tiny dots' in note for note in report['notes']))

    def test_invalid_parameters_are_rejected_before_detection(self):
        self.save(Image.new('RGB', (20, 10), 'white'))
        for region in (None, [], [0, 0, 20], [-.1, 0, 20, 10], [0, 0, 20.1, 10],
                       [0, 0, 0, 10], [0, 0, 20, -1], [0, 0, float('nan'), 1],
                       [0, 0, float('inf'), 1], [True, 0, 1, 1], [0, 0, 10**1000, 1],
                       [10, 2, 1e-300, 1], [2, 4, 1, 1e-300]):
            with self.subTest(region=region), self.assertRaises(ValueError):
                c.refine_crop(self.path, region)
        for parameters in ({'tolerance': float('nan')}, {'tolerance': -1}, {'tolerance': 256},
                           {'tolerance': True}, {'padding': -1}, {'padding': .5}, {'padding': True},
                           {'min_area': 0}, {'min_area': 1.5}, {'background': '#fff'},
                           {'background': [0, 0, 256]}, {'background': [0, True, 0]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                c.refine_crop(self.path, [0, 0, 20, 10], **parameters)

    def test_missing_optional_dependencies_give_actionable_message(self):
        import builtins
        original_import = builtins.__import__
        def no_opencv(name, *args, **kwargs):
            if name == 'cv2':
                raise ImportError('missing test dependency')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=no_opencv), self.assertRaisesRegex(ValueError, 'requirements-vision.txt'):
            c._vision()

    def test_broken_binary_dependency_gives_actionable_message(self):
        import builtins
        original_import = builtins.__import__
        def broken_opencv(name, *args, **kwargs):
            if name == 'cv2':
                raise OSError('binary could not load')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=broken_opencv), self.assertRaisesRegex(ValueError, 'requirements-vision.txt'):
            c._vision()

    @needs_vision
    def test_preview_is_deterministic_separate_copy_and_source_bound(self):
        image = Image.new('RGB', (20, 10), 'white')
        ImageDraw.Draw(image).rectangle((5, 3, 9, 6), fill='black'); self.save(image)
        original = self.path.read_bytes(); report = c.refine_crop(self.path, [0, 0, 20, 10], padding=0)
        first, second = self.root / 'first.png', self.root / 'second.png'
        self.assertEqual(c.save_crop_preview(self.path, report, first), str(first.resolve()))
        c.save_crop_preview(self.path, report, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(self.path.read_bytes(), original)
        with Image.open(first) as preview:
            self.assertEqual(preview.getpixel((0, 0)), (20, 125, 192))
            self.assertEqual(preview.getpixel((5, 3)), (42, 168, 70))
        with self.assertRaises(ValueError): c.save_crop_preview(self.path, report, self.path)
        with self.assertRaises(ValueError): c.save_crop_preview(self.path, report, first)
        self.save(Image.new('RGB', (20, 10), 'black'))
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            c.save_crop_preview(self.path, report, self.root / 'stale.png')


if __name__ == '__main__':
    unittest.main()
