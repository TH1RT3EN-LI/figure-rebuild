"""Exact rectangular support for a bounded family of source stroke paints.

Commands are already in PDF/page coordinates. The source affine is used only
to scale the declared stroke width; it must not be applied to commands again.
Only independent nonzero axis-aligned M-L subpaths with butt caps, no dash,
no fill, and an exact axis similarity are supported. Unknown cases return
None, never an empty-success substitute.
"""
from fractions import Fraction
import math


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite numeric coordinate")
    return Fraction(value)


def _point(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("Expected two coordinates")
    return tuple(_number(v) for v in value)


def _axis_scale(matrix):
    if not isinstance(matrix, (tuple, list)) or len(matrix) != 6:
        raise ValueError("Expected a source affine")
    a, b, c, d, _, _ = map(_number, matrix)
    if b == c == 0 and a != 0 and abs(a) == abs(d):
        return abs(a)
    if a == d == 0 and b != 0 and abs(b) == abs(c):
        return abs(b)
    return None


def _intersect(a, b):
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def _positive(box):
    return box[0] < box[2] and box[1] < box[3]


def _strings(values):
    return [str(v) for v in values]


def clip_axis_butt_stroke_to_rect(commands, rectangle, *, source_transform,
                                stroke_width, fill, linecap, dasharray,
                                linejoin="miter", max_commands=8192,
                                max_subpaths=256):
    """Return a relation, optional derived commands and proof, or None.

    ``inside`` returns ``commands=None``: the caller must preserve the original
    stroke commands/style and painter identity. ``outside`` returns an empty
    tuple, proving zero-area intersection. ``intersection`` returns one M/L/Z
    compound fill. Its *single paint* must use the original stroke color and
    opacity times stroke-opacity, with no stroke. Never split its contours
    into independently composited objects. The caller must first validate all
    other source effects and clip context; this is a geometry-only helper.

    Intersection rectangles must have pairwise disjoint interiors. This also
    makes nonzero and evenodd fill equivalent for Office renderers. Overlaps
    are rejected rather than introducing holes or repeated alpha compositing.
    All original and output coordinates are exact rationals of the supplied
    finite input values, including IEEE floats, not original decimal tokens.
    Output coordinates are rounded to binary64 once. Their total axis order
    (including the clip boundaries) must remain strictly unchanged: distinct
    exact boundaries cannot collapse. This preserves the axis-rectangle
    arrangement topology, not pointwise geometry or rasterizer coverage.
    """
    if (fill != "none" or linecap != "butt" or dasharray != "none"
            or linejoin not in ("miter", "round", "bevel")
            or type(max_commands) is not int or not 1 <= max_commands <= 8192
            or type(max_subpaths) is not int or not 1 <= max_subpaths <= 256
            or not isinstance(commands, (tuple, list)) or not commands
            or len(commands) > max_commands or len(commands) % 2
            or len(commands)//2 > max_subpaths):
        return None
    try:
        scale = _axis_scale(source_transform)
        width = _number(stroke_width)
        if scale is None or width <= 0:
            return None  # Width zero is not permission to invent a PDF hairline.
        if not isinstance(rectangle, (tuple, list)) or len(rectangle) != 4:
            return None
        clip = tuple(_number(v) for v in rectangle)
        if not _positive(clip):
            return None
        # Source/ROI boundaries supplied by the parser are binary64. Do not
        # silently move an integer clip boundary that binary64 cannot store.
        if any(Fraction(float(v)) != v for v in clip):
            return None
        radius = width*scale/2
        rectangles, details = [], []
        for i in range(0, len(commands), 2):
            move, line = commands[i:i+2]
            if (not isinstance(move, (tuple, list)) or not isinstance(line, (tuple, list))
                    or len(move) != 2 or len(line) != 2 or move[0] != "M" or line[0] != "L"):
                return None
            a, b = _point(move[1]), _point(line[1])
            if a == b:
                return None  # Deliberately exclude degenerate source subpaths.
            if a[0] == b[0]:
                bounds = a[0]-radius, min(a[1], b[1]), a[0]+radius, max(a[1], b[1])
            elif a[1] == b[1]:
                bounds = min(a[0], b[0]), a[1]-radius, max(a[0], b[0]), a[1]+radius
            else:
                return None
            clipped = _intersect(bounds, clip)
            relation = "inside" if clipped == bounds else "intersection" if _positive(clipped) else "outside"
            details.append({"source_command_indices": [i, i+1], "relation": relation,
                            "complete_butt_stroke_bounds_exact": _strings(bounds),
                            "intersection_bounds_exact": _strings(clipped),
                            "zero_area_intersection": not _positive(clipped)})
            if _positive(clipped):
                rectangles.append(clipped)
        proof = {
            "method": "exact_axis_butt_segment_rectangle_intersection",
            "source_command_coordinate_space": "already transformed PDF/page coordinates",
            "source_affine_applied_again": False,
            "source_transform_exact": _strings(map(_number, source_transform)),
            "source_axis_similarity_scale_exact": str(scale),
            "source_width_exact": str(width), "source_half_width_in_page_units_exact": str(radius),
            "rectangle_exact": _strings(clip), "subpaths": details,
            "source_commands_mutated": False, "stroke_cap": "butt", "dasharray": "none",
            "source_join": linejoin, "joins_present": False,
            "predicate_arithmetic": "exact_rationals_of_supplied_input_values",
            "curve_or_general_polyline_clipping": False,
            "geometry_flattened": False,
            "rendering_equivalence": "not asserted; stroke/fill edge coverage can differ",
        }
        if all(d["relation"] == "inside" for d in details):
            return {"relation": "inside", "commands": None, "proof": {
                **proof, "geometry_action": "preserve_original_stroke_commands_and_style",
                "derived_geometry": False, "source_clipped_stroke_region_preserved_exactly": True}}
        if not rectangles:
            return {"relation": "outside", "commands": (), "proof": {
                **proof, "geometry_action": "skip_zero_area_intersection",
                "derived_geometry": False, "empty_fill_intersection": True}}

        # An a:path may use evenodd in native Office rendering. Until exact
        # rectangle union boundaries are implemented, do not emit overlapping
        # contours even if their intended nonzero winding would be a union.
        touching_pairs = []
        for i, a in enumerate(rectangles):
            for j in range(i+1, len(rectangles)):
                overlap = _intersect(a, rectangles[j])
                if _positive(overlap):
                    return None
                if overlap[0] <= overlap[2] and overlap[1] <= overlap[3]:
                    touching_pairs.append([i, j])

        coordinate_maps, axis_proofs, maximum_roundoff = [], [], Fraction(0)
        for axis in (0, 1):
            exact = sorted({clip[axis], clip[axis+2],
                            *(r[axis] for r in rectangles), *(r[axis+2] for r in rectangles)})
            rounded = [float(v) for v in exact]
            if any(not math.isfinite(v) for v in rounded):
                return None
            if any(a >= b for a, b in zip(rounded, rounded[1:])):
                return None
            errors = [abs(v-Fraction(w)) for v, w in zip(exact, rounded)]
            maximum_roundoff = max(maximum_roundoff, *errors)
            coordinate_maps.append(dict(zip(exact, rounded)))
            axis_proofs.append({"axis": "x" if axis == 0 else "y",
                                "unique_exact_coordinate_count": len(exact),
                                "minimum_exact_separation": str(min(b-a for a, b in zip(exact, exact[1:]))),
                                "strict_order_preserved_after_binary64": True,
                                "maximum_coordinate_roundoff_exact": str(max(errors))})
        output = []
        for x0, y0, x1, y1 in rectangles:
            x0, x1 = coordinate_maps[0][x0], coordinate_maps[0][x1]
            y0, y1 = coordinate_maps[1][y0], coordinate_maps[1][y1]
            if not (clip[0] <= Fraction(x0) < Fraction(x1) <= clip[2]
                    and clip[1] <= Fraction(y0) < Fraction(y1) <= clip[3]):
                return None
            output.extend((("M", (x0, y0)), ("L", (x1, y0)),
                           ("L", (x1, y1)), ("L", (x0, y1)), ("Z",)))
        if len(output) > max_commands:
            return None
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        return None
    return {"relation": "intersection", "commands": tuple(output), "proof": {
        **proof, "geometry_action": "one_compound_fill_with_original_stroke_paint",
        "derived_geometry": True, "output_rectangle_count": len(rectangles),
        "all_output_contours_same_orientation": True,
        "output_contour_interiors_pairwise_disjoint": True,
        "boundary_touching_output_rectangle_pairs": touching_pairs,
        "nonzero_evenodd_fill_equivalent": True,
        "same_paint_required": True,
        "paint_mapping": "one fill = original stroke color; alpha = opacity * stroke-opacity; no stroke",
        "exact_intersection_region_preserved": True,
        "rounded_output_arrangement_topology_preserved": True,
        "rounded_output_region_preserved_exactly": maximum_roundoff == 0,
        "source_clipped_stroke_region_preserved_exactly": maximum_roundoff == 0,
        "axis_order_proofs": axis_proofs,
        "output_conversion": "round exact rectangle boundary coordinates to binary64 once",
        "maximum_coordinate_roundoff_source_units_exact": str(maximum_roundoff),
        "roundoff_scope": "source coordinates only; not a target affine, native storage, or rendering bound"}}
