"""Fill-only normalization must preserve exact path topology and source data."""
import copy
import math
import unittest

from figure_rebuild.pdf_fill import prove_evenodd_nonzero_equivalent


def triangle(x=0):
    return [("M", (x, 0)), ("L", (x+2, 0)), ("L", (x, 2))]


class PdfFillImplicitClosureTests(unittest.TestCase):
    def test_open_polygon_has_proof_only_implicit_closure_receipt(self):
        commands = triangle()
        before = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        normalization = proof["fill_proof_normalization"]
        self.assertEqual(normalization["implicit_closures"], [
            {"reason": "end_of_path", "after_source_command_index": 2,
             "from": [0, 2], "to": [0, 0]}])
        self.assertTrue(normalization["proof_only"])
        self.assertFalse(normalization["source_commands_changed"])
        self.assertEqual(commands, before)
        self.assertNotIn(("Z",), commands)

    def test_multiple_movetos_close_each_fill_subpath(self):
        commands = triangle()+triangle(4)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["contour_count"], 2)
        closures = proof["fill_proof_normalization"]["implicit_closures"]
        self.assertEqual([c["reason"] for c in closures], ["next_moveto", "end_of_path"])
        self.assertEqual([c["after_source_command_index"] for c in closures], [2, 5])

    def test_duplicate_straight_vertices_record_original_indices(self):
        commands = [("M", (0, 0)), ("L", (0, 0)), ("L", (2, 0)),
                    ("L", (2.0, -0.0)), ("L", (0, 2)), ("L", (0, 2))]
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["fill_proof_normalization"]["skipped_zero_length_line_indices"],
                         [1, 3, 5])

    def test_repeated_close_is_no_additional_fill_contour(self):
        commands = triangle()+[("Z",), ("Z",), ("Z",)]
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["contour_count"], 1)
        normalization = proof["fill_proof_normalization"]
        self.assertEqual(normalization["ignored_repeated_close_indices"], [4, 5])
        self.assertEqual(normalization["implicit_closures"], [])

    def test_repeated_close_then_moveto_keeps_subpaths_separate(self):
        commands = triangle()+[("Z",), ("Z",)]+triangle(4)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["contour_count"], 2)
        self.assertEqual(proof["fill_proof_normalization"]["ignored_repeated_close_indices"], [4])

    def test_post_close_line_restarts_at_initial_point_and_touch_remains_unknown(self):
        # Treating this L as continuing from (0, 2) would incorrectly separate
        # the contours. SVG Z puts the cursor at (0, 0), a shared boundary.
        commands = triangle()+[("Z",), ("Z",), ("L", (-2, 0)), ("L", (-2, -2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_post_close_cubic_and_initial_close_fail_closed(self):
        for commands in ([("Z",)]+triangle(),
                         triangle()+[("Z",), ("C", (0, -1), (-1, -1), (-1, 0))]):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_closing_edge_self_crossing_is_not_omitted(self):
        # Explicit edges do not cross; only the implicit last-to-first edge
        # crosses the interior vertical edge at (2, 1).
        commands = [("M", (0, 0)), ("L", (2, 0)), ("L", (2, 2)), ("L", (4, 2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_implicit_nested_contours_still_rejected(self):
        outer = [("M", (0, 0)), ("L", (8, 0)), ("L", (8, 8)), ("L", (0, 8))]
        inner = [("M", (2, 2)), ("L", (3, 2)), ("L", (2, 3))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(outer+inner))

    def test_zero_area_and_empty_subpaths_remain_unknown(self):
        for commands in ([("M", (0, 0))], [("M", (0, 0)), ("Z",), ("Z",)],
                         [("M", (0, 0)), ("L", (0, 0)), ("L", (0, 0))],
                         [("M", (0, 0)), ("L", (2, 0)), ("L", (4, 0))],
                         [("M", (0, 0))]+triangle(4)):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_duplicate_does_not_erase_backtracking(self):
        commands = [("M", (0, 0)), ("L", (4, 0)), ("L", (4, 0)),
                    ("L", (2, 0)), ("L", (0, 2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_almost_duplicate_not_treated_as_duplicate(self):
        x = math.nextafter(2.0, 0.0)
        commands = [("M", (0, 0)), ("L", (2, 0)), ("L", (x, 0)), ("L", (0, 2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_nonadjacent_repeated_vertex_still_rejected(self):
        commands = triangle()+[("L", (0, 0)), ("L", (-2, 0)), ("L", (-2, 2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_implicit_curve_with_duplicate_leading_line_preserves_source(self):
        commands = [["M", [0, 0]], ["L", [0, 0]], ["C", [1, 0], [1, 1], [0, 1]]]
        before = copy.deepcopy(commands)
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["cubic_count"], 1)
        self.assertFalse(proof["curve_approximation"])
        self.assertEqual(proof["fill_proof_normalization"]["skipped_zero_length_line_indices"], [1])
        self.assertEqual(commands, before)

    def test_implicit_cubic_triangle_uses_unchanged_exact_proof(self):
        commands = [("M", (0, 0)), ("L", (0, 0)),
                    ("C", (2, 0), (1, 0), (2, 1)), ("L", (0, 1))]
        proof = prove_evenodd_nonzero_equivalent(commands)
        self.assertEqual(proof["proof"],
                         "single_cubic_two_lines_strict_halfplanes_and_monotone_projection")

    def test_zero_length_cubic_is_not_erased(self):
        commands = triangle()+[("C", (0, 2), (0, 2), (0, 2))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_implicit_curved_self_loop_is_not_assumed_simple(self):
        commands = [("M", (0, 0)), ("C", (2, 2), (-2, 2), (0, 0))]
        self.assertIsNone(prove_evenodd_nonzero_equivalent(commands))

    def test_input_and_subpath_resource_bounds_survive_normalization(self):
        self.assertIsNone(prove_evenodd_nonzero_equivalent(
            triangle()+[("Z",)]*8190))
        self.assertIsNone(prove_evenodd_nonzero_equivalent(
            [command for x in range(17) for command in triangle(x*4)]))

    def test_invalid_or_nonfinite_skipped_candidate_fails_closed(self):
        for command in (("L", (float("nan"), 0)), ("L", (False, 0)),
                        ("Z", 1), ("C", (0, 0), (0, 0)), ("Q", (0, 0))):
            self.assertIsNone(prove_evenodd_nonzero_equivalent(triangle()+[command]))


if __name__ == "__main__":
    unittest.main()
