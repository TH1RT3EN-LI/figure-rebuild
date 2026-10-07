"""Exact whole-cubic fill normalization under a strict control-hull domain.

Curves are used as chords only in a certified topology computation. Output
retains their original complete controls; it never flattens or cuts a curve.
This module does not modify XML, paint, source commands or caller receipts.
"""
from collections import Counter
from fractions import Fraction as F
from functools import cmp_to_key
import math

from . import pdf_winding as _line

UnsupportedPdfCubicWindingError = _line.UnsupportedPdfWindingError


class _Arithmetic(_line._Budget):
    """One shared counter and checked rational arithmetic across every phase."""
    def add(self, a, b):
        self.spend()
        return _line._checked(a + b)

    def sub(self, a, b):
        self.spend()
        return _line._checked(a - b)

    def mul(self, a, b):
        self.spend()
        return _line._checked(a * b)

    def div(self, a, b):
        self.spend()
        if not b:
            _fail('degenerate', 'Exact cubic proof encountered a zero divisor')
        return _line._checked(a / b)

    def compare(self, a, b):
        self.spend()
        return (a > b) - (a < b)

    def point_compare(self, a, b):
        return self.compare(a[0], b[0]) or self.compare(a[1], b[1])

    def text(self, value):
        self.spend()
        return _line._fraction_text(value)

    def vector(self, a, b):
        return self.sub(a[0], b[0]), self.sub(a[1], b[1])

    def cross(self, a, b):
        return self.sub(self.mul(a[0], b[1]), self.mul(a[1], b[0]))

    def dot(self, a, b):
        return self.add(self.mul(a[0], b[0]), self.mul(a[1], b[1]))

    def orient(self, a, b, c):
        return self.cross(self.vector(b, a), self.vector(c, a))

    def serialize(self, value):
        # Only internally constructed records enter this bounded traversal.
        self.spend()
        if isinstance(value, F):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.serialize(item) for item in value]
        return value


def _fail(code, message, **details):
    raise UnsupportedPdfCubicWindingError(code, message, **details)


def _limits(max_input_segments, max_commands, max_atomic_edges,
            max_operations, max_probe_halvings):
    values = [max_input_segments, max_commands, max_atomic_edges,
              max_operations, max_probe_halvings]
    if any(type(v) is not int or v <= 0 for v in values):
        raise ValueError('Cubic proof budgets must be positive integers')
    return {'segments': max_input_segments, 'commands': max_commands,
            'atoms': max_atomic_edges, 'operations': max_operations,
            'halvings': max_probe_halvings}


def _number(value, budget, integer=False):
    budget.spend()
    if type(value) not in (int, float, F):
        _fail('input', 'Cubic coordinates require finite int, float or Fraction values')
    if isinstance(value, float):
        if integer or not math.isfinite(value):
            _fail('input', 'Native coordinates require exact integers; other coordinates must be finite')
    if isinstance(value, int) and value.bit_length() > 4096:
        _fail('budget', 'Cubic input integer bit budget exceeded')
    if isinstance(value, F):
        if max(value.numerator.bit_length(), value.denominator.bit_length()) > 4096:
            _fail('budget', 'Cubic input fraction bit budget exceeded')
        if integer and value.denominator != 1:
            _fail('native_integer', 'Native coordinate is not integral')
    result = F(value)
    if integer and result.denominator != 1:
        _fail('native_integer', 'Native coordinate is not integral')
    return result


def _point(value, budget, integer=False):
    if type(value) not in (list, tuple) or len(value) != 2:
        _fail('input', 'Cubic coordinates require a pair')
    return tuple(_number(v, budget, integer) for v in value)


def _hull(points, budget):
    # A segment has only two or four controls, but each comparison and exact
    # orientation is charged as well as guarded against fraction growth.
    budget.spend(len(points))
    points = sorted(set(points), key=cmp_to_key(budget.point_compare))
    if len(points) <= 2:
        return points
    lower, upper = [], []
    for row, ordered in ((lower, points), (upper, reversed(points))):
        for point in ordered:
            budget.spend()
            while len(row) >= 2 and budget.orient(row[-2], row[-1], point) <= 0:
                budget.spend()
                row.pop()
            row.append(point)
    return lower[:-1] + upper[:-1]


