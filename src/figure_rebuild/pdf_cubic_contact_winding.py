"""Exact endpoint-contact fill normalization preserving every output C whole.

Proof-only de Casteljau halves establish an embedded endpoint-contact graph.
Artificial joins are allowed only within the same original C; no true curve
interior crossing is admitted. Output contains complete original Cs or none.
"""
from fractions import Fraction as F
from functools import cmp_to_key
from . import pdf_cubic_winding as _cubic
_line = _cubic._line
_fail = _cubic._fail
UnsupportedPdfCubicWindingError = _cubic.UnsupportedPdfCubicWindingError

def _half(points, budget):
    levels = [points]
    while len(levels[-1]) > 1:
        budget.spend()
        levels.append([tuple((budget.mul(budget.add(x, y), F(1, 2)) for x, y in zip(a, b))) for a, b in zip(levels[-1], levels[-1][1:])])
    return ([p[0] for p in levels], [p[-1] for p in reversed(levels)])

def _expanded(source, budget, depth, max_segments):
    if type(depth) is not int or depth not in (0, 1):
        raise ValueError('Contact proof subdivision depth must be 0 or 1')
    result = []
    curve_parts = {}
    projections = []
    for s in source:
        budget.spend()
        if s['kind'] != 'C':
            result.append({**s, 'original_segment_id': s['id'], 'original_endpoints': (s['points'][0], s['points'][-1])})
            continue
        proof = _cubic._monotone(s['points'], budget)
        if proof is None:
            _fail('curve_injectivity', 'Original whole C is not proved injective', segment=s['id'])
        projections.append({'source_id': s['id'], 'proof': proof})
        parts = _half(s['points'], budget) if depth else [s['points']]
        curve_parts[s['id']] = []
        for i, pts in enumerate(parts):
            budget.spend()
            p = {**s, 'id': s['id'] + ':proof-' + str(i), 'points': pts, 'hull': _cubic._hull(pts, budget), 'original_C_id': s['id'], 'original_segment_id': s['id'], 'original_endpoints': (s['points'][0], s['points'][-1]), 'proof_part_index': i, 'proof_part_count': len(parts)}
            result.append(p)
            curve_parts[s['id']].append(p)
    if len(result) > max_segments:
        _fail('budget', 'Proof-only proxy segment budget exceeded')
    return (result, curve_parts, projections)

def _contact_domain(proxy, budget):
    """Every deformation hull meets others only at their true graph endpoints.

    This is stronger than merely testing original curves. All original L are
    fixed. Each strictly monotone C part deforms to its chord inside its hull;
    singleton endpoint contacts therefore persist without crossing/reordering
    graph edges. Cross-subpath endpoint incidence is retained, not discarded.
    """
    tests = []
    blockers = []
    contacts = []
    for i, a in enumerate(proxy):
        for b in proxy[i + 1:]:
            budget.spend()
            if a['kind'] == b['kind'] == 'L':
                continue
            shared = set([a['points'][0], a['points'][-1]]) & set([b['points'][0], b['points'][-1]])
            # Coordinate equality of artificial half endpoints is insufficient.
            # The shared point must be an original endpoint of BOTH segments;
            # only consecutive halves of one C may use an artificial proof join.
            shared_point = next(iter(shared)) if len(shared) == 1 else None
            point = None
            identity_kind = None
            budget.spend()
            if shared_point is not None:
                same_curve = a.get('original_C_id') is not None and a.get('original_C_id') == b.get('original_C_id')
                if same_curve:
                    first, second = (a, b) if a['proof_part_index'] < b['proof_part_index'] else (b, a)
                    budget.spend()
                    if second['proof_part_index'] == first['proof_part_index'] + 1 and shared_point == first['points'][-1] == second['points'][0]:
                        point = shared_point
                        identity_kind = 'same_original_C_consecutive_proof_join'
                elif shared_point in a['original_endpoints'] and shared_point in b['original_endpoints']:
                    point = shared_point
                    identity_kind = 'both_original_segment_endpoints'
            proof = _cubic._separation(a['hull'], b['hull'], point, budget)
            row = {'pair': [a['id'], b['id']], 'coincident_piece_endpoint': shared_point, 'admitted_endpoint': point, 'endpoint_identity_kind': identity_kind, 'certificate': proof}
            tests.append(row)
            if proof is None:
                blockers.append(row)
            elif proof['status'] != 'strict_separation':
                contacts.append({'point': point, 'endpoint_identity_kind': identity_kind, 'original_segments': [a['original_segment_id'], b['original_segment_id']], 'segments': [a['id'], b['id']], 'original_subpaths': [a['subpath'], b['subpath']], 'C_part_origins': [a.get('original_C_id'), b.get('original_C_id')]})
    if blockers:
        _fail('contact_domain', 'Proof-only hull domain has unproved intersection; endpoint contacts alone are allowed', blockers=budget.serialize(blockers))
    return (tests, contacts)

