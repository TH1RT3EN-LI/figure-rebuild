"""Exact fill clipping when only straight segments cross convex clip planes.

This explicit geometry API does not infer PDF paint identity, styles or context.
Cubic control hulls must lie wholly on one side of each plane. No curve is
flattened, subdivided or solved at an approximate intersection.
"""
from fractions import Fraction
import hashlib
import math


class UnsupportedConvexClipError(ValueError):
    """The input or clipping work is outside the supported subset."""


class _Meter:
    def __init__(self, maximum, bits):
        self.maximum, self.bits, self.used = maximum, bits, 0

    def take(self):
        self.used += 1
        if self.used > self.maximum:
            raise UnsupportedConvexClipError('Clipping operation budget exceeded')

    def checked(self, value):
        self.take()
        if max(abs(value.numerator).bit_length(), value.denominator.bit_length()) > self.bits:
            raise UnsupportedConvexClipError('Clipping rational size budget exceeded')
        return value

    def number(self, value):
        if type(value) not in (int, float, Fraction) or (type(value) is float and not math.isfinite(value)):
            raise UnsupportedConvexClipError('Expected finite built-in geometry numbers')
        return self.checked(Fraction(value))

    def add(self, a, b): return self.checked(a+b)
    def sub(self, a, b): return self.checked(a-b)
    def mul(self, a, b): return self.checked(a*b)
    def div(self, a, b): return self.checked(a/b)


def _append(items, item, maximum):
    if len(items) >= maximum:
        raise UnsupportedConvexClipError('Clipping command budget exceeded')
    items.append(item)


def _parse(commands, meter, maximum):
    if type(commands) not in (tuple, list) or len(commands) < 1:
        raise UnsupportedConvexClipError('Invalid source command count')
    if len(commands) > maximum:
        raise UnsupportedConvexClipError('Clipping command budget exceeded')
    contours, edges = [], []
    start = last = None
    empty_moves = 0

    def finish():
        nonlocal edges, start, last, empty_moves
        if start is None:
            return
        if edges:
            if last != start:
                _append(edges, ('L', (last, start)), maximum)
            contours.append(edges)
        else:
            empty_moves += 1
        edges, start, last = [], None, None

    for command in commands:
        meter.take()
        if type(command) not in (list, tuple) or not command or type(command[0]) is not str:
            raise UnsupportedConvexClipError('Invalid source command')
        op = command[0]
        if op not in ('M', 'L', 'C', 'Z') or len(command) != {'M':2, 'L':2, 'C':4, 'Z':1}[op]:
            raise UnsupportedConvexClipError('Unsupported source command')
        points = []
        for point in command[1:]:
            if type(point) not in (list, tuple) or len(point) != 2:
                raise UnsupportedConvexClipError('Expected two-coordinate points')
            points.append(tuple(meter.number(value) for value in point))
        if op == 'M':
            finish(); start = last = points[0]
        elif start is None:
            raise UnsupportedConvexClipError('Source command without a move')
        elif op == 'Z':
            finish()
        else:
            _append(edges, (op, (last, *points)), maximum); last = points[-1]
    finish()
    if len(contours) > 1:
        raise UnsupportedConvexClipError('Only one drawable source contour is supported')
    return contours[0] if contours else [], empty_moves


def _half(plane, point, meter):
    a,b = plane
    return meter.sub(meter.mul(meter.sub(b[0], a[0]), meter.sub(point[1], a[1])),
                     meter.mul(meter.sub(b[1], a[1]), meter.sub(point[0], a[0])))


def _convex(commands, meter):
    edges, empty = _parse(commands, meter, 128)
    if not 3 <= len(edges) <= 128 or any(kind != 'L' or p[0] == p[-1] for kind,p in edges):
        raise UnsupportedConvexClipError('Clip must be one nondegenerate linear polygon')
    if len({p[0]for _,p in edges}) != len(edges):
        raise UnsupportedConvexClipError('Clip vertices must be distinct; repeated winding is unsupported')
    area = Fraction(0)
    for _,(a,b) in edges:
        area = meter.add(area, meter.sub(meter.mul(a[0], b[1]), meter.mul(b[0], a[1])))
    if not area:
        raise UnsupportedConvexClipError('Degenerate clip polygon')
    sign = 1 if area > 0 else -1
    # Every vertex must lie in every oriented edge halfplane. Local turns alone
    # would admit a multiply-wound star instead of a convex polygon.
    for _,plane in edges:
        if any(meter.mul(_half(plane, p[0], meter), sign) < 0 for _,p in edges):
            raise UnsupportedConvexClipError('Clip polygon is not convex')
    return edges, sign, empty