def _parse(commands, limits, budget, integer=False):
    if type(commands) not in (list, tuple) or len(commands) > limits['commands']:
        _fail('budget', 'Cubic command budget exceeded')
    segments, contours, row, skipped, canonical = [], [], [], [], []
    start = cursor = None

    def append(kind, points, index):
        budget.spend()
        if kind == 'L' and points[0] == points[-1]:
            skipped.append(index)
            return
        if len(segments) >= limits['segments']:
            _fail('budget', 'Cubic segment budget exceeded')
        item = {'id': f's{len(contours)}:cmd{index}:{kind}', 'kind': kind,
                'command_index': index, 'subpath': len(contours),
                'points': points, 'hull': _hull(points, budget)}
        segments.append(item)
        row.append(item)

    for index, command in enumerate(commands):
        budget.spend()
        if type(command) not in (list, tuple) or not command:
            _fail('input', 'Invalid cubic path command')
        op = command[0]
        if type(op) is not str:
            _fail('input', 'Cubic command opcode must be a string')
        if op == 'M' and len(command) == 2:
            if start is not None:
                _fail('unclosed', 'Whole-cubic proof requires explicit subpath closure')
            start = cursor = _point(command[1], budget, integer)
            canonical.append(('M', start))
        elif op in ('L', 'C') and len(command) == (2 if op == 'L' else 4):
            if cursor is None:
                _fail('input', 'Cubic segment requires an active move')
            points = [cursor] + [_point(p, budget, integer) for p in command[1:]]
            append(op, points, index)
            cursor = points[-1]
            canonical.append((op, *points[1:]))
        elif op == 'Z' and len(command) == 1:
            if start is None:
                _fail('input', 'Close requires an active move')
            append('L', [cursor, start], index)
            if row:
                contours.append(row)
            row = []
            start = cursor = None
            canonical.append(('Z',))
        else:
            _fail('unsupported_command', 'Whole-cubic proof supports explicit-closed M/L/C/Z only')
    if start is not None:
        _fail('unclosed', 'Whole-cubic proof requires explicit subpath closure')
    return segments, contours, skipped, canonical


def _monotone(points, budget):
    differences = [budget.vector(b, a) for a, b in zip(points, points[1:])]
    axes = [(F(1), F(0)), (F(-1), F(0)), (F(0), F(1)), (F(0), F(-1)),
            budget.vector(points[-1], points[0])]
    for axis in axes:
        coefficients = [budget.mul(F(3), budget.dot(axis, v)) for v in differences]
        budget.spend(len(coefficients))
        if min(coefficients) >= 0 and max(coefficients) > 0:
            return {'projection': axis, 'derivative_bernstein_coefficients': coefficients}
    return None


def _separation(first, second, shared, budget):
    axes = [(F(1), F(0)), (F(0), F(1))]
    for hull in (first, second):
        for a, b in zip(hull, hull[1:] + hull[:1]):
            budget.spend()
            if a != b:
                d = budget.vector(b, a)
                axes.append((-d[1], d[0]))
    for axis in axes:
        projections = [[budget.dot(axis, p) for p in hull] for hull in (first, second)]
        for hi, hj in ((0, 1), (1, 0)):
            hulls = (first, second)
            a, b = projections[hi], projections[hj]
            budget.spend(len(a) + len(b))
            edge, other_edge = max(a), min(b)
            if budget.compare(edge, other_edge) < 0:
                return {'status': 'strict_separation', 'axis': axis,
                        'first_hull': hi, 'maximum': edge, 'minimum_other': other_edge}
            if shared is not None and budget.compare(edge, other_edge) == 0:
                budget.spend(len(a) + len(b))
                face_a = [p for p, value in zip(hulls[hi], a) if value == edge]
                face_b = [p for p, value in zip(hulls[hj], b) if value == edge]
                if ((face_a == [shared] and shared in face_b) or
                        (face_b == [shared] and shared in face_a)):
                    return {'status': 'adjacent_single_endpoint', 'axis': axis,
                            'support_value': edge, 'shared_endpoint': shared,
                            'singleton_hull': hi if face_a == [shared] else hj}
    return None


