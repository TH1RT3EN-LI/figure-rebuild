"""Source conversion must bind an annular proof to its full paint context."""
import copy
import json
from pathlib import Path
import unittest

from figure_rebuild.pdf_source import (
    extract_outlined_svg, outline_paths, UnsupportedPdfPaintError,
)


def path_data(commands):
    return " ".join(c[0] + " ".join(repr(v) for p in c[1:] for v in p)
                    for c in commands)


class PdfAnnularClipIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / "fixtures/sam2-fill-ring.json").read_text())

    def document(self, *, paint_attrs="", clip_attrs="", child_attrs="", extra_child="",
                 outer_attrs="", defs_attrs="", extra_defs="", clip_rule="nonzero"):
        f = self.fixture
        return extract_outlined_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800">'
            f'<defs {defs_attrs}><clipPath id="clip" {clip_attrs}>'
            f'<path d="{path_data(f["clip_commands"])}" clip-rule="{clip_rule}" {child_attrs}/>'
            f'{extra_child}</clipPath>{extra_defs}</defs>'
            f'<g {outer_attrs}><path d="{path_data(f["commands"])}" fill="#abcdef" '
            f'clip-path="url(#clip)" {paint_attrs}/></g></svg>')

    def test_actual_curved_annulus_is_intersected_without_filling_its_hole(self):
        document = self.document()
        before = copy.deepcopy(document.paints)
        result = outline_paths(document, glyph_mode="outline", region=(350, 220, 390, 260))
        self.assertEqual(document.paints, before)
        self.assertEqual(len(result.objects), 1)
        obj, receipt = result.objects[0], result.provenance[0]
        self.assertEqual(obj["style"]["fill"], "#abcdef")
        self.assertEqual(sum("cubicTo" in c for c in obj["commands"]), 8)
        self.assertEqual(sum("moveTo" in c for c in obj["commands"]), 2)
        self.assertEqual(sum("close" in c for c in obj["commands"]), 2)
        proof = receipt["annular_fill_intersection"]
        self.assertTrue(proof["source_commands_changed"])
        self.assertFalse(proof["source_input_mutated"])
        self.assertFalse(proof["original_boundary_coordinates_changed"])
        self.assertEqual(len(proof["shared_line_cancellations"]), 4)
        self.assertEqual(receipt["clip_geometry_proofs"][0]["relation"], "exact_intersection")
        self.assertFalse(receipt["clip_boundary_rounding"])

    def test_remaining_roi_must_contain_complete_derived_control_hull(self):
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "crosses clip/region"):
            outline_paths(self.document(), glyph_mode="outline", region=(360, 220, 390, 260))

    def test_unknown_context_is_not_bypassed_by_geometry_fallback(self):
        variants = [
            {"paint_attrs": 'stroke="black" stroke-width=".1"'},
            {"paint_attrs": 'opacity=".5" stroke="black"'},
            {"paint_attrs": 'mask="url(#unknown)"'},
            {"outer_attrs": 'opacity=".5"'},
            {"outer_attrs": 'isolation="isolate"'},
            {"clip_attrs": 'opacity=".5"'},
            {"child_attrs": 'filter="url(#unknown)"'},
            {"child_attrs": 'xmlns="urn:foreign"'},
            {"defs_attrs": 'clip-rule="evenodd"'},
            {"clip_rule": "evenodd"},
            {"extra_child": '<path d="M0 0H1V1H0Z"/>'},
            {"outer_attrs": 'clip-path="url(#other)"',
             "extra_defs": '<clipPath id="other"><path d="M300 200C300 300 450 300 450 200Z"/></clipPath>'},
        ]
        for kwargs in variants:
            with self.subTest(kwargs=kwargs), self.assertRaises(UnsupportedPdfPaintError):
                outline_paths(self.document(**kwargs), glyph_mode="outline")

    def test_ordinary_fill_opacity_and_target_transform_are_preserved(self):
        result = outline_paths(self.document(paint_attrs='fill-opacity=".4"'),
                               glyph_mode="outline", transform=(2, 0, 0, 2, 5, 7))
        self.assertEqual(result.objects[0]["style"]["opacity"], .4)
        source_result = outline_paths(self.document(), glyph_mode="outline")
        first = source_result.objects[0]["commands"][0]["moveTo"]
        self.assertEqual(result.objects[0]["commands"][0]["moveTo"],
                         {"x": first["x"] * 2 + 5, "y": first["y"] * 2 + 7})


if __name__ == "__main__":
    unittest.main()
