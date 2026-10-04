"""Bounded algebraic transverse C/L fill normalization on an integer grid.

Only strictly transverse single C/L events are admitted. Source arrangement,
shared-node encoding and final integer-curve topology share one work counter.
No C/C crossing, tangent contact, approximate flattening or style is inferred.
"""
from fractions import Fraction as F
from itertools import product
from functools import cmp_to_key
from . import pdf_cubic_winding as C
from . import pdf_cubic_contact_winding as K
W=C._line
Unsupported=C.UnsupportedPdfCubicWindingError
fail=C._fail


class Arithmetic(C._Arithmetic):
    def interval(self,v):
        self.spend();return tuple(W._checked(F(x)) for x in v)
    def plus(self,a,b):return self.interval((self.add(a[0],b[0]),self.add(a[1],b[1])))
    def minus(self,a,b):return self.interval((self.sub(a[0],b[1]),self.sub(a[1],b[0])))
    def times(self,a,b):
        values=[self.mul(x,y) for x in a for y in b]
        return self.interval((min(values),max(values)))
    def ivector(self,a,b):return tuple(self.minus(x,y) for x,y in zip(a,b))
    def icross(self,a,b):return self.minus(self.times(a[0],b[1]),self.times(a[1],b[0]))
    def idot(self,a,b):return self.plus(self.times(a[0],b[0]),self.times(a[1],b[1]))
    def poly(self,poly,t):
        v=(F(0),F(0))
        for c in reversed(poly):v=self.plus(self.times(v,t),(c,c))
        return v
    def ev(self,poly,t):
        v=F(0)
        for c in reversed(poly):v=self.add(self.mul(v,t),c)
        return v


def power(p,B):
    a,b,c,d=p
    return [a,B.mul(3,B.sub(b,a)),B.mul(3,B.add(B.sub(a,B.mul(2,b)),c)),
            B.add(B.sub(B.mul(3,b),a),B.sub(d,B.mul(3,c)))]


def sign(interval):
    return 1 if interval[0]>0 else -1 if interval[1]<0 else 0