def clip_fill_at_convex_line_boundaries(commands, clip_paths, *, fill_rule,
                                        max_operations=200_000, max_commands=4096,
                                        max_integer_bits=2048):
    """Intersect one filled contour with an explicit convex clip chain.

    Inputs use root-coordinate M/L/C/Z tuples, not local glyph coordinates.
    Clip paths are ``{'commands': ..., 'fill_rule': 'nonzero'|'evenodd'}``.
    Output coordinates remain exact Fractions. Caller validates all source
    paint/style/context/ownership facts and performs final native output review.
    An empty result proves geometric fill intersection only, not antialiasing
    equivalence. Unsupported crossings and exhausted budgets raise unchanged.
    """
    for name,value,ceiling in [('max_operations', max_operations, 4_000_000),
                               ('max_commands', max_commands, 10_000),
                               ('max_integer_bits', max_integer_bits, 4096)]:
        if type(value) is not int or not 1 <= value <= ceiling:
            raise UnsupportedConvexClipError('Invalid '+name)
    if type(fill_rule) is not str or fill_rule not in ('nonzero', 'evenodd'):
        raise UnsupportedConvexClipError('Invalid source fill rule')
    if type(clip_paths) not in (tuple, list) or not 1 <= len(clip_paths) <= 16:
        raise UnsupportedConvexClipError('Invalid clip chain count')
    meter = _Meter(max_operations, max_integer_bits)
    source, source_empty = _parse(commands, meter, max_commands)
    clips = []
    # Validate every clip before using any empty/intersection result.
    for clip in clip_paths:
        if (type(clip) is not dict or set(clip) != {'commands', 'fill_rule'}
                or type(clip['fill_rule']) is not str or clip['fill_rule'] not in ('nonzero', 'evenodd')):
            raise UnsupportedConvexClipError('Invalid clip definition')
        clips.append(_convex(clip['commands'], meter))
    initial = [('M', source[0][1][0])] + [(kind,*p[1:])for kind,p in source] + [('Z',)] if source else []
    edges = source
    stages, crossings = [], 0
    for clip_index,(planes,sign,empty) in enumerate(clips):
        for plane_index,(_,plane) in enumerate(planes):
            kept = []
            stage_crossings = inside_curves = outside_curves = 0
            for kind,points in edges:
                meter.take()
                distances = [meter.mul(_half(plane, p, meter), sign) for p in points]
                if min(distances) >= 0:
                    _append(kept, (kind, points), max_commands)
                    inside_curves += kind == 'C'
                elif max(distances) <= 0:
                    outside_curves += kind == 'C'
                elif kind == 'C':
                    raise UnsupportedConvexClipError('Cubic control hull crosses a clip plane')
                else:
                    p,q = points
                    fraction = meter.div(distances[0], meter.sub(distances[0], distances[1]))
                    intersection = tuple(meter.add(a, meter.mul(fraction, meter.sub(b, a))) for a,b in zip(p,q))
                    _append(kept, ('L', (p, intersection) if distances[0] > 0 else (intersection, q)), max_commands)
                    stage_crossings += 1
            edges = []
            for index,current in enumerate(kept):
                meter.take()
                previous = kept[index-1]
                if previous[1][-1] != current[1][0]:
                    _append(edges, ('L', (previous[1][-1], current[1][0])), max_commands)
                _append(edges, current, max_commands)
            crossings += stage_crossings
            stages.append({'clip_index':clip_index, 'plane_index':plane_index,
                           'line_crossings':stage_crossings, 'inside_cubic_control_hulls':inside_curves,
                           'outside_cubic_control_hulls':outside_curves, 'output_edges':len(edges)})
    output = [('M', edges[0][1][0])] + [(kind,*p[1:])for kind,p in edges] + [('Z',)] if edges else []
    if len(output) > max_commands:
        raise UnsupportedConvexClipError('Clipping command budget exceeded')
    def digest(path):
        value = hashlib.sha256(b'exact-root-command-stream-v1\0')
        for command in path:
            meter.take(); value.update(command[0].encode('ascii')+b'\0')
            for point in command[1:]:
                for number in point:
                    meter.take(); value.update(str(number).encode('ascii')+b'\0')
            value.update(b';')
        return value.hexdigest()
    source_digest = digest(initial)
    clip_digests = [digest([('M',p[0][1][0])]+[(k,*v[1:])for k,v in p]+[('Z',)])for p,s,e in clips]
    return {'commands':output, 'provenance':{
        'method':'exact_convex_halfplane_fill_clipping_at_line_crossings_only',
        'predicate_arithmetic':'exact_rationals_of_supplied_root_coordinates',
        'source_geometry_sha256':source_digest, 'source_fill_rule':fill_rule,
        'clip_geometry_sha256':clip_digests, 'geometry_hash_encoding':'exact-root-command-stream-v1',
        'clip_count':len(clips), 'clip_fill_rules':[c['fill_rule']for c in clip_paths],
        'source_empty_move_subpaths':source_empty, 'clip_empty_move_subpaths':[c[2]for c in clips],
        'source_cubic_count':sum(k == 'C'for k,p in source),
        'output_cubic_count':sum(k == 'C'for k,p in edges),
        'line_crossings':crossings, 'stages':stages, 'curves_flattened':False,
        'cubic_intersections_approximated':False, 'geometric_fill_intersection_empty':not bool(output),
        'operations_used':meter.used, 'max_operations':max_operations,
        'max_commands':max_commands, 'max_integer_bits':max_integer_bits,
        'operation_unit':'visited commands/segments/encoded coordinates plus stored exact numbers and binary rational operations',
        'caller_preconditions':'one source-bound filled paint; validated style, context and complete actual clip chain; no stroke, mask or compositing authorization',
        'raster_equivalence_proved':False}}