def _normalize_contact(commands, limits, budget, depth, integer=False):
    source, contours, skipped, canonical = _cubic._parse(commands, limits, budget, integer)
    proxy, parts, projections = _expanded(source, budget, depth, limits['segments'] * (1 << depth))
    tests, contacts = _contact_domain(proxy, budget)
    rings, curve_edges, classified, intersections, topology, atoms = _cubic._arrangement(proxy, limits, budget)
    byid = {s['id']: s for s in source}
    original_vertices = set()
    protected = set()
    proof_vertices = set()
    for s in source:
        budget.spend()
        original_vertices.update([s['points'][0], s['points'][-1]])
        if s['kind'] == 'C':
            protected.update([s['points'][0], s['points'][-1]])
    for pieces in parts.values():
        for p in pieces[:-1]:
            budget.spend()
            proof_vertices.add(p['points'][-1])
    if proof_vertices & original_vertices:
        _fail('proof_vertex_alias', 'An internal proof split vertex aliases an original endpoint')
    output = []
    identities = []
    origins = []
    used = set()
    rotated_rings = []

    def key(a, b):
        return tuple(sorted((a, b), key=cmp_to_key(budget.point_compare)))

    def origin(p, index):
        budget.spend()
        if p in proof_vertices:
            _fail('proof_vertex_leak', 'A proof-only C subdivision vertex reached output')
        if p not in original_vertices and p not in intersections:
            _fail('vertex_identity', 'New output point is not a genuine L/L intersection')
        origins.append({'output_command_index': index, 'point': p, 'origin': 'original_vertex' if p in original_vertices else 'new_L_L_intersection', 'protected_C_endpoint': p in protected, 'source_line_pairs': intersections.get(p, [])})
    for ri, ring in enumerate(rings):
        budget.spend(len(ring))
        starts = [i for i, p in enumerate(ring) if p not in proof_vertices]
        if not starts:
            _fail('curve_chain', 'Boundary ring consists entirely of artificial C proof vertices')
        start = starts[0]
        ring = ring[start:] + ring[:start]
        rotated_rings.append(ring)
        edges = list(zip(ring, ring[1:] + ring[:1]))
        output.append(('M', ring[0]))
        origin(ring[0], len(output) - 1)
        i = 0
        while i < len(edges):
            budget.spend()
            a, b = edges[i]
            piece = curve_edges.get(key(a, b))
            if piece is None:
                output.append(('L', b))
                origin(b, len(output) - 1)
                i += 1
                continue
            oid = piece['original_C_id']
            src = byid[oid]
            chain = parts[oid]
            forward = a == src['points'][0]
            expected = chain if forward else list(reversed(chain))
            controls = src['points'] if forward else list(reversed(src['points']))
            if controls[0] != a or oid in used:
                _fail('curve_chain', 'Output begins inside a C chain or repeats whole C', source_id=oid)
            if i + len(expected) > len(edges):
                _fail('curve_chain', 'Whole C chain wraps across an artificial output start')
            for j, want in enumerate(expected):
                budget.spend()
                x, y = edges[i + j]
                found = curve_edges.get(key(x, y))
                pa = want['points'][0 if forward else -1]
                pb = want['points'][-1 if forward else 0]
                if found is None or found['id'] != want['id'] or (x, y) != (pa, pb):
                    _fail('partial_C', 'Only a partial/reordered original C chain is a selected boundary', source_id=oid)
            output.append(('C', *controls[1:]))
            origin(controls[-1], len(output) - 1)
            used.add(oid)
            identities.append({'source_id': oid, 'decision': 'restored_whole', 'direction': 'forward' if forward else 'reverse', 'output_ring': ri, 'output_command_index': len(output) - 1, 'original_controls': src['points'], 'output_controls': controls, 'proof_part_count': len(chain)})
            i += len(chain)
        output.append(('Z',))
    classified = {tuple(x['edge']): x for x in classified}
    for oid, chain in parts.items():
        budget.spend()
        if oid in used:
            continue
        rows = []
        for p in chain:
            budget.spend()
            r = classified.get(key(p['points'][0], p['points'][-1]))
            if r is None or r['boundary']:
                _fail('partial_C', 'Missing original C has an unaccounted selected part', source_id=oid)
            rows.append(r)
        identities.append({'source_id': oid, 'decision': 'discarded_nonboundary', 'original_controls': byid[oid]['points'], 'proof_part_classifications': rows})
    if len(identities) != len(parts):
        _fail('curve_identity', 'Incomplete original C accounting')
    # Re-expand the actual whole-C output: every proof loop must match the
    # arranged boundary exactly, so a receipt alone cannot authorize restoration.
    outlimits = {**limits, 'segments': limits['atoms'], 'commands': limits['atoms'] * 3 + 16}
    outsource, outcontours, _, _ = _cubic._parse(output, outlimits, budget)
    outproxy, _, _ = _expanded(outsource, budget, depth, outlimits['segments'] * (1 << depth))
    outtests, outcontacts = _contact_domain(outproxy, budget)
    bycontour = {}
    for s in outproxy:
        budget.spend()
        bycontour.setdefault(s['subpath'], []).append(s['points'][0])
    actual_rings = list(bycontour.values())
    if actual_rings != rotated_rings:
        _fail('curve_chain', 'Whole-C restoration does not reproduce the proved boundary proxy graph')
    outtopology = _line._validate_rings(actual_rings, budget)
    if outtopology != topology:
        _fail('topology', 'Whole-C output changed exact proxy topology')
    proof = {'method': 'exact_proof_only_halves_endpoint_contact_embedding_and_whole_C_chain_restoration', 'proof_mode': 'endpoint_contact', 'proof_split_depth': depth, 'source_proxy_segment_limit': limits['segments'] * (1 << depth), 'output_proxy_segment_limit': outlimits['segments'] * (1 << depth), 'simplify_used': False, 'coordinate_domain': 'exact_rational_no_float_export', 'source_fill_rule': 'nonzero', 'output_fill_rule': 'evenodd', 'compound_paint_count': 1, 'source_commands_modified': False, 'input_commands_modified': False, 'fill_only': True, 'original_stroke_must_be_preserved_separately': True, 'output_proof_C_fragments': 0, 'source_curve_count': len(parts), 'source_segment_count': len(source), 'proof_proxy_segment_count': len(proxy), 'atomic_edge_count': atoms, 'curve_projection_proofs': projections, 'contact_graph': contacts, 'hull_tests': tests, 'output_hull_tests': outtests, 'boundary_classification': list(classified.values()), 'chord_loop_topology': topology, 'output_ring_count': len(rings), 'empty_fill': not rings, 'zero_length_L_ignored_for_proof': skipped, 'input_fraction_bit_limit': 4096, 'intermediate_fraction_bit_limit': _line._INTERMEDIATE_BITS, 'native_integer_encoding_verified': False, 'native_renderer_verified': False, 'rgb_alpha_error_bound': None}
    return (output, identities, origins, proof, actual_rings, canonical)