def _isolation(segments, contours, budget):
    adjacent = {}
    for row in contours:
        for a, b in zip(row, row[1:] + row[:1]):
            budget.spend()
            adjacent.setdefault(frozenset((a['id'], b['id'])), set()).add(a['points'][-1])
    curves = []
    for seg in segments:
        budget.spend()
        if seg['kind'] == 'C':
            projection = _monotone(seg['points'], budget)
            if projection is None:
                _fail('curve_injectivity', 'Whole C lacks a proved strictly monotone projection', segment=seg['id'])
            curves.append({'source_id': seg['id'], 'points': seg['points'], 'injectivity': projection})
    tests, blockers = [], []
    for i, a in enumerate(segments):
        for b in segments[i + 1:]:
            budget.spend()
            if a['kind'] == b['kind'] == 'L':
                continue
            shared = adjacent.get(frozenset((a['id'], b['id'])), set())
            point = next(iter(shared)) if len(shared) == 1 else None
            proof = _separation(a['hull'], b['hull'], point, budget)
            if proof is None:
                blockers.append([a['id'], b['id']])
            else:
                tests.append({'pair': [a['id'], b['id']], **proof})
    if blockers:
        _fail('curve_hull_isolation', 'Original whole-C control hulls are not isolated',
              blocking_pairs=blockers, blocker_count=len(blockers))
    return curves, tests


def _arrangement(source, limits, budget):
    segments = [(s['points'][0], s['points'][-1]) for s in source]
    splits = [{a, b} for a, b in segments]
    intersection_origins = {}
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            points = _line._intersections(a, b, *segments[j], budget)
            budget.spend(len(points))
            splits[i].update(points)
            splits[j].update(points)
            if source[i]['kind'] == source[j]['kind'] == 'L':
                for point in points:
                    budget.spend()
                    intersection_origins.setdefault(point, []).append((source[i]['id'], source[j]['id']))
    multiplicities, curve_edges = {}, {}
    for src, (a, b), points in zip(source, segments, splits):
        budget.spend()
        if src['kind'] == 'C':
            if points != {a, b}:
                _fail('split_curve_chord', 'A whole C chord cannot be split', segment=src['id'])
            edge = tuple(sorted((a, b), key=cmp_to_key(budget.point_compare)))
            if edge in curve_edges:
                _fail('curve_identity', 'Two source C segments have the same chord')
            curve_edges[edge] = src
        axis = 0 if a[0] != b[0] else 1
        denominator = budget.sub(b[axis], a[axis])
        parameters = [(budget.div(budget.sub(p[axis], a[axis]), denominator), p) for p in points]
        parameters.sort(key=cmp_to_key(lambda x, y: budget.compare(x[0], y[0])))
        ordered = [p for _, p in parameters]
        for start, end in zip(ordered, ordered[1:]):
            budget.spend()
            if start == end:
                continue
            edge = tuple(sorted((start, end), key=cmp_to_key(budget.point_compare)))
            multiplicities[edge] = multiplicities.get(edge, 0) + (1 if edge == (start, end) else -1)
            if len(multiplicities) > limits['atoms']:
                _fail('budget', 'Cubic arrangement atomic edge budget exceeded')
    atoms = list(multiplicities)
    boundary, classifications = [], []
    for edge, count in multiplicities.items():
        budget.spend()
        if not count:
            continue
        (left, right), halvings = _line._side_samples(edge, atoms, budget, limits['halvings'])
        wl, wr = _line._winding(segments, left, budget), _line._winding(segments, right, budget)
        if wl - wr != count:
            _fail('winding_invariant', 'Atomic winding jump differs from source multiplicity')
        kept = bool(wl) != bool(wr)
        classifications.append({'edge': edge, 'left_winding': wl, 'right_winding': wr,
                                'boundary': kept, 'side_probe_halvings': halvings})
        if kept:
            boundary.append(edge if wl else tuple(reversed(edge)))
    outgoing, incoming = {}, {}
    for a, b in boundary:
        budget.spend()
        if a in outgoing or b in incoming:
            _fail('touching_boundary', 'Filled boundary branches or touches')
        outgoing[a], incoming[b] = b, a
    if set(outgoing) != set(incoming):
        _fail('open_boundary', 'Filled boundary is not closed')
    remaining, rings = set(outgoing), []
    while remaining:
        budget.spend(len(remaining))
        start = min(remaining, key=cmp_to_key(budget.point_compare))
        current, row = start, []
        while current in remaining:
            budget.spend()
            remaining.remove(current)
            row.append(current)
            current = outgoing[current]
        if current != start:
            _fail('open_boundary', 'Filled boundary fails to return to its start')
        rings.append(row)  # NEVER simplify: retain every protected C endpoint.
    topology = _line._validate_rings(rings, budget)
    return rings, curve_edges, classifications, intersection_origins, topology, len(atoms)


