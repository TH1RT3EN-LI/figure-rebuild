"""Synthetic geometry checks: signed shifts, local failures and safe fallbacks."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from tools.figure_rebuild import registration
from tools.figure_rebuild import compare as comparison

try:
    import cv2
    import numpy as np
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


def figure(size=(400, 300)):
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 110, 90), fill='#214889', outline='black', width=2)
    draw.ellipse((225, 42, 273, 105), fill='#D4D85C', outline='#454545', width=3)
    draw.line((48, 210, 140, 250, 207, 181), fill='#762835', width=5)
    draw.polygon([(277, 180), (337, 224), (255, 258)], fill='#856CAB')
    draw.rectangle((175, 128, 194, 145), fill='#333333')
    return image


def shifted(image, dx, dy):
    array = cv2.warpAffine(np.asarray(image), np.array([[1, 0, dx], [0, 1, dy]], np.float32),
                           image.size, flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return Image.fromarray(array)


class SafeFallbacks(unittest.TestCase):
    def test_dependency_missing_is_unavailable_not_error(self):
        error = ModuleNotFoundError("No module named 'cv2'", name='cv2')
        with patch.object(registration, '_load_vision', side_effect=error):
            result = registration.diagnose_registration(figure(), figure())
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['missing_module'], 'cv2')
        self.assertFalse(result['transform_applied'])
        json.dumps(result, allow_nan=False)

    def test_vision_dynamic_library_error_is_unavailable(self):
        with patch.object(registration, '_load_vision', side_effect=OSError('library load failed')):
            result = registration.diagnose_registration(figure(), figure())
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['dependency_error'], 'OSError')

    def test_size_mismatch_is_failure(self):
        result = registration.diagnose_registration(figure(), figure((300, 300)))
        self.assertEqual(result['status'], 'failure')
        self.assertEqual(result['reason'], 'different_canvas_sizes')

    def test_malformed_regions_do_not_hide_behind_missing_dependency(self):
        with patch.object(registration, '_load_vision', side_effect=ImportError('no cv2')):
            with self.assertRaisesRegex(ValueError, 'box'):
                registration.diagnose_registration(figure(), figure(), regions=[{'id': 'bad'}])

    def test_invalid_shift_limit(self):
        for limit in (0, -1, math.inf, math.nan, True):
            with self.assertRaises(ValueError):
                registration.diagnose_registration(figure(), figure(), max_shift_px=limit)

    def test_path_and_rotated_box_project_to_comparison_canvas(self):
        manifest = {'canvas': {'width': 200, 'height': 100}, 'objects': [
            {'id': 'line', 'kind': 'path', 'commands': [{'moveTo': {'x': 10, 'y': 20}},
                {'lineTo': {'x': 40, 'y': 20}}], 'style': {'stroke_width': 4}},
            {'id': 'rotated-label', 'kind': 'text', 'box': {'x': 80, 'y': 40, 'width': 40, 'height': 10}, 'rotation': 90}]}
        regions = registration.manifest_regions(manifest, (400, 200))
        self.assertEqual(regions[0]['box'], {'x': 16, 'y': 36, 'width': 68, 'height': 8})
        self.assertAlmostEqual(regions[1]['box']['x'], 190)
        self.assertAlmostEqual(regions[1]['box']['y'], 50)
        self.assertAlmostEqual(regions[1]['box']['width'], 20)
        self.assertAlmostEqual(regions[1]['box']['height'], 80)

    def test_malformed_manifest_errors_are_distinct(self):
        for scene in ({}, {'canvas': {'width': 200, 'height': 100}, 'objects': [
                {'id': 'x', 'kind': 'image', 'box': {'x': 0, 'y': 0, 'width': -1, 'height': 10}}]}):
            with self.assertRaises(ValueError):
                registration.manifest_regions(scene, (200, 100))

    def test_anchor_text_requires_actual_resolved_renderer_box(self):
        scene = {'canvas': {'width': 200, 'height': 100}, 'objects': [
            {'id': 'label', 'kind': 'text', 'text': 'hello', 'anchor': {'x': 40, 'y': 50}, 'font_size': 14}]}
        with self.assertRaisesRegex(ValueError, 'box: label'):
            registration.manifest_regions(scene, (200, 100))

    def test_compare_keeps_legacy_metrics_and_handles_optional_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source.png'; target = root/'target.png'
            figure().save(source); figure().save(target)
            font = ImageFont.load_default()
            with patch.object(comparison.ImageFont, 'truetype', return_value=font), \
                    patch.object(registration, '_load_vision', side_effect=ImportError('no cv2')):
                report = comparison.compare(source, target, root/'comparison.png', root/'metrics.json', 'unused.ttf')
            self.assertEqual(report['geometry']['status'], 'unavailable')
            self.assertEqual(report['mean_absolute_rgb_error'], 0)
            self.assertTrue(report['same_canvas_size'])
            self.assertEqual(report['visual_acceptance'], 'pending')
            self.assertTrue((root/'comparison.png').exists())
            self.assertEqual(json.loads((root/'metrics.json').read_text()), report)

    def test_compare_bad_manifest_fails_even_without_optional_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'source.png'
            figure().save(source)
            with patch.object(registration, '_load_vision', side_effect=ImportError('no cv2')):
                with self.assertRaisesRegex(ValueError, 'canvas'):
                    comparison.compare(source, source, root/'comparison.png', root/'metrics.json', 'unused.ttf', manifest={})
            self.assertFalse((root/'metrics.json').exists())


@unittest.skipUnless(HAS_VISION, 'optional numpy/opencv packages not installed')
class TranslationChecks(unittest.TestCase):
    def test_integer_translation_sign_and_accuracy(self):
        for dx, dy in ((13, -7), (-9, 6)):
            with self.subTest(dx=dx, dy=dy):
                source = figure(); target = shifted(source, dx, dy)
                result = registration.diagnose_registration(source, target)
                self.assertEqual(result['status'], 'reliable', result)
                self.assertAlmostEqual(result['translation']['dx'], dx, delta=0.3)
                self.assertAlmostEqual(result['translation']['dy'], dy, delta=0.3)
                self.assertGreater(result['raw_geometry']['symmetric_mean_distance_px'], 3)
                self.assertFalse(result['transform_applied'])

    def test_fractional_translation_is_refined_without_altering_images(self):
        source = figure(); target = shifted(source, 4.25, -3.5)
        initial = (source.tobytes(), target.tobytes())
        result = registration.diagnose_registration(source, target)
        self.assertEqual(result['status'], 'reliable', result)
        self.assertAlmostEqual(result['translation']['dx'], 4.25, delta=0.3)
        self.assertAlmostEqual(result['translation']['dy'], -3.5, delta=0.3)
        self.assertEqual(initial, (source.tobytes(), target.tobytes()))
        json.dumps(result, allow_nan=False)

    def test_swapping_source_and_target_reverses_sign(self):
        source = figure(); target = shifted(source, 5.5, 8.25)
        result = registration.diagnose_registration(target, source)
        self.assertEqual(result['status'], 'reliable', result)
        self.assertAlmostEqual(result['translation']['dx'], -5.5, delta=0.3)
        self.assertAlmostEqual(result['translation']['dy'], -8.25, delta=0.3)

    def test_blank_exact_match_is_not_a_reliable_registration(self):
        blank = Image.new('RGB', (300, 200), 'white')
        result = registration.diagnose_registration(blank, blank)
        self.assertEqual(result['status'], 'low_confidence')
        self.assertEqual(result['raw_geometry']['status'], 'both_blank')
        self.assertEqual(result['raw_geometry']['symmetric_mean_distance_px'], 0)
        self.assertIsNone(result['translation']['dx'])

    def test_missing_image_is_unreliable_and_reports_missing_edges(self):
        result = registration.diagnose_registration(figure(), Image.new('RGB', (400, 300), 'white'))
        self.assertEqual(result['status'], 'low_confidence')
        self.assertEqual(result['raw_geometry']['status'], 'missing_edges')
        self.assertEqual(result['raw_geometry']['recall'], 0)

    def test_exact_nonblank_match_reliably_reports_zero(self):
        result = registration.diagnose_registration(figure(), figure())
        self.assertEqual(result['status'], 'reliable')
        self.assertEqual(result['translation']['dx'], 0)
        self.assertEqual(result['raw_geometry']['f1'], 1)

    def test_shift_limit_gates_instead_of_clamping_or_correcting(self):
        source = figure(); target = shifted(source, 15, -6)
        result = registration.diagnose_registration(source, target, max_shift_px=10)
        self.assertEqual(result['status'], 'low_confidence')
        self.assertEqual(result['reason'], 'max_shift_exceeded')
        self.assertGreater(result['translation']['dx'], 10)
        self.assertFalse(result['transform_applied'])
        self.assertEqual(result['reliability_scope'], 'translation_estimate_only')
        self.assertEqual(result['visual_acceptance'], 'pending')

    def test_low_canvas_overlap_is_unreliable(self):
        source = figure((120, 120)); target = shifted(source, 28, 0)
        result = registration.diagnose_registration(source, target)
        self.assertNotEqual(result['status'], 'reliable')

    def test_unrelated_structures_not_reliable(self):
        target = Image.new('RGB', (400, 300), 'white')
        draw = ImageDraw.Draw(target)
        for x in range(20, 390, 23):
            draw.line((x, 15, 380-x, 280), fill='black', width=3)
        result = registration.diagnose_registration(figure(), target)
        self.assertNotEqual(result['status'], 'reliable', result)

    def test_ecc_nonconvergence_does_not_claim_reliable(self):
        with patch.object(cv2, 'findTransformECC', side_effect=cv2.error('did not converge')):
            result = registration.diagnose_registration(figure(), shifted(figure(), 4, -2))
        self.assertEqual(result['status'], 'low_confidence')
        self.assertEqual(result['reason'], 'ecc_nonconvergence')

    def test_missing_local_part_is_visible_without_white_background_dilution(self):
        source = figure(); target = source.copy()
        ImageDraw.Draw(target).rectangle((220, 35, 280, 112), fill='white')
        regions = [{'id': 'blue-box', 'kind': 'path', 'box': {'x': 40, 'y': 40, 'width': 70, 'height': 50}},
                   {'id': 'removed-circle', 'kind': 'path', 'box': {'x': 225, 'y': 42, 'width': 48, 'height': 63}}]
        result = registration.diagnose_registration(source, target, regions=regions)
        local = {region['id']: region for region in result['regions']}
        self.assertEqual(local['blue-box']['f1'], 1)
        self.assertEqual(local['removed-circle']['source_box_geometry']['status'], 'missing_edges')
        self.assertEqual(local['removed-circle']['source_box_geometry']['f1'], 0)
        self.assertLess(local['removed-circle']['f1'], .2)
        self.assertEqual(local['removed-circle']['measurement'], 'unaligned')

    def test_moved_local_part_remains_measured_outside_its_tight_bbox(self):
        source = Image.new('RGB', (400, 300), 'white')
        ImageDraw.Draw(source).rectangle((45, 45, 65, 65), fill='black')
        target = shifted(source, 30, 0)
        regions = [{'id': 'tiny-box', 'box': {'x': 45, 'y': 45, 'width': 20, 'height': 20}}]
        result = registration.diagnose_registration(source, target, regions=regions)
        self.assertGreater(result['regions'][0]['target_edge_pixels'], 0)
        self.assertLess(result['regions'][0]['f1'], .3)
        self.assertGreater(result['regions'][0]['symmetric_mean_distance_px'], 5)

    def test_analysis_cpu_budget_and_finite_metrics(self):
        source = figure().resize((1800, 1350))
        regions = [{'id': str(i), 'box': {'x': 40, 'y': 40, 'width': 20, 'height': 20}} for i in range(260)]
        result = registration.diagnose_registration(source, source, regions=regions)
        self.assertEqual(max(result['analysis_size']), 1280)
        self.assertEqual(len(result['regions']), 256)
        self.assertEqual(result['regions_omitted_for_budget'], 4)
        json.dumps(result, allow_nan=False)

    def test_large_region_pixel_budget_is_reported(self):
        source = figure().resize((1280, 960))
        regions = [{'id': str(i), 'box': {'x': 0, 'y': 0, 'width': 1280, 'height': 960}} for i in range(20)]
        result = registration.diagnose_registration(source, source, regions=regions)
        self.assertGreater(result['regions_omitted_for_budget'], 0)
        self.assertLessEqual(result['roi_analysis_pixels'], registration.MAX_ROI_PIXELS)


if __name__ == '__main__':
    unittest.main()