def constant_point(p):return tuple((v,v) for v in p)
def corners(box):return list(product(*box))
def key(a,b):return tuple(sorted((a,b)))
def nearest(v,B):
    v=B.add(F(v),F(1,2));B.spend()
    return W._checked(F(v.numerator//v.denominator))


def event(curve,line,B,steps):
    p=curve['points'];a,b=line['points'];d=B.vector(b,a)
    coeff=[B.cross(d,B.vector(q,a)) for q in p]
    derivative=[B.mul(3,B.sub(y,x)) for x,y in zip(coeff,coeff[1:])]
    if not (min(derivative)>0 or max(derivative)<0) or B.mul(coeff[0],coeff[-1])>=0:
        fail('crossing_domain','C/L hull overlap lacks strict derivative and endpoint-sign proof',pair=[curve['id'],line['id']])
    poly=power(coeff,B);lo,hi=F(0),F(1)
    for _ in range(steps):
        mid=B.div(B.add(lo,hi),F(2));value=B.ev(poly,mid)
        if not value:
            lo=hi=mid
            break
        if B.mul(B.ev(poly,lo),value)<0:hi=mid
        else:lo=mid
    t=(lo,hi);xy=[power([q[i] for q in p],B) for i in (0,1)]
    cp=[B.poly(row,t) for row in xy]
    n=B.dot(d,d)
    u=(F(0),F(0))
    for i in (0,1):u=B.plus(u,B.times((d[i],d[i]),B.minus(cp[i],(a[i],a[i]))))
    u=tuple(B.div(v,n) for v in u)
    if not 0<u[0]<=u[1]<1:fail('finite_crossing','Isolated root not proved inside finite L')
    box=tuple(B.plus((a[i],a[i]),B.times((d[i],d[i]),u)) for i in (0,1))
    um=B.div(B.add(u[0],u[1]),F(2))
    proxy=tuple(B.add(a[i],B.mul(d[i],um)) for i in (0,1))
    halves={'left':[[],[],[],[]],'right':[[],[],[],[]]}
    for axis in (0,1):
        p0,p1,p2,p3=[q[axis] for q in p]
        controls_left=[[p0],[p0,B.sub(p1,p0)],
                       [p0,B.mul(2,B.sub(p1,p0)),B.add(B.sub(p0,B.mul(2,p1)),p2)],xy[axis]]
        controls_right=[xy[axis],
                        [p1,B.mul(2,B.sub(p2,p1)),B.add(B.sub(p1,B.mul(2,p2)),p3)],
                        [p2,B.sub(p3,p2)],[p3]]
        for side,polys in [('left',controls_left),('right',controls_right)]:
            for row,polynomial in zip(halves[side],polys):row.append(B.poly(polynomial,t))
    return {'id':'event:'+curve['id']+'|'+line['id'],'curve':curve['id'],'line':line['id'],
            'parameter':t,'line_parameter':u,'line_side_power':poly,'line_side_bernstein':coeff,
            'line_side_derivative_bernstein':derivative,'point_box':box,'proxy':proxy,'controls':halves,
            'root_count_proof':'Strict derivative plus opposite signs on full original [0,1]: exactly one simple root',
            'fragment_hull_support':'Strict derivative Bernstein coefficients persist under restriction. Integrating from f(root)=0 gives strict one-side controls except shared endpoint.'}


def source_graph(commands,B,limits,steps):
    source,contours,skipped,canonical=C._parse(commands,limits,B,integer=True)
    if any(abs(v)>2**31-1 for s in source for p in s['points'] for v in p):fail('input_budget','Prototype native coordinates limited to signed 31-bit magnitude')
    projections={s['id']:C._monotone(s['points'],B) for s in source if s['kind']=='C'}
    if not projections or not all(projections.values()):fail('injectivity','Requires nonempty independently monotone source Cs')
    events=[];by_segment={};pairs=[]
    for i,a in enumerate(source):
        for b in source[i+1:]:
            B.spend()
            common=set((a['points'][0],a['points'][-1]))&set((b['points'][0],b['points'][-1]))
            if a['kind']==b['kind']=='L':
                points=W._intersections(*a['points'],*b['points'],B)
                if any(p not in common for p in points):fail('LL_domain','No proper or T-junction L/L events in initial domain')
                if len(points)==2 and set(a['points'])!=set(b['points']):fail('LL_domain','Only identical full L overlap permitted')
                pairs.append({'pair':[a['id'],b['id']],'kind':'LL','intersections':sorted(points)})
                continue
            shared=next(iter(common)) if len(common)==1 else None
            proof=C._separation(a['hull'],b['hull'],shared,B)
            if proof:
                pairs.append({'pair':[a['id'],b['id']],'kind':'parent_hull','proof':proof});continue
            if a['kind']==b['kind']=='C':fail('CC_domain','Unproved C/C parent hull relation')
            cv,ln=(a,b) if a['kind']=='C' else (b,a)
            if cv['id'] in by_segment or ln['id'] in by_segment:fail('event_budget','Each C and L permits at most one event')
            e=event(cv,ln,B,steps);events.append(e);by_segment[cv['id']]=by_segment[ln['id']]=e
            pairs.append({'pair':[a['id'],b['id']],'kind':'transverse_event','event':e['id']})
    if not events or len(events)>16:fail('event_budget','Requires 1..16 transverse events')
    nodes={};original_nodes={}
    for s in source:
        for p in (s['points'][0],s['points'][-1]):
            B.spend()
            if p not in original_nodes:
                nid='vertex:'+str(len(original_nodes));original_nodes[p]=nid
                nodes[nid]={'id':nid,'box':constant_point(p),'proxy':p,'origin':'original_integer'}
    for e in events:
        B.spend();nodes[e['id']]={'id':e['id'],'box':e['point_box'],'proxy':e['proxy'],'origin':'algebraic_event'}
    pieces=[]
    for s in source:
        a,b=original_nodes[s['points'][0]],original_nodes[s['points'][-1]];e=by_segment.get(s['id'])
        branches=[('whole',a,b)] if e is None else [('left',a,e['id']),('right',e['id'],b)]
        for side,na,nb in branches:
            B.spend()
            if s['kind']=='C':
                controls=[constant_point(p) for p in s['points']] if e is None else e['controls'][side]
            else:controls=[nodes[na]['box'],nodes[nb]['box']]
            pieces.append({'id':s['id']+':'+side,'kind':s['kind'],'parent':s['id'],'subpath':s['subpath'],
                           'nodes':(na,nb),'points':[nodes[na]['proxy'],nodes[nb]['proxy']],
                           'control_intervals':controls,'event':None if e is None else e['id'],'side':side})
    parent_pairs={frozenset(r['pair']):r for r in pairs}
    fragment_pairs=[]
    for i,a in enumerate(pieces):
        for b in pieces[i+1:]:
            B.spend()
            if a['kind']==b['kind']=='L':continue
            shared=set(a['nodes'])&set(b['nodes'])
            if a['parent']==b['parent']:
                if a['kind']!='C' or a['side']==b['side'] or len(shared)!=1:
                    fail('fragment_identity','Unexpected same-parent fragment relation')
                rule='same_original_C_strict_monotone_projection_at_interior_cut'
                parent=None
            else:
                parent=parent_pairs[frozenset((a['parent'],b['parent']))]
                if parent['kind']=='transverse_event':
                    if shared!={parent['event']}:
                        fail('fragment_event_identity','C/L fragments do not share their certified event')
                    rule='strict_Bernstein_line_side_support_at_unique_algebraic_root'
                elif parent['kind']=='parent_hull':
                    rule=('convex_subhull_inherits_strict_parent_separation' if parent['proof']['status']=='strict_separation'
                          else 'parent_endpoint_singleton_inherited_or_excluded_by_monotone_parameter')
                else:fail('fragment_domain','Unaccounted C-containing fragment pair')
            fragment_pairs.append({'pair':[a['id'],b['id']],'parent_pair':None if parent is None else parent['pair'],
                                   'rule':rule,'actual_shared_node_ids':sorted(shared)})
    # Fragment-to-chord homotopy: all unrelated pairs inherit parent hull
    # separation; event C/L pairs use strict support; same-parent C pieces
    # inherit monotone projection support at their common algebraic endpoint.
    return source,nodes,pieces,events,{'source_parent_pairs':pairs,'source_fragment_pairs':fragment_pairs,'source_monotone_projections':projections,
         'fragment_homotopy_rule':'Parent convex-subhull inheritance; event strict line-side support; same-C monotone projection support',
         'zero_length_source_L_ignored':skipped}


def proxy_certificate(nodes,pieces,B):
    rows=[];values=list(nodes.values())
    for i,a in enumerate(values):
        for b in values[i+1:]:
            B.spend()
            if not any(x[1]<y[0] or y[1]<x[0] for x,y in zip(a['box'],b['box'])):
                fail('node_alias','Distinct node boxes not strictly separated',nodes=[a['id'],b['id']])
    for i,a in enumerate(pieces):
        for b in pieces[i+1:]:
            B.spend();common=set(a['nodes'])&set(b['nodes'])
            if len(common)==2:
                if a['kind']!=b['kind'] or a['kind']!='L':fail('edge_alias','Only duplicate full L edges may coincide')
                rows.append({'pair':[a['id'],b['id']],'kind':'duplicate_L_identity'});continue
            if not common:
                hulls=[C._hull([p for n in s['nodes'] for p in corners(nodes[n]['box'])],B) for s in (a,b)]
                proof=C._separation(*hulls,None,B)
                if not proof:fail('proxy_embedding','Nonincident edge uncertainty hulls lack strict separation',pair=[a['id'],b['id']])
                rows.append({'pair':[a['id'],b['id']],'kind':'strict_uncertainty_hull_separation','proof':proof});continue
            nid=next(iter(common));others=[next(n for n in s['nodes'] if n!=nid) for s in (a,b)]
            vectors=[B.ivector(nodes[n]['box'],nodes[nid]['box']) for n in others]
            determinant=B.icross(*vectors)
            if sign(determinant):
                rows.append({'pair':[a['id'],b['id']],'kind':'incident_fan_order','det_interval':determinant});continue
            # Opposite directions are sufficient for local embedding even
            # when the common endpoint moves. Original L pieces additionally
            # have the exact original-line constraint throughout the homotopy.
            same_line=a['kind']==b['kind']=='L' and a['parent']==b['parent']
            static=all(lo==hi for n in (*a['nodes'],*b['nodes']) for lo,hi in nodes[n]['box'])
            if (same_line or static) and B.idot(*vectors)[1]<0:
                rows.append({'pair':[a['id'],b['id']],'kind':'opposite_original_line_or_static_rays','dot_interval':B.idot(*vectors)});continue
            fail('proxy_fan','Unproved incident ray order',pair=[a['id'],b['id']])
    return rows


def face_cycles(pieces,classifications,B):
    """Label every face-boundary cycle of the signed support quotient graph.

    Disconnected components can give several boundary cycles for one face;
    cycles are not misreported as uniquely distinct geometric faces.
    """
    multiplicities={}
    for p in pieces:
        B.spend()
        a,b=p['points'];edge=key(a,b)
        multiplicities[edge]=multiplicities.get(edge,0)+(1 if edge==(a,b) else -1)
    records={tuple(tuple(p) for p in r['edge']):r for r in classifications}
    active={edge:m for edge,m in multiplicities.items() if m}
    if set(active)!=set(records):fail('face_accounting','Nonzero support edge accounting incomplete')
    neighbors={}
    for a,b in active:
        B.spend()
        neighbors.setdefault(a,[]).append(b);neighbors.setdefault(b,[]).append(a)
    def around(vertex):
        def compare(a,b):
            va=B.vector(a,vertex);vb=B.vector(b,vertex)
            ha=0 if va[1]>0 or va[1]==0 and va[0]>=0 else 1
            hb=0 if vb[1]>0 or vb[1]==0 and vb[0]>=0 else 1
            if ha!=hb:return -1 if ha<hb else 1
            cross=B.cross(va,vb)
            if not cross:fail('face_fan','Distinct active halfedges share an outgoing ray')
            return -1 if cross>0 else 1
        return sorted(neighbors[vertex],key=cmp_to_key(compare))
    fans={v:around(v) for v in neighbors}
    remaining={(a,b) for a,b in active}|{(b,a) for a,b in active}
    cycles=[]
    while remaining:
        start=min(remaining);current=start;cycle=[];labels=[]
        while current in remaining:
            B.spend();remaining.remove(current);a,b=current;cycle.append(current)
            edge=key(a,b);record=records[edge]
            labels.append(record['left_winding'] if edge==current else record['right_winding'])
            fan=fans[b];at=fan.index(a);current=(b,fan[(at-1)%len(fan)])
        if current!=start or len(set(labels))!=1:fail('face_winding','Face-boundary cycle open or has inconsistent winding labels')
        cycles.append({'oriented_halfedges':cycle,'exact_winding_label':labels[0]})
    if sum(len(r['oriented_halfedges']) for r in cycles)!=2*len(active):fail('face_accounting','Incomplete oriented face boundaries')
    return {'face_boundary_cycles':cycles,'cycle_count_is_not_geometric_face_count':True,
            'nonzero_support_edge_multiplicities':[{'edge':e,'multiplicity':m} for e,m in active.items()],
            'zero_multiplicity_support_edges':[e for e,m in multiplicities.items() if not m],
            'all_nonzero_halfedges_have_one_left_face_boundary_label':True}


def encode(nodes,pieces,rings,curve_edges,B,max_error):
    grid={};maximum=[F(0),F(0)]
    for nid,n in nodes.items():
        B.spend()
        values=[]
        for axis,interval in enumerate(n['box']):
            B.spend();q=nearest(interval[0],B)
            if q!=nearest(interval[1],B):fail('grid_cell','Algebraic node lacks unique rounding cell')
            error=max(abs(B.sub(q,v)) for v in interval)
            if error>max_error[axis]:fail('grid_error','Node total error exceeds bound')
            values.append(F(q))
        grid[nid]=tuple(values)
    lookup={n['proxy']:nid for nid,n in nodes.items()}
    if len(lookup)!=len(nodes):fail('proxy_alias','Two proxy coordinates alias')
    by_edge={key(*p['points']):p for p in pieces if p['kind']=='C'}
    output=[];account=[]
    for ri,ring in enumerate(rings):
        B.spend()
        output.append(('M',grid[lookup[ring[0]]]))
        for a,b in zip(ring,ring[1:]+ring[:1]):
            B.spend()
            na,nb=lookup[a],lookup[b];piece=by_edge.get(key(a,b))
            if piece is None:
                output.append(('L',grid[nb]));controls=[nodes[na]['box'],nodes[nb]['box']]
                final=[grid[na],grid[nb]];origin='original_L_fragment'
            else:
                controls=piece['control_intervals'] if piece['points'][0]==a else list(reversed(piece['control_intervals']))
                final=[]
                for point in controls:
                    B.spend()
                    row=[]
                    for interval in point:
                        q=nearest(interval[0],B)
                        if q!=nearest(interval[1],B):fail('grid_cell','Fragment control lacks unique rounding cell')
                        row.append(q)
                    final.append(tuple(row))
                if final[0]!=grid[na] or final[-1]!=grid[nb]:fail('grid_shared_node','Curve and L event did not round to one node identity')
                output.append(('C',*final[1:]));origin=piece['id']
            errors=[]
            for intervals,p in zip(controls,final):
                B.spend()
                row=[]
                for axis,(interval,q) in enumerate(zip(intervals,p)):
                    error=max(abs(B.sub(q,v)) for v in interval)
                    if error>max_error[axis]:fail('grid_error','Final control total error exceeds original algebraic fragment bound')
                    maximum[axis]=max(maximum[axis],error);row.append(error)
                errors.append(row)
            account.append({'ring':ri,'command_index':len(output)-1,'origin':origin,'node_ids':[na,nb],
                            'original_algebraic_control_intervals':controls,'actual_final_integer_controls':final,
                            'total_coordinate_error_bounds':errors})
        output.append(('Z',))
    return output,account,maximum


def _domain(limits, root_steps, maximum):
    if type(root_steps) is not int or not 16<=root_steps<=96:raise ValueError('root_steps must be16..96')
    for key,cap in [('segments',128),('commands',512),('atoms',2048),('operations',8_000_000),('halvings',80)]:
        if type(limits[key]) is not int or not 1<=limits[key]<=cap:raise ValueError('Transverse '+key+' exceeds bounded domain')
    if type(maximum) in (F,int):maximum=(maximum,maximum)
    if type(maximum) not in (tuple,list) or len(maximum)!=2 or any(type(v) not in (F,int) or not 0<=v<=F(1,2) for v in maximum):raise ValueError('max_coordinate_error requires exact bounds in [0,.5]')
    return maximum


def _normalize_with_budget(commands,limits,B,*,root_steps=80,max_coordinate_error=(F(1,2),F(1,2))):
    max_axis_error=_domain(limits,root_steps,max_coordinate_error)
    started=B.used
    phases={}
    def mark(name):phases[name]=B.used-started-sum(phases.values())
    source,nodes,pieces,events,admission=source_graph(commands,B,limits,root_steps)
    mark('source_admission_and_fragment_graph')
    embedding=proxy_certificate(nodes,pieces,B)
    mark('proxy_embedding')
    rings,curve_edges,classifications,intersections,topology,atoms=C._arrangement(pieces,limits,B)
    mark('exact_proxy_arrangement')
    if any(p not in {n['proxy'] for n in nodes.values()} for p in intersections):fail('proxy_new_intersection','Proxy arrangement invented an event')
    output,account,maximum=encode(nodes,pieces,rings,curve_edges,B,max_axis_error)
    mark('integer_encoding')
    faces=face_cycles(pieces,classifications,B)
    classified={key(*r['edge']):r for r in classifications}
    decisions=[]
    for p in pieces:
        B.spend()
        record=classified.get(key(*p['points']))
        if record is None and p['kind']=='C':fail('fragment_accounting','C fragment lacks a winding-side classification')
        decisions.append({'piece_id':p['id'],'parent_id':p['parent'],'kind':p['kind'],
                          'decision':'discarded_zero_multiplicity_L' if record is None else 'selected_boundary' if record['boundary'] else 'discarded_nonboundary',
                          'winding_classification':record})
    mark('face_labels_and_fragment_accounting')
    # Independently prove actual final integer C geometry, not only its chords.
    final_rings,final_topology,final_projections,final_hulls,canonical=K._candidate_rings(output,limits,B,depth=1,integer=True)
    if final_topology!=topology:fail('final_topology','Rounded native fragments change loop orientation or containment')
    mark('final_integer_curve_topology')
    proof={'method':'bounded_algebraic_transverse_CL_fragment_graph_and_single_grid_encoding',
      'events':events,'source_admission':admission,'algebraic_to_proxy_embedding':embedding,
      'complete_proxy_edge_side_winding_labels':classifications,'source_boundary_proxy_rings':rings,
      'boundary_classification':classifications,'proof_mode':'transverse_line',
      'source_fill_rule':'nonzero','output_fill_rule':'evenodd','fill_only':True,
      'source_signed_support_quotient_faces':faces,
      'source_boundary_topology':topology,'source_graph_atomic_edges':atoms,
      'source_fragment_pieces':pieces,'node_intervals':list(nodes.values()),
      'complete_source_fragment_decisions':decisions,
      'final_control_accounting':account,'maximum_total_new_control_error_local':maximum,
      'final_integer_curve_projections':final_projections,'final_integer_hull_certificates':final_hulls,
      'final_integer_topology':final_topology,'compound_paint_count':1,
      'source_to_initial_native_error_included':False,'rgb_alpha_error_bound':None,
      'budget_counting_unit':'instrumented bounded work units; not a literal count of every Python or Fraction primitive operation',
      'phase_work_units_before_receipt':phases,
      'PPT_or_native_wrapper_verification':False}
    before_receipt=B.used
    serialized=B.serialize(proof);serialized['exact_operations_including_receipt']=B.used-started
    serialized['receipt_serialization_work_units']=B.used-before_receipt
    return {'commands':output,'proof':serialized}


def normalize_transverse_line_cubic_fill(commands,*,root_steps=80,max_coordinate_error=F(1,2),
        max_input_segments=128,max_commands=512,max_atomic_edges=2048,max_operations=4_000_000,max_probe_halvings=80):
    """Normalize original integer nonzero fill; return verified integer fragments.

    Controls are rounded once from original algebraic subcurve intervals. The
    error is additional to source-to-initial-native quantization, not inclusive.
    Budget counts instrumented bounded work units, not all Python primitives.
    """
    limits=C._limits(max_input_segments,max_commands,max_atomic_edges,max_operations,max_probe_halvings)
    return _normalize_with_budget(commands,limits,Arithmetic(max_operations),root_steps=root_steps,max_coordinate_error=max_coordinate_error)


def _verify_integer_with_budget(source,candidate,limits,B,*,root_steps=80,max_coordinate_error=F(1,2)):
    # Recompute the source arrangement/encoding, never accept caller receipts.
    started=B.used
    expected=_normalize_with_budget(source,limits,B,root_steps=root_steps,max_coordinate_error=max_coordinate_error)
    _,_,_,canonical=C._parse(candidate,limits,B,integer=True)
    if canonical!=expected['commands']:fail('encoding_identity','Decoded integer commands differ from recomputed source encoding')
    _,topology,projections,hulls,_=K._candidate_rings(canonical,limits,B,depth=1,integer=True)
    if topology!=expected['proof']['final_integer_topology']:fail('final_topology','Actual candidate topology changed')
    result={'verified':True,'source_recomputed':True,'actual_integer_candidate_reproved':True,'proof_mode':'transverse_line',
            'final_integer_topology':topology,
            'maximum_total_new_control_error_local':expected['proof']['maximum_total_new_control_error_local'],
            'maximum_new_coordinate_error_local_units':expected['proof']['maximum_total_new_control_error_local'],
            'maximum_error_scope':'Original algebraic subcurve to final integer controls, including shared crossing endpoints; no second LL rounding',
            'actual_integer_curve_projections':projections,'actual_integer_hull_certificates':hulls,
            'source_to_initial_native_error_included':False,'PPT_verified':False}
    serialized=B.serialize(result)
    serialized['exact_operations_including_recompute_and_candidate_parse']=B.used-started
    return serialized


def verify_transverse_line_cubic_integer_encoding(source,candidate,*,root_steps=80,max_coordinate_error=F(1,2),
        max_input_segments=128,max_commands=512,max_atomic_edges=2048,max_operations=4_000_000,max_probe_halvings=80):
    """Independently rederive source fragments and prove decoded candidate geometry."""
    limits=C._limits(max_input_segments,max_commands,max_atomic_edges,max_operations,max_probe_halvings)
    return _verify_integer_with_budget(source,candidate,limits,Arithmetic(max_operations),root_steps=root_steps,max_coordinate_error=max_coordinate_error)


def _classify_with_budget(commands,limits,B):
    """Select one domain from authenticated native commands, before normalizing.

    A certified finite interior transverse C/L event selects this module. If no
    such event is certified, the existing endpoint-contact hull domain must be
    independently proved before selecting contact. Unknown domains reject;
    neither normalizer is attempted as a fallback after the other's failure.
    """
    _domain(limits,80,F(1,2));started=B.used
    source,_,_,_=C._parse(commands,limits,B,integer=True)
    for i,a in enumerate(source):
        for b in source[i+1:]:
            B.spend()
            if {a['kind'],b['kind']}!={'C','L'}:continue
            shared=set((a['points'][0],a['points'][-1]))&set((b['points'][0],b['points'][-1]))
            if C._separation(a['hull'],b['hull'],next(iter(shared)) if len(shared)==1 else None,B):continue
            curve,line=(a,b) if a['kind']=='C' else (b,a)
            try:witness=event(curve,line,B,80)
            except Unsupported as error:
                if error.code not in ('crossing_domain','finite_crossing'):raise
                continue
            result=B.serialize({'selected_proof_mode':'transverse_line',
                'selection_rule':'Certified unique finite interior transverse C/L root',
                'event_witness':witness,'normalizer_attempted_during_classification':False})
            result['classification_work_units']=B.used-started
            return result
    proxy,_,_=K._expanded(source,B,1,limits['segments']*2)
    K._contact_domain(proxy,B)
    result={'selected_proof_mode':'endpoint_contact','proof_split_depth':1,
            'selection_rule':'No certified transverse event; endpoint-contact proof-only hull domain independently established',
            'normalizer_attempted_during_classification':False}
    result=B.serialize(result);result['classification_work_units']=B.used-started
    return result