def _normalize(commands, limits, budget, integer=False):
    source, contours, skipped, canonical = _parse(commands, limits, budget, integer)
    curves, tests = _isolation(source, contours, budget)
    rings, curve_edges, classifications, intersections, topology, atom_count = _arrangement(source, limits, budget)
    originals, protected = set(), set()
    for segment in source:
        budget.spend()
        endpoints = [segment['points'][0], segment['points'][-1]]
        originals.update(endpoints)
        if segment['kind'] == 'C':
            protected.update(endpoints)
    result, identities, origins, used = [], [], [], set()

    def origin(point, index):
        budget.spend()
        old = point in originals
        if not old and point not in intersections:
            _fail('vertex_identity', 'New vertex has no original L/L intersection identity')
        origins.append({'output_command_index': index, 'point': point,
                        'origin': 'original_vertex' if old else 'new_L_L_intersection',
                        'protected_C_endpoint': point in protected,
                        'source_line_pairs': [] if old else intersections[point]})

    for ri, ring in enumerate(rings):
        result.append(('M', ring[0]))
        origin(ring[0], len(result) - 1)
        for a, b in zip(ring, ring[1:] + ring[:1]):
            budget.spend()
            edge = tuple(sorted((a, b), key=cmp_to_key(budget.point_compare)))
            src = curve_edges.get(edge)
            if src:
                forward = src['points'][0] == a
                controls = src['points'] if forward else list(reversed(src['points']))
                if controls[0] != a or controls[-1] != b or src['id'] in used:
                    _fail('curve_identity', 'C endpoint, direction or source identity is ambiguous')
                result.append(('C', *controls[1:]))
                used.add(src['id'])
                identities.append({'source_id': src['id'], 'decision': 'restored_whole',
                                   'output_ring': ri, 'output_command_index': len(result) - 1,
                                   'direction': 'forward' if forward else 'reverse',
                                   'original_controls': src['points'], 'output_controls': controls})
            else:
                result.append(('L', b))
            origin(b, len(result) - 1)
        result.append(('Z',))
    classified = {tuple(c['edge']): c for c in classifications}
    for src in source:
        budget.spend()
        if src['kind'] == 'C' and src['id'] not in used:
            edge = tuple(sorted((src['points'][0], src['points'][-1]), key=cmp_to_key(budget.point_compare)))
            proof = classified.get(edge)
            if proof is None or proof['boundary']:
                _fail('curve_identity', 'Absent C has no proved nonboundary classification')
            identities.append({'source_id': src['id'], 'decision': 'discarded_nonboundary',
                               'original_controls': src['points'], 'classification': proof})
    if len(identities) != len(curves):
        _fail('curve_identity', 'Incomplete C accounting')
    proof = {'method': 'exact_isolated_whole_cubic_chord_arrangement_and_restoration',
             'coordinate_domain': 'exact_rational_no_float_export', 'source_fill_rule': 'nonzero',
             'output_fill_rule': 'evenodd', 'compound_paint_count': 1, 'fill_only': True,
             'original_stroke_must_be_preserved_separately': True, 'source_commands_modified': False,
             'source_segment_count': len(source), 'source_curve_count': len(curves),
             'source_curve_projection_proofs': curves, 'hull_pair_tests': tests,
             'hull_pair_status_counts': dict(Counter(t['status'] for t in tests)),
             'atomic_edge_count': atom_count, 'boundary_classification': classifications,
             'output_ring_count': len(rings), 'empty_fill': not rings,
             'chord_loop_topology': topology, 'simplify_used': False,
             'zero_length_L_ignored_for_proof': skipped,
             'native_integer_encoding_verified': False, 'rgb_alpha_error_bound': None,
             'input_fraction_bit_limit': 4096, 'intermediate_fraction_bit_limit': _line._INTERMEDIATE_BITS}
    return result, identities, origins, proof, rings, canonical


