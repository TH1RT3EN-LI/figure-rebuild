"""Bounded exact polygon fill normalization for native even-odd consumers.

Only the filled region is normalized. Callers must retain the original commands
for strokes. No styles, opacity, source commands, or caller state are modified.
Curves and ambiguous/touching output boundaries fail closed.
"""
from fractions import Fraction
import math

_INTERMEDIATE_BITS = 8192


class UnsupportedPdfWindingError(ValueError):
    """The requested fill could not be represented under the declared budget."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


class _Budget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def spend(self, count=1):
        self.used += count
        if self.used > self.limit:
            raise UnsupportedPdfWindingError('budget', 'Exact polygon operation budget exhausted')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise UnsupportedPdfWindingError('input', 'Polygon coordinates must be finite numbers')
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsupportedPdfWindingError('input', 'Polygon coordinates must be finite numbers')
    result = Fraction(value)
    if max(result.numerator.bit_length(), result.denominator.bit_length()) > 4096:
        raise UnsupportedPdfWindingError('budget', 'Polygon coordinate bit budget exceeded')
    return result


def _checked(value):
    """Bound every retained rational before later arithmetic or receipts.

    Binary rational operations may briefly use up to twice this many bits; no
    unchecked cumulative sum/product or decimal formatting is allowed.
    """
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > _INTERMEDIATE_BITS:
        raise UnsupportedPdfWindingError('budget', 'Intermediate polygon fraction bit budget exceeded')
    return value


def _fraction_text(value):
    """Respect a host's stricter integer-to-decimal conversion limit."""
    _checked(value)
    try:
        return str(value)
    except ValueError as error:
        raise UnsupportedPdfWindingError('budget', 'Polygon fraction receipt exceeds runtime decimal formatting budget') from error


def _sub(a, b):
    return _checked(a[0] - b[0]), _checked(a[1] - b[1])


def _cross(a, b):
    return _checked(_checked(a[0] * b[1]) - _checked(a[1] * b[0]))


