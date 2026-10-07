"""Bounded exact clipping of one nonzero annular fill by a nested path.

This is geometry only. The caller must separately validate a single paint's
style, group/mask/compositing semantics, the single clip resource, and every
other clip/ROI. It must never combine independent paints before calling this
helper. A receipt proves a set identity over the supplied coordinate values,
not the precision of upstream PDF extraction or downstream raster rendering.
"""
from collections import defaultdict
from fractions import Fraction

from .pdf_fill import (
    _convex_hull, _hulls_separated, _normalize_fill_for_proof, _point,
    prove_evenodd_nonzero_equivalent,
)


class _Unknown(Exception):
    pass


def _edges(commands):
    normalized = _normalize_fill_for_proof(commands)
    if normalized is None:
        raise _Unknown
    proof_commands, receipt = normalized
    edges, cursor, start = [], None, None
    for index, command in enumerate(proof_commands):
        op = command[0]
        if op == "M":
            cursor = start = command[1]
        elif op in ("L", "C"):
            raw = (cursor, *command[1:])
            edges.append({"op": op, "raw": raw, "points": tuple(map(_point, raw)),
                          "proof_command_index": index})
            cursor = command[-1]
        elif op == "Z" and _point(cursor) != _point(start):
            raw = (cursor, start)
            edges.append({"op": "L", "raw": raw, "points": tuple(map(_point, raw)),
                          "proof_command_index": index})
            cursor = start
        elif op == "Z":
            cursor = start
        else:
            raise _Unknown
    if not edges or len(edges) > 256:
        raise _Unknown
    return edges, receipt


def _cycles(edges, cancel_lines):
    lines, removed, cancelled = defaultdict(list), set(), []
    for i, edge in enumerate(edges):
        if edge["op"] == "L":
            lines[edge["points"]].append(i)
    # Repeated edges would change winding multiplicity. Only one exactly
    # reversed occurrence on each side is accepted for cancellation.
    if any(len(indices) != 1 for indices in lines.values()):
        raise _Unknown
    if cancel_lines:
        for points, indices in lines.items():
            reverse = tuple(reversed(points))
            if reverse in lines and indices[0] not in removed:
                i, j = indices[0], lines[reverse][0]
                removed.update((i, j))
                cancelled.append({"edge_indices": [i, j],
                                  "proof_command_indices": [edges[i]["proof_command_index"],
                                                            edges[j]["proof_command_index"]],
                                  "first_endpoints": [list(p) for p in edges[i]["raw"]],
                                  "reverse_endpoints": [list(p) for p in edges[j]["raw"]],
                                  "multiplicity_each_direction": 1})
    outgoing, incoming = {}, {}
    for i, edge in enumerate(edges):
        if i in removed:
            continue
        a, b = edge["points"][0], edge["points"][-1]
        if a == b or a in outgoing or b in incoming:
            raise _Unknown
        outgoing[a], incoming[b] = i, i
    if not outgoing or set(outgoing) != set(incoming):
        raise _Unknown
    remaining, cycles = set(outgoing.values()), []
    while remaining:
        first = min(remaining)
        cycle, index = [], first
        while index in remaining:
            remaining.remove(index)
            cycle.append(edges[index])
            index = outgoing[edges[index]["points"][-1]]
        if index != first:
            raise _Unknown
        cycles.append(cycle)
    return cycles, cancelled


def _commands(cycle, reverse=False):
    ordered = list(reversed(cycle)) if reverse else cycle
    raw = [tuple(reversed(e["raw"])) if reverse else e["raw"] for e in ordered]
    commands = [["M", list(raw[0][0])]]
    commands.extend([e["op"], *[list(p) for p in points[1:]]]
                    for e, points in zip(ordered, raw))
    commands.append(["Z"])
    return commands