def normalize_isolated_cubic_fill(commands, *, max_input_segments=128, max_commands=512,
                                  max_atomic_edges=2048, max_operations=1_000_000,
                                  max_probe_halvings=80):
    """Normalize a nonzero compound fill, retaining whole isolated original C.

    Commands are explicit-closed M/L/C/Z tuples. Coordinates may be finite
    int/float/Fraction; floats mean their exact binary64 values, not decimals.
    Returned command coordinates are Fractions, with NO coordinate conversion.
    Identity/origin/proof receipts encode rational values as strings. Styles and
    strokes are outside this fill-only API. Unsupported input raises explicitly.
    """
    limits = _limits(max_input_segments, max_commands, max_atomic_edges,
                     max_operations, max_probe_halvings)
    budget = _Arithmetic(max_operations)
    return _normalize_isolated_with_budget(commands, limits, budget)


def _normalize_isolated_with_budget(commands, limits, budget):
    return _normalization_result(commands, limits, budget, _normalize)


def _normalization_result(commands, limits, budget, normalize_geometry):
    """Private shared-counter entry; the provider is selected by trusted code."""
    started = budget.used
    output, identities, origins, proof, _, _ = normalize_geometry(commands, limits, budget)
    result = {'commands': output, 'curve_identities': budget.serialize(identities),
              'vertex_origins': budget.serialize(origins), 'proof': budget.serialize(proof)}
    result['proof']['exact_predicate_operations'] = budget.used - started
    return result


def _chord_rings(commands, limits, budget, integer=False):
    segments, contours, _, canonical = _parse(commands, limits, budget, integer)
    curves, tests = _isolation(segments, contours, budget)
    rings = [[seg['points'][0] for seg in row] for row in contours]
    topology = _line._validate_rings(rings, budget)
    return rings, topology, curves, tests, canonical


def verify_isolated_cubic_integer_encoding(source_native_commands, candidate_integer_commands, *,
                                            max_coordinate_error=F(1, 2),
                                            max_input_segments=128, max_commands=512,
                                            max_atomic_edges=2048, max_operations=1_000_000,
                                            max_probe_halvings=80):
    """Reprove an independently decoded final integer encoding from its source.

    Source and candidate must contain exact integers (int or integral Fraction).
    This function recomputes normalization; it never trusts a supplied proof or
    identity receipt. C controls/endpoints and surviving original vertices must
    be identical; only NEW L/L intersections may move, at most half a local grid
    unit by default. A scalar or two per-axis smaller bounds are accepted. The
    caller must decode actual final XML and separately bind unchanged paint,
    frame/path dimensions and slide-EMU scaling. No source-to-initial-native
    quantization or RGB bound is inferred from this additional-encoding proof.
    """
    limits = _limits(max_input_segments, max_commands, max_atomic_edges,
                     max_operations, max_probe_halvings)
    budget = _Arithmetic(max_operations)
    return _verify_integer_encoding(
        source_native_commands, candidate_integer_commands, limits, budget,
        max_coordinate_error=max_coordinate_error,
        normalize_geometry=_normalize, candidate_geometry=_chord_rings)