def _proof_depth(value):
    if type(value) is not int or value not in (0, 1):
        raise ValueError('Contact proof depth must be built-in integer 0 or 1')
    return value

def _candidate_rings(commands, limits, budget, *, depth, integer=False):
    source, _, _, canonical = _cubic._parse(commands, limits, budget, integer)
    proxy, _, projections = _expanded(source, budget, depth, limits['segments'] * (1 << depth))
    tests, _ = _contact_domain(proxy, budget)
    contours = {}
    for segment in proxy:
        budget.spend()
        contours.setdefault(segment['subpath'], []).append(segment['points'][0])
    rings = list(contours.values())
    topology = _line._validate_rings(rings, budget)
    return (rings, topology, projections, tests, canonical)

def _strategies(proof_split_depth):
    """Trusted internal providers; no public callable or caller proof admission."""
    depth = _proof_depth(proof_split_depth)

    def normalize_geometry(commands, limits, budget, integer=False):
        return _normalize_contact(commands, limits, budget, depth, integer)

    def candidate_geometry(commands, limits, budget, integer=False):
        return _candidate_rings(commands, limits, budget, depth=depth, integer=integer)
    return (normalize_geometry, candidate_geometry)

def _normalize_with_budget(commands, limits, budget, *, proof_split_depth=1):
    normalize_geometry, _ = _strategies(proof_split_depth)
    return _cubic._normalization_result(commands, limits, budget, normalize_geometry)