def _on(a, b, p):
    return (_cross(_sub(b, a), _sub(p, a)) == 0 and
            min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
            min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _intersections(a, b, c, d, budget):
    """Exact intersection points; collinear overlaps return their endpoints."""
    budget.spend()
    r, s = _sub(b, a), _sub(d, c)
    denominator = _cross(r, s)
    if denominator:
        t = _checked(_cross(_sub(c, a), s) / denominator)
        u = _checked(_cross(_sub(c, a), r) / denominator)
        if 0 <= t <= 1 and 0 <= u <= 1:
            return {(_checked(a[0] + _checked(t * r[0])),
                     _checked(a[1] + _checked(t * r[1])))}
        return set()
    if _cross(_sub(c, a), r):
        return set()
    return {p for p in (a, b, c, d) if _on(a, b, p) and _on(c, d, p)}


def _parse(commands, max_segments):
    segments, current, contours = [], [], []
    implicit = duplicates = empty_moves = 0
    has_move = False

    def finish(explicit):
        nonlocal current, implicit, duplicates, empty_moves
        if not current:
            return
        if len(current) == 1:
            empty_moves += 1
            current = []
            return
        if current[-1] != current[0]:
            if not explicit:
                implicit += 1
            current.append(current[0])
        for a, b in zip(current, current[1:]):
            if a == b:
                duplicates += 1
            else:
                segments.append((a, b))
                if len(segments) > max_segments:
                    raise UnsupportedPdfWindingError('budget', 'Polygon input segment budget exceeded')
        contour = [current[0]]
        for point in current[1:]:
            if point != contour[-1]:
                contour.append(point)
        if len(contour) > 1 and contour[-1] == contour[0]:
            contour.pop()
        contours.append(contour)
        current = []

    if not isinstance(commands, (tuple, list)) or len(commands) > max_segments * 4 + 16:
        raise UnsupportedPdfWindingError('budget', 'Polygon command budget exceeded')
    for command in commands:
        if not isinstance(command, (tuple, list)) or not command:
            raise UnsupportedPdfWindingError('input', 'Invalid polygon command')
        op = command[0]
        if op in ('M', 'L'):
            if (len(command) != 2 or not isinstance(command[1], (tuple, list)) or
                    len(command[1]) != 2):
                raise UnsupportedPdfWindingError('input', 'Invalid polygon coordinate pair')
            p = tuple(_number(v) for v in command[1])
            if op == 'M':
                finish(False)
                current = [p]
                has_move = True
            elif not current:
                raise UnsupportedPdfWindingError('input', 'Line requires an active move subpath')
            else:
                current.append(p)
        elif op == 'Z' and len(command) == 1:
            if not has_move:
                raise UnsupportedPdfWindingError('input', 'Close requires a preceding move subpath')
            finish(True)
        else:
            raise UnsupportedPdfWindingError('unsupported_command', 'Only polygon M/L/Z fills are supported')
    finish(False)
    return segments, {'implicit_closures': implicit, 'zero_length_edges_ignored_for_proof': duplicates,
                      'empty_moves_ignored_for_proof': empty_moves}, contours


def _winding(segments, p, budget):
    value = 0
    for a, b in segments:
        budget.spend()
        side = _cross(_sub(b, a), _sub(p, a))
        if side == 0 and _on(a, b, p):
            raise UnsupportedPdfWindingError('boundary_probe', 'Winding probe lies on a source boundary')
        if a[1] <= p[1] < b[1] and side > 0:
            value += 1
        elif b[1] <= p[1] < a[1] and side < 0:
            value -= 1
    return value


def _side_samples(edge, atoms, budget, max_halvings):
    a, b = edge
    delta = _sub(b, a)
    normal = (-delta[1], delta[0])
    middle = tuple(_checked(_checked(x + y) / 2) for x, y in zip(a, b))
    distance = _checked(Fraction(1, 1) / max(abs(delta[0]), abs(delta[1]), 1))
    for halving in range(max_halvings + 1):
        budget.spend()
        points = [tuple(_checked(middle[i] + _checked(sign * distance * normal[i]))
                        for i in range(2)) for sign in (1, -1)]
        clear = True
        for other in atoms:
            if other == edge:
                continue
            if any(_intersections(middle, p, *other, budget) for p in points):
                clear = False
                break
        if clear:
            return points, halving
        distance = _checked(distance / 2)
    raise UnsupportedPdfWindingError('budget', 'Exact side-probe separation budget exhausted')


def _area(ring, budget):
    area = Fraction(0)
    for a, b in zip(ring, ring[1:] + ring[:1]):
        budget.spend()
        area = _checked(area + _cross(a, b))
    return _checked(area / 2)


def _simplify(ring, budget):
    """Remove strictly between collinear vertices; never move an endpoint."""
    changed = True
    while changed and len(ring) > 3:
        changed = False
        for i, point in enumerate(ring):
            budget.spend()
            a, b = ring[i - 1], ring[(i + 1) % len(ring)]
            if a != b and _on(a, b, point):
                ring = ring[:i] + ring[i + 1:]
                changed = True
                break
    return ring


def _validate_rings(rings, budget):
    """Prove strict simplicity/disjointness and return containment/orientation."""
    edges = []
    signs = []
    for ri, ring in enumerate(rings):
        if len(ring) < 3 or len(set(ring)) != len(ring):
            raise UnsupportedPdfWindingError('degenerate_boundary', 'Output boundary repeats or collapses vertices')
        area = _area(ring, budget)
        if not area:
            raise UnsupportedPdfWindingError('degenerate_boundary', 'Output boundary has zero signed area')
        signs.append(1 if area > 0 else -1)
        edges.extend((ri, j, a, b) for j, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1])))
    for i, (ri, j, a, b) in enumerate(edges):
        for rj, k, c, d in edges[i + 1:]:
            intersections = _intersections(a, b, c, d, budget)
            adjacent = ri == rj and ((j - k) % len(rings[ri]) in (1, len(rings[ri]) - 1))
            expected = {a, b} & {c, d} if adjacent else set()
            if intersections != expected:
                raise UnsupportedPdfWindingError('touching_boundary', 'Output boundaries intersect, overlap, or touch')
    containment = []
    for i, ring in enumerate(rings):
        row = []
        for j, other in enumerate(rings):
            row.append(False if i == j else bool(_winding(list(zip(other, other[1:] + other[:1])), ring[0], budget)))
        containment.append(row)
    return {'orientation': signs, 'containment': containment}


