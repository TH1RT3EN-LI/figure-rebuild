"""Source identity and analytic geometry tests, independent of a PDF corpus."""
import math
import json
from pathlib import Path
import unittest
from unittest import mock

from figure_rebuild.pdf_source import (
    PdfSourceError, UnsupportedPdfPaintError, extract_outlined_svg, outline_paths,
)


def source(body):
    return extract_outlined_svg(f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">{body}</svg>')


def point(command, key):
    return command[key]["x"], command[key]["y"]


class PdfSourceTests(unittest.TestCase):
    def test_expanded_command_budget_is_explicit_and_recorded(self):
        svg = '<svg width="10" height="10"><defs><path id="p" d="M0 0L1 1"/></defs><use href="#p"/><use href="#p"/></svg>'
        with self.assertRaisesRegex(PdfSourceError, "total command budget"):
            extract_outlined_svg(svg, max_total_commands=3)
        result = extract_outlined_svg(svg, max_total_commands=4)
        self.assertEqual(result.parser_limits['expanded_commands'], 4)
        self.assertEqual(result.parser_limits['parsed_commands_including_failed_paths'], 4)
        self.assertEqual(result.parser_limits['max_total_commands'], 4)
        for value in (True, 0, -1, 2_000_001, 4.0, '4', None):
            with self.subTest(value=value), self.assertRaises(PdfSourceError):
                extract_outlined_svg(svg, max_total_commands=value)

    def test_empty_references_cannot_expand_without_a_node_budget(self):
        # No path commands: a command-only guard cannot stop this use graph.
        definitions = '<g id="g0"/>' + ''.join(
            f'<g id="g{i}"><use href="#g{i-1}"/><use href="#g{i-1}"/></g>'
            for i in range(1, 18))
        with self.assertRaisesRegex(PdfSourceError, "expanded node budget"):
            extract_outlined_svg(f'<svg width="10" height="10"><defs>{definitions}</defs><use href="#g17"/></svg>')

    def test_failed_referenced_paths_still_consume_the_shared_command_budget(self):
        svg = ('<svg width="10" height="10"><defs><path id="p" '
               'd="M0 0L1 1L2 2L1e999 0"/></defs><use href="#p"/><use href="#p"/></svg>')
        with self.assertRaisesRegex(PdfSourceError, "total command budget"):
            extract_outlined_svg(svg, max_total_commands=7)
        result = extract_outlined_svg(svg, max_total_commands=8)
        self.assertEqual(len(result.paints), 2)
        self.assertTrue(all(p.unsupported and not p.commands for p in result.paints))
        self.assertEqual(result.parser_limits['expanded_commands'], 0)
        self.assertEqual(result.parser_limits['parsed_commands_including_failed_paths'], 8)

    def test_per_path_resource_budget_is_fatal_even_with_larger_total_budget(self):
        with mock.patch('figure_rebuild.pdf_source._MAX_COMMANDS', 3):
            with self.assertRaisesRegex(PdfSourceError, "path exceeds command budget"):
                extract_outlined_svg('<svg width="10" height="10"><path d="M0 0L1 1L2 2Z"/></svg>',
                                     max_total_commands=100)

    def test_expanded_group_depth_raises_a_declared_source_error(self):
        with self.assertRaisesRegex(PdfSourceError, "hierarchy depth budget"):
            source('<g>' * 1100 + '<path d="M0 0L1 1"/>' + '</g>' * 1100)
        for body in ('<defs><g id="unused">{}</g></defs>', '<metadata id="unused">{}</metadata>'):
            with self.subTest(body=body), self.assertRaisesRegex(PdfSourceError, "input hierarchy depth budget"):
                source(body.format('<g>' * 1100 + '</g>' * 1100))

    def test_tolerated_anisotropy_cannot_prove_a_visible_stroke_strip_empty(self):
        sy = 1 + 2**-35
        doc = source(f'<path transform="matrix(1 0 0 {sy} 0 0)" fill="none" stroke="black" stroke-width="10" d="M20 20H100V100H20Z"/>')
        for region in ((30, (20*sy+5+25*sy)/2, 80, 70),
                       (30, 14, 80, (20*sy-5+15*sy)/2)):
            with self.subTest(region=region), self.assertRaisesRegex(UnsupportedPdfPaintError, "crosses"):
                outline_paths(doc, glyph_mode="outline", region=region)

    def test_round_join_curve_inside_clip_is_not_rejected_as_a_miter(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/d4rt-stroke248.json').read_text())
        data = ' '.join(c[0] + ' '.join(repr(v) for p in c[1:] for v in p)
                        for c in fixture['local_commands'])
        matrix = 'matrix(' + ' '.join(map(str, fixture['matrix'])) + ')'
        doc = source(f'<path d="{data}" transform="{matrix}" fill="none" stroke="black" '
                     f'stroke-width="{fixture["width"]}" stroke-linecap="butt" stroke-linejoin="round"/>')
        original = doc.paints[0].commands
        result = outline_paths(doc, glyph_mode='outline', region=fixture['effective_clip'])
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(doc.paints[0].commands, original)
        proof = result.provenance[0]['stroke_bounds_proof']
        self.assertEqual(proof['centerline'], 'source_Bezier_control_hull')
        self.assertGreater(proof['source_bounds_outward'][1], fixture['effective_clip'][1])
        self.assertEqual(result.objects[0]['style']['stroke_linejoin'], 'round')
        self.assertFalse(result.provenance[0]['clip_boundary_rounding'])

    def test_invisible_evenodd_path_retains_an_explicit_skip_receipt(self):
        # A nested contour would require a fill-rule proof if it were visible.
        shape = 'M0 0H20V20H0Z M5 5H15V15H5Z'
        for attrs in ('fill-opacity="0"', 'opacity="0" stroke="#ff0000"',
                      'fill="none" stroke="#ff0000" stroke-opacity="0"'):
            doc = source(f'<path d="{shape}" fill-rule="evenodd" {attrs}/>')
            result = outline_paths(doc, glyph_mode="outline")
            self.assertFalse(result.objects)
            self.assertEqual(result.skipped[0]['reason'], 'no visible source paint')
        with self.assertRaises(UnsupportedPdfPaintError):
            outline_paths(source(f'<path d="{shape}" fill-rule="evenodd" fill-opacity="0.000001"/>'), glyph_mode="outline")

    def test_zero_alpha_does_not_bypass_unknown_effects_or_visible_stroke(self):
        for attrs in ('filter="url(#unknown)"', 'mask="url(#unknown)"',
                      'stroke="#ff0000" stroke-opacity="1"'):
            doc = source(f'<path d="M0 0H20V20H0Z M5 5H15V15H5Z" fill-rule="evenodd" fill-opacity="0" {attrs}/>')
            with self.subTest(attrs=attrs), self.assertRaises(UnsupportedPdfPaintError):
                outline_paths(doc, glyph_mode="outline")

    def test_point_only_space_glyph_is_accounted_for_without_a_drawable_object(self):
        doc = source('<defs><path id="space" d="M0 0V0Z"/></defs><use href="#space" data-text=" " x="10" y="20"/>')
        result = outline_paths(doc, glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(result.skipped[0]['source_text_unverified'], ' ')
        self.assertEqual(result.skipped[0]['source_point'], [10, 20])
        stroked = source('<path d="M10 20L10 20Z" fill="none" stroke="#ff0000" stroke-linecap="round"/>')
        self.assertFalse(outline_paths(stroked, glyph_mode="outline").skipped)

    def test_cubic_control_points_and_affine_are_not_sampled(self):
        document = source('<path id="p" transform="matrix(2,1,.5,3,7,11)" d="M1 2 C3 5 9 7 12 13"/>')
        result = outline_paths(document, glyph_mode="outline", transform=(1,0,0,1,10,20))
        commands = result.objects[0]["commands"]
        self.assertEqual(len(commands), 2)
        self.assertEqual(point(commands[0], "moveTo"), (20, 38))
        self.assertEqual(commands[1]["cubicTo"], {"x1":25.5, "y1":49, "x2":38.5, "y2":61, "x":47.5, "y":82})
        self.assertEqual(result.provenance[0]["source_element_id"], "p")

    def test_quadratic_elevation_matches_polynomial_at_many_parameters(self):
        doc = source('<path d="M1 2 Q7 14 19 5"/>')
        commands = doc.paints[0].commands
        self.assertEqual([c[0] for c in commands], ["M", "C"])
        a, b, c, d = commands[0][1], *commands[1][1:]
        for i in range(21):
            t = i/20
            quadratic = tuple((1-t)**2*a[j]+2*(1-t)*t*(7,14)[j]+t*t*d[j] for j in (0,1))
            cubic = tuple((1-t)**3*a[j]+3*(1-t)**2*t*b[j]+3*(1-t)*t*t*c[j]+t**3*d[j] for j in (0,1))
            self.assertLess(math.dist(quadratic, cubic), 1e-12)

    def test_coincident_glyphs_use_actual_resource_not_nearest_origin_or_unicode(self):
        doc = source('''<defs><path id="glyph-zero" d="M0 0L4 0L4 8Z"/><path id="glyph-one" d="M0 0L1 0L1 8Z"/></defs>
          <use href="#glyph-zero" data-text='"' transform="matrix(1,0,0,1,20,30)"/>
          <use href="#glyph-one" data-text='"' transform="matrix(1,0,0,1,20,30)"/>''')
        self.assertEqual(len(doc.paints), 2)
        self.assertNotEqual(doc.paints[0].source_id, doc.paints[1].source_id)
        self.assertEqual([p.resource_id for p in doc.paints], ["glyph-zero", "glyph-one"])
        result = outline_paths(doc, glyph_mode="outline")
        self.assertEqual([point(o["commands"][1],"lineTo")[0] for o in result.objects], [24,21])
        self.assertEqual([p["source_text_unverified"] for p in result.provenance], ['"','"'])
        self.assertTrue(all(o["kind"] == "path" and "text" not in o for o in result.objects))
        self.assertTrue(all(p["text_editable"] is False for p in result.provenance))

    def test_repeated_type3_group_glyphs_have_distinct_child_identity_and_order(self):
        doc=source('''<defs><g id="glyph" fill="#123456" transform="matrix(2,0,0,2,0,0)"><path id="stem" d="M0 0L1 0"/><path id="bar" d="M0 1L2 1"/></g></defs>
        <use href="#glyph" data-text="T" x="5"/><use href="#glyph" data-text="T" x="20" fill="#abcdef"/>''')
        result=outline_paths(doc,glyph_mode="outline")
        self.assertEqual(len({p.source_id for p in doc.paints}),4)
        self.assertEqual([p.kind for p in doc.paints],["glyph"]*4)
        self.assertEqual([p.source_element_id for p in doc.paints],["stem","bar","stem","bar"])
        self.assertEqual([p.paint_index for p in doc.paints],[0,1,2,3])
        self.assertEqual([point(o["commands"][0],"moveTo")[0] for o in result.objects],[5,5,20,20])
        self.assertEqual([o["style"]["fill"] for o in result.objects],["#123456"]*4)
        self.assertTrue(all(p["resource_id"]=="glyph" for p in result.provenance))

    def test_empty_type3_resource_keeps_occurrence_without_inventing_outline(self):
        doc=source('<defs><g id="empty"/></defs><use href="#empty" data-text="Q"/><use href="#empty" data-text="Q"/>')
        self.assertEqual(len(doc.paints),2)
        self.assertEqual([p.source_text for p in doc.paints],["Q","Q"])
        result=outline_paths(doc,glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(len(result.skipped),2)
        self.assertEqual(len({p["source_id"] for p in result.skipped}),2)

    def test_referenced_groups_keep_inherited_style_cycles_and_effect_failures(self):
        doc=source('<defs><g id="glyph"><path d="M0 0L2 2"/></g></defs><use href="#glyph" fill="#abcdef"/>')
        self.assertEqual(outline_paths(doc,glyph_mode="outline").objects[0]["style"]["fill"],"#abcdef")
        cyclic=source('<defs><g id="g"><path d="M0 0L1 1"/><use href="#g"/></g></defs><use href="#g"/>')
        self.assertEqual(len(cyclic.paints),2)
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"cyclic"):
            outline_paths(cyclic,glyph_mode="outline")
        alpha=source('<defs><g id="g" opacity=".5"><path d="M0 0L1 1"/></g></defs><use href="#g"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Group opacity"):
            outline_paths(alpha,glyph_mode="outline")

    def test_nested_references_compose_use_translation_and_resource_transform(self):
        doc = source('''<defs><path id="p" transform="matrix(2,0,0,2,1,1)" d="M0 0L1 1"/>
        <use id="alias" href="#p" x="3" y="4" transform="matrix(1,0,0,1,10,20)"/></defs>
        <g transform="matrix(2,0,0,2,0,0)"><use href="#alias" x="1" y="2"/></g>''')
        paint = doc.paints[0]
        self.assertEqual(paint.reference_chain, ("alias", "p"))
        self.assertEqual(paint.commands[:2], (("M", (30,54)), ("L",(34,58))))

    def test_nonpainting_resources_outside_defs_do_not_consume_paint_order(self):
        doc=source('<clipPath id="c"><path d="M0 0H10V10H0Z"/></clipPath><mask id="m"><image href="data:image/png;base64,AA==" width="1" height="1"/></mask><path d="M1 1L2 2"/>')
        self.assertEqual(len(doc.paints),1)
        self.assertEqual(doc.paints[0].paint_index,0)
        self.assertEqual(set(doc.resource_xml),{"c","m"})

    def test_foreign_attributes_and_leaf_effects_cannot_change_or_disappear(self):
        cases=['<path xmlns:v="urn:foreign" v:fill="#ff0000" d="M0 0L2 2"/>',
               '<path d="M0 0L2 2"><set attributeName="fill" to="#ff0000"/></path>']
        for body in cases:
            with self.subTest(body=body):
                doc=source(body)
                self.assertEqual(doc.paints[0].style["fill"],"#000000")
                self.assertTrue(doc.paints[0].unsupported)
                with self.assertRaises(UnsupportedPdfPaintError):outline_paths(doc,glyph_mode="outline")

    def test_conflicting_reference_spellings_are_not_silently_guessed(self):
        doc=source('<defs><path id="a" d="M0 0L1 1"/><path id="b" d="M0 0L9 9"/></defs><use xmlns:xlink="http://www.w3.org/1999/xlink" href="#a" xlink:href="#b"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"conflicting href"):
            outline_paths(doc,glyph_mode="outline")

    def test_identity_independent_of_unverified_text(self):
        a=source('<defs><path id="g" d="M0 0L3 4"/></defs><use href="#g" data-text="a"/>')
        b=source('<defs><path id="g" d="M0 0L3 4"/></defs><use href="#g" data-text="changed"/>')
        self.assertEqual(a.paints[0].source_id, b.paints[0].source_id)
        self.assertNotEqual(a.source_sha256, b.source_sha256)

    def test_image_is_separate_occurrence_and_subset_preserves_paint_order(self):
        doc=source('<path d="M0 0L1 1"/><image id="im" width="2" height="2" href="data:image/png;base64,AA=="/><use href="#im"/><path d="M0 0L2 2"/>')
        self.assertEqual([p.kind for p in doc.paints], ["path","image","image","path"])
        self.assertEqual(doc.paints[2].resource_id, "im")
        with self.assertRaisesRegex(UnsupportedPdfPaintError, "images require separate"):
            outline_paths(doc,glyph_mode="outline")
        selected = [doc.paints[3].source_id, doc.paints[0].source_id]
        result=outline_paths(doc,glyph_mode="outline",paint_ids=selected)
        self.assertEqual([p["z_index"] for p in result.objects],[0,3])
        self.assertEqual(result.source_paint_count,4)
        self.assertEqual(len(result.selected_paint_ids),2)

    def test_clip_matrix_uses_declaration_space_not_child_transform(self):
        doc=source('''<defs><clipPath id="c"><path d="M0 0H10V10H0Z"/></clipPath></defs>
          <g transform="matrix(1,0,0,1,10,20)" clip-path="url(#c)">
          <path transform="matrix(1,0,0,1,2,3)" d="M0 0L2 2"/></g>''')
        self.assertEqual(doc.paints[0].clips[0]["transform"], [1,0,0,1,10,20])
        result=outline_paths(doc,glyph_mode="outline")
        self.assertEqual(point(result.objects[0]["commands"][0],"moveTo"),(12,23))
        self.assertEqual(result.provenance[0]["clip_context"][0]["id"],"c")

    def test_clip_crossing_rejected_and_wholly_outside_skipped(self):
        prefix='<defs><clipPath id="c"><rect x="0" y="0" width="10" height="10"/></clipPath></defs>'
        crossed=source(prefix+'<path clip-path="url(#c)" fill="none" stroke="#000000" d="M2 2L12 3"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
            outline_paths(crossed,glyph_mode="outline")
        outside=source(prefix+'<path clip-path="url(#c)" d="M20 20L25 25"/>')
        result=outline_paths(outside,glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(len(result.skipped),1)

    def test_complex_clip_is_retained_but_not_silently_applied(self):
        doc=source('<defs><clipPath id="c"><path d="M0 0Q4 4 8 0Z"/></clipPath></defs><path clip-path="url(#c)" d="M1 1L2 2"/>')
        self.assertIn("Q4 4",doc.paints[0].clips[0]["element"])
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Complex clip"):
            outline_paths(doc,glyph_mode="outline")

    def test_group_alpha_not_multiplied_into_overlapping_children(self):
        doc=source('<g opacity=".5"><path d="M0 0H10V10H0Z"/><path d="M5 0H15V10H5Z"/></g>')
        self.assertEqual(len(doc.paints),2)
        self.assertNotIn("opacity",doc.paints[0].style)
        self.assertEqual(doc.paints[0].groups[-1]["opacity"],".5")
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Group opacity"):
            outline_paths(doc,glyph_mode="outline")

    def test_leaf_alpha_inherited_fill_and_similarity_stroke(self):
        doc=source('<g fill="#abc" fill-opacity=".5"><path opacity=".4" d="M0 0L1 1"/></g>')
        result=outline_paths(doc,glyph_mode="outline")
        self.assertEqual(result.objects[0]["style"], {"fill":"#aabbcc","stroke":"none","stroke_width":0,"opacity":.2})
        doc=source('<path fill="none" stroke="#123456" stroke-width="2" transform="matrix(0,2,-2,0,40,0)" d="M0 0L1 1"/>')
        result=outline_paths(doc,glyph_mode="outline",transform=(3,0,0,3,0,0))
        self.assertEqual(result.objects[0]["style"]["stroke_width"],12)
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Nonuniform/skew stroke"):
            outline_paths(doc,glyph_mode="outline",transform=(2,0,0,3,0,0))

    def test_native_stroke_cap_join_and_miter_are_preserved_with_render_caveat(self):
        for cap,join,limit in [("square","miter",8),("round","round",10),("butt","bevel",2)]:
            with self.subTest(cap=cap,join=join):
                doc=source(f'<path d="M10 10L20 20L30 10" fill="none" stroke="#123456" stroke-linecap="{cap}" stroke-linejoin="{join}" stroke-miterlimit="{limit}"/>')
                result=outline_paths(doc,glyph_mode="outline")
                style=result.objects[0]["style"]
                self.assertEqual((style["stroke_linecap"],style["stroke_linejoin"]),(cap,join))
                self.assertEqual(style.get("stroke_miterlimit"),limit if join=="miter" else None)
                self.assertTrue(result.provenance[0]["stroke_visual_verification_required"])
                self.assertEqual(result.provenance[0]["stroke_preview_renderer_support"],"not_verified_or_unsupported")
        doc=source('<path d="M0 0L10 10" stroke="#000" stroke-dasharray="0,2"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"strictly positive"):
            outline_paths(doc,glyph_mode="outline")

    def test_evenodd_proof_keeps_disjoint_polygon_geometry_but_not_holes(self):
        doc=source('<path d="M0 0H5V5H0Z M10 0H15V5H10Z" fill-rule="evenodd"/>')
        result=outline_paths(doc,glyph_mode="outline")
        proof=result.provenance[0]["fill_rule_equivalence"]
        self.assertEqual(proof["contour_count"],2)
        self.assertFalse(proof["source_commands_changed"])
        self.assertEqual(sum("close" in c for c in result.objects[0]["commands"]),2)
        hole=source('<path d="M0 0H10V10H0Z M2 2H8V8H2Z" fill-rule="evenodd"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Evenodd"):
            outline_paths(hole,glyph_mode="outline")

    def test_dash_split_preserves_opaque_fill_then_stroke_and_paint_identity(self):
        doc=source('<path id="before" d="M0 0L1 1"/><path d="M10 10H30V30H10Z" fill="#123456" stroke="#abcdef" stroke-dasharray="3,2"/><path id="after" d="M0 0L1 1"/>')
        result=outline_paths(doc,glyph_mode="outline")
        self.assertEqual([o["z_index"] for o in result.objects],[0,1,1,2])
        fill,stroke=result.objects[1:3]
        self.assertEqual((fill["style"]["fill"],fill["style"]["stroke"]),("#123456","none"))
        self.assertEqual((stroke["style"]["fill"],stroke["style"]["stroke"]),("none","#abcdef"))
        self.assertIn("close",fill["commands"][-1])
        self.assertNotEqual(fill["id"],stroke["id"])
        fp,sp=result.provenance[1:3]
        self.assertEqual(fp["source_paint_id"],sp["source_paint_id"])
        self.assertEqual((fp["source_paint_suborder"],sp["source_paint_suborder"]),(0,1))
        self.assertFalse(sp["dash_lowering"]["geometry_flattened"])

    def test_dash_phase_before_crop_and_original_transform(self):
        doc=source('<path d="M0 0H20" transform="matrix(2,0,0,2,10,20)" fill="none" stroke="#000" stroke-dasharray="4,2" stroke-dashoffset="1"/>')
        result=outline_paths(doc,glyph_mode="outline")
        commands=result.objects[0]["commands"]
        self.assertEqual(point(commands[0],"moveTo"),(10,20))
        self.assertEqual(point(commands[1],"lineTo"),(16,20))
        self.assertEqual(point(commands[2],"moveTo"),(20,20))
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
            outline_paths(doc,glyph_mode="outline",region=(15,15,60,25))

    def test_rectangular_clip_allowance_is_explicit_bounded_and_narrow(self):
        prefix='<defs><clipPath id="c"><rect x="9.00002" y="9" width="22" height="22"/></clipPath></defs>'
        doc=source(prefix+'<path d="M10 10H30V30H10Z" stroke="#000" stroke-width="2" clip-path="url(#c)"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
            outline_paths(doc,glyph_mode="outline")
        r=outline_paths(doc,glyph_mode="outline",max_clip_overhang=1e-4,transform=(2,0,0,2,0,0))
        receipt=r.provenance[0]["clip_boundary_rounding"][0]
        self.assertEqual(receipt["classification"],"bounded_source_rounding")
        self.assertFalse(receipt["exact_noop_clip"])
        self.assertAlmostEqual(receipt["overhang_left_top_right_bottom_source_units"][0],.00002)
        self.assertAlmostEqual(receipt["maximum_overhang_target_units"],.00004)
        diagonal=source(prefix+'<path d="M10 10L30 30" stroke="#000" stroke-width="2" clip-path="url(#c)"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
            outline_paths(diagonal,glyph_mode="outline",max_clip_overhang=.01)

    def test_equal_component_alpha_is_not_replaced_by_whole_object_alpha(self):
        doc=source('<path d="M0 0H10V10H0Z" fill="#fff" stroke="#000" fill-opacity=".5" stroke-opacity=".5"/>')
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"Combined fill/stroke alpha"):
            outline_paths(doc,glyph_mode="outline")

    def test_inkscape_layer_metadata_has_no_paint_effect(self):
        doc=source('<g xmlns:i="http://www.inkscape.org/namespaces/inkscape" i:groupmode="layer" i:label="Layer 1"><path d="M0 0L1 1"/></g>')
        self.assertEqual(len(outline_paths(doc,glyph_mode="outline").objects),1)

    def test_effects_remain_explicit_failures_and_do_not_erase_good_paints(self):
        cases=[('<path d="M0 0L1 1" fill="url(#gradient)"/>',"gradients"),
               ('<path d="M0 0L1 1" fill-rule="evenodd"/>',"Evenodd"),
               ('<path d="M0 0A1 1 0 0 0 2 2"/>',"elliptical arcs"),
               ('<g style="mix-blend-mode:multiply"><path d="M0 0L1 1"/></g>',"blend/isolation"),
               ('<path d="M0 0L1 1" mask="url(#m)"/>',"mask"),
               ('<path d="M0 0L1 1" style="filter:blur(2px)"/>',"filter"),
               ('<foreignObject><text>do not drop</text></foreignObject>',"unsupported paint element"),
               ('<path d="M0 0L1 1" magic-effect="on"/>',"unsupported attribute"),
               ('<path d="L1 1"/>',"begin with moveto")]
        for body,error in cases:
            with self.subTest(body=body):
                doc=source('<path id="good" d="M0 0L2 2"/>'+body)
                self.assertEqual(len(doc.paints),2)
                self.assertTrue(doc.paints[1].source_xml)
                with self.assertRaisesRegex(UnsupportedPdfPaintError,error):
                    outline_paths(doc,glyph_mode="outline")

    def test_cycles_missing_external_references_are_not_followed(self):
        for body,error in [('<defs><use id="a" href="#b"/><use id="b" href="#a"/></defs><use href="#a"/>',"cyclic"),
                           ('<use href="#missing"/>',"missing or external"),
                           ('<use href="https://example.com/g.svg#x"/>',"missing or external")]:
            with self.subTest(body=body):
                doc=source(body)
                self.assertEqual(len(doc.paints),1)
                with self.assertRaisesRegex(UnsupportedPdfPaintError,error):outline_paths(doc,glyph_mode="outline")

    def test_invalid_documents_and_explicit_policy(self):
        for svg,error in [('<!DOCTYPE svg><svg/>',"DTD"),
                          ('<svg width="10" height="10"><path id="p"/><path id="p"/></svg>',"Duplicate"),
                          ('<svg width="10" height="10"><defs><style>path {fill:red}</style></defs></svg>',"Stylesheets"),
                          ('<svg width="10" height="10"><path transform="scale(2)"/></svg>',"Only MuPDF matrix")]:
            with self.subTest(svg=svg),self.assertRaisesRegex(PdfSourceError,error):extract_outlined_svg(svg)
        doc=source('<path d="M0 0L1 1"/>')
        with self.assertRaisesRegex(PdfSourceError,"Explicit glyph_mode"):
            outline_paths(doc,glyph_mode="live")
        with self.assertRaisesRegex(PdfSourceError,"Unknown paint"):
            outline_paths(doc,glyph_mode="outline",paint_ids=["invented"])

    def test_roi_and_explicit_coordinate_mapping(self):
        doc=source('<path d="M10 20L15 25"/><path d="M70 70L80 80"/>')
        result=outline_paths(doc,glyph_mode="outline",region=(10,20,30,40),transform=(2,0,0,2,-20,-40))
        self.assertEqual(point(result.objects[0]["commands"][0],"moveTo"),(0,0))
        self.assertEqual(point(result.objects[0]["commands"][1],"lineTo"),(10,10))
        self.assertEqual(len(result.skipped),1)

    def test_filled_axis_rectangle_intersects_all_rectangular_clips_exactly(self):
        doc=source('''<defs><clipPath id="a"><rect x="10" y="15" width="50" height="55"/></clipPath>
            <clipPath id="b"><rect x="20" y="10" width="60" height="70"/></clipPath></defs>
            <g clip-path="url(#a)"><path clip-path="url(#b)" fill="#abc" d="M0 0H90V90H0Z"/></g>''')
        result=outline_paths(doc,glyph_mode="outline",region=(0,0,100,50),transform=(2,0,0,3,-40,-45))
        receipt=result.provenance[0]["rectangle_fill_intersection"]
        self.assertEqual(receipt["output_rectangle_bounds"],[20,15,60,50])
        self.assertTrue(receipt["source_commands_changed"])
        self.assertEqual(result.objects[0]["commands"], [
            {"moveTo":{"x":0,"y":0}}, {"lineTo":{"x":80,"y":0}},
            {"lineTo":{"x":80,"y":105}}, {"lineTo":{"x":0,"y":105}}, {"close":{}}])
        self.assertEqual(doc.paints[0].commands[0],("M",(0,0)))

    def test_filled_rectangle_clipping_is_not_a_tolerance_snap(self):
        doc=source('<path fill="white" d="M0 0H10V10H0Z"/>')
        lo=math.nextafter(0.,1.)
        result=outline_paths(doc,glyph_mode="outline",region=(lo,0,9,10))
        self.assertEqual(result.objects[0]["commands"][0]["moveTo"]["x"],lo)
        self.assertEqual(result.provenance[0]["rectangle_fill_intersection"]["output_rectangle_bounds"],[lo,0,9,10])

    def test_rectangle_stroke_enclosing_clip_has_no_visible_sides(self):
        doc=source('<path fill="none" stroke="black" stroke-width="2" stroke-linejoin="miter" d="M0 0H100V100H0Z"/>')
        result=outline_paths(doc,glyph_mode="outline",region=(20,20,80,80))
        self.assertFalse(result.objects)
        self.assertEqual(len(result.skipped),1)
        self.assertEqual(len(result.skipped[0]["source_side_envelopes"]),4)
        # A genuinely clipped source side must not disappear by this shortcut.
        with self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
            outline_paths(doc,glyph_mode="outline",region=(-1,20,80,80))

    def test_partial_stroke_concave_or_curved_fill_still_requires_geometry_clipping(self):
        for path in ('<path stroke="black" d="M0 0H10V10H0Z"/>',
                     '<path d="M0 0L10 0L3 4L5 10Z"/>',
                     '<path d="M0 0C10 0 10 10 0 10Z"/>'):
            with self.subTest(path=path),self.assertRaisesRegex(UnsupportedPdfPaintError,"crosses clip"):
                outline_paths(source(path),glyph_mode="outline",region=(2,2,8,8))

    def test_disjoint_clip_stack_is_auditable_empty_paint(self):
        doc=source('''<defs><clipPath id="a"><rect width="10" height="10"/></clipPath>
            <clipPath id="b"><rect x="20" y="20" width="10" height="10"/></clipPath></defs>
            <g clip-path="url(#a)"><path clip-path="url(#b)" fill="white" d="M0 0H50V50H0Z"/></g>''')
        result=outline_paths(doc,glyph_mode="outline")
        self.assertFalse(result.objects)
        self.assertEqual(len(result.skipped),1)


if __name__ == "__main__":
    unittest.main()
