"""Bounded dash placement with exact line/cubic subsegments, using only stdlib.

Subdivision is used to bound arc length, never to flatten emitted geometry.
For each cubic, chord length is a lower bound and control-polygon length an
upper bound (in real arithmetic). A conservative floating-point allowance is
reported separately. Every emitted cubic is a de Casteljau subcurve.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import heapq
import math
from typing import Sequence


class PdfDashError(ValueError):
    """A dash cannot be lowered within the declared geometry/error budget."""


@dataclass(frozen=True)
class DashResult:
    commands: tuple
    provenance: dict


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PdfDashError("Dash geometry and lengths require finite numbers")
    return float(value)


def _point(point):
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        raise PdfDashError("Expected a two-coordinate point")
    return tuple(_finite(v) for v in point)


def _lerp(a, b, t):
    return tuple((1-t)*x+t*y for x, y in zip(a, b))


def _split(points, t):
    a, b, c = (_lerp(points[i], points[i+1], t) for i in range(3))
    d, e = _lerp(a, b, t), _lerp(b, c, t)
    f = _lerp(d, e, t)
    return (points[0], a, d, f), (f, e, c, points[3])


def _portion(points, start, end):
    if len(points) == 2:
        return (_lerp(points[0], points[1], start), _lerp(points[0], points[1], end))
    if start == 0 and end == 1: return points
    left = points if end == 1 else _split(points, end)[0]
    return left if start == 0 else _split(left, start/end)[1]


def _length_bounds(points):
    chord = math.dist(points[0], points[-1])
    polygon = math.fsum(math.dist(a, b) for a, b in zip(points, points[1:]))
    return min(chord, polygon), max(chord, polygon)


class _ArcTable:
    def __init__(self, points, budget, leaf_limit):
        self.points = points
        self.depth = 0
        self.exact_length = False
        if len(points) == 2:
            self.lower = self.upper = self.length = math.dist(*points)
            if self.length == 0: raise PdfDashError("Zero-length source segments are unsupported (cap semantics may differ)")
            delta = [Fraction(b)-Fraction(a) for a,b in zip(points[0],points[1])]
            self.exact_length = Fraction(self.length)**2 == sum(v*v for v in delta)
            self.leaves = ()
            return
        low, high = _length_bounds(points)
        if high == 0: raise PdfDashError("Degenerate zero-length cubic is unsupported")
        # Heap entries: negative gap, unique serial, t0, t1, controls, bounds, depth.
        heap = [(-(high-low), 0, 0., 1., points, low, high, 0)]
        gap, serial = high-low, 1
        while True:
            if gap <= 2*budget:
                gap = math.fsum(item[6]-item[5] for item in heap)
                if gap <= 2*budget: break
            if len(heap) >= leaf_limit: raise PdfDashError("Cubic arc-length subdivision budget exceeded")
            _, _, t0, t1, curve, lo, hi, depth = heapq.heappop(heap)
            if depth >= 32: raise PdfDashError("Cubic arc-length bound did not converge")
            left, right = _split(curve, .5)
            mid = (t0+t1)/2
            children = []
            for a,b,child in ((t0,mid,left),(mid,t1,right)):
                lower, upper = _length_bounds(child)
                children.append((-(upper-lower),serial,a,b,child,lower,upper,depth+1))
                serial += 1
            gap += math.fsum(row[6]-row[5] for row in children)-(hi-lo)
            for child in children: heapq.heappush(heap,child)
            self.depth = max(self.depth,depth+1)
        self.leaves = sorted(heap,key=lambda item:item[2])
        self.ends = [row[3] for row in self.leaves]
        self.prefix_low, self.prefix_high = [0.], [0.]
        for row in self.leaves:
            self.prefix_low.append(math.fsum((self.prefix_low[-1],row[5])))
            self.prefix_high.append(math.fsum((self.prefix_high[-1],row[6])))
        self.lower, self.upper = self.prefix_low[-1], self.prefix_high[-1]
        self.length = (self.lower+self.upper)/2
        if not self.length > 0: raise PdfDashError("Degenerate cubic arc length")

    def prefix_bounds(self,t):
        if t <= 0: return 0.,0.
        if t >= 1: return self.lower,self.upper
        if not self.leaves:
            value = self.length*t
            return value,value
        index = bisect_right(self.ends,t)
        row = self.leaves[index]
        a,b,curve = row[2],row[3],row[4]
        left = _split(curve,(t-a)/(b-a))[0]
        low,high = _length_bounds(left)
        return self.prefix_low[index]+low,self.prefix_high[index]+high

    def inverse(self,distance,tolerance):
        if distance <= 0: return 0.,abs(distance)
        if distance >= self.length:
            return 1.,max(abs(self.lower-distance),abs(self.upper-distance))
        if not self.leaves: return distance/self.length,0.
        lo,hi=0.,1.
        for _ in range(64):
            t=(lo+hi)/2
            lower,upper=self.prefix_bounds(t)
            error=max(abs(lower-distance),abs(upper-distance))
            if error <= tolerance: return t,error
            if upper < distance: lo=t
            elif lower > distance: hi=t
            else:
                raise PdfDashError("Arc-length interval is too wide to invert within tolerance")
            if math.nextafter(lo,hi) >= hi: break
        raise PdfDashError("Arc-length inversion did not converge within tolerance")


def _subpaths(commands):
    result,segments=[],[]
    current=first=None
    def finish(closed):
        nonlocal segments,current,first
        if first is not None:
            if not segments: raise PdfDashError("Empty/degenerate dashed subpath is unsupported")
            result.append((tuple(segments),closed))
        segments=[];current=first=None
    for command in commands:
        if not isinstance(command,(tuple,list)) or not command: raise PdfDashError("Invalid source path command")
        op,*raw=command
        points=tuple(_point(point) for point in raw)
        if op=='M' and len(points)==1:
            finish(False);current=first=points[0]
        elif op in ('L','C') and len(points)==(1 if op=='L' else 3) and current is not None:
            segment=(current,*points)
            if all(point==current for point in segment): raise PdfDashError("Zero-length source segment is unsupported")
            segments.append(segment);current=points[-1]
        elif op=='Z' and not points and current is not None:
            if current!=first: segments.append((current,first))
            finish(True)
        else: raise PdfDashError("Dashing accepts normalized M/L/C/Z subpaths only")
    finish(False)
    if not result: raise PdfDashError("No drawable dashed subpaths")
    return result


def _run_commands(pieces,closed=False):
    commands=[('M',pieces[0][0])]
    for piece in pieces:
        if len(piece)==2: commands.append(('L',piece[-1]))
        else: commands.append(('C',*piece[1:]))
    if closed: commands.append(('Z',))
    return commands


def lower_dashes(commands: Sequence, pattern: Sequence[float], phase: float = 0., *,
                 tolerance: float = 1e-4, max_output_commands: int = 200000,
                 max_arc_leaves: int = 50000) -> DashResult:
    """Return one compound native stroke path and auditable dash placement.

    Lengths and ``tolerance`` use the command coordinate system. Positive phase
    consumes that distance into the repeated pattern at each subpath start;
    negative phase is reduced modulo the cycle. Odd arrays repeat twice.
    Zero/negative entries and degenerate segments fail closed. Clips must be
    applied only after this operation; this function does not clip geometry.
    Contiguous on-pieces remain one subpath, including a closed path's seam.
    """
    pattern=tuple(_finite(v) for v in pattern)
    phase,tolerance=_finite(phase),_finite(tolerance)
    if not pattern or any(v<=0 for v in pattern): raise PdfDashError("Dash array entries must be strictly positive; zero-dot/degenerate patterns unsupported")
    if tolerance <= 0: raise PdfDashError("Dash tolerance must be positive")
    if not isinstance(max_output_commands,int) or max_output_commands<2 or not isinstance(max_arc_leaves,int) or max_arc_leaves<2:
        raise PdfDashError("Invalid dash resource budget")
    normalized=pattern if len(pattern)%2==0 else pattern*2
    cycle=math.fsum(normalized)
    if not math.isfinite(cycle): raise PdfDashError("Nonfinite dash cycle")
    phase=phase%cycle
    subpaths=_subpaths(commands)
    total_segments=sum(len(segments) for segments,_ in subpaths)
    if total_segments>max_output_commands: raise PdfDashError("Source path exceeds dash command budget")
    budget=tolerance/(8*total_segments)
    all_commands,subpath_reports=[],[]
    global_inverse_error=0.
    floating_snaps=[]
    total_leaves=0
    max_depth=0
    coordinate_scale=max(1.,*(abs(v) for segments,_ in subpaths for segment in segments for point in segment for v in point))
    for subpath_index,(segments,closed) in enumerate(subpaths):
        models=[_ArcTable(segment,budget,max_arc_leaves) for segment in segments]
        total_leaves+=sum(max(1,len(model.leaves)) for model in models)
        max_depth=max(max_depth,*(model.depth for model in models))
        if total_leaves>max_arc_leaves: raise PdfDashError("Total arc-length subdivision budget exceeded")
        length=math.fsum(model.length for model in models)
        low=math.fsum(model.lower for model in models)
        high=math.fsum(model.upper for model in models)
        prefix_error=(high-low)/2
        index,offset=0,phase
        while offset>=normalized[index]:
            offset-=normalized[index];index=(index+1)%len(normalized)
        remaining=normalized[index]-offset
        runs=[];active=[];active_intervals=[];subdistance=0.
        starts_on=(index%2==0)
        starts_at_zero=starts_on
        max_inverse_error=0.
        events=0
        def end_run():
            nonlocal active,active_intervals
            if active:
                runs.append({'pieces':active,'intervals':active_intervals,'closed':False})
                active=[];active_intervals=[]
        cumulative_length_error = 0.
        for segment_index,(segment,model) in enumerate(zip(segments,models)):
            cumulative_length_error += (model.upper-model.lower)/2
            if not model.exact_length:
                cumulative_length_error += 64*math.ulp(max(1., model.length))
            distance,t0,t0_error=0.,0.,0.
            while distance < model.length:
                available=model.length-distance
                snap=64*math.ulp(max(1.,model.length,remaining,available,coordinate_scale))
                boundary_difference=abs(remaining-available)
                snapped_boundary=boundary_difference==0
                if 0 < boundary_difference <= snap:
                    raise PdfDashError("Nonzero dash boundary uncertainty may change cap/join topology")
                if (closed or segment_index < len(segments)-1) and cumulative_length_error > 0 and boundary_difference <= cumulative_length_error:
                    raise PdfDashError("Dash transition at an uncertain segment junction may change cap/join topology")
                if snapped_boundary:
                    floating_snaps.append(0.)
                take=available if snapped_boundary else min(remaining,available)
                if take<=0 or distance+take==distance: raise PdfDashError("Dash entry vanishes at floating-point resolution")
                endpoint=distance+take
                at_end=(take==available)
                t1,error=(1.,(model.upper-model.lower)/2) if at_end else model.inverse(endpoint,tolerance/2)
                max_inverse_error=max(max_inverse_error,error+prefix_error)
                if index%2==0:
                    piece=_portion(segment,t0,t1)
                    if all(point==piece[0] for point in piece):
                        raise PdfDashError("Dash segment vanishes at coordinate precision")
                    if active:
                        # The independent de Casteljau arithmetic may round a
                        # shared endpoint differently. Use the previous exact
                        # stored endpoint; the FP allowance covers this ULP drift.
                        piece=(active[-1][-1],*piece[1:])
                    active.append(piece)
                    active_intervals.append({'segment_index':segment_index,'t0':t0,'t1':t1,
                                             'arc_start_estimate':subdistance+distance,
                                             'arc_end_estimate':subdistance+endpoint,
                                             'arc_position_error_upper':max(t0_error,error)+prefix_error,
                                             'arc_start_error_upper':t0_error+prefix_error,
                                             'arc_end_error_upper':error+prefix_error})
                distance=model.length if at_end else endpoint
                t0,t0_error=t1,error
                remaining=0. if snapped_boundary else remaining-take
                if remaining<=0:
                    if index%2==0: end_run()
                    index=(index+1)%len(normalized);remaining=normalized[index]
                    events+=1
                    if events+len(all_commands)>max_output_commands: raise PdfDashError("Dash output command budget exceeded")
            subdistance+=model.length
        end_run()
        # If a numerically bounded closed length straddles a dash transition,
        # we cannot decide whether the seam is a cap or a join. Do not guess.
        seam_margin=min(remaining,normalized[index]-remaining)
        if closed and prefix_error>0 and seam_margin<=prefix_error:
            raise PdfDashError("Closed-path seam coincides with an uncertain numerical dash boundary")
        ends_at_length=bool(runs and runs[-1]['intervals'][-1]['arc_end_estimate']==subdistance)
        if closed and starts_at_zero and ends_at_length:
            if len(runs)==1:
                runs[0]['closed']=True
            else:
                last=runs.pop()
                first=runs.pop(0)
                last['pieces'].extend(first['pieces'])
                last['intervals'].extend(first['intervals'])
                last['seam_join']=True
                runs.insert(0,last)
        run_reports=[]
        for run in runs:
            run_commands=_run_commands(run['pieces'],run['closed'])
            if len(all_commands)+len(run_commands)>max_output_commands: raise PdfDashError("Dash output command budget exceeded")
            all_commands.extend(run_commands)
            run_reports.append({'closed':run['closed'],'joins_closed_seam':run.get('seam_join',False),
                                'source_intervals':run['intervals']})
        global_inverse_error=max(global_inverse_error,max_inverse_error)
        subpath_reports.append({'subpath_index':subpath_index,'closed_source':closed,'length_estimate':length,
                                'length_lower':low,'length_upper':high,'length_error_upper':prefix_error,
                                'dash_runs':run_reports})
    # This explicit conservative allowance covers binary64 arithmetic in the
    # length tree, de Casteljau splits and accumulated path distances. If it is
    # material to the requested budget the operation fails instead of claiming
    # the real-arithmetic chord/polygon bound is a machine interval proof.
    float_allowance=512*math.ulp(coordinate_scale)*max(1,total_leaves+len(all_commands))*max(1,max_depth)
    error=global_inverse_error+float_allowance
    if error>tolerance: raise PdfDashError("Arc placement plus floating-point allowance exceeds dash tolerance")
    return DashResult(tuple(all_commands),{
        'method':'de Casteljau subcurves; chord/control-polygon arc-length brackets; bounded bisection',
        'geometry_flattened':False,'pattern':list(pattern),'normalized_pattern':list(normalized),
        'normalized_phase':phase,'tolerance_source_units':tolerance,
        'maximum_arc_position_error_upper':error,'floating_point_allowance':float_allowance,
        'arc_length_bound_note':'Chord/polygon bounds hold in real arithmetic; conservative binary64 allowance included separately, not formal interval arithmetic.',
        'phase_application':'original source subpath before any clipping; reset at every moveto',
        'subpaths':subpath_reports,'arc_subdivision_leaves':total_leaves,
        'output_command_count':len(all_commands),'floating_boundary_snap_count':len(floating_snaps),
        'maximum_floating_boundary_snap':max(floating_snaps,default=0.),
    })