def _area(cycle):
    """Signed integral of (x dy - y dx)/2, exactly for L/C segments."""
    area = Fraction(0)
    for edge in cycle:
        s = edge["points"]
        if len(s) == 2:
            polys = [[s[0][i], s[1][i]-s[0][i]] for i in (0, 1)]
        else:
            polys = [[s[0][i], 3*(s[1][i]-s[0][i]),
                      3*(s[2][i]-2*s[1][i]+s[0][i]),
                      s[3][i]-3*s[2][i]+3*s[1][i]-s[0][i]] for i in (0, 1)]
        x, y = polys
        area += sum(x[i]*j*y[j]/(i+j) for i in range(len(x)) for j in range(1, len(y)))
        area -= sum(y[i]*j*x[j]/(i+j) for i in range(len(y)) for j in range(1, len(x)))
    return area/2


def _half(points):
    levels = [points]
    while len(levels[-1]) > 1:
        levels.append([tuple((a[i]+b[i])/2 for i in (0, 1))
                       for a, b in zip(levels[-1], levels[-1][1:])])
    return [row[0] for row in levels], [row[-1] for row in reversed(levels)]


def _size(points):
    return sum(max(p[i] for p in points)-min(p[i] for p in points) for i in (0, 1))


def _separate(first, second, budget):
    pending, leaves, depth_seen = [(first, second, 0)], 0, 0
    while pending:
        a, b, depth = pending.pop()
        budget["remaining"] -= 1
        if budget["remaining"] < 0:
            raise _Unknown
        depth_seen = max(depth_seen, depth)
        if _hulls_separated(_convex_hull(a), _convex_hull(b)):
            leaves += 1
            continue
        if depth >= budget["max_depth"]:
            raise _Unknown
        pairs = [(part, b) for part in _half(a)] if _size(a) >= _size(b) else [
            (a, part) for part in _half(b)]
        pending.extend((x, y, depth+1) for x, y in pairs)
    return leaves, depth_seen


def _winding(cycle, witness, budget):
    """Exact half-open horizontal ray count for y-monotone L/C pieces."""
    count, receipts = 0, []
    for edge_index, edge in enumerate(cycle):
        points = edge["points"]
        ys = [p[1] for p in points]
        steps = [b-a for a, b in zip(ys, ys[1:])]
        if any(v > 0 for v in steps) and any(v < 0 for v in steps):
            raise _Unknown
        if not min(ys[0], ys[-1]) <= witness[1] < max(ys[0], ys[-1]):
            continue
        upward = ys[-1] > ys[0]
        for depth in range(budget["max_depth"]+1):
            budget["remaining"] -= 1
            if budget["remaining"] < 0:
                raise _Unknown
            lo, hi = min(p[0] for p in points), max(p[0] for p in points)
            if hi < witness[0] or lo > witness[0]:
                right = lo > witness[0]
                if right:
                    count += 1 if upward else -1
                receipts.append({"edge_index": edge_index, "depth": depth,
                                 "crosses_right_ray": right, "upward": upward})
                break
            left, right = _half(points)
            mid = left[-1][1]
            points = left if (witness[1] <= mid if upward else witness[1] >= mid) else right
        else:
            raise _Unknown
    return count, receipts


def _inside(inner, outer, budget):
    leaves, depth_seen = 0, 0
    for first in inner:
        for second in outer:
            n, depth = _separate(first["points"], second["points"], budget)
            leaves += n
            depth_seen = max(depth_seen, depth)
    winding, ray = _winding(outer, inner[0]["points"][0], budget)
    if abs(winding) != 1:
        raise _Unknown
    return {"boundary_separation": "strict_exact_control_hull_separation_after_bounded_subdivision",
            "segment_pairs": len(inner)*len(outer), "separation_leaves": leaves,
            "max_subdivision_depth": depth_seen,
            "witness": list(inner[0]["raw"][0]), "outer_winding_at_witness": winding,
            "ray_crossings": ray}