def _float_output(rings, max_error, budget):
    """Require exact coordinates or a bounded, separately proved topology."""
    exact_topology = _validate_rings(rings, budget)
    converted, maximum = [], Fraction(0)
    for ring in rings:
        row = []
        for point in ring:
            try:
                floats = tuple(float(v) for v in point)
            except OverflowError as error:
                raise UnsupportedPdfWindingError('float_range', 'Boundary exceeds binary64 range') from error
            if not all(math.isfinite(v) for v in floats):
                raise UnsupportedPdfWindingError('float_range', 'Boundary exceeds binary64 range')
            rounded = tuple(Fraction(v) for v in floats)
            budget.spend()
            error = max(abs(_checked(a - b)) for a, b in zip(point, rounded))
            maximum = max(maximum, error)
            if error > max_error:
                raise UnsupportedPdfWindingError('coordinate_roundoff', 'Boundary coordinate exceeds declared rounding budget',
                                                exact_point=[_fraction_text(v) for v in point],
                                                rounding_error=_fraction_text(error), permitted_error=_fraction_text(max_error))
            row.append(rounded)
        converted.append(row)
    rounded_topology = _validate_rings(converted, budget)
    if rounded_topology != exact_topology:
        raise UnsupportedPdfWindingError('rounding_topology', 'Binary64 conversion changes boundary topology')
    commands = []
    for ring in converted:
        commands.append(('M', [float(v) for v in ring[0]]))
        commands.extend(('L', [float(v) for v in p]) for p in ring[1:])
        commands.append(('Z',))
    bound = float(maximum)
    if Fraction(bound) < maximum:
        bound = math.nextafter(bound, math.inf)
    return commands, {'exact_binary64_coordinates': maximum == 0,
                      'maximum_coordinate_error_exact': _fraction_text(maximum),
                      'maximum_coordinate_error_bound': bound,
                      'permitted_coordinate_error_exact': _fraction_text(max_error),
                      'topology': exact_topology,
                      'rounded_topology_revalidated_exactly': True,
                      'pointwise_fill_equivalence_after_rounding': maximum == 0,
                      'rgb_alpha_error_bound': None}


def verify_simple_loop_topology(reference_commands, candidate_commands, *,
                                max_input_segments=256, max_operations=1_000_000):
    """Revalidate a later coordinate encoding, e.g. actual DrawingML integers.

    Both inputs must already describe corresponding strictly simple, disjoint
    polygon loops in the same coordinate system and ring/vertex order. Exact
    Fraction coordinates are accepted for a native integer transform's inverse
    mapping. This API does not normalize either geometry or permit curves. It
    verifies separation, holes and orientation, not pointwise fill equality.
    Callers must separately enforce and disclose their permitted vertex error.
    """
    for value in (max_input_segments, max_operations):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('Polygon budgets must be positive integers')
    _, _, reference = _parse(reference_commands, max_input_segments)
    _, _, candidate = _parse(candidate_commands, max_input_segments)
    if [len(r) for r in reference] != [len(r) for r in candidate]:
        raise UnsupportedPdfWindingError('rounding_topology', 'Coordinate encoding collapses or changes loop vertices')
    budget = _Budget(max_operations)
    before = _validate_rings(reference, budget)
    after = _validate_rings(candidate, budget)
    if before != after:
        raise UnsupportedPdfWindingError('rounding_topology', 'Coordinate encoding changes loop containment or orientation')
    maximum = max((abs(_checked(a - b)) for left, right in zip(reference, candidate)
                   for p, q in zip(left, right) for a, b in zip(p, q)), default=Fraction(0))
    try:
        bound = float(maximum)
    except OverflowError as error:
        raise UnsupportedPdfWindingError('float_range', 'Coordinate encoding error exceeds binary64 range') from error
    if not math.isfinite(bound):
        raise UnsupportedPdfWindingError('float_range', 'Coordinate encoding error exceeds binary64 range')
    if Fraction(bound) < maximum:
        bound = math.nextafter(bound, math.inf)
    return {'method': 'exact_simple_loop_orientation_separation_containment_revalidation',
            'topology_unchanged': True, 'topology': before,
            'maximum_vertex_coordinate_delta_exact': _fraction_text(maximum),
            'maximum_vertex_coordinate_delta_bound': bound,
            'pointwise_geometry_identical': maximum == 0,
            'rgb_alpha_error_bound': None, 'exact_predicate_operations': budget.used}


