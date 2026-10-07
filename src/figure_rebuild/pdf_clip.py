"""Exact, conservative no-op/outside proofs for line/cubic clipping contours.

This module never clips or approximates output geometry. It only proves that
a whole axis-aligned paint bound has constant clip occupancy. Each cubic may
be replaced *for the proof only* by chords whose convex hulls are strictly
separated from that bound; the resulting homotopy cannot cross any point of
the bound. Exact rational winding of those chords therefore classifies every
point in the bound. A crossing, contact or exhausted budget returns ``None``.
"""
from .pdf_fill import _orientation, _point


def _separated(points, box):
    """A separating axis is a sufficient proof, including degenerate hulls."""
    axes = [(1, 0), (0, 1)]
    axes.extend((-(b[1]-a[1]), b[0]-a[0])
                for i, a in enumerate(points) for b in points[i+1:] if a != b)
    for x, y in axes:
        p = [x*a[0]+y*a[1] for a in points]
        q = [x*a[0]+y*a[1] for a in box]
        if max(p) < min(q) or max(q) < min(p):
            return True
    return False


def _split(points):
    def mid(a, b):
        return ((a[0]+b[0])/2, (a[1]+b[1])/2)
    a, b, c, d = points
    ab, bc, cd = mid(a, b), mid(b, c), mid(c, d)
    abc, bcd = mid(ab, bc), mid(bc, cd)
    m = mid(abc, bcd)
    return (a, ab, abc, m), (m, bcd, cd, d)


def prove_clip_box_relation(commands, bounds, *, fill_rule="nonzero", max_depth=18,
                            max_segments=8192):
    """Return an exact whole-box ``inside``/``outside`` proof or ``None``.

    Input M/L/C/Z contours use SVG fill semantics: each open subpath closes
    implicitly, and all subpaths in one path share its nonzero/evenodd rule.
    Self-intersections and holes are allowed; topology is measured by winding,
    not inferred from orientation or a bounding rectangle. Separate SVG clip
    children must be classified independently and combined as a union.
    """
    if (fill_rule not in ("nonzero", "evenodd") or isinstance(max_depth, bool)
            or not isinstance(max_depth, int) or not 0 <= max_depth <= 24
            or isinstance(max_segments, bool) or not isinstance(max_segments, int)
            or not 1 <= max_segments <= 65536):
        return None
    try:
        if len(bounds) != 4:
            return None
        lo, hi = _point(bounds[:2]), _point(bounds[2:])
        if lo[0] >= hi[0] or lo[1] >= hi[1]:
            return None
        box = (lo, (hi[0], lo[1]), hi, (lo[0], hi[1]))
        center = ((lo[0]+hi[0])/2, (lo[1]+hi[1])/2)
        segments, cursor, start = [], None, None
        command_count, contour_count, cubic_count = 0, 0, 0
        for command in commands:
            command_count += 1
            if command_count > max_segments or not isinstance(command, (list, tuple)) or not command:
                return None
            op = command[0]
            if op == "M" and len(command) == 2:
                if cursor is not None:
                    segments.append((cursor, start))
                cursor = start = _point(command[1])
                contour_count += 1
            elif op == "L" and len(command) == 2 and cursor is not None:
                point = _point(command[1])
                segments.append((cursor, point))
                cursor = point
            elif op == "C" and len(command) == 4 and cursor is not None:
                points = tuple(_point(p) for p in command[1:])
                segments.append((cursor, *points))
                cursor = points[-1]
                cubic_count += 1
            elif op == "Z" and len(command) == 1 and cursor is not None:
                segments.append((cursor, start))
                cursor = start = None
            else:
                return None
        if cursor is not None:
            segments.append((cursor, start))
        if not contour_count:
            return None
    except (ValueError, TypeError, OverflowError):
        return None

    winding, examined, leaves, deepest = 0, 0, 0, 0
    for segment in segments:
        stack = [(segment, 0)]
        while stack:
            points, depth = stack.pop()
            examined += 1
            if examined > max_segments:
                return None
            if _separated(points, box):
                a, b = points[0], points[-1]
                if a[1] <= center[1] < b[1] and _orientation(a, b, center) > 0:
                    winding += 1
                elif b[1] <= center[1] < a[1] and _orientation(a, b, center) < 0:
                    winding -= 1
                leaves += 1
                deepest = max(deepest, depth)
            elif len(points) == 4 and depth < max_depth:
                left, right = _split(points)
                stack.extend(((right, depth+1), (left, depth+1)))
            else:
                return None
    inside = winding != 0 if fill_rule == "nonzero" else winding % 2 != 0
    return {"relation": "inside" if inside else "outside",
            "proof": "exact_control_hull_separation_and_constant_winding_on_box",
            "predicate_arithmetic": "exact_rationals_of_input_coordinates",
            "fill_rule": fill_rule, "winding_at_box_center": winding,
            "contour_count": contour_count, "cubic_count": cubic_count,
            "source_command_count": command_count, "examined_segments": examined,
            "proof_chord_count": leaves, "maximum_subdivision_depth": deepest,
            "source_commands_changed": False, "output_geometry_approximated": False}
