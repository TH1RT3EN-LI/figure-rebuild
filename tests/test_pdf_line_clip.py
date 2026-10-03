"""Finite-stroke geometry, native alpha, and actual source regression cases."""
import copy
from decimal import Decimal
from fractions import Fraction as F
import math
import unittest

from figure_rebuild.pdf_line_clip import clip_axis_butt_stroke_to_rect


def call(commands, rectangle, **kwargs):
    options = dict(source_transform=(1, 0, 0, 1, 0, 0), stroke_width=2,
                   fill="none", linecap="butt", dasharray="none")
    options.update(kwargs)
    return clip_axis_butt_stroke_to_rect(commands, rectangle, **options)


def segments(*pairs):
    return [command for a, b in pairs for command in (("M", a), ("L", b))]


def native_render(commands, *, width=2, rectangle=None, fill=False,
                  alpha=0.5, scale=4, split_fills=False):
    try:
        import pymupdf as fitz
    except ImportError:
        raise unittest.SkipTest("Optional PyMuPDF source dependency unavailable")
    document = fitz.open()
    try:
        page = document.new_page(width=40, height=40)
        ext = document.get_new_xref()
        document.update_object(ext, f"<</Type/ExtGState /CA {alpha} /ca {alpha}>>")
        document.xref_set_key(page.xref, "Resources", f"<</ExtGState<</A {ext} 0 R>>>>")
        number = lambda x: format(Decimal(x), "f")
        content = ["q", "/A gs", "0 0 0 RG 0 0 0 rg", f"{number(width)} w 0 J"]
        if rectangle:
            x0, y0, x1, y1 = rectangle
            content.append(" ".join(number(n) for n in (x0, y0, x1-x0, y1-y0))+" re W n")
        for command in commands:
            if command[0] == "Z":
                content.append("h")
                if split_fills:
                    content.append("f")
            else:
                content.append(" ".join(number(n) for n in command[1])
                               + (" m" if command[0] == "M" else " l"))
        if not split_fills:
            content.append("f" if fill else "S")
        content.append("Q")
        xref = document.get_new_xref()
        document.update_object(xref, "<<>>")
        document.update_stream(xref, "\n".join(content).encode())
        page.set_contents(xref)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
        return pixmap.width, pixmap.height, bytes(pixmap.samples[3::4])
    finally:
        document.close()


def alpha_at(image, x, y, scale):
    width, height, alpha = image
    return alpha[(height-1-int(y*scale))*width+int(x*scale)]


