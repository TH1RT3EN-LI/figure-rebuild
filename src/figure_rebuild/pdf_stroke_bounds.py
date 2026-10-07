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


class PdfCurveStrokeProofError(PdfStrokeBoundsError):
    """No bounded curve certificate; callers must retain the source clip."""


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


def prove_bounded_curve_stroke_support(commands, matrix, width, miter_limit=4, *,
                                      linecap="butt", linejoin="miter",
                                      dasharray="none", fill="none",
                                      command_budget=256, segment_budget=128,
                                      subpath_budget=32, subdivision_budget=4096,
                                      bounding_depth=4):
    """Bound regular cubic strokes and their joins without changing geometry.

    Commands are ALREADY in output coordinates, as in the tangent interface.
    The exact similarity matrix only scales the declared stroke width. A
    bounded dyadic de Casteljau tree tightens the centerline control hull.
    Each miter is bounded at its actual join point; its radius does not expand
    unrelated curve extrema. The source miter cutoff is part of the proof.

    Positive dash arrays, with any phase, paint a subset of the solid stroke
    tube for butt/round caps. This proves support, not dash placement. Cubic
    derivative hulls need a bounded, exact positive half-plane certificate;
    a failed sufficient proof, a cusp or an exhausted budget raises. Zero
    endpoint derivatives require finite nonzero one-sided tangent limits.

    The receipt is explicit and read-only. It does not admit a source group,
    remove a clip, lower dashes, implement a device hairline or certify pixels.
    """
    def reject(reason):
        raise PdfCurveStrokeProofError(reason)

    def number(value):
        try:
            q = _number(value)
        except PdfStrokeBoundsError as error:
            raise PdfCurveStrokeProofError("nonfinite_or_nonnumeric") from error
        if max(abs(q.numerator).bit_length(), q.denominator.bit_length()) > 4096:
            reject("rational_input_bit_budget")
        return q

    def exact_string(value):
        if max(abs(value.numerator).bit_length(), value.denominator.bit_length()) > 16384:
            reject("exact_receipt_bit_budget")
        try:
            return str(value)
        except ValueError as error:
            raise PdfCurveStrokeProofError("exact_receipt_format_limit") from error

    def strings(values):
        return [exact_string(v) for v in values]

    def point(value):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            reject("point_arity")
        return tuple(number(v) for v in value)

    def sub(a, b):
        return a[0]-b[0], a[1]-b[1]

    def dot(a, b):
        return a[0]*b[0]+a[1]*b[1]

    def midpoint(a, b):
        return (a[0]+b[0])/2, (a[1]+b[1])/2

    def root_upper(value):
        # A dyadic with more than 53 significant bits can still be an exact
        # rational square. Preserve exact boundary contact in that case.
        n, d = math.isqrt(value.numerator), math.isqrt(value.denominator)
        if n*n == value.numerator and d*d == value.denominator:
            return Fraction(n, d)
        return _sqrt_upper(value)

    def box(points):
        return (min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points))

    def union(a, b):
        return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])

    for label, value, maximum in (("command", command_budget, 256),
                                  ("segment", segment_budget, 128),
                                  ("subpath", subpath_budget, 32),
                                  ("subdivision", subdivision_budget, 4096),
                                  ("bounding_depth", bounding_depth, 8)):
        if type(value) is not int or not 1 <= value <= maximum:
            reject("invalid_"+label+"_budget")
    if not isinstance(commands, (list, tuple)) or not 1 <= len(commands) <= command_budget:
        reject("command_budget")
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        reject("matrix_arity")
    matrix = tuple(number(v) for v in matrix)
    a, b, c, d, _, _ = matrix
    squared = a*a+b*b
    if squared <= 0 or squared != c*c+d*d or a*c+b*d != 0:
        reject("not_exact_nondegenerate_similarity")
    width, miter = number(width), number(miter_limit)
    if width <= 0 or miter < 1:
        reject("width_or_miter_domain")
    if fill != "none" or linecap not in ("butt", "round") or linejoin not in ("miter", "round", "bevel"):
        reject("style_outside_domain")
    if dasharray != "none":
        if not isinstance(dasharray, (list, tuple)) or not 1 <= len(dasharray) <= 1024:
            reject("unsupported_dash_array")
        if any(number(v) <= 0 for v in dasharray):
            reject("nonpositive_dash")

    nodes = segment_count = 0
    regularity, empty, subpaths, segments = [], [], [], []
    current = start = None
    closed = False

    def use_node():
        nonlocal nodes
        nodes += 1
        if nodes > subdivision_budget:
            reject("subdivision_budget")

    def finish():
        nonlocal segments, current, start, closed
        if start is None:
            return
        if len(subpaths)+len(empty) >= subpath_budget:
            reject("subpath_budget")
        if segments:
            subpaths.append((segments, closed))
        else:
            if closed or linecap != "butt":
                reject("empty_subpath_cap_or_close_not_proven")
            empty.append({"point_exact": strings(start), "proof": "nonpainting_butt_move_only"})
        segments, current, start, closed = [], None, None, False

    def add(kind, points, index):
        nonlocal current, segment_count
        if segment_count >= segment_budget:
            reject("segment_budget")
        incoming, outgoing = sub(points[1], points[0]), sub(points[-1], points[-2])
        if kind == "L" and dot(incoming, incoming) == 0:
            if linecap != "butt":
                reject("zero_line_cap_not_proven")
            regularity.append({"source_command_index": index,
                               "proof": "exact_zero_butt_line_ignored_in_support_only"})
            return
        if kind == "C":
            vectors = [sub(points[i+1], points[i]) for i in range(3)]
            incoming = next((v for v in vectors if dot(v, v) > 0), None)
            outgoing = next((v for v in reversed(vectors) if dot(v, v) > 0), None)
        if incoming is None or outgoing is None or not dot(incoming, incoming) or not dot(outgoing, outgoing):
            reject("no_finite_nonzero_endpoint_tangent")
        if kind == "C":
            intervals = []

            def certify(vectors, lo, hi, depth=0):
                use_node()
                if ((lo > 0 and not dot(vectors[0], vectors[0])) or
                        (hi < 1 and not dot(vectors[-1], vectors[-1]))):
                    reject("interior_zero_derivative")
                witnesses = [tuple(sum(v[k] for v in vectors) for k in (0, 1)), *vectors]
                for witness in witnesses:
                    projections = [dot(witness, v) for v in vectors]
                    if all(v >= 0 for v in projections) and any(v > 0 for v in projections):
                        intervals.append({"parameter_interval_exact": strings((lo, hi)),
                                          "witness_exact": strings(witness),
                                          "projection_coefficients_exact": strings(projections)})
                        return
                if depth >= 8:
                    reject("cubic_regular_halfplane_not_proven")
                v01, v12 = midpoint(vectors[0], vectors[1]), midpoint(vectors[1], vectors[2])
                vm, t = midpoint(v01, v12), (lo+hi)/2
                certify((vectors[0], v01, vm), lo, t, depth+1)
                certify((vm, v12, vectors[-1]), t, hi, depth+1)

            certify(vectors, Fraction(0), Fraction(1))
            regularity.append({"source_command_index": index,
                               "method": "piecewise_nonnegative_Bernstein_derivative_projection",
                               "intervals": intervals})
        segments.append({"kind": kind, "points": points, "incoming": incoming,
                         "outgoing": outgoing, "source_command_index": index})
        segment_count += 1
        current = points[-1]

    for index, row in enumerate(commands):
        if not isinstance(row, (list, tuple)) or not row or not isinstance(row[0], str):
            reject("command_shape")
        op = row[0]
        if len(row) != {"M": 2, "L": 2, "C": 4, "Z": 1}.get(op):
            reject("command_arity_or_kind")
        points = [point(p) for p in row[1:]]
        if op == "M":
            finish()
            start = current = points[0]
        elif op == "Z":
            if current is None or closed:
                reject("invalid_close")
            if current != start:
                add("L", [current, start], index)
            closed, current = True, start
        else:
            if current is None or closed:
                reject("draw_after_close_or_no_move")
            add(op, [current, *points], index)
    finish()
    if not segment_count:
        reject("no_drawable_segments")

    joins = []
    for subpath_index, (ss, isclosed) in enumerate(subpaths):
        pairs = list(zip(ss, ss[1:]))+([(ss[-1], ss[0])] if isclosed else [])
        for index, (previous, following) in enumerate(pairs):
            if previous["points"][-1] != following["points"][0]:
                reject("disconnected_join")
            u, v = previous["outgoing"], following["incoming"]
            uv, norm_product = dot(u, v), dot(u, u)*dot(v, v)
            cosine_lower = None
            if linejoin in ("round", "bevel") or (u[0]*v[1]-u[1]*v[0] == 0 and uv > 0):
                factor = Fraction(1)
                method = "round_bevel_or_exact_positive_tangent"
            elif uv >= 0:
                cosine_lower = uv/root_upper(norm_product)
                factor = min(miter*miter, 2/(1+cosine_lower))
                method = "positive_dot_lower_cosine_and_source_miter_cutoff"
            else:
                factor = miter*miter
                method = "source_miter_cutoff_conservative_radius"
            radius = root_upper(width*width*squared*factor/4)
            joins.append({"subpath": subpath_index, "join": index,
                          "point_exact": strings(previous["points"][-1]),
                          "dot_exact": exact_string(uv), "norm_product_exact": exact_string(norm_product),
                          "cosine_lower_exact": exact_string(cosine_lower) if cosine_lower is not None else None,
                          "miter_factor_squared_upper_exact": exact_string(factor),
                          "local_radius_exact": exact_string(radius), "method": method})

    def split(p):
        p01, p12, p23 = midpoint(p[0], p[1]), midpoint(p[1], p[2]), midpoint(p[2], p[3])
        p012, p123 = midpoint(p01, p12), midpoint(p12, p23)
        mid = midpoint(p012, p123)
        return (p[0], p01, p012, mid), (mid, p123, p23, p[3])

    def bound(p, depth):
        use_node()
        if not depth:
            return box(p)
        left, right = split(p)
        return union(bound(left, depth-1), bound(right, depth-1))

    hulls, curves = [], []
    for ss, _ in subpaths:
        for segment in ss:
            if segment["kind"] == "C":
                bb = bound(segment["points"], bounding_depth)
                curves.append({"source_command_index": segment["source_command_index"],
                               "exact_dyadic_control_hull_subdivision_depth": bounding_depth,
                               "bounds_exact": strings(bb)})
            else:
                bb = box(segment["points"])
            hulls.append(bb)
    center = (min(b[0] for b in hulls), min(b[1] for b in hulls),
              max(b[2] for b in hulls), max(b[3] for b in hulls))
    radius = root_upper(width*width*squared/4)
    support = center[0]-radius, center[1]-radius, center[2]+radius, center[3]+radius
    for join in joins:
        x, y = map(Fraction, join["point_exact"])
        r = Fraction(join["local_radius_exact"])
        support = union(support, (x-r, y-r, x+r, y+r))

    def outward(value, upper):
        try:
            result = float(value)
        except (OverflowError, ValueError) as error:
            raise PdfCurveStrokeProofError("support_output_overflow") from error
        if not math.isfinite(result):
            reject("support_output_nonfinite")
        if (Fraction(result) < value if upper else Fraction(result) > value):
            result = math.nextafter(result, math.inf if upper else -math.inf)
        if not math.isfinite(result):
            reject("support_output_nonfinite")
        return result

    return {"status": "proven", "method": "exact_regular_curve_tube_and_local_join_support_v1",
            "source_commands_changed": False, "curve_approximation": False,
            "source_affine_applied_again": False, "source_matrix_exact": strings(matrix),
            "linecap": linecap, "linejoin": linejoin,
            "dash_support_phase_independent": dasharray != "none",
            "source_miter_limit_exact": exact_string(miter),
            "centerline_bounds_exact_rationals": strings(center),
            "stroke_bounds_exact_rationals": strings(support),
            "stroke_bounds_outward": [outward(v, i >= 2) for i, v in enumerate(support)],
            "tube_radius_exact": exact_string(radius), "joins": joins,
            "cubic_regularity": regularity, "curve_bounds": curves,
            "nonpainting_empty_butt_subpaths": empty, "subdivision_nodes": nodes,
            "source_segments": segment_count,
            "resource_budgets": {"commands": command_budget, "segments": segment_budget,
                                 "subpaths": subpath_budget, "subdivision_nodes": subdivision_budget,
                                 "bounding_depth": bounding_depth},
            "scope": "declared support only; source clips, groups, masks and native output require separate verification"}
