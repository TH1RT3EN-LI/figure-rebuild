"""Conservative stroke support in transformed coordinates.

This bounds declared SVG stroke geometry, not a PDF device-space hairline.
The centerline bounds must already be in the matrix's output coordinates;
the returned Fractions expand those bounds on x/y. Round/bevel joins and
butt/round caps stay inside the radius width/2 disk around the centerline.
After an arbitrary affine transform, that disk's axis supports are the row
L2 norms times the radius. A square cap adds perpendicular tangent/normal
offsets, so its radius is at most sqrt(2) times the half-width.

All square roots are rounded upward using exact integer arithmetic. No
near-similarity or floating epsilon establishes a geometric containment proof.
Miter joins preserve the previous, deliberately generous row-L1 bound. The
rectangle flag is a caller certificate of a single closed, axis-aligned
rectangle in OUTPUT coordinates, and tightens miter bounds only when the
matrix is an exact nondegenerate similarity over its input values.
"""
from fractions import Fraction
import math


class PdfStrokeBoundsError(ValueError):
    """Invalid or unsupported input; no bound has been established."""


class PdfTangentStrokeProofError(PdfStrokeBoundsError):
    """No tangent certificate; callers must retain conservative supports."""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PdfStrokeBoundsError("Stroke bounds require finite numeric values")
    try:
        if not math.isfinite(value):
            raise PdfStrokeBoundsError("Stroke bounds require finite numeric values")
    except OverflowError as error:
        raise PdfStrokeBoundsError("Stroke bounds exceed finite input values") from error
    return Fraction(value)


def _power_two(exponent):
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