def _verify_integer_with_budget(source_native_commands, candidate_integer_commands, limits, budget, *, max_coordinate_error=F(1, 2), proof_split_depth=1):
    normalize_geometry, candidate_geometry = _strategies(proof_split_depth)
    return _cubic._verify_integer_encoding(source_native_commands, candidate_integer_commands, limits, budget, max_coordinate_error=max_coordinate_error, normalize_geometry=normalize_geometry, candidate_geometry=candidate_geometry, proof_metadata={'method': 'recomputed_exact_native_endpoint_contact_cubic_and_integer_encoding', 'proof_mode': 'endpoint_contact', 'proof_split_depth': proof_split_depth, 'proof_pieces_are_not_output_geometry': True})

def normalize_endpoint_contact_cubic_fill(commands, *, proof_split_depth=1, max_input_segments=128, max_commands=512, max_atomic_edges=2048, max_operations=1000000, max_probe_halvings=80):
    """Normalize a nonzero fill with exact endpoint-contact graph proofs.

    All output Cs retain complete original controls (possibly reversed), or are
    proved wholly nonboundary. Proof-only depth 0/1 never authorizes partial C,
    snapping or a true interior crossing. Styles and original strokes are not
    modified. Coordinates and failure contracts match the isolated API.
    """
    limits = _cubic._limits(max_input_segments, max_commands, max_atomic_edges, max_operations, max_probe_halvings)
    budget = _cubic._Arithmetic(max_operations)
    return _normalize_with_budget(commands, limits, budget, proof_split_depth=proof_split_depth)

def verify_endpoint_contact_cubic_integer_encoding(source_native_commands, candidate_integer_commands, *, proof_split_depth=1, max_coordinate_error=F(1, 2), max_input_segments=128, max_commands=512, max_atomic_edges=2048, max_operations=1000000, max_probe_halvings=80):
    """Recompute from independent original integer commands, then prove output.

    Every original C control/endpoint and surviving original vertex stays fixed.
    Only new L/L intersections may move within at most half a local grid unit.
    The actual candidate's contact domains and exact proxy topology are reproved;
    no caller receipt substitutes for source or final-geometry verification.
    Frame, paint, source-file identity and slide scaling belong to the caller.
    """
    limits = _cubic._limits(max_input_segments, max_commands, max_atomic_edges, max_operations, max_probe_halvings)
    budget = _cubic._Arithmetic(max_operations)
    return _verify_integer_with_budget(source_native_commands, candidate_integer_commands, limits, budget, max_coordinate_error=max_coordinate_error, proof_split_depth=proof_split_depth)