def _verify_integer_encoding(source_native_commands, candidate_integer_commands,
                             limits, budget, *, max_coordinate_error,
                             normalize_geometry, candidate_geometry,
                             proof_metadata=None):
    """One integer engine for privately selected, independently reproved domains.

    Providers use the supplied counter; no caller result/receipt is accepted.
    Returned operation usage is a phase delta, while the counter remains shared.
    """
    started = budget.used
    if type(max_coordinate_error) in (list, tuple):
        if len(max_coordinate_error) != 2:
            raise ValueError('Cubic encoding error needs a scalar or two axis bounds')
        errors = [_number(v, budget) for v in max_coordinate_error]
    else:
        errors = [_number(max_coordinate_error, budget)] * 2
    if any(v < 0 or v > F(1, 2) for v in errors):
        raise ValueError('Cubic encoding permits at most one half local grid unit per axis')
    exact, identities, origins, proof, reference_rings, _ = normalize_geometry(
        source_native_commands, limits, budget, integer=True)
    # Parsing first checks integers/command budgets before any candidate walk.
    output_limits = {**limits, 'segments': limits['atoms'], 'commands': limits['atoms'] * 3 + 16}
    _, _, _, candidate = _parse(candidate_integer_commands, output_limits, budget, integer=True)
    if len(exact) != len(candidate):
        _fail('encoding_structure', 'Integer encoding changed command count')
    budget.spend(len(origins) + len(identities))
    origin_by_index = {row['output_command_index']: row for row in origins}
    curve_start_by_index = {row['output_command_index']: row['output_controls'][0]
                            for row in identities if row['decision'] == 'restored_whole'}
    maximum, encoding = [F(0), F(0)], {}
    cursor = None
    for index, (before, after) in enumerate(zip(exact, candidate)):
        budget.spend()
        if before[0] != after[0] or len(before) != len(after):
            _fail('encoding_structure', 'Integer encoding changed command kind or control count')
        if before[0] == 'Z':
            cursor = None
            continue
        if before[0] == 'C':
            if before[1:] != after[1:] or cursor != curve_start_by_index[index]:
                _fail('curve_identity', 'Integer encoding moved or reversed a protected C control/endpoint')
        info = origin_by_index[index]
        point, encoded = before[-1], after[-1]
        delta = [abs(budget.sub(point[i], encoded[i])) for i in (0, 1)]
        if any(delta) and (info['origin'] != 'new_L_L_intersection' or info['protected_C_endpoint']):
            _fail('vertex_identity', 'Integer encoding moved an original or protected endpoint')
        if any(delta[i] > errors[i] for i in (0, 1)):
            _fail('encoding_error', 'Integer L/L intersection displacement exceeds the declared bound')
        if point in encoding and encoding[point] != encoded:
            _fail('encoding_structure', 'The same exact endpoint received different integer encodings')
        encoding[point] = encoded
        maximum = [max(maximum[i], delta[i]) for i in (0, 1)]
        cursor = encoded
    candidate_rings, topology, curves, tests, _ = candidate_geometry(
        candidate, output_limits, budget, integer=True)
    if [len(r) for r in reference_rings] != [len(r) for r in candidate_rings] or topology != proof['chord_loop_topology']:
        _fail('encoding_topology', 'Integer encoding changed loop orientation, containment or vertices')
    receipt = {'method': 'recomputed_exact_native_whole_cubic_and_integer_encoding_proof',
               'source_native_nonzero_geometry_recomputed': True,
               'candidate_must_be_decoded_from_actual_final_xml': True,
               'C_controls_and_protected_endpoints_identical': True,
               'original_integer_vertices_identical': True,
               'only_new_L_L_intersections_may_move': True,
               'maximum_new_coordinate_error_local_units': maximum,
               'declared_coordinate_error_local_units': errors,
               'final_chord_topology': topology, 'final_curve_projection_proofs': curves,
               'final_hull_pair_tests': tests, 'curve_identities': identities,
               'vertex_origins': origins, 'compound_paint_count': 1,
               'exact_pointwise_fill_equivalence_to_initial_native': not any(maximum),
               'existing_source_to_initial_native_error_included': False,
               'frame_paint_and_slide_scaling_verified': False, 'rgb_alpha_error_bound': None}
    if proof_metadata is not None:
        receipt.update(proof_metadata)
    receipt = budget.serialize(receipt)
    receipt['exact_predicate_operations'] = budget.used - started
    return receipt