def normalize_nonzero_polygons(commands, *, max_coordinate_error=0,
                               max_input_segments=256, max_atomic_edges=2048,
                               max_operations=1_000_000, max_probe_halvings=80):
    """Return an even-odd-equivalent simple-loop representation of a fill.

    All intersections and winding classifications use exact rational arithmetic.
    Default export rejects any binary64 coordinate rounding. A positive explicit
    ``max_coordinate_error`` (in input units) allows only bounded vertex rounding
    with exact reproof of simplicity, strict separation, orientation and the
    complete loop containment matrix. This preserves topology, not pointwise
    fill membership or RGB/alpha values near the moved boundary.

    The returned ``commands`` describe one compound filled path, so original
    alpha is applied once; do not independently paint each output loop. Empty
    commands prove an empty fill and do not authorize dropping an input stroke.
    """
    error_budget = _number(max_coordinate_error)
    if error_budget < 0:
        raise ValueError('max_coordinate_error must be nonnegative')
    for value in (max_input_segments, max_atomic_edges, max_operations, max_probe_halvings):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('Polygon budgets must be positive integers')
    budget = _Budget(max_operations)
    segments, normalization, _ = _parse(commands, max_input_segments)
    splits = [{a, b} for a, b in segments]
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            points = _intersections(a, b, *segments[j], budget)
            splits[i].update(points)
            splits[j].update(points)
    multiplicities = {}
    for (a, b), points in zip(segments, splits):
        axis = 0 if a[0] != b[0] else 1
        denominator = _checked(b[axis] - a[axis])
        def parameter(point):
            budget.spend()
            return _checked(_checked(point[axis] - a[axis]) / denominator)
        ordered = sorted(points, key=parameter)
        for start, end in zip(ordered, ordered[1:]):
            budget.spend()
            if start == end:
                continue
            edge = tuple(sorted((start, end)))
            multiplicities[edge] = multiplicities.get(edge, 0) + (1 if edge == (start, end) else -1)
            if len(multiplicities) > max_atomic_edges:
                raise UnsupportedPdfWindingError('budget', 'Atomic polygon edge budget exceeded')
    atoms = list(multiplicities)
    boundary, classification = [], []
    for edge, count in multiplicities.items():
        if count == 0:
            continue
        (left, right), halvings = _side_samples(edge, atoms, budget, max_probe_halvings)
        wl, wr = _winding(segments, left, budget), _winding(segments, right, budget)
        if wl - wr != count:
            raise UnsupportedPdfWindingError('winding_invariant', 'Exact atomic winding jump disagrees with source multiplicity')
        if bool(wl) == bool(wr):
            continue
        directed = edge if wl else tuple(reversed(edge))
        boundary.append(directed)
        classification.append({'a': [_fraction_text(v) for v in edge[0]], 'b': [_fraction_text(v) for v in edge[1]],
                               'left_winding': wl, 'right_winding': wr,
                               'side_probe_halvings': halvings})
    outgoing, incoming = {}, {}
    for a, b in boundary:
        if a in outgoing or b in incoming:
            raise UnsupportedPdfWindingError('touching_boundary', 'Filled boundary has an ambiguous branching/touching vertex')
        outgoing[a] = b
        incoming[b] = a
    if set(outgoing) != set(incoming):
        raise UnsupportedPdfWindingError('open_boundary', 'Filled boundary is not closed')
    remaining = set(outgoing)
    rings = []
    while remaining:
        start = min(remaining)
        ring, current = [], start
        while current in remaining:
            budget.spend()
            remaining.remove(current)
            ring.append(current)
            current = outgoing[current]
        if current != start:
            raise UnsupportedPdfWindingError('open_boundary', 'Filled boundary does not return to its start')
        rings.append(_simplify(ring, budget))
    output, export = _float_output(rings, error_budget, budget)
    return {'commands': output, 'proof': {
        'method': 'exact_fraction_planar_arrangement_nonzero_boundary',
        'source_fill_rule': 'nonzero', 'output_fill_rule': 'evenodd',
        'fill_only': True, 'original_stroke_must_be_preserved_separately': True,
        'source_commands_modified': False, 'source_segment_count': len(segments),
        'atomic_edge_count': len(atoms),
        'cancelled_atomic_edges': sum(v == 0 for v in multiplicities.values()),
        'boundary_edge_count': len(boundary), 'output_ring_count': len(rings),
        'empty_fill': not rings, 'input_normalization_for_proof': normalization,
        'boundary_winding_classification': classification, 'coordinate_export': export,
        'intermediate_fraction_bit_limit': _INTERMEDIATE_BITS,
        'exact_predicate_operations': budget.used,
    }}