class AxisButtStrokeClipTests(unittest.TestCase):
    def test_inside_preserves_original_stroke_even_with_overlap(self):
        commands = segments(((1, 5), (9, 5)), ((5, 1), (5, 9)))
        before = copy.deepcopy(commands)
        result = call(commands, (0, 0, 10, 10))
        self.assertEqual(result["relation"], "inside")
        self.assertIsNone(result["commands"])
        self.assertFalse(result["proof"]["derived_geometry"])
        self.assertEqual(commands, before)

    def test_finite_butt_bounds_avoid_spurious_miter_and_cap_overhang(self):
        result = call(segments(((0, 5), (10, 5))), (0, 4, 10, 6))
        self.assertEqual(result["relation"], "inside")
        self.assertEqual(result["proof"]["subpaths"][0]["complete_butt_stroke_bounds_exact"],
                         ["0", "4", "10", "6"])
        self.assertFalse(result["proof"]["joins_present"])

    def test_empty_and_boundary_touch_are_explicit_zero_area(self):
        for rect in ((0, 7, 10, 9), (0, 6, 10, 8), (10, 4, 12, 6)):
            result = call(segments(((0, 5), (10, 5))), rect)
            self.assertEqual(result["relation"], "outside")
            self.assertEqual(result["commands"], ())
            self.assertTrue(result["proof"]["empty_fill_intersection"])

    def test_true_clip_is_one_rectangle_with_original_end_cap_extent(self):
        result = call(segments(((0, 5), (10, 5))), (3, 4.5, 7, 8))
        self.assertEqual(result["relation"], "intersection")
        self.assertEqual(result["commands"], (("M", (3., 4.5)), ("L", (7., 4.5)),
                         ("L", (7., 6.)), ("L", (3., 6.)), ("Z",)))
        self.assertTrue(result["proof"]["same_paint_required"])
        self.assertTrue(result["proof"]["nonzero_evenodd_fill_equivalent"])

    def test_world_coordinates_are_not_transformed_again(self):
        result = call(segments(((10, 10), (20, 10))), (12, 0, 18, 20),
                      source_transform=(2, 0, 0, -2, 300, 400))
        self.assertEqual(result["commands"][0], ("M", (12., 8.)))
        self.assertEqual(result["commands"][2], ("L", (18., 12.)))
        self.assertFalse(result["proof"]["source_affine_applied_again"])

    def test_reflection_and_quarter_turn_scale_are_exact(self):
        for matrix in ((-2, 0, 0, 2, 99, 99), (0, 2, -2, 0, 99, 99),
                       (0, -2, -2, 0, 99, 99)):
            result = call(segments(((10, 10), (10, 20))), (0, 12, 20, 18),
                          source_transform=matrix)
            self.assertEqual(result["commands"][0], ("M", (8., 12.)))
            self.assertEqual(result["commands"][2], ("L", (12., 18.)))

    def test_nonuniform_near_similarity_shear_and_general_rotation_reject(self):
        for matrix in ((1, 0, 0, 2, 0, 0),
                       (1, 0, 0, math.nextafter(1, math.inf), 0, 0),
                       (1, 1e-300, 0, 1, 0, 0), (3, 4, -4, 3, 0, 0),
                       (0, 0, 0, 0, 0, 0), (1, 0, 0, 1, math.inf, 0)):
            self.assertIsNone(call(segments(((0, 5), (10, 5))), (3, 0, 7, 10),
                                   source_transform=matrix))

    def test_nonzero_roundoff_is_disclosed_but_distinct_axis_order_survives(self):
        result = call(segments(((0, 5), (10, 5))), (3, 0, 7, 10), stroke_width=.2)
        self.assertEqual(result["relation"], "intersection")
        proof = result["proof"]
        self.assertGreater(F(proof["maximum_coordinate_roundoff_source_units_exact"]), 0)
        self.assertTrue(proof["rounded_output_arrangement_topology_preserved"])
        self.assertFalse(proof["rounded_output_region_preserved_exactly"])

    def test_sub_ulp_width_and_clip_gap_may_not_collapse(self):
        self.assertIsNone(call(segments(((0, 1), (10, 1))), (3, 0, 7, 2),
                               stroke_width=1e-17))
        # Original upper edge is less than one ulp below the clip top edge.
        width = math.nextafter(2., -math.inf)
        self.assertIsNone(call(segments(((0, 1), (10, 1))), (3, -1, 7, 2),
                               stroke_width=width))
        # Two disjoint rectangles have a positive exact gap around y=3; both
        # gap edges would round to 3, despite neither rectangle collapsing.
        self.assertIsNone(call(segments(((0, 2), (10, 2)), ((0, 4), (10, 4))),
                               (3, -1, 7, 10), stroke_width=width))

    def test_intersecting_duplicate_and_nested_output_rectangles_reject(self):
        cases = [segments(((0, 10), (30, 10)), ((15, 0), (15, 30))),
                 segments(((0, 10), (30, 10)), ((0, 10), (30, 10))),
                 segments(((0, 10), (30, 10)), ((5, 10), (25, 10)))]
        for commands in cases:
            self.assertIsNone(call(commands, (8, 5, 24, 22), stroke_width=4))

    def test_disjoint_output_contours_remain_one_compound_paint(self):
        commands = segments(((0, 5), (20, 5)), ((0, 10), (20, 10)))
        result = call(commands, (3, 0, 17, 15))
        self.assertEqual(result["relation"], "intersection")
        self.assertEqual(sum(c[0] == "M" for c in result["commands"]), 2)
        self.assertTrue(result["proof"]["output_contour_interiors_pairwise_disjoint"])
        self.assertTrue(result["proof"]["same_paint_required"])

    def test_shared_boundary_touch_preserves_fill_area_without_overlap(self):
        commands = segments(((0, 5), (20, 5)), ((0, 7), (20, 7)))
        result = call(commands, (3, 0, 17, 10))
        self.assertEqual(result["relation"], "intersection")
        self.assertEqual(result["proof"]["boundary_touching_output_rectangle_pairs"], [[0, 1]])

    def test_alpha_observation_disjoint_compound_matches_source_paint(self):
        commands = segments(((0, 10), (30, 10)), ((0, 20), (30, 20)))
        rect = (8, 5, 24, 25)
        result = call(commands, rect, stroke_width=4)
        for scale in (1, 2, 4):
            source = native_render(commands, width=4, rectangle=rect, alpha=.5, scale=scale)
            output = native_render(result["commands"], fill=True, alpha=.5, scale=scale)
            for point in ((12, 10), (20, 20), (12, 15), (5, 10)):
                self.assertEqual(alpha_at(source, *point, scale), alpha_at(output, *point, scale))
            self.assertGreater(alpha_at(source, 12, 10, scale), 120)
            self.assertLess(alpha_at(source, 12, 10, scale), 135)

    def test_native_alpha_demonstrates_why_overlaps_cannot_be_split(self):
        commands = segments(((5, 10), (25, 10)), ((15, 0), (15, 25)))
        source = native_render(commands, width=6, alpha=.5)
        # Two corresponding finite butt rectangles, deliberately wrong as two
        # paints. This observation checks that the alpha test is discriminating.
        boxes = [(5, 7, 25, 13), (12, 0, 18, 25)]
        rectangles = [cmd for x0, y0, x1, y1 in boxes for cmd in
                      (("M", (x0, y0)), ("L", (x1, y0)), ("L", (x1, y1)),
                       ("L", (x0, y1)), ("Z",))]
        wrong = native_render(rectangles, fill=True, alpha=.5, split_fills=True)
        self.assertGreater(alpha_at(wrong, 15, 10, 4), alpha_at(source, 15, 10, 4)+50)
        self.assertIsNone(call(commands, (10, 5, 24, 22), stroke_width=6))

    def test_unknown_geometry_styles_numbers_and_budgets_fail_closed(self):
        valid = segments(((0, 5), (10, 5)))
        invalid = [[], [("M", (0, 0))], [("L", (0, 0)), ("M", (1, 1))],
                   [("M", (0, 0)), ("L", (5, 0)), ("L", (10, 0))],
                   [("M", (0, 0)), ("C", (1, 0), (1, 1), (0, 1))],
                   segments(((0, 0), (5, 5))), segments(((0, 0), (0, 0))),
                   segments(((True, 5), (10, 5))), segments(((0, math.nan), (10, 5)))]
        for commands in invalid:
            self.assertIsNone(call(commands, (3, 0, 7, 10)))
        for kwargs in ({"fill": "#000000"}, {"linecap": "round"}, {"linecap": "square"},
                       {"linejoin": "arcs"}, {"dasharray": "1,1"}, {"dasharray": []},
                       {"stroke_width": 0}, {"stroke_width": -1}, {"stroke_width": True},
                       {"stroke_width": math.inf}, {"max_commands": True},
                       {"max_commands": 1}, {"max_subpaths": 0}, {"max_subpaths": 257}):
            self.assertIsNone(call(valid, (3, 0, 7, 10), **kwargs))
        for rectangle in ((3, 0, 3, 10), (3, 0, 7, math.inf), (True, 0, 7, 10),
                          (2**53+1, 0, 2**53+4, 10)):
            self.assertIsNone(call(valid, rectangle))

    def test_resource_limits_and_derived_command_budget(self):
        commands = segments(*[((0, 3*i), (10, 3*i)) for i in range(256)])
        self.assertEqual(call(commands, (3, -1, 7, 1000))["relation"], "intersection")
        self.assertIsNone(call(commands+segments(((0, 999), (10, 999))), (3, -1, 7, 1000)))
        self.assertIsNone(call(segments(((0, 5), (10, 5))), (3, 0, 7, 10), max_commands=2))

    def test_twenty_nine_bound_real_source_regressions(self):
        counts = {"inside": 0, "outside": 0, "intersection": 0}
        for row in REAL_SOURCE_CASES:
            with self.subTest(figure_id=row["figure_id"], source_id=row["source_id"]):
                result = call(row["commands"], row["rectangle"],
                              source_transform=row["source_transform"], stroke_width=row["width"],
                              linejoin=row["linejoin"])
                self.assertIsNotNone(result)
                self.assertEqual(result["relation"], row["expected"])
                counts[result["relation"]] += 1
                if result["relation"] == "intersection":
                    self.assertEqual(result["proof"]["output_rectangle_count"], 1)
        self.assertEqual(counts, {"inside": 19, "outside": 2, "intersection": 8})