def clip_nonzero_annular_fill(commands, clip_commands, *, source_fill_rule,
                              clip_fill_rule, max_subdivisions=4096, max_depth=24):
    """Return retained boundary commands and proof, or ``None`` if unknown.

    Exactly reversed shared straight edges may cancel within ONE compound
    nonzero fill. Remaining edges must form two oppositely oriented, simple,
    strictly nested cycles. A single simple clip must lie strictly between
    them. Output is the clip boundary and the retained inner boundary with
    opposite orientations. All predicates use exact input-coordinate rationals;
    no curve is approximated. Proof subdivisions never become output geometry.

    Geometry-only preconditions from the module docstring are mandatory caller
    checks. ``None`` never permits omitting a paint or ignoring a clip.
    """
    if source_fill_rule != "nonzero" or clip_fill_rule != "nonzero":
        return None
    if (type(max_subdivisions) is not int or not 1 <= max_subdivisions <= 65536
            or type(max_depth) is not int or not 0 <= max_depth <= 64):
        return None
    if any(not isinstance(c, (list, tuple)) or len(c) > 2048 for c in (commands, clip_commands)):
        return None
    try:
        source_edges, source_normalization = _edges(commands)
        clip_edges, clip_normalization = _edges(clip_commands)
        source_cycles, cancellations = _cycles(source_edges, True)
        clip_cycles, _ = _cycles(clip_edges, False)
        if len(source_cycles) != 2 or len(clip_cycles) != 1:
            return None
        proofs = [prove_evenodd_nonzero_equivalent(_commands(c))
                  for c in [*source_cycles, *clip_cycles]]
        if not all(proofs):
            return None
        areas = [_area(c) for c in source_cycles]
        if areas[0]*areas[1] >= 0:
            return None
        outer_index = max(range(2), key=lambda i: abs(areas[i]))
        outer, inner = source_cycles[outer_index], source_cycles[1-outer_index]
        clip = clip_cycles[0]
        clip_area = _area(clip)
        if not clip_area:
            return None
        budget = {"remaining": max_subdivisions, "max_depth": max_depth}
        relations = {"inner_inside_outer": _inside(inner, outer, budget),
                     "inner_inside_clip": _inside(inner, clip, budget),
                     "clip_inside_outer": _inside(clip, outer, budget)}
        reverse_inner = areas[1-outer_index]*clip_area > 0
        output = _commands(clip)+_commands(inner, reverse_inner)
        return {"commands": output, "proof": {
            "proof": "exact_shared_line_cancellation_and_strictly_nested_annular_clip",
            "source_fill_rule": "nonzero", "clip_fill_rule": "nonzero",
            "output_fill_rule": "nonzero",
            "predicate_arithmetic": "exact_rationals_of_supplied_input_coordinates",
            "precision_scope": "does_not_certify_upstream_PDF_extraction_or_downstream_raster_precision",
            "caller_preconditions": "one_fill_paint_neutral_validated_effect_context_single_clip_other_clips_proved_noop",
            "source_commands_changed": True, "source_input_mutated": False,
            "original_boundary_coordinates_changed": False, "derived_boundary_selection": True,
            "curve_approximation": False, "proof_subdivisions_in_output": False,
            "shared_line_cancellations": cancellations,
            "source_fill_normalization": source_normalization,
            "clip_fill_normalization": clip_normalization,
            "cycle_simplicity_proofs": proofs, "source_outer_cycle_index": outer_index,
            "source_signed_areas": [str(a) for a in areas],
            "clip_signed_area": str(clip_area), "inner_direction_reversed": reverse_inner,
            "nesting_certificates": relations,
            "budget": {"max_subdivisions": max_subdivisions, "max_depth": max_depth,
                       "exact_hull_or_ray_checks_used": max_subdivisions-budget["remaining"]}}}
    except (_Unknown, ValueError, TypeError, OverflowError, IndexError, KeyError):
        return None
