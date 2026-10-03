"""Radial pointer relationships, transforms and manifest compatibility."""
import copy
import math
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from figure_rebuild.geometry import radial_pointer_commands
from figure_rebuild.scene_compile import compile_scene
from figure_rebuild import validate


def vertices(commands):
    return [tuple(command[next(iter(command))][key] for key in ('x', 'y'))
            for command in commands[:-1]]


class RadialPointerGeometry(unittest.TestCase):
    def test_axis_passes_through_pivot_with_symmetric_perpendicular_base(self):
        pivot, width, length = (127.25, 83.5), 4.5, 39.0
        for degrees in (0, 17, 45, 90, 143, 180, 225, 270, 317):
            with self.subTest(degrees=degrees):
                angle = math.radians(degrees)
                direction = (math.cos(angle), math.sin(angle))
                tip = tuple(pivot[i] + length * direction[i] for i in range(2))
                commands = radial_pointer_commands(pivot, tip, width)
                left, actual_tip, right = vertices(commands)
                self.assertEqual(actual_tip, tip)
                for i in range(2):
                    self.assertAlmostEqual((left[i] + right[i]) / 2, pivot[i])
                for corner in (left, right):
                    offset = tuple(corner[i] - pivot[i] for i in range(2))
                    self.assertAlmostEqual(math.hypot(*offset), width)
                    self.assertAlmostEqual(sum(offset[i] * direction[i] for i in range(2)), 0)
                area = abs((actual_tip[0] - left[0]) * (right[1] - left[1])
                           - (actual_tip[1] - left[1]) * (right[0] - left[0])) / 2
                self.assertAlmostEqual(area, length * width)
                self.assertEqual(commands[-1], {'close': {}})

    def test_rotation_translation_and_uniform_scale_preserve_the_relationship(self):
        pivot, tip, width = (12.5, 19.75), (43.0, -7.0), 3.25
        base = vertices(radial_pointer_commands(pivot, tip, width))
        for degrees, scale, translation in ((90, 1, (0, 0)), (-37, 2.5, (95.25, 140.5)),
                                            (180, .25, (-81.0, 13.0))):
            with self.subTest(degrees=degrees, scale=scale):
                angle = math.radians(degrees)
                c, s = math.cos(angle), math.sin(angle)
                def transform(point):
                    return (scale * (point[0] * c - point[1] * s) + translation[0],
                            scale * (point[0] * s + point[1] * c) + translation[1])
                actual = vertices(radial_pointer_commands(transform(pivot), transform(tip), width * scale))
                for actual_point, original_point in zip(actual, base):
                    for got, expected in zip(actual_point, transform(original_point)):
                        self.assertAlmostEqual(got, expected)

    def test_inputs_remain_unchanged_and_paths_are_independent(self):
        pivot, tip = [10, 20], [30, 5]
        before = copy.deepcopy((pivot, tip))
        first = radial_pointer_commands(pivot, tip, 2)
        second = radial_pointer_commands(pivot, tip, 2)
        first[0]['moveTo']['x'] = -100
        self.assertEqual((pivot, tip), before)
        self.assertNotEqual(first, second)

    def test_blunt_end_shares_axis_and_preserves_declared_tip_center_and_width(self):
        pivot, tip, width, cap_width = (15.0, 38.0), (47.0, 11.0), 4.0, .6
        commands = radial_pointer_commands(pivot, tip, width, tip_half_width=cap_width)
        left, cap_left, cap_right, right = vertices(commands)
        direction = (tip[0] - pivot[0], tip[1] - pivot[1])
        length = math.hypot(*direction)
        for first, second, center, radius in ((left, right, pivot, width),
                                               (cap_left, cap_right, tip, cap_width)):
            for i in range(2):
                self.assertAlmostEqual((first[i] + second[i]) / 2, center[i])
            self.assertAlmostEqual(math.hypot(first[0] - second[0], first[1] - second[1]), 2 * radius)
            self.assertAlmostEqual(sum((first[i] - second[i]) * direction[i] for i in range(2)), 0)
        polygon = (left, cap_left, cap_right, right)
        area = abs(sum(polygon[i][0] * polygon[(i + 1) % 4][1]
                       - polygon[(i + 1) % 4][0] * polygon[i][1] for i in range(4))) / 2
        self.assertAlmostEqual(area, (width + cap_width) * length)

    def test_invalid_tip_width_is_rejected(self):
        for width in (-1, True, '1', float('nan'), float('inf')):
            with self.subTest(width=width), self.assertRaises(ValueError):
                radial_pointer_commands((0, 0), (10, 10), 2, tip_half_width=width)
        with self.assertRaisesRegex(ValueError, 'tip cap'):
            radial_pointer_commands((1e16, 1e16), (1e16 + 4, 1e16), 2, tip_half_width=.01)

    def test_invalid_or_degenerate_geometry_is_rejected(self):
        cases = [((0, 0), (0, 0), 1), ((0, 0), (1, 1), 0), ((0, 0), (1, 1), -1),
                 ((0, 0), (1, 1), True), ((0, 0), (1, 1), '2'),
                 ((0, 0), (1, 1), float('inf')), ((0, 0), (1, 1), float('nan')),
                 ((True, 0), (1, 1), 1), ((float('nan'), 0), (1, 1), 1),
                 ((0, 0), (float('inf'), 1), 1), ((0, 0, 1), (1, 1), 1),
                 (None, (1, 1), 1), ((0, 0), ('1', 1), 1),
                 ((1e16, 1e16), (1e16 + 4, 1e16), .01)]
        for pivot, tip, width in cases:
            with self.subTest(pivot=pivot, tip=tip, width=width):
                with self.assertRaises(ValueError):
                    radial_pointer_commands(pivot, tip, width)

    def test_native_path_is_valid_and_survives_scene_materialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            source = job / 'source.png'
            Image.new('RGB', (120, 100), 'white').save(source)
            obj = {'id': 'dial-needle', 'kind': 'path',
                   'commands': radial_pointer_commands((60, 80), (87, 49), 4),
                   'style': {'fill': '#3A444C', 'stroke': 'none', 'stroke_width': 0}}
            manifest = {'schema_version': 1, 'id': 'radial-pointer-fixture', 'revision': 1,
                        'canvas': {'width': 120, 'height': 100},
                        'source': {'path': 'source.png', 'sha256': validate.digest(source),
                                   'kind': 'user_original', 'width': 120, 'height': 100},
                        'recognition': {'provider': 'calling_host', 'status': 'reviewed'},
                        'objects': [obj]}
            self.assertEqual(validate.validate(manifest, job)['status'], 'PASS')
            compiled, _ = compile_scene(manifest, job)
            self.assertEqual(compiled['objects'][0]['id'], obj['id'])
            self.assertEqual(compiled['objects'][0]['kind'], 'path')
            self.assertEqual(compiled['objects'][0]['commands'], obj['commands'])


if __name__ == '__main__':
    unittest.main()
