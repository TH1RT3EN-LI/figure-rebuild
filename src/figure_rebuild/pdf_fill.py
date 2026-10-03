"""Conservative fill-rule equivalence proofs for source polygon contours.

DrawingML does not expose SVG's evenodd switch. Simple, mutually disjoint
polygon interiors have the same fill under either rule, regardless of contour
orientation. Prove that restricted case without moving any source coordinates;
Curved contours require an ordered convex control polygon, a proved
single-cubic/two-line Jordan contour, or pairwise separated segment control
hulls. The source coordinates stay unchanged.
Intersections, touching contours and nesting remain unsupported.
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
    """Return a bounded fill-rule equivalence receipt, otherwise ``None``.

    This does not normalize arbitrary evenodd paths. The original M/L/C/Z
    commands are unchanged. Fill-only implicit closures and exactly repeated
    straight vertices are handled in a separate proof representation, with a
    receipt; those changes must never be applied to a stroked source path.
    Contours may be concave and use either
    orientation; contained, intersecting or touching contours are rejected.
    Resource bounds also fail closed. ``None`` is not permission to omit paint.
    """
    if not isinstance(commands, (list, tuple)) or len(commands) > 8192:
        return None
    normalized = _normalize_fill_for_proof(commands)
    if normalized is None:
        return None
    proof_commands, receipt = normalized
    proof = _prove_closed_fill(proof_commands)
    if proof is None:
        return None
    return {**proof, "fill_proof_normalization": receipt}


def _normalize_fill_for_proof(commands):
    """Apply SVG fill semantics to a copy, never to output/stroke commands.

    Open subpaths are implicitly closed at a subsequent M and at path end.
    Z resets the current point to its subpath start: any subsequent L/C starts
    a new subpath there. Repeated Z adds no fill edge. Only an L whose endpoint
    exactly equals the current point can be omitted; curves are never erased.
    A moveto subpath with no drawing command contributes no fill boundary and
    may be excluded from this proof copy. Degenerate L/C contours still reach
    the topology proof and fail closed, even if a zero-length L was omitted.
    """
    normalized = []
    receipt = {"proof_only": True, "source_commands_changed": False,
               "implicit_closures": [], "skipped_zero_length_line_indices": [],
               "ignored_repeated_close_indices": [],
               "post_close_subpath_starts": [], "skipped_empty_subpaths": []}
    start = cursor = raw_start = raw_cursor = None
    active = False
    subpath_count = 0
    subpaths, subpath = [], None

    def finish_subpath(after_index):
        subpath["proof_end"] = len(normalized)
        subpath["source_end"] = after_index

    def close_implicitly(reason, after_index):
        normalized.append(("Z",))
        receipt["implicit_closures"].append(
            {"reason": reason, "after_source_command_index": after_index,
             "from": list(raw_cursor), "to": list(raw_start)})
        finish_subpath(after_index)

    try:
        for index, command in enumerate(commands):
            if not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2:
                point = _point(command[1])
                if active:
                    close_implicitly("next_moveto", index-1)
                start = cursor = point
                raw_start = raw_cursor = command[1]
                active = True
                subpath_count += 1
                subpath = {"proof_start": len(normalized), "source_start": index,
                           "has_drawing_command": False}
                subpaths.append(subpath)
                normalized.append(command)
            elif op in ("L", "C") and len(command) == (2 if op == "L" else 4):
                points = [_point(p) for p in command[1:]]
                if start is None:
                    return None
                if not active:
                    # A close resets cursor to start, not the last explicit
                    # vertex. Preserve that distinction in the proof copy.
                    subpath = {"proof_start": len(normalized), "source_start": index,
                               "has_drawing_command": False}
                    subpaths.append(subpath)
                    normalized.append(("M", raw_start))
                    active = True
                    subpath_count += 1
                    receipt["post_close_subpath_starts"].append(
                        {"before_source_command_index": index, "point": list(raw_start)})
                subpath["has_drawing_command"] = True
                if op == "L" and points[-1] == cursor:
                    receipt["skipped_zero_length_line_indices"].append(index)
                else:
                    normalized.append(command)
                cursor, raw_cursor = points[-1], command[-1]
            elif op == "Z" and len(command) == 1 and start is not None:
                if active:
                    normalized.append(command)
                    finish_subpath(index)
                    active = False
                else:
                    receipt["ignored_repeated_close_indices"].append(index)
                cursor, raw_cursor = start, raw_start
            else:
                return None
            if subpath_count > 16:
                return None
        if active:
            close_implicitly("end_of_path", len(commands)-1)
    except (ValueError, TypeError, OverflowError):
        return None
    # Filter only after the entire input has passed validation. A malformed or
    # nonfinite command in an apparently empty subpath must never be hidden.
    excluded = set()
    for subpath in subpaths:
        if not subpath["has_drawing_command"]:
            indices = list(range(subpath["proof_start"], subpath["proof_end"]))
            excluded.update(indices)
            receipt["skipped_empty_subpaths"].append({
                "reason": "moveto_subpath_has_no_drawing_commands",
                "moveto_source_command_index": subpath["source_start"],
                "source_command_indices": list(range(subpath["source_start"], subpath["source_end"]+1)),
                "proof_command_indices_before_filter": indices,
                "source_commands_changed": False})
    normalized = [command for index, command in enumerate(normalized) if index not in excluded]
    return normalized, receipt


def _prove_closed_fill(commands):
    """Topology proof over validated, explicitly closed proof commands."""
    if any(isinstance(c, (list, tuple)) and c and c[0] == "C" for c in commands):
        return (_prove_convex_curve_contours(commands)
                or _prove_cubic_two_line_contour(commands)
                or _prove_disjoint_segment_hulls(commands))
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
    vertices = sum(len(p) for p in polygons)
    if vertices*(vertices-1)//2 > 250000:
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


def _prove_convex_curve_contours(commands):
    """Prove simplicity using *control polygons*, never sampled curve points.

    Each cubic is injective under a proved monotone linear projection, and
    lies in the hull of its consecutive control vertices. For a
    simple convex polygon, hulls of disjoint boundary intervals have disjoint
    interiors. Their curves can therefore meet only at their common endpoint.
    Collinear continuation is allowed; backtracking and repeated nonadjacent
    control vertices are rejected by the polygon proof below. No output curve
    is flattened or changed. Disjointness is checked on the enclosing control
    polygons, a deliberately stricter condition than curve disjointness.
    """
    polygon_commands, contours = [], []
    current, curved, curve_count = None, False, 0
    try:
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2 and current is None:
                current, curved = [_point(command[1])], False
                polygon_commands.append(command)
            elif op == "L" and len(command) == 2 and current is not None:
                point = _point(command[1])
                if point != current[-1]:
                    current.append(point)
                polygon_commands.append(command)
            elif op == "C" and len(command) == 4 and current is not None:
                points = [_point(p) for p in command[1:]]
                if points[-1] == current[-1]:
                    return None  # Closed single-segment loops need another proof.
                if not _monotone_projection([current[-1], *points]):
                    return None
                for raw, point in zip(command[1:], points):
                    if point != current[-1]:
                        current.append(point)
                    polygon_commands.append(("L", raw))
                curved, curve_count = True, curve_count + 1
            elif op == "Z" and len(command) == 1 and current is not None:
                if len(current) > 1 and current[-1] == current[0]:
                    current.pop()
                contours.append((current, curved))
                polygon_commands.append(command)
                current = None
                if len(contours) > 16:
                    return None
            else:
                return None
            if current is not None and len(current) > 512:
                return None
    except (ValueError, TypeError, OverflowError):
        return None
    if current is not None or not contours:
        return None
    for polygon, curved in contours:
        if not curved:
            continue
        signs = {_orientation(polygon[i-1], polygon[i], polygon[(i+1) % len(polygon)]) > 0
                 for i in range(len(polygon))
                 if _orientation(polygon[i-1], polygon[i], polygon[(i+1) % len(polygon)]) != 0}
        if len(signs) != 1:
            return None
    proof = _prove_closed_fill(polygon_commands)
    if proof is None:
        return None
    return {**proof, "proof": "ordered_convex_bezier_control_polygons_with_disjoint_interiors",
            "curved_contour_count": sum(curved for _, curved in contours),
            "cubic_count": curve_count, "curve_approximation": False,
            "individual_curve_simplicity": "strictly_monotone_linear_projection"}


def _prove_cubic_two_line_contour(commands):
    """Prove a single closed contour with two lines and one injective cubic.

    Write the contour A->B, cubic B->C, C->A. A/B/C must be noncollinear.
    Every cubic control lies in the AB halfplane toward C and the AC
    halfplane toward B. Since C and B respectively lie strictly inside those
    halfplanes, positive Bernstein weights put every interior curve point
    strictly inside both. The cubic cannot meet either straight edge, and its
    monotone projection proves it cannot meet itself. This is a Jordan curve,
    even when the cubic bends inward and its full control polygon is concave.

    Only one contour is accepted: combining independently simple contours
    would require another proof of disjoint interiors. No curve is flattened.
    """
    if not isinstance(commands, (list, tuple)) or not 4 <= len(commands) <= 6:
        return None
    segments, cursor, start, closed = [], None, None, False
    try:
        for index, command in enumerate(commands):
            if not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2 and index == 0:
                cursor = start = _point(command[1])
            elif op == "L" and len(command) == 2 and cursor is not None:
                target = _point(command[1])
                if target == cursor:
                    return None
                segments.append(("L", cursor, target))
                cursor = target
            elif op == "C" and len(command) == 4 and cursor is not None:
                points = tuple(_point(p) for p in command[1:])
                segments.append(("C", cursor, *points))
                cursor = points[-1]
            elif (op == "Z" and len(command) == 1 and cursor is not None
                  and index == len(commands)-1):
                if cursor != start:
                    segments.append(("L", cursor, start))
                closed = True
            else:
                return None
    except (ValueError, TypeError, OverflowError):
        return None
    if not closed or len(segments) != 3 or sum(s[0] == "C" for s in segments) != 1:
        return None
    curve_index = next(i for i, segment in enumerate(segments) if segment[0] == "C")
    curve = segments[curve_index]
    following = segments[(curve_index+1) % 3]
    preceding = segments[(curve_index+2) % 3]
    if following[0] != "L" or preceding[0] != "L":
        return None
    b, p1, p2, c = curve[1:]
    a = following[-1]
    if following[1] != c or preceding[1] != a or preceding[-1] != b:
        return None
    orientation = _orientation(a, b, c)
    controls = (b, p1, p2, c)
    if orientation == 0 or not _monotone_projection(controls):
        return None
    if any(_orientation(a, b, p)*orientation < 0
           or _orientation(a, c, p)*orientation > 0 for p in controls):
        return None
    return {"source_fill_rule": "evenodd", "output_fill_rule": "nonzero",
            "proof": "single_cubic_two_lines_strict_halfplanes_and_monotone_projection",
            "predicate_arithmetic": "exact_rationals_of_input_coordinates",
            "contour_count": 1, "curved_contour_count": 1, "cubic_count": 1,
            "straight_segment_count": 2,
            "individual_curve_simplicity": "strictly_monotone_linear_projection",
            "curve_line_disjointness": "strict_interior_by_positive_bernstein_endpoint_weights",
            "source_commands_changed": False, "curve_approximation": False}


def _monotone_projection(points):
    # A weakly ordered, nonconstant sequence of projected control values gives
    # a strictly monotone Bezier coordinate: its derivative is a nonnegative
    # Bernstein combination, strictly positive for every t in (0,1).
    edges = [(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:]) if a != b]
    axes = [(1, 0), (0, 1), (points[-1][0]-points[0][0], points[-1][1]-points[0][1])]
    axes += [(-y, x) for x, y in edges]
    for x, y in axes:
        products = [x*a+y*b for a, b in edges]
        if products and (min(products) >= 0 and max(products) > 0
                         or max(products) <= 0 and min(products) < 0):
            return True
    return False


def _convex_hull(points):
    """Exact hull vertices, omitting interior collinear control points."""
    points = sorted(set(points))
    if len(points) <= 2:
        return points
    lower, upper = [], []
    for point in points:
        while len(lower) > 1 and _orientation(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) > 1 and _orientation(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1]+upper[:-1]


def _hulls_separated(first, second, shared=None):
    """Strict separation, or intersection only at a specified shared point.

    For adjacent segments, a supporting line may meet both hulls only when at
    least one exposed face is exactly the shared point. The other face must
    contain that same point. An overlapping edge/area is never accepted.
    All predicates use exact input-coordinate rationals, without an epsilon.
    """
    axes = [(1, 0), (0, 1)]
    for hull in (first, second):
        axes.extend((a[1]-b[1], b[0]-a[0])
                    for a, b in zip(hull, hull[1:]+hull[:1]) if a != b)
    for x, y in axes:
        first_projection = [x*p[0]+y*p[1] for p in first]
        second_projection = [x*p[0]+y*p[1] for p in second]
        for a, values_a, b, values_b in (
                (first, first_projection, second, second_projection),
                (second, second_projection, first, first_projection)):
            edge, other_edge = max(values_a), min(values_b)
            if edge < other_edge:
                return True
            if shared is not None and edge == other_edge:
                face_a = [p for p, value in zip(a, values_a) if value == edge]
                face_b = [p for p, value in zip(b, values_b) if value == edge]
                if ((face_a == [shared] and shared in face_b)
                        or (face_b == [shared] and shared in face_a)):
                    return True
    return False


def _prove_disjoint_segment_hulls(commands):
    """Prove a single Jordan contour without global control-polygon convexity.

    Each nonzero line or strictly monotone-projected cubic is injective. Its
    image stays inside its own convex control hull. Nonadjacent hulls must be
    strictly disjoint; adjacent hulls may intersect only at their common
    endpoint, including the final-to-first pair. Consequently the continuous
    closed contour is injective except for its single start/end identification
    and bounds a Jordan interior. Either fill rule paints that same interior.

    Only one contour is considered; this proof cannot establish that multiple
    contour interiors are unnested. Source commands and curves remain exact.
    """
    segments, start, cursor = [], None, None
    closed, cubic_count, control_count = False, 0, 0
    try:
        for index, command in enumerate(commands):
            if not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2 and index == 0:
                cursor = start = _point(command[1])
            elif op in ("L", "C") and len(command) == (2 if op == "L" else 4) and cursor is not None:
                points = [cursor, *[_point(p) for p in command[1:]]]
                if points[0] == points[-1]:
                    return None
                if op == "C":
                    if not _monotone_projection(points):
                        return None
                    cubic_count += 1
                segments.append(points)
                control_count += len(points)
                cursor = points[-1]
            elif op == "Z" and len(command) == 1 and cursor is not None and index == len(commands)-1:
                if cursor != start:
                    segments.append([cursor, start])
                    control_count += 2
                closed = True
            else:
                return None
            if control_count > 512:
                return None
    except (ValueError, TypeError, OverflowError):
        return None
    # Two-segment loops share two endpoints; the one-shared-point certificate
    # below intentionally does not cover them (the convex proof may do so).
    pair_count = len(segments)*(len(segments)-1)//2
    if not closed or len(segments) < 3 or not cubic_count or pair_count > 250000:
        return None
    hulls = [_convex_hull(segment) for segment in segments]
    for i, first in enumerate(hulls):
        for j in range(i+1, len(hulls)):
            shared = (segments[i][-1] if j == i+1 else
                      segments[0][0] if i == 0 and j == len(hulls)-1 else None)
            if not _hulls_separated(first, hulls[j], shared):
                return None
    return {"source_fill_rule": "evenodd", "output_fill_rule": "nonzero",
            "proof": "single_jordan_contour_with_pairwise_disjoint_segment_control_hulls",
            "predicate_arithmetic": "exact_rationals_of_input_coordinates",
            "contour_count": 1, "curved_contour_count": 1, "cubic_count": cubic_count,
            "segment_count": len(segments), "control_point_count": control_count,
            "segment_pairs_checked": pair_count,
            "individual_curve_simplicity": "strictly_monotone_linear_projection",
            "adjacent_hull_contact": "supporting_line_with_singleton_shared_endpoint_face",
            "nonadjacent_hulls": "strict_separation",
            "source_commands_changed": False, "curve_approximation": False}
