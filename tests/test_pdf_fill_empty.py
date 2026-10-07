"""Only moveto-only subpaths are absent from fill topology proofs."""
import copy
import unittest

from figure_rebuild.pdf_fill import prove_evenodd_nonzero_equivalent


def triangle(x=0):
    return [["M", [x, 0]], ["L", [x+2, 0]], ["L", [x, 2]], ["Z"]]


class PdfFillEmptyTests(unittest.TestCase):
    def test_empty_moveto_at_start_middle_and_end_is_receipted(self):
        cases = [([["M", [100, 100]]]+triangle(), [0]),
                 (triangle()+[["M", [100, 100]]]+triangle(4), [4]),
                 (triangle()+[["M", [100, 100]]], [4])]
        for commands, indices in cases:
            with self.subTest(indices=indices, command_count=len(commands)):
                original = copy.deepcopy(commands)
                proof = prove_evenodd_nonzero_equivalent(commands)
                self.assertIsNotNone(proof)
                receipt = proof["fill_proof_normalization"]
                self.assertEqual([x["moveto_source_command_index"]
                                  for x in receipt["skipped_empty_subpaths"]], indices)
                self.assertTrue(all(x["reason"] == "moveto_subpath_has_no_drawing_commands"
                                    for x in receipt["skipped_empty_subpaths"]))
                self.assertFalse(receipt["source_commands_changed"])
                self.assertEqual(commands, original)

    def test_consecutive_empty_movetos_and_safe_repeated_closes(self):
        commands = [["M", [10, 10]], ["Z"], ["Z"], ["M", [11, 11]],
                    ["M", [12, 12]], ["Z"], ["Z"]]+triangle()
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertIsNotNone(proof)
        receipt = proof["fill_proof_normalization"]
        self.assertEqual([x["source_command_indices"] for x in receipt["skipped_empty_subpaths"]],
                         [[0, 1], [3], [4, 5]])
        self.assertEqual(receipt["ignored_repeated_close_indices"], [2, 6])
        self.assertEqual(proof["contour_count"], 1)

    def test_actual_trailing_moveto_ellipse_keeps_all_source_commands(self):
        commands = [["M", [376.05011219999994, 125.04271800000001]],
                    ["C", [376.5249334, 124.3633946], [377.45207719999996, 124.19415140000001], [378.12233399999997, 124.6622566]],
                    ["C", [378.7929266, 125.1306976], [378.95008099999995, 126.0605278], [378.4752598, 126.740187]],
                    ["C", [378.00043859999994, 127.4181672], [377.07295899999997, 127.58875359999999], [376.40270219999996, 127.12064840000001]],
                    ["C", [375.73244539999996, 126.6508642], [375.575291, 125.721034], [376.05011219999994, 125.04271800000001]],
                    ["Z"], ["M", [376.05011219999994, 125.04271800000001]]]
        before = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["cubic_count"], 4)
        self.assertEqual(proof["fill_proof_normalization"]["skipped_empty_subpaths"][0]
                         ["moveto_source_command_index"], 6)
        self.assertEqual(commands, before)

    def test_point_line_or_constant_cubic_is_not_reclassified_empty(self):
        for degenerate in ([["M", [10, 10]], ["L", [10, 10]], ["Z"]],
                           [["M", [10, 10]], ["C", [10, 10], [10, 10], [10, 10]], ["Z"]],
                           [["M", [10, 10]], ["L", [11, 10]], ["L", [10, 10]], ["Z"]]):
            with self.subTest(degenerate=degenerate):
                commands = triangle()+degenerate
                before = copy.deepcopy(commands)
                self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))
                self.assertEqual(commands, before)

    def test_hole_and_self_intersection_remain_unknown(self):
        outer = [["M", [0, 0]], ["L", [10, 0]], ["L", [10, 10]], ["L", [0, 10]], ["Z"]]
        hole = [["M", [2, 2]], ["L", [3, 2]], ["L", [2, 3]], ["Z"]]
        bowtie = [["M", [0, 0]], ["L", [2, 2]], ["L", [0, 2]], ["L", [2, 0]], ["Z"]]
        for commands in (outer+[["M", [90, 90]]]+hole, bowtie+[["M", [0, 0]]]):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_unknown_and_nonfinite_commands_cannot_hide_in_empty_subpaths(self):
        invalid = [[["M", [float("nan"), 0]]], [["M", [0, float("inf")]]],
                   [["M", [True, 0]]], [["M", [0, 0]], ["Q", [0, 0], [0, 0]]],
                   [["M", [0, 0]], ["L", [float("nan"), 0]]],
                   [["M", [0, 0]], ["Z", "extra"]], [["M"]]]
        for bad in invalid:
            for commands in (bad+triangle(), triangle()+bad):
                self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_post_close_draw_still_starts_at_the_closed_subpath_origin(self):
        commands = [["M", [10, 10]], ["Z"], ["L", [12, 10]], ["L", [10, 12]], ["Z"]]
        original = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["fill_proof_normalization"]["post_close_subpath_starts"],
                         [{"before_source_command_index": 2, "point": [10, 10]}])
        self.assertEqual(commands, original)

    def test_empty_only_path_is_not_claimed_a_drawable_success(self):
        for commands in ([["M", [0, 0]]], [["M", [0, 0]], ["Z"], ["M", [1, 1]]]):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_source_stroke_and_output_commands_are_not_normalized(self):
        from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30">'
               '<path d="M5 5L15 5L5 15ZM20 20Z" fill="#123456" '
               'fill-rule="evenodd" stroke="#000000" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        document = extract_outlined_svg(svg)
        before = copy.deepcopy(document.paints[0].commands)
        result = outline_paths(document, glyph_mode="outline")
        self.assertEqual(document.paints[0].commands, before)
        self.assertEqual(len(result.objects[0]["commands"]), len(before))
        self.assertEqual(sum("moveTo" in c for c in result.objects[0]["commands"]), 2)
        self.assertEqual(result.objects[0]["style"]["stroke_linecap"], "round")
        self.assertEqual(result.objects[0]["style"]["stroke_linejoin"], "round")
        self.assertEqual(result.objects[0]["style"]["stroke_width"], 2)

    def test_empty_subpaths_do_not_bypass_the_existing_input_budget(self):
        self.assertIsNone(prove_evenodd_nonzero_equivalent([["M", [i, i]] for i in range(17)]+triangle()))


if __name__ == "__main__":
    unittest.main()
