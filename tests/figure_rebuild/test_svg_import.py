"""Geometry/semantic checks for strict editable SVG import (stdlib only)."""
import math
from pathlib import Path
import tempfile
import unittest

from tools.figure_rebuild.svg_import import SVGImportError, import_svg


def distance_to_segment(p, a, b):
    vx, vy = b[0]-a[0], b[1]-a[1]
    if not vx and not vy:
        return math.dist(p, a)
    t = max(0, min(1, ((p[0]-a[0])*vx+(p[1]-a[1])*vy)/(vx*vx+vy*vy)))
    return math.dist(p, (a[0]+t*vx, a[1]+t*vy))


def vertices(obj):
    return [(v["x"], v["y"]) for command in obj["commands"] for key, v in command.items() if key != "close"]


class SVGImportTests(unittest.TestCase):
    def parse(self, contents, tolerance=0.35):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/"source.svg"
            file.write_text(contents, encoding="utf-8")
            return import_svg(file, tolerance)

    def test_viewbox_origin_units_and_painter_order(self):
        scene = self.parse('''<svg xmlns="http://www.w3.org/2000/svg" width="2in" height="96px" viewBox="10 20 100 100">
          <g id="layer" fill="#ae0b2a" transform="translate(5 0)">
            <rect id="back" x="10" y="20" width="20" height="10"/>
            <path id="front" d="M10 20h20v10h-20z" fill="rgb(0, 50%, 100%)"/>
          </g></svg>''')
        self.assertEqual(scene["canvas"], {"width": 192, "height": 96})
        self.assertEqual([o["id"] for o in scene["objects"]], ["back", "front"])
        back, front = scene["objects"]
        self.assertEqual(back["group_id"], "layer")
        self.assertEqual(back["style"]["fill"], "#ae0b2a")
        self.assertEqual(front["style"]["fill"], "#0080ff")
        # The square viewBox is centered in the 2:1 viewport; origin is removed.
        self.assertAlmostEqual(vertices(back)[0][0], 52.8)
        self.assertAlmostEqual(vertices(back)[0][1], 0)

    def test_cubic_flattens_with_requested_canvas_error(self):
        tolerance = 0.2
        obj = self.parse('<svg width="400" height="200"><path id="curve" transform="scale(3 2)" d="M0 0 C0 100 100 100 100 0"/></svg>', tolerance)["objects"][0]
        points = vertices(obj)
        self.assertGreater(len(points), 10)
        self.assertEqual(points[0], (0, 0))
        self.assertEqual(points[-1], (300, 0))
        worst = 0
        for index in range(1001):
            t = index/1000
            p = (300*(3*t*t-2*t*t*t), 600*t*(1-t))
            worst = max(worst, min(distance_to_segment(p, a, b) for a, b in zip(points, points[1:])))
        self.assertLessEqual(worst, tolerance)

    def test_all_relative_command_families_and_reflected_controls(self):
        scene = self.parse('<svg width="300" height="300"><path id="p" d="m10 10 20 0 h10 v10 c0 10 10 10 10 0 s10 -10 10 0 q10 20 20 0 t20 0 a10 10 0 01 20 0 z"/></svg>')
        p = vertices(scene["objects"][0])
        self.assertEqual(p[0], (10, 10))
        self.assertEqual(p[-1], (120, 20))
        self.assertIn((30, 10), p)
        self.assertIn((40, 10), p)
        self.assertIn((40, 20), p)
        # Q and its reflected T bow in opposite directions, preserving smoothness.
        self.assertGreater(max(y for x, y in p if 60 <= x <= 80), 20)
        self.assertLess(min(y for x, y in p if 80 <= x <= 100), 20)
        self.assertEqual(scene["objects"][0]["commands"][-1], {"close": {}})

    def test_arc_rotation_radius_correction_and_affine_transforms(self):
        # The two semicircles become a rotated/skewed ellipse; endpoint math and
        # conservative chord error are tested against the analytical affine circle.
        tolerance = .12
        obj = self.parse('<svg width="300" height="300"><circle id="ring" cx="40" cy="50" r="20" transform="translate(30 40) matrix(2 .5 .4 1 0 0)"/></svg>', tolerance)["objects"][0]
        points = vertices(obj)
        self.assertEqual(points[0], (170, 120))
        for index in range(720):
            angle = index*math.pi/360
            x, y = 40+20*math.cos(angle), 50+20*math.sin(angle)
            target = (2*x+.4*y+30, .5*x+y+40)
            self.assertLessEqual(min(distance_to_segment(target, a, b) for a, b in zip(points, points[1:])), tolerance)
        # Radii too small for the endpoint separation must expand per SVG spec.
        arc = self.parse('<svg width="100" height="100"><path d="M0 0 A1 1 45 0 1 10 0"/></svg>')["objects"][0]
        self.assertEqual(vertices(arc)[-1], (10, 0))
        self.assertGreater(len(vertices(arc)), 3)

    def test_compound_hole_remains_one_object_with_opposite_winding(self):
        obj = self.parse('<svg width="100" height="100"><path id="hole" d="M0 0H100V100H0Z M25 25V75H75V25Z"/></svg>')["objects"][0]
        self.assertEqual(obj["id"], "hole")
        self.assertEqual(sum("moveTo" in c for c in obj["commands"]), 2)
        self.assertEqual(sum("close" in c for c in obj["commands"]), 2)
        loops, loop = [], []
        for command in obj["commands"]:
            if "close" in command:
                loops.append(loop); loop = []
            else:
                q = next(iter(command.values())); loop.append((q["x"], q["y"]))
        areas = [sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(loop, loop[1:]+loop[:1]))/2 for loop in loops]
        self.assertGreater(areas[0], 0)
        self.assertLess(areas[1], 0)

    def test_basic_shapes_rounded_rect_and_text_baseline(self):
        scene = self.parse('''<svg width="200" height="100"><g id="labels" transform="translate(5 7) scale(2)">
          <text id="caption" x="30" y="20" font-size="10" font-weight="700" font-style="italic" text-anchor="middle" fill="#333">Hello 中文</text>
          <text id="lines" font-size="8"><tspan id="line-a" x="0" y="30">First</tspan><tspan id="line-b" x="0" y="40">Second</tspan></text>
          </g><rect id="rounded" x="0" y="0" width="20" height="10" rx="2"/>
          <ellipse id="oval" cx="10" cy="20" rx="8" ry="4"/>
          <line id="line" x1="1" y1="2" x2="3" y2="4" stroke="black"/>
          <polyline id="polyline" points="0,0 2,3 4,0" fill="none"/>
          <polygon id="polygon" points="0,0 2,3 4,0"/></svg>''')
        caption = scene["objects"][0]
        self.assertEqual(caption["kind"], "text")
        self.assertEqual(caption["anchor"], {"x": 65, "y": 47})
        self.assertEqual(caption["font_size"], 20)
        self.assertEqual(caption["alignment"], "center")
        self.assertTrue(caption["bold"] and caption["italic"])
        self.assertEqual(caption["text"], "Hello 中文")
        self.assertEqual([o["id"] for o in scene["objects"]], ["caption", "line-a", "line-b", "rounded", "oval", "line", "polyline", "polygon"])
        self.assertGreater(len(vertices(scene["objects"][3])), 8)
        self.assertNotIn("close", scene["objects"][-2]["commands"][-1])
        self.assertIn("close", scene["objects"][-1]["commands"][-1])

    def test_anonymous_identity_does_not_depend_on_displayed_text(self):
        first = self.parse('<svg width="100" height="100"><g><text x="1" y="10">A</text><rect width="5" height="5"/></g></svg>')
        second = self.parse('<svg width="100" height="100"><g><text x="1" y="10">Entirely different</text><rect width="5" height="5"/></g></svg>')
        self.assertEqual([o["id"] for o in first["objects"]], [o["id"] for o in second["objects"]])
        self.assertEqual(first["objects"][0]["group_id"], first["objects"][1]["group_id"])

    def test_rejects_unsupported_input_with_actionable_errors(self):
        cases = [
            ('<image href="data:image/png;base64,AAAA"/>', "Unsupported SVG element"),
            ('<defs><linearGradient id="g"/></defs>', "Unsupported SVG element"),
            ('<rect width="10" height="10" fill="url(#paint)"/>', "Unsupported paint"),
            ('<rect width="10" height="10" fill-rule="evenodd"/>', "normalize compound contour winding"),
            ('<g opacity="0.5"><rect width="10" height="10"/></g>', "Group opacity"),
            ('<path d="M0 0L20 20" stroke="red" transform="scale(2 1)"/>', "expand the stroke"),
            ('<path d="M0 0L20 20" stroke="red" stroke-linecap="round"/>', "outline this effect"),
            ('<text transform="rotate(10)" y="10">Word</text>', "outline the text"),
            ('<text y="10">Word<tspan>tail</tspan></text>', "Mixed text/tspan"),
            ('<rect width="10" height="10" style="filter:blur(2px)"/>', "filter is unsupported"),
            ('<foreignObject/>', "Unsupported SVG element"),
            ('<use href="#shape"/>', "Unsupported SVG element"),
        ]
        for markup, expected in cases:
            with self.subTest(markup=markup), self.assertRaisesRegex(SVGImportError, expected):
                self.parse(f'<svg width="100" height="100">{markup}</svg>')

    def test_input_validation_entities_duplicates_and_path_errors(self):
        cases = [
            ('<!DOCTYPE svg [<!ENTITY x "bad">]><svg/>', "DTD/entity"),
            ('<svg><rect id="a"/><rect id="a"/></svg>', "Duplicate source ID"),
            ('<svg><path d="L0 0"/></svg>', "begin with moveto"),
            ('<svg><path d="M0 0 LNaN 1"/></svg>', "Expected a number"),
            ('<svg><path d="M0 0 A10 10 0 2 0 10 10"/></svg>', "flags must be"),
            ('<svg><path d="M0 0 R1 2"/></svg>', "Unsupported or missing"),
            ('<svg width="0"/>', "positive"),
        ]
        for markup, expected in cases:
            with self.subTest(markup=markup), self.assertRaisesRegex(SVGImportError, expected):
                self.parse(markup)
        with self.assertRaisesRegex(SVGImportError, "tolerance"):
            self.parse('<svg/>', tolerance=0)


if __name__ == "__main__":
    unittest.main()
