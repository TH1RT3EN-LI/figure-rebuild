"""Missing source detail remains diagnosable despite output boxes/ROI budgets."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from figure_rebuild import compare as comparison
from figure_rebuild import registration

try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    HAS_VISION = True
except ImportError:
    HAS_VISION = False


def separated_parts():
    source = Image.new('RGB', (512, 256), 'white')
    draw = ImageDraw.Draw(source)
    draw.rectangle((30, 40, 85, 90), fill='navy')
    draw.ellipse((430, 175, 470, 220), fill='black')
    target = source.copy()
    ImageDraw.Draw(target).rectangle((420, 165, 480, 230), fill='white')
    return source, target


class CoverageValidation(unittest.TestCase):
    def test_limits_reject_unbounded_values_before_optional_dependencies(self):
        image = Image.new('RGB', (100, 100), 'white')
        invalid = {'max_regions': [True, -1, 16_385, 1.5],
                   'max_roi_pixels': [0, 256_000_001, float('inf')],
                   'max_analysis_side': [0, 4097],
                   'source_grid_cell_size': [0, 63, 1025]}
        with patch.object(registration, '_load_vision', side_effect=ImportError('missing')):
            for key, values in invalid.items():
                for value in values:
                    with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                        registration.diagnose_registration(image, image, **{key: value})

    def test_unavailable_diagnostics_do_not_claim_coverage(self):
        image = Image.new('RGB', (100, 100), 'white')
        with patch.object(registration, '_load_vision', side_effect=ImportError('missing')):
            report = registration.diagnose_registration(image, image)
        self.assertEqual(report['coverage']['status'], 'not_evaluated')
        self.assertEqual(report['coverage']['source_grid']['status'], 'not_evaluated')
        self.assertEqual(report['source_grid_regions'], [])

    def test_source_box_and_actual_frame_are_projected_separately(self):
        scene = {'canvas': {'width': 100, 'height': 100}, 'objects': [
            {'id': 'photo', 'kind': 'image', 'rotation': 90,
             'expected_source_box': {'x': 10, 'y': 20, 'width': 40, 'height': 20},
             'actual_frame': {'x': 20, 'y': 20, 'width': 20, 'height': 20}}]}
        result = registration.manifest_regions(scene, (200, 200))[0]
        self.assertEqual(result['box_basis'], 'expected_source_box')
        for key, value in {'x': 40, 'y': 20, 'width': 40, 'height': 80}.items():
            self.assertAlmostEqual(result['box'][key], value)
        self.assertEqual(result['actual_frame'], {'x': 40, 'y': 40, 'width': 40, 'height': 40})
        del scene['objects'][0]['expected_source_box']
        self.assertEqual(registration.manifest_regions(scene, (200, 200))[0]['box_basis'],
                         'actual_frame_fallback')

    def test_invalid_expected_bounds_cannot_fall_back_to_good_output_bounds(self):
        scene = {'canvas': {'width': 100, 'height': 100}, 'objects': [
            {'id': 'photo', 'kind': 'image', 'expected_source_box': None,
             'actual_frame': {'x': 20, 'y': 20, 'width': 20, 'height': 20}}]}
        with self.assertRaisesRegex(ValueError, 'box: photo'):
            registration.manifest_regions(scene, (100, 100))


@unittest.skipUnless(HAS_VISION, 'optional numpy/opencv packages not installed')
class SourceCoverage(unittest.TestCase):
    def test_missing_object_outside_every_output_bbox_has_local_evidence(self):
        source, target = separated_parts()
        # The deleted circle is absent even from the recognition/object list.
        regions = [{'id': 'survivor', 'box': {'x': 25, 'y': 35, 'width': 65, 'height': 60}}]
        originals = source.tobytes(), target.tobytes()
        report = registration.diagnose_registration(source, target, regions=regions)
        self.assertEqual(report['regions'][0]['source_box_geometry']['f1'], 1)
        missing = [r for r in report['source_grid_regions']
                   if r['source_box_geometry']['status'] == 'missing_edges']
        self.assertTrue(missing, report['source_grid_regions'])
        self.assertTrue(any(r['source_box']['x'] >= 384 for r in missing))
        self.assertTrue(all(r['source_box_geometry']['measurement'] == 'unaligned' for r in missing))
        self.assertLess(report['raw_geometry']['recall'], 1)
        self.assertEqual(report['coverage']['source_grid']['covered_canvas_fraction'], 1)
        self.assertEqual((source.tobytes(), target.tobytes()), originals)
        self.assertFalse(report['transform_applied'])
        self.assertEqual(report['visual_acceptance'], 'pending')

    def test_image_requested_footprint_is_not_replaced_by_fitted_output_frame(self):
        source, target = separated_parts()
        expected = {'x': 20, 'y': 30, 'width': 470, 'height': 210}
        actual = {'x': 25, 'y': 35, 'width': 65, 'height': 60}
        scene = {'canvas': {'width': 512, 'height': 256}, 'objects': [
            {'id': 'photo', 'kind': 'image', 'box': actual,
             'expected_source_box': expected, 'actual_frame': actual}]}
        report = registration.diagnose_registration(source, target,
                    regions=registration.manifest_regions(scene, source.size))
        local = report['regions'][0]
        self.assertEqual(local['source_box'], expected)
        self.assertEqual(local['actual_frame'], actual)
        self.assertLess(local['source_box_geometry']['recall'], 1)
        self.assertEqual(report['coverage']['object_regions']['expected_source_box_count'], 1)

    def test_saturated_object_limit_keeps_source_grid_and_lists_every_omission(self):
        source, target = separated_parts()
        regions = [{'id': f'object-{i}', 'box': {'x': 25, 'y': 35, 'width': 65, 'height': 60}}
                   for i in range(300)]
        report = registration.diagnose_registration(source, target, regions=regions)
        self.assertEqual(report['regions_diagnosed'], 256)
        self.assertEqual(report['regions_omitted_for_budget'], 44)
        omitted = report['coverage']['object_regions']['omitted']
        self.assertEqual([r['id'] for r in omitted], [f'object-{i}' for i in range(256, 300)])
        self.assertTrue(all(r['reason'] == 'region_limit' for r in omitted))
        self.assertEqual(report['coverage']['status'], 'partial')
        self.assertEqual(report['coverage']['source_grid']['status'], 'complete')
        self.assertTrue(any(r['source_box_geometry']['status'] == 'missing_edges'
                            for r in report['source_grid_regions']))
        full = registration.diagnose_registration(source, target, regions=regions, max_regions=300)
        self.assertEqual(full['regions_diagnosed'], 300)
        self.assertEqual(full['coverage']['status'], 'complete')
        self.assertEqual(full['raw_geometry'], report['raw_geometry'])
        self.assertEqual(full['visual_acceptance'], 'pending')

    def test_pixel_budget_skips_large_roi_without_hiding_later_small_roi(self):
        image = Image.new('RGB', (100, 100), 'white')
        ImageDraw.Draw(image).rectangle((2, 2, 4, 4), fill='black')
        regions = [{'id': 'too-large', 'box': {'x': 0, 'y': 0, 'width': 100, 'height': 100}},
                   {'id': 'small', 'box': {'x': 1, 'y': 1, 'width': 5, 'height': 5}}]
        report = registration.diagnose_registration(image, image, regions=regions,
                                                     max_roi_pixels=23_000)
        self.assertEqual([r['id'] for r in report['regions']], ['small'])
        self.assertEqual(report['coverage']['object_regions']['omitted'],
                         [{'id': 'too-large', 'reason': 'pixel_budget'}])
        self.assertEqual(report['coverage']['source_grid']['covered_canvas_fraction'], 1)
        self.assertLessEqual(report['roi_analysis_pixels'], 23_000)

    def test_grid_budget_exhaustion_is_not_reported_as_full_source_coverage(self):
        source, _ = separated_parts()
        report = registration.diagnose_registration(source, source, max_roi_pixels=60_000)
        grid = report['coverage']['source_grid']
        self.assertEqual(grid['status'], 'partial')
        self.assertLess(grid['covered_canvas_fraction'], 1)
        self.assertEqual(grid['requested'], grid['diagnosed'] + len(grid['omitted']))
        self.assertTrue(all(r['reason'] == 'pixel_budget' for r in grid['omitted']))
        self.assertEqual(report['status'], 'reliable')  # only the translation estimate
        self.assertEqual(report['visual_acceptance'], 'pending')
        self.assertLessEqual(report['roi_analysis_pixels'], 60_000)

    def test_downsampled_grid_covers_each_analysis_pixel_once_and_reports_resolution(self):
        source = Image.new('RGB', (2003, 701), 'white')
        ImageDraw.Draw(source).rectangle((1800, 600, 1900, 650), fill='black')
        report = registration.diagnose_registration(source, source, max_analysis_side=700, max_regions=0)
        grid = report['coverage']['source_grid']
        self.assertEqual(grid['covered_analysis_pixels'], 700 * report['analysis_size'][1])
        self.assertEqual(grid['covered_canvas_fraction'], 1)
        self.assertTrue(report['coverage']['analysis_downsampled'])
        self.assertEqual(report['coverage']['scope'], 'unaligned_diagnostics_at_analysis_resolution')
        json.dumps(report, allow_nan=False)

    def test_compare_options_do_not_change_raw_rgb_error_or_comparison_image(self):
        source, target = separated_parts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source.save(root/'source.png'); target.save(root/'target.png')
            font = ImageFont.load_default()
            with patch.object(comparison.ImageFont, 'truetype', return_value=font):
                reports = [comparison.compare(root/'source.png', root/'target.png', root/f'comparison-{i}.png',
                            root/f'metrics-{i}.json', 'unused.ttf', diagnostic_options=options)
                           for i, options in enumerate([{}, {'max_regions': 0, 'max_roi_pixels': 1}])]
            self.assertEqual(reports[0]['mean_absolute_rgb_error'], reports[1]['mean_absolute_rgb_error'])
            self.assertEqual(reports[0]['geometry']['raw_geometry'], reports[1]['geometry']['raw_geometry'])
            self.assertEqual((root/'comparison-0.png').read_bytes(), (root/'comparison-1.png').read_bytes())
            self.assertEqual(reports[1]['geometry']['coverage']['source_grid']['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
