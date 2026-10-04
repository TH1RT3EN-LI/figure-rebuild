"""Recorded measurements must be complete and consistent, without font replay."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from figure_rebuild.output_review import (_verify_text_fit_measurements,
                                         _text_fit_wrapped_lines, _text_fit_round, _json_record)


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/text-fit-writer-records.json').read_text())
ROTATED = json.loads((Path(__file__).parent / 'fixtures/text-fit-rotated-anchor-records.json').read_text())


class TextFitReceiptConsistency(unittest.TestCase):
    def test_math_round_does_not_add_a_second_rounding(self):
        # Expected Math.round results also replayed against the real Node writer
        # runtime in the external contract validation evidence.
        for value, expected in ((math.nextafter(.5, 0), 0), (.5, 1),
                                (math.nextafter(.5, 1), 1),
                                (math.nextafter(1.5, 0), 1), (1.5, 2),
                                (math.nextafter(1.5, 2), 2),
                                (4503599627370497., 4503599627370497)):
            with self.subTest(value=value):
                self.assertEqual(_text_fit_round(value, 'round fixture'), expected)

    def test_text_fit_json_rejects_ambiguous_keys_and_nonstandard_numbers(self):
        for payload in ('{"x":1,"x":2}', '{"nested":{"x":1,"x":2}}',
                        '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'text-fit.json'; path.write_text(payload)
                with self.assertRaises(ValueError):
                    _json_record(path, strict_numbers_and_keys=True)

    def one(self, name='baseline'):
        scene = copy.deepcopy(FIXTURE['resolved'])
        scene['objects'] = [o for o in scene['objects'] if o['id'] == name]
        measured = [copy.deepcopy(r) for r in FIXTURE['text_fit']['objects'] if r['id'] == name]
        self.assertEqual(len(scene['objects']), 1)
        self.assertEqual(len(measured), 1)
        return scene, measured

    def test_twenty_real_current_writer_outputs(self):
        scene, measured = FIXTURE['resolved'], FIXTURE['text_fit']['objects']
        self.assertEqual(len(measured), 20)
        before = copy.deepcopy((scene, measured))
        _verify_text_fit_measurements(scene, measured)
        self.assertEqual((scene, measured), before)

    def test_real_writer_hard_breaks_long_whitespace_and_subpixel_height(self):
        for case in FIXTURE['independent_additional_writer_cases']:
            with self.subTest(case=case['case']):
                _verify_text_fit_measurements(case['resolved'], case['measurements'])

    def test_rotated_source_anchor_receipts_and_baseline_are_verified(self):
        for case in ROTATED['cases']:
            scene, rows = copy.deepcopy((case['resolved'], case['measurements']))
            with self.subTest(object=scene['objects'][0]['id']):
                before = copy.deepcopy((scene, rows))
                _verify_text_fit_measurements(scene, rows)
                self.assertEqual((scene, rows), before)
                # The recorded frame's baseline must reach the original anchor
                # after rotating around the DrawingML frame center.
                obj = scene['objects'][0]; box = rows[0]['box']; layout = rows[0]['layout']
                content = layout['content_box']; align = obj['alignment']
                x = content['x'] + (content['width'] / 2 if align == 'center' else content['width'] if align == 'right' else 0)
                y = content['y'] + layout['native_baseline_ascent']
                cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
                angle = math.radians(obj['rotation'])
                self.assertAlmostEqual(cx + math.cos(angle) * (x - cx) - math.sin(angle) * (y - cy), obj['anchor']['x'])
                self.assertAlmostEqual(cy + math.sin(angle) * (x - cx) + math.cos(angle) * (y - cy), obj['anchor']['y'])

    def test_rotated_anchor_rejects_changed_rotation_anchor_or_unshifted_frame(self):
        for case in ('rotation', 'anchor', 'unshifted_frame'):
            scene, rows = copy.deepcopy((ROTATED['cases'][0]['resolved'], ROTATED['cases'][0]['measurements']))
            obj = scene['objects'][0]; row = rows[0]
            if case == 'rotation': obj['rotation'] += 1
            elif case == 'anchor': obj['anchor']['x'] += .01
            else:
                # A pre-fix frame has consistent content/insets but rotates its
                # baseline away from the source. Internal consistency alone is
                # insufficient to accept that receipt.
                x = obj['anchor']['x'] - row['layout']['insets']['left']
                y = obj['anchor']['y'] - row['layout']['insets']['top'] - row['layout']['native_baseline_ascent']
                row['box']['x'], row['box']['y'] = x, y
                row['layout']['content_box']['x'] = x + row['layout']['insets']['left']
                row['layout']['content_box']['y'] = y + row['layout']['insets']['top']
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, 'anchored_box'):
                _verify_text_fit_measurements(scene, rows)

    def test_id_only_missing_fields_and_unknown_context_reject(self):
        for field in ('box', 'layout'):
            scene, rows = self.one(); del rows[0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)
        for field in FIXTURE['text_fit']['objects'][0]['layout']:
            scene, rows = self.one(); rows[0]['layout'].pop(field, None)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)
        scene, rows = self.one(); rows[0]['layout']['mystery'] = 1
        with self.assertRaisesRegex(ValueError, 'Text-fit'):
            _verify_text_fit_measurements(scene, rows)

    def test_nonfinite_boolean_and_negative_numeric_data_reject(self):
        for field in ('required_width', 'required_height', 'line_height', 'ascent', 'descent',
                      'native_baseline_ascent', 'default_native_baseline_ascent'):
            for value in (True, None, '12', float('nan'), float('inf'), -1):
                scene, rows = self.one(); rows[0]['layout'][field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'Text-fit'):
                    _verify_text_fit_measurements(scene, rows)
        for target in ('box', 'content_box', 'insets', 'renderer_baseline'):
            scene, rows = self.one()
            record = rows[0]['box'] if target == 'box' else rows[0]['layout'][target]
            key = next(k for k, v in record.items() if isinstance(v, (int, float)))
            record[key] = False
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)

    def test_writer_overflow_tolerance_is_exactly_point001(self):
        for extra in (.0005, .001, math.nextafter(.001, math.inf), .0011):
            scene, rows = self.one()
            content = rows[0]['layout']['content_box']['width']
            rows[0]['layout']['required_width'] = content + extra
            if rows[0]['layout']['required_width'] <= content + .001:
                _verify_text_fit_measurements(scene, rows)
            else:
                with self.assertRaisesRegex(ValueError, 'overflows'):
                    _verify_text_fit_measurements(scene, rows)

    def test_arithmetic_frames_anchor_pitch_renderer_and_height_reject(self):
        for case in ('content_x', 'inset', 'source_box', 'anchor', 'pitch', 'height', 'adjustment', 'renderer_size', 'percent'):
            scene, rows = self.one('anchor_left' if case == 'anchor' else 'explicit_lineheight')
            row = rows[0]; layout = row['layout']
            if case == 'content_x': layout['content_box']['x'] += .0000001
            elif case == 'inset': layout['insets']['left'] += 1
            elif case == 'source_box': scene['objects'][0]['box']['x'] += 1
            elif case == 'anchor': scene['objects'][0]['anchor']['x'] += 1
            elif case == 'pitch': layout['line_height'] += .0000001
            elif case == 'height': layout['required_height'] += .0000001
            elif case == 'adjustment': layout['baseline_adjustment_px'] += 1
            elif case == 'renderer_size': layout['renderer_baseline']['rendered_font_size_px'] += 1
            else: layout['renderer_baseline']['spacing_thousandths_percent'] += 1
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)

    def test_lines_and_types_reject_without_normalizing_source_content(self):
        for changed in ([], ['wrong'], ['A\nB'], [False]):
            scene, rows = self.one(); rows[0]['layout']['lines'] = changed
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)
        for count in (True, 1.0, 0, 2):
            scene, rows = self.one(); rows[0]['layout']['line_count'] = count
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)
        self.assertTrue(_text_fit_wrapped_lines('a b', ['a', 'b']))
        self.assertFalse(_text_fit_wrapped_lines('a b', ['ab']))
        self.assertFalse(_text_fit_wrapped_lines('a\n\nb', ['a', 'b']))
        self.assertTrue(_text_fit_wrapped_lines('a\n\nb', ['a', '', 'b']))
        self.assertFalse(_text_fit_wrapped_lines('a\nb', ['a', '', 'b']))
        self.assertFalse(_text_fit_wrapped_lines('a\n', ['a', '', '']))
        self.assertFalse(_text_fit_wrapped_lines('a', ['', 'a']))
        self.assertFalse(_text_fit_wrapped_lines('a', ['a', '']))
        self.assertTrue(_text_fit_wrapped_lines('a\n', ['a', '']))
        self.assertFalse(_text_fit_wrapped_lines('e\u0301e\u0301', ['ee']))
        self.assertFalse(_text_fit_wrapped_lines('a\u0085b', ['a', 'b']))  # not ECMAScript trim space

    def test_source_canvas_and_extreme_renderer_arithmetic_fail_closed(self):
        scene, rows = self.one(); scene['canvas']['width'] = 1
        with self.assertRaisesRegex(ValueError, 'outside source canvas'):
            _verify_text_fit_measurements(scene, rows)
        for value in (1e308, 1e-308, float('inf')):
            scene, rows = self.one(); rows[0]['layout']['renderer_baseline']['scale'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Text-fit'):
                _verify_text_fit_measurements(scene, rows)


if __name__ == '__main__':
    unittest.main()
