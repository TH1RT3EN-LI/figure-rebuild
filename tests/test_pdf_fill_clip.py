import copy

import json

import math

from pathlib import Path

import unittest

from figure_rebuild.pdf_fill_clip import clip_nonzero_annular_fill

def polygon(points, reverse=False):
    points = list(reversed(points)) if reverse else points
    return [['M', list(points[0])], *[['L', list(p)] for p in points[1:]], ['Z']]

def rectangle(low, high, reverse=False):
    return polygon([(low, low), (high, low), (high, high), (low, high)], reverse)

def strips(reverse=False):
    quarters = [((0, 0), (30, 0), (20, 10), (10, 10)), ((30, 0), (30, 30), (20, 20), (20, 10)), ((30, 30), (0, 30), (10, 20), (20, 20)), ((0, 30), (0, 0), (10, 10), (10, 20))]
    return [c for points in quarters for c in polygon(points, reverse)]

def call(source, clip=None, **kwargs):
    options = {'source_fill_rule': 'nonzero', 'clip_fill_rule': 'nonzero', **kwargs}
    return clip_nonzero_annular_fill(source, rectangle(5, 25) if clip is None else clip, **options)


def reverse_subpaths(commands):
    """Test-only exact edge reversal, including implicit closing lines."""
    paths, current, start, cursor = [], [], None, None

    def finish():
        if not current:
            return
        if cursor != start:
            current.append(('L', cursor, start))
        reversed_path = [['M', list(start)]]
        for edge in reversed(current):
            reversed_path.append([edge[0], *[list(p) for p in reversed(edge[1:-1])]])
        paths.extend(reversed_path+[['Z']])

    for c in commands:
        if c[0] == 'M':
            finish()
            current, cursor, start = [], c[1], c[1]
        elif c[0] in ('L', 'C'):
            current.append((c[0], cursor, *c[1:]))
            cursor = c[-1]
        elif c[0] == 'Z':
            if cursor != start:
                current.append(('L', cursor, start))
            cursor = start
    finish()
    return paths

def raster(commands, clip=None, scale=8):
    try:
        import pymupdf as fitz
    except ImportError:
        raise unittest.SkipTest('Optional PyMuPDF source dependency unavailable')
    doc = fitz.open()
    page = doc.new_page(width=32, height=32)

    def path(values):
        return '\n'.join((' '.join((format(n, '.17g') for p in c[1:] for n in p)) + ' ' + {'M': 'm', 'L': 'l', 'C': 'c', 'Z': 'h'}[c[0]] for c in values))
    parts = ['q', '1 0 0 -1 0 32 cm']
    if clip is not None:
        parts.extend([path(clip), 'W n'])
    parts.extend(['0 0 0 rg', path(commands), 'f', 'Q'])
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, '\n'.join(parts).encode())
    page.set_contents(xref)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    return pix


