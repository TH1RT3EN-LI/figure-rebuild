"""Conservative fill-rule equivalence proofs for source polygon contours.

DrawingML does not expose SVG's evenodd switch. Simple, mutually disjoint
polygon interiors have the same fill under either rule, regardless of contour
orientation. Prove that restricted case without moving any source coordinates;
curves, intersections, touching contours and nesting remain unsupported.
"""
from fractions import Fraction
import math


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a coordinate pair")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError("Expected finite coordinates")
    # Exact predicates over the supplied coordinates: a floating epsilon must
    # not turn a small crossing or gap into a different topological result.
    return tuple(Fraction(v) for v in value)


def _orientation(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _on_segment(a, b, p):
    return (_orientation(a, b, p) == 0
            and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _intersects(a, b, c, d):
    ab_c, ab_d = _orientation(a, b, c), _orientation(a, b, d)
    cd_a, cd_b = _orientation(c, d, a), _orientation(c, d, b)
    if ((ab_c > 0) != (ab_d > 0) and ab_c and ab_d
            and (cd_a > 0) != (cd_b > 0) and cd_a and cd_b):
        return True
    return (_on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


def _inside(point, polygon):
    winding = 0
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if a[1] <= point[1] < b[1] and _orientation(a, b, point) > 0:
            winding += 1
        elif b[1] <= point[1] < a[1] and _orientation(a, b, point) < 0:
            winding -= 1
    return winding != 0


def prove_evenodd_nonzero_equivalent(commands):
    """Return a proof receipt for disjoint simple polygons, otherwise ``None``.

    This does not normalize arbitrary evenodd paths. The original M/L/Z
    commands are unchanged. Closed contours may be concave and use either
    orientation; contained, intersecting or touching contours are rejected.
    Resource bounds also fail closed. ``None`` is not permission to omit paint.
    """
    polygons, current = [], None
    try:
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2 and current is None:
                current = [_point(command[1])]
            elif op == "L" and len(command) == 2 and current is not None:
                point = _point(command[1])
                if point != current[-1]:
                    current.append(point)
                if len(current) > 512:
                    return None
            elif op == "Z" and len(command) == 1 and current is not None:
                if len(current) > 1 and current[-1] == current[0]:
                    current.pop()
                if len(current) < 3:
                    return None
                polygons.append(current)
                current = None
                if len(polygons) > 16:
                    return None
            else:
                return None
    except (ValueError, TypeError, OverflowError):
        return None
    if current is not None or not polygons:
        return None

    all_edges = []
    for polygon in polygons:
        edges = list(zip(polygon, polygon[1:]+polygon[:1]))
        area2 = sum(a[0]*b[1]-a[1]*b[0] for a, b in edges)
        if area2 == 0:
            return None
        for i, (a, b) in enumerate(edges):
            c = edges[(i+1) % len(edges)][1]
            if _orientation(a, b, c) == 0:
                # Adjacent edges may continue straight, but may not backtrack.
                if (b[0]-a[0])*(c[0]-b[0]) + (b[1]-a[1])*(c[1]-b[1]) <= 0:
                    return None
            for j in range(i+1, len(edges)):
                if j == i+1 or (i == 0 and j == len(edges)-1):
                    continue
                if _intersects(a, b, *edges[j]):
                    return None
        all_edges.append(edges)

    for i, polygon in enumerate(polygons):
        for j in range(i+1, len(polygons)):
            other = polygons[j]
            if any(_intersects(a, b, c, d) for a, b in all_edges[i] for c, d in all_edges[j]):
                return None
            if _inside(polygon[0], other) or _inside(other[0], polygon):
                return None
    return {"source_fill_rule": "evenodd", "output_fill_rule": "nonzero",
            "proof": "simple_polygon_contours_with_disjoint_interiors",
            "predicate_arithmetic": "exact_rationals_of_input_coordinates",
            "contour_count": len(polygons),
            "vertex_count": sum(len(p) for p in polygons),
            "source_commands_changed": False}
