"""Bounded rectangular clipping of independently convex filled contours.

Each source contour keeps its winding contribution. Intersecting every contour
with the same rectangle preserves their summed winding inside it, including
oppositely oriented holes. Strokes and curves are outside this helper's scope.
"""
from fractions import Fraction
import math

from .pdf_fill import _normalize_fill_for_proof, _orientation, _point


def _deduplicate(points):
    result = []
    for point in points:
        if not result or result[-1] != point:
            result.append(point)
    if len(result) > 1 and result[-1] == result[0]:
        result.pop()
    return result


def _convex_orientation(points, budget):
    """0 is collinear, +/-1 convex winding, None unknown; no epsilon."""
    if len(points) < 3:
        return 0
    direction = 0
    for a, b in zip(points, points[1:]+points[:1]):
        for c in points:
            budget[0] -= 1
            if budget[0] < 0:
                return None
            sign = _orientation(a, b, c)
            if sign:
                current = 1 if sign > 0 else -1
                if direction and direction != current:
                    return None
                direction = current
    if direction and len(set(points)) != len(points):
        return None  # A repeated walk is not a single convex contour.
    return direction


def _halfplane(points, axis, edge, keep_greater):
    if not points:
        return []

    def inside(point):
        return point[axis] >= edge if keep_greater else point[axis] <= edge

    output = []
    previous = points[-1]
    previous_inside = inside(previous)
    for point in points:
        current_inside = inside(point)
        if current_inside != previous_inside:
            t = (edge-previous[axis])/(point[axis]-previous[axis])
            crossing = tuple(a+(b-a)*t for a, b in zip(previous, point))
            output.append(crossing)
        if current_inside:
            output.append(point)
        previous, previous_inside = point, current_inside
    return _deduplicate(output)


def clip_convex_fill_to_rect(commands, rectangle, *, fill_rule='nonzero',
                             max_commands=8192, max_predicates=65536):
    """Return derived M/L/Z commands plus a receipt, or None if unproved.

    This accepts a nonzero fill with up to 16 individually convex polygon
    contours; their mutual nesting/overlap does not need to be simplified.
    Exact rational predicates and intersections use the original finite input
    coordinates. Output coordinates are rounded to binary64 once, with their
    maximum error recorded. Compound contours require zero coordinate roundoff
    because per-contour orientation alone cannot protect narrow gaps or holes.
    A single nondegenerate contour must remain convex with the same orientation
    after conversion. No source command is mutated.
    An empty result proves all contour intersections have zero fill area.
    """
    if (fill_rule != 'nonzero' or type(max_commands) is not int or
            not 1 <= max_commands <= 8192 or type(max_predicates) is not int or
            not 1 <= max_predicates <= 262144 or not isinstance(commands, (list, tuple)) or
            len(commands) > max_commands):
        return None
    try:
        if not isinstance(rectangle, (list, tuple)) or len(rectangle) != 4:
            return None
        lo, hi = _point(rectangle[:2]), _point(rectangle[2:])
        if lo[0] >= hi[0] or lo[1] >= hi[1]:
            return None
        if any(not isinstance(cmd, (list, tuple)) or not cmd or cmd[0] not in ('M', 'L', 'Z')
               for cmd in commands):
            return None
        normalized = _normalize_fill_for_proof(commands)
        if normalized is None:
            return None
        normalized_commands, normalization = normalized
        contours, current = [], None
        for command in normalized_commands:
            if command[0] == 'M':
                if current is not None:
                    return None
                current = [_point(command[1])]
            elif command[0] == 'L' and current is not None:
                current.append(_point(command[1]))
            elif command[0] == 'Z' and current is not None:
                contours.append(_deduplicate(current))
                current = None
            else:
                return None
        if current is not None or len(contours) > 16:
            return None
        budget = [max_predicates]
        output, details, maximum_roundoff = [], [], Fraction(0)
        for contour in contours:
            direction = _convex_orientation(contour, budget)
            if direction is None:
                return None
            clipped = contour
            for axis, edge, greater in ((0, lo[0], True), (0, hi[0], False),
                                        (1, lo[1], True), (1, hi[1], False)):
                clipped = _halfplane(clipped, axis, edge, greater)
            clipped_direction = _convex_orientation(clipped, budget)
            if clipped_direction is None or (clipped_direction and clipped_direction != direction):
                return None
            detail = {'source_vertex_count': len(contour), 'source_orientation': direction,
                      'intersection_vertex_count': len(clipped),
                      'intersection_orientation': clipped_direction,
                      'zero_area_intersection': clipped_direction == 0}
            details.append(detail)
            if not clipped_direction:
                continue
            rounded = [tuple(float(v) for v in point) for point in clipped]
            if any(not math.isfinite(v) for point in rounded for v in point):
                return None
            exact_rounded = [tuple(Fraction(v) for v in point) for point in rounded]
            if _convex_orientation(exact_rounded, budget) != direction:
                return None
            for exact_point, float_point in zip(clipped, exact_rounded):
                maximum_roundoff = max(maximum_roundoff,
                                       *(abs(a-b) for a, b in zip(exact_point, float_point)))
            output.append(('M', rounded[0]))
            output.extend(('L', point) for point in rounded[1:])
            output.append(('Z',))
        if len(output) > max_commands or (len(contours) > 1 and maximum_roundoff):
            return None
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        return None
    return {'commands': tuple(output), 'proof': {
        'method': 'per_convex_contour_exact_rational_rectangle_intersection',
        'source_fill_rule': fill_rule, 'predicate_arithmetic': 'exact_rationals_of_input_coordinates',
        'rectangle_exact': [str(v) for v in (*lo, *hi)],
        'contours': details, 'normalization_for_fill': normalization,
        'source_commands_mutated': False, 'derived_commands_changed': True,
        'source_contour_winding_preserved': maximum_roundoff == 0,
        'exact_intersection_winding_preserved': True,
        'source_contour_orientation_preserved': True,
        'output_winding_preserved_exactly': maximum_roundoff == 0,
        'winding_rule': 'sum original contour contributions inside the common rectangle; zero outside',
        'curve_or_stroke_clipping': False, 'geometry_flattened': False,
        'output_conversion': 'round each exact intersection coordinate to binary64 once',
        'maximum_coordinate_roundoff_source_units_exact': str(maximum_roundoff),
        'roundoff_scope': 'coordinates only; not a rendering or target affine error bound',
        'rendering_equivalence': 'not asserted; changed edge endpoints can change rasterizer coverage',
        'predicates_used': max_predicates-budget[0],
        'empty_fill_intersection': not output}}