class PdfFillClipTests(unittest.TestCase):
    def test_curved_source_and_clip_orientation_combinations(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/sam2-fill-ring.json').read_text())
        translate = lambda cmds: [[c[0], *[[p[0]-350, p[1]-220] for p in c[1:]]]
                                  for c in cmds]
        original_source, original_clip = map(translate, (fixture['commands'], fixture['clip_commands']))
        for source_reverse in (False, True):
            for clip_reverse in (False, True):
                with self.subTest(source_reverse=source_reverse, clip_reverse=clip_reverse):
                    source = reverse_subpaths(original_source) if source_reverse else original_source
                    clip = reverse_subpaths(original_clip) if clip_reverse else original_clip
                    result = call(source, clip)
                    self.assertIsNotNone(result)
                    expected, actual = raster(source, clip), raster(result['commands'])
                    self.assertEqual(expected.samples, actual.samples)
                    self.assertEqual(actual.pixel(17*8, 17*8)[3], 0)

    def test_strip_annulus_both_source_and_clip_orientations(self):
        for source_reverse in [False, True]:
            for clip_reverse in [False, True]:
                with self.subTest(source_reverse=source_reverse, clip_reverse=clip_reverse):
                    source, clip = (strips(source_reverse), rectangle(5, 25, clip_reverse))
                    before = copy.deepcopy((source, clip))
                    result = call(source, clip)
                    assert result is not None
                    assert (source, clip) == before
                    assert len(result['proof']['shared_line_cancellations']) == 4
                    assert result['proof']['source_commands_changed'] is True
                    assert result['proof']['source_input_mutated'] is False
                    assert result['proof']['original_boundary_coordinates_changed'] is False
                    assert result['proof']['curve_approximation'] is False
                    native, derived = (raster(source, clip), raster(result['commands']))
                    assert native.samples == derived.samples
                    assert derived.pixel(15 * 8, 15 * 8)[3] == 0
                    assert derived.pixel(7 * 8, 15 * 8)[3] == 255
                    assert derived.pixel(2 * 8, 15 * 8)[3] == 0

    def test_direct_two_contour_annulus_needs_no_edge_cancellation(self):
        result = call(rectangle(0, 30) + rectangle(10, 20, True))
        assert result is not None
        assert result['proof']['shared_line_cancellations'] == []

    def test_actual_sam_source_preserves_cubics_and_explicit_precision_scope(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/sam2-fill-ring.json').read_text())
        source, clip = (fixture['commands'], fixture['clip_commands'])
        before = copy.deepcopy((source, clip))
        result = call(source, clip)
        assert result is not None
        assert (source, clip) == before
        assert len(result['proof']['shared_line_cancellations']) == 4
        assert sum((c[0] == 'C' for c in result['commands'])) == 8
        assert sum((c[0] == 'M' for c in result['commands'])) == 2
        original_points = {tuple(p) for c in source + clip for p in c[1:]}
        assert all((tuple(p) in original_points for c in result['commands'] for p in c[1:]))
        assert 'upstream_PDF' in result['proof']['precision_scope']
        assert result['proof']['proof_subdivisions_in_output'] is False
        assert call(source, clip, max_depth=0) is None
        assert call(source, clip, max_subdivisions=1) is None

    def test_overlapping_touching_equal_and_outside_clip_reject(self):
        for clip in [rectangle(15, 25), rectangle(10, 25), rectangle(0, 30), rectangle(31, 35)]:
            with self.subTest(clip=clip):
                assert call(strips(), clip) is None

    def test_single_ulp_gap_is_distinguished_from_contact_and_overlap(self):
        source = rectangle(0, 30) + rectangle(10, 20, True)
        gap = rectangle(math.nextafter(10, -math.inf), math.nextafter(20, math.inf))
        assert call(source, gap) is not None
        assert call(source, rectangle(10, 20)) is None
        overlap = rectangle(math.nextafter(10, math.inf), math.nextafter(20, -math.inf))
        assert call(source, overlap) is None

    def test_same_orientation_fills_hole_and_must_not_be_reinterpreted(self):
        source = rectangle(0, 30) + rectangle(10, 20)
        assert raster(source).pixel(15 * 8, 15 * 8)[3] == 255
        assert call(source) is None

    def test_edge_multiplicity_and_branch_ambiguity_reject(self):
        assert call(strips() + strips()) is None
        source = strips()
        source[2][1][0] = math.nextafter(source[2][1][0], math.inf)
        assert call(source) is None

    def test_extra_cycles_and_self_intersection_reject(self):
        assert call(strips() + rectangle(40, 45)) is None
        assert call(strips(), rectangle(5, 25) + rectangle(40, 45)) is None
        bowtie = polygon([(5, 5), (25, 25), (5, 25), (25, 5)])
        assert call(strips(), bowtie) is None

    def test_rules_and_resource_budgets_fail_closed(self):
        for options in [dict(source_fill_rule='evenodd'), dict(clip_fill_rule='evenodd'), dict(source_fill_rule=None), dict(max_subdivisions=0), dict(max_subdivisions=True), dict(max_depth=65), dict(max_depth=-1)]:
            with self.subTest(options=options):
                assert call(strips(), **options) is None

    def test_unsupported_commands_multi_paint_and_nonfinite_values_reject(self):
        for source in [[], [strips(), strips()], [['Q', [0, 1], [2, 3]]], [['M', [0, 0]], ['L', [float('nan'), 1]]], [['M', [0, 0]], ['L', [True, 1]]]]:
            with self.subTest(source=source):
                assert call(source) is None

    def test_implicit_source_and_clip_closures_are_receipted(self):
        source = rectangle(0, 30)[:-1] + rectangle(10, 20, True)[:-1]
        result = call(source, rectangle(5, 25)[:-1])
        assert result is not None
        assert len(result['proof']['source_fill_normalization']['implicit_closures']) == 2
        assert len(result['proof']['clip_fill_normalization']['implicit_closures']) == 1


if __name__ == "__main__":
    unittest.main()