# Exact source fixtures from clip-cross-recheck-87ad02d-001, compacted below.
# The external audit retains full PDF/SVG/ROI hashes and source identity.
REAL_SOURCE_CASES = [{'figure_id': 'ccf-2020-03-f02',
  'source_id': 'svg-paint-0-62',
  'commands': [['M', [55.44, 57.59601]], ['L', [541.44, 57.59601]]],
  'source_transform': [1.0, 0.0, 0.0, -1.0, 55.44, 57.59601],
  'width': 0.996,
  'linejoin': 'miter',
  'rectangle': [315.0, 50.0, 565.0, 262.0],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2020-03-f03',
  'source_id': 'svg-paint-0-62',
  'commands': [['M', [55.44, 57.59601]], ['L', [541.44, 57.59601]]],
  'source_transform': [1.0, 0.0, 0.0, -1.0, 55.44, 57.59601],
  'width': 0.996,
  'linejoin': 'miter',
  'rectangle': [315.0, 50.0, 565.0, 203.0],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2020-03-f04',
  'source_id': 'svg-paint-0-1-61',
  'commands': [['M', [55.44, 57.59601]], ['L', [541.44, 57.59601]]],
  'source_transform': [1.0, 0.0, 0.0, -1.0, 55.44, 57.59601],
  'width': 0.996,
  'linejoin': 'miter',
  'rectangle': [40, 60, 565, 300],
  'expected': 'outside'},
 {'figure_id': 'ccf-2020-17-f02',
  'source_id': 'svg-paint-0-1-10-0-0-596',
  'commands': [['M', [350.215203920486, 85.36169948919479]], ['L', [350.215203920486, 173.507383203768]]],
  'source_transform': [0.0008479134, 0.0, 0.0, 0.0008479134, 53.798, 83.68578],
  'width': 381.0,
  'linejoin': 'round',
  'rectangle': [53.798, 83.68578, 558.19454, 172],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2021-01-f01',
  'source_id': 'svg-paint-0-39-73',
  'commands': [['M', [378.25425327999994, 86.4572086]],
               ['L', [378.25425327999994, 88.9273506744]],
               ['M', [378.25425327999994, 93.86763482319999]],
               ['L', [378.25425327999994, 98.8109836644]],
               ['M', [378.25425327999994, 103.7512678132]],
               ['L', [378.25425327999994, 108.6946166544]],
               ['M', [378.25425327999994, 113.61038326399999]],
               ['L', [378.25425327999994, 118.575184952]],
               ['M', [378.25425327999994, 123.509339716]],
               ['L', [378.25425327999994, 128.44349448]],
               ['M', [378.25425327999994, 133.377649244]],
               ['L', [378.25425327999994, 138.342450932]],
               ['M', [378.25425327999994, 143.276605696]],
               ['L', [378.25425327999994, 148.21076046]],
               ['M', [378.25425327999994, 153.144915224]],
               ['L', [378.25425327999994, 158.109716912]],
               ['M', [378.25425327999994, 163.04387167599998]],
               ['L', [378.25425327999994, 167.97802644]],
               ['M', [378.25425327999994, 172.91218120399998]],
               ['L', [378.25425327999994, 177.876982892]],
               ['M', [378.25425327999994, 182.81113765599997]],
               ['L', [378.25425327999994, 187.74529242]],
               ['M', [378.25425327999994, 192.679447184]],
               ['L', [378.25425327999994, 197.644248872]],
               ['M', [378.25425327999994, 202.57840363599996]],
               ['L', [378.25425327999994, 207.5125584]],
               ['M', [378.25425327999994, 212.44671316400002]],
               ['L', [378.25425327999994, 217.41151485199998]],
               ['M', [378.25425327999994, 222.34566961599995]],
               ['L', [378.25425327999994, 227.27982437999998]],
               ['M', [378.25425327999994, 232.213979144]],
               ['L', [378.25425327999994, 237.17878083199997]],
               ['M', [378.25425327999994, 242.112935596]],
               ['L', [378.25425327999994, 244.59533643999998]]],
  'source_transform': [0.30646924, 0.0, 0.0, 0.30646924, 141.66, 81.86017],
  'width': 4.0,
  'linejoin': 'miter',
  'rectangle': [141.66, 81.86016000000001, 470.34432000000004, 247.854],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-01-f01',
  'source_id': 'svg-paint-0-39-74',
  'commands': [['M', [378.25425327999994, 86.487855524]], ['L', [378.25425327999994, 85.84427011999999]]],
  'source_transform': [0.30646924, 0.0, 0.0, 0.30646924, 141.66, 81.86017],
  'width': 4.0,
  'linejoin': 'miter',
  'rectangle': [141.66, 81.86016000000001, 470.34432000000004, 247.854],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-01-f01',
  'source_id': 'svg-paint-0-39-75',
  'commands': [['M', [378.25425327999994, 244.564689516]], ['L', [378.25425327999994, 245.20827492]]],
  'source_transform': [0.30646924, 0.0, 0.0, 0.30646924, 141.66, 81.86017],
  'width': 4.0,
  'linejoin': 'miter',
  'rectangle': [141.66, 81.86016000000001, 470.34432000000004, 247.854],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-02-f01',
  'source_id': 'svg-paint-0-63',
  'commands': [['M', [55.44, 57.59601]], ['L', [541.44, 57.59601]]],
  'source_transform': [1.0, 0.0, 0.0, -1.0, 55.44, 57.59601],
  'width': 0.996,
  'linejoin': 'miter',
  'rectangle': [51, 59, 564, 305],
  'expected': 'outside'},
 {'figure_id': 'ccf-2021-03-f04',
  'source_id': 'svg-paint-0-1-0-383',
  'commands': [['M', [97.6394261926, 109.65350179500001]], ['L', [97.6394261926, 72.63657059500001]]],
  'source_transform': [0.23135582, 0.0, 0.0, 0.23135582, 50.227678, 72.11602],
  'width': 1.0,
  'linejoin': 'miter',
  'rectangle': [50.112, 72.00034959999999, 286.3263, 130.99607431312],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-03-f04',
  'source_id': 'svg-paint-0-1-0-385',
  'commands': [['M', [60.6224949926, 109.65350179500001]], ['L', [60.6224949926, 72.63657059500001]]],
  'source_transform': [0.23135582, 0.0, 0.0, 0.23135582, 50.227678, 72.11602],
  'width': 1.0,
  'linejoin': 'miter',
  'rectangle': [50.112, 72.00034959999999, 286.3263, 130.99607431312],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-03-f04',
  'source_id': 'svg-paint-0-1-0-386',
  'commands': [['M', [69.8767277926, 109.537823885]], ['L', [69.8767277926, 72.520892685]]],
  'source_transform': [0.23135582, 0.0, 0.0, 0.23135582, 50.227678, 72.11602],
  'width': 1.0,
  'linejoin': 'miter',
  'rectangle': [50.112, 72.00034959999999, 286.3263, 130.99607431312],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-03-f04',
  'source_id': 'svg-paint-0-1-0-388',
  'commands': [['M', [60.6224949926, 72.63657059500001]], ['L', [97.6394261926, 72.63657059500001]]],
  'source_transform': [0.23135582, 0.0, 0.0, 0.23135582, 50.227678, 72.11602],
  'width': 1.0,
  'linejoin': 'miter',
  'rectangle': [50.112, 72.00034959999999, 286.3263, 130.99607431312],
  'expected': 'inside'},
 {'figure_id': 'ccf-2021-03-f04',
  'source_id': 'svg-paint-0-1-0-390',
  'commands': [['M', [88.3851933926, 109.537823885]], ['L', [88.3851933926, 72.520892685]]],
  'source_transform': [0.23135582, 0.0, 0.0, 0.23135582, 50.227678, 72.11602],
  'width': 1.0,
  'linejoin': 'miter',
  'rectangle': [50.112, 72.00034959999999, 286.3263, 130.99607431312],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-0',
  'commands': [['M', [75.9305, 102.8069999999999]], ['L', [75.9305, 261.7869999999999]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-1',
  'commands': [['M', [67.98440000000001, 102.8069999999999]], ['L', [67.98440000000001, 278.86299999999994]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-2',
  'commands': [['M', [60.5055, 137.39599999999996]], ['L', [108.977, 137.39599999999996]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-3',
  'commands': [['M', [60.973000000000006, 130.85199999999998]],
               ['L', [109.44500000000001, 130.85199999999998]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-4',
  'commands': [['M', [60.5055, 123.37399999999991]], ['L', [108.977, 123.37399999999991]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-5',
  'commands': [['M', [60.973000000000006, 115.89499999999998]],
               ['L', [109.44500000000001, 115.89499999999998]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-6',
  'commands': [['M', [59.5707, 183.21000000000004]], ['L', [108.043, 183.21000000000004]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-7',
  'commands': [['M', [60.03830000000001, 176.19899999999996]],
               ['L', [108.50999999999999, 176.19899999999996]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-8',
  'commands': [['M', [59.5707, 168.7199999999999]], ['L', [108.043, 168.7199999999999]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-9',
  'commands': [['M', [60.03830000000001, 161.70899999999995]],
               ['L', [108.50999999999999, 161.70899999999995]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-10',
  'commands': [['M', [59.5707, 226.21899999999994]], ['L', [108.043, 226.21899999999994]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-11',
  'commands': [['M', [59.5707, 219.20799999999997]], ['L', [108.043, 219.20799999999997]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-12',
  'commands': [['M', [59.5707, 212.18999999999994]], ['L', [108.043, 212.18999999999994]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'inside'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-19',
  'commands': [['M', [83.8766, 109.81799999999998]], ['L', [83.8766, 268.7979999999999]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-20',
  'commands': [['M', [91.8262, 102.8069999999999]], ['L', [91.8262, 261.7869999999999]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'intersection'},
 {'figure_id': 'ccf-2025-03-f02',
  'source_id': 'svg-paint-0-1-0-0-0-17-21',
  'commands': [['M', [99.7723, 102.8069999999999]], ['L', [99.7723, 261.7869999999999]]],
  'source_transform': [0.1, 0.0, 0.0, -0.1, 0.0, 792.0],
  'width': 6.23355,
  'linejoin': 'miter',
  'rectangle': [58.5, 71.99899999999991, 553.508, 246.84799999999996],
  'expected': 'intersection'}]


if __name__ == "__main__":
    unittest.main()