def _sqrt_upper(value):
    """An exact or upward dyadic bound with at most 53 significant bits.

    Integer isqrt and a squared rational comparison determine the rounding
    direction, including subnormal-scale values and squared values larger
    than the floating-point range. The return value is always a Fraction.
    """
    if value == 0:
        return Fraction(0)
    exponent = (value.numerator.bit_length()-value.denominator.bit_length())//2
    if _power_two(2*exponent) > value:
        exponent -= 1
    step = _power_two(exponent-52)
    scaled = value/(step*step)
    root = math.isqrt(scaled.numerator//scaled.denominator)
    if root*root*scaled.denominator < scaled.numerator:
        root += 1
    return root*step


def stroke_envelope(matrix, width, miter_limit, rectangle=False, *,
                    linecap="butt", linejoin="miter"):
    """Return outward Fraction x/y supports for one declared SVG stroke.

    Four positional arguments preserve the previous helper's calling shape.
    Explicit styles allow a tighter round/bevel bound. ``rectangle=True``
    requires the caller's closed output-axis rectangle proof; it is not a
    tolerance-based geometric guess. Width zero has SVG zero-width semantics
    and returns zero supports. Unknown styles, negative width and nonfinite
    inputs raise ``PdfStrokeBoundsError``. Coordinates are never modified.
    """
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        raise PdfStrokeBoundsError("Stroke matrix requires six finite values")
    values = tuple(_number(v) for v in matrix)
    width, miter = _number(width), _number(miter_limit)
    if width < 0 or miter < 1:
        raise PdfStrokeBoundsError("Stroke width must be nonnegative and miter limit at least one")
    if type(rectangle) is not bool:
        raise PdfStrokeBoundsError("Rectangle certificate must be a boolean")
    if linecap not in ("butt", "round", "square") or linejoin not in ("miter", "round", "bevel"):
        raise PdfStrokeBoundsError("Unsupported stroke cap or join")
    if width == 0:
        return Fraction(0), Fraction(0)
    a, b, c, d = values[:4]
    if linejoin in ("round", "bevel"):
        multiplier = 2 if linecap == "square" else 1
        return (width*_sqrt_upper(multiplier*(a*a+c*c))/2,
                width*_sqrt_upper(multiplier*(b*b+d*d))/2)
    squared = a*a+b*b
    if rectangle and squared > 0 and squared == c*c+d*d and a*c+b*d == 0:
        return (width*_sqrt_upper(squared)/2,)*2
    # Retain the legacy miter bound, also safely enclosing square caps.
    factor = width*max(Fraction(2), miter/2)
    return factor*(abs(a)+abs(c)), factor*(abs(b)+abs(d))


def prove_tangent_stroke_support(commands, matrix, width, miter_limit=4, *,
                                linecap="butt", linejoin="miter",
                                dasharray="none", fill="none",
                                command_budget=256, segment_budget=128,
                                subpath_budget=32):
    """Certify a sufficient half-width support for an exact smooth stroke.

    Commands are already in the matrix's output coordinates. The matrix is
    used only to scale stroke width; it is never applied to commands again.
    Every actual join must have identical nonzero oriented tangents. Cubics
    need a strict common positive derivative projection on [0, 1]. Exact
    nondegenerate similarities (including reflections) are supported; no
    tolerance establishes tangency, regularity or transform equivalence.

    The JSON-compatible receipt contains outward *Fraction strings* in
    ``axis_support_exact_rationals``. Consumers expand their control hull
    using those values, not diagnostic floats. Unsupported geometry, style,
    malformed inputs or exhausted proof budgets raise the dedicated error;
    this is a failed sufficient proof, not permission to discard a clip.
    """
    def reject(reason):
        raise PdfTangentStrokeProofError(reason)

    def number(value):
        try:
            result = _number(value)
        except PdfStrokeBoundsError as error:
            raise PdfTangentStrokeProofError("nonfinite_or_nonnumeric") from error
        if max(abs(result.numerator).bit_length(), result.denominator.bit_length()) > 4096:
            reject("rational_input_bit_budget")
        return result

    def point(value):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            reject("point_arity")
        return tuple(number(v) for v in value)

    def difference(a, b):
        return a[0]-b[0], a[1]-b[1]

    def dot(a, b):
        return a[0]*b[0]+a[1]*b[1]

    def exact_string(value):
        # Proof intermediates can have larger denominators than inputs.
        # Respect both this receipt budget and Python's host-controlled
        # integer formatting limit; never change process-wide limits.
        if max(abs(value.numerator).bit_length(), value.denominator.bit_length()) > 16384:
            reject("exact_receipt_bit_budget")
        try:
            return str(value)
        except ValueError as error:
            raise PdfTangentStrokeProofError("exact_receipt_format_limit") from error

    def strings(values):
        return [exact_string(v) for v in values]

    for label, value, maximum in (("command", command_budget, 256),
                                  ("segment", segment_budget, 128),
                                  ("subpath", subpath_budget, 32)):
        if type(value) is not int or not 1 <= value <= maximum:
            reject("invalid_"+label+"_budget")
    if not isinstance(commands, (list, tuple)) or not 1 <= len(commands) <= command_budget:
        reject("command_budget")
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        reject("matrix_arity")
    matrix = tuple(number(v) for v in matrix)
    a, b, c, d, _, _ = matrix
    squared = a*a+b*b
    determinant = a*d-b*c
    if not determinant or squared <= 0 or squared != c*c+d*d or a*c+b*d != 0:
        reject("not_exact_nondegenerate_similarity")
    width, miter_limit = number(width), number(miter_limit)
    if width <= 0 or miter_limit < 1:
        reject("width_or_miter_domain")
    if linecap not in ("butt", "round"):
        reject("cap_requires_larger_support")
    if linejoin != "miter" or dasharray != "none" or fill != "none":
        reject("style_outside_domain")

    controls, subpaths, segments = [], [], []
    current = start = None
    closed = False
    segment_count = empty_moves = 0

    def finish():
        nonlocal segments, current, start, closed, empty_moves
        if start is None:
            return
        if closed and not segments:
            reject("empty_closed_subpath")
        if segments:
            subpaths.append((segments, closed))
        else:
            # Keep move-only positions in the control hull. They do not
            # connect neighbouring drawable subpaths or hide an empty Z.
            empty_moves += 1
        if len(subpaths)+empty_moves > subpath_budget:
            reject("subpath_budget")
        segments, current, start, closed = [], None, None, False

    def add(kind, points, index, implicit_close=False):
        nonlocal segment_count, current
        if segment_count >= segment_budget:
            reject("segment_budget")
        regularity = None
        if kind == "L":
            incoming = outgoing = difference(points[-1], points[0])
            if dot(incoming, incoming) == 0:
                reject("zero_line_segment")
        else:
            vectors = [difference(points[k+1], points[k]) for k in range(3)]
            incoming, outgoing = vectors[0], vectors[-1]
            if dot(incoming, incoming) == 0 or dot(outgoing, outgoing) == 0:
                reject("zero_endpoint_derivative")
            witness = tuple(sum(v[k] for v in vectors) for k in range(2))
            projections = [dot(witness, v) for v in vectors]
            if not all(v > 0 for v in projections):
                reject("cubic_regular_halfplane_not_proven")
            regularity = {
                "method": "strict_positive_Bernstein_derivative_projection",
                "witness_exact": strings(witness),
                "derivative_control_vectors_exact": [strings(v) for v in vectors],
                "positive_projection_coefficients_exact": strings(projections),
            }
        segments.append({"kind": kind, "start": points[0], "end": points[-1],
                         "incoming": incoming, "outgoing": outgoing,
                         "source_command_index": index, "implicit_close": implicit_close,
                         "regularity": regularity})
        segment_count += 1
        current = points[-1]

    for index, command in enumerate(commands):
        if not isinstance(command, (list, tuple)) or not command:
            reject("command_shape")
        op = command[0]
        if not isinstance(op, str):
            reject("command_kind_type")
        arity = {"M": 2, "L": 2, "C": 4, "Z": 1}.get(op)
        if arity is None or len(command) != arity:
            reject("command_arity_or_kind")
        points = [point(p) for p in command[1:]]
        controls.extend(points)
        if op == "M":
            finish()
            start = current = points[0]
        elif op == "Z":
            if current is None or closed:
                reject("invalid_close")
            if current != start:
                add("L", [current, start], index, implicit_close=True)
            closed, current = True, start
        else:
            if current is None or closed:
                reject("draw_after_close_or_no_move")
            add(op, [current, *points], index)
    finish()
    if not segment_count:
        reject("no_drawable_segments")

    joins, subpath_receipts = [], []
    for subpath_index, (segments, closed) in enumerate(subpaths):
        pairs = list(zip(segments, segments[1:]))
        if closed:
            pairs.append((segments[-1], segments[0]))
        for join_index, (previous, following) in enumerate(pairs):
            if previous["end"] != following["start"]:
                reject("disconnected_join")
            u, v = previous["outgoing"], following["incoming"]
            cross, product = u[0]*v[1]-u[1]*v[0], dot(u, v)
            if cross != 0:
                reject("join_not_exactly_tangent")
            if product <= 0:
                reject("reverse_or_cusp_join")
            joins.append({
                "subpath": subpath_index, "join": join_index,
                "closure_join": closed and join_index == len(pairs)-1,
                "previous_source_command_index": previous["source_command_index"],
                "following_source_command_index": following["source_command_index"],
                "point_exact": strings(previous["end"]),
                "incoming_direction_exact": strings(u), "outgoing_direction_exact": strings(v),
                "cross_exact": exact_string(cross), "dot_exact": exact_string(product),
            })
        subpath_receipts.append({"subpath": subpath_index, "closed": closed, "segments": [
            {"kind": s["kind"], "start_exact": strings(s["start"]),
             "end_exact": strings(s["end"]), "source_command_index": s["source_command_index"],
             "implicit_close": s["implicit_close"], "regularity": s["regularity"]}
            for s in segments]})

    radius_squared = width*width*squared/4
    radius = _sqrt_upper(radius_squared)
    xs, ys = [p[0] for p in controls], [p[1] for p in controls]
    hull = min(xs), min(ys), max(xs), max(ys)
    bounds = hull[0]-radius, hull[1]-radius, hull[2]+radius, hull[3]+radius

    def outward(value, upper):
        try:
            result = float(value)
        except (OverflowError, ValueError) as error:
            raise PdfTangentStrokeProofError("support_output_overflow") from error
        if not math.isfinite(result):
            reject("support_output_nonfinite")
        if (Fraction(result) < value if upper else Fraction(result) > value):
            result = math.nextafter(result, math.inf if upper else -math.inf)
        if not math.isfinite(result):
            reject("support_output_nonfinite")
        return result

    return {
        "status": "proven", "method": "exact_regular_same_direction_tangent_support_v1",
        "command_count": len(commands), "segment_count": segment_count,
        "subpath_count": len(subpaths), "empty_move_only_subpaths": empty_moves,
        "closed_subpaths": sum(closed for _, closed in subpaths),
        "budgets": {"commands": command_budget, "segments": segment_budget, "subpaths": subpath_budget},
        "joins": joins, "subpaths": subpath_receipts,
        "source_matrix_exact": strings(matrix), "determinant_exact": exact_string(determinant),
        "similarity_norm_squared_exact": exact_string(squared), "width_exact": exact_string(width),
        "miter_limit_exact": exact_string(miter_limit), "linecap": linecap, "linejoin": linejoin,
        "dasharray": dasharray, "fill": fill,
        "radius_squared_exact": exact_string(radius_squared), "radius_upper_exact": exact_string(radius),
        "radius_upper_float": outward(radius, True),
        "axis_support_exact_rationals": [exact_string(radius), exact_string(radius)],
        "centerline_control_hull_exact": strings(hull), "support_bounds_exact": strings(bounds),
        "support_bounds_outward": [outward(v, index >= 2) for index, v in enumerate(bounds)],
        "source_commands_changed": False, "source_affine_applied_again": False,
        "scope": "declared stroke geometric support only; existing clip/group/mask admission remains required",
    }
