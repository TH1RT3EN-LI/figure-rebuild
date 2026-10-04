"""Strict integer DrawingML geometry decoding for the whole-cubic verifier.

These helpers neither mutate XML nor validate shape paint/parent/frame identity.
A later wrapper must bind those independently to the actual source and output.
"""
from fractions import Fraction as F
from xml.etree import ElementTree as ET

from . import pdf_cubic_winding as _cubic
from .pdf_cubic_winding import _Arithmetic, _fail, _number

_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_MAX_INTEGER = 2**31 - 1


def _integer(text, budget, positive=False):
    budget.spend()
    if type(text) is not str or len(text) > 11:
        _fail('native_integer', 'Native path integer text exceeds the bounded syntax')
    try:
        number = int(text)
    except ValueError as error:
        _fail('native_integer', 'Invalid native path integer')
    if str(number) != text or abs(number) > _MAX_INTEGER or (positive and number <= 0):
        _fail('native_integer', 'Native path requires canonical bounded integers')
    return number


def _whitespace(value, budget):
    budget.spend()
    if value is not None and (type(value) is not str or len(value) > 64 or value.strip()):
        _fail('native_xml', 'Unexpected or oversized native path text')


def _decode(path, budget, max_commands):
    budget.spend()
    if type(path) is not ET.Element or type(path.tag) is not str or path.tag != _A + 'path':
        _fail('native_xml', 'Expected one DrawingML a:path element')
    if type(max_commands) is not int or max_commands <= 0:
        raise ValueError('Native command budget must be a positive integer')
    if len(path) > max_commands:
        _fail('budget', 'Native path command budget exceeded')
    if type(path.attrib) is not dict or len(path.attrib) > 5:
        _fail('native_xml', 'Native path attribute container exceeds the bounded schema')
    budget.spend(len(path.attrib))
    if (any(type(key) is not str or type(value) is not str for key, value in path.attrib.items()) or
            not set(path.attrib) <= {'w', 'h', 'fill', 'stroke', 'extrusionOk'}):
        _fail('native_xml', 'Unsupported native path attributes')
    width, height = [_integer(path.get(key), budget, True) for key in ('w', 'h')]
    if path.get('fill', 'norm') != 'norm':
        _fail('native_fill_mode', 'Only full native path fill is supported')
    if any(path.get(key, 'true') not in ('true', 'false', '0', '1') for key in ('stroke', 'extrusionOk')):
        _fail('native_xml', 'Invalid native path Boolean attribute')
    _whitespace(path.text, budget)
    _whitespace(path.tail, budget)
    commands = []
    kinds = {_A+'moveTo': ('M', 1), _A+'lnTo': ('L', 1),
             _A+'cubicBezTo': ('C', 3), _A+'close': ('Z', 0)}
    for node in path:
        budget.spend()
        if (type(node) is not ET.Element or type(node.tag) is not str or
                node.tag not in kinds or type(node.attrib) is not dict or node.attrib):
            _fail('native_xml', 'Unsupported native path command or attributes')
        op, count = kinds[node.tag]
        if len(node) != count:
            _fail('native_xml', 'Native command has the wrong number of points')
        _whitespace(node.text, budget)
        _whitespace(node.tail, budget)
        points = []
        for point in node:
            budget.spend()
            if (type(point) is not ET.Element or type(point.tag) is not str or
                    point.tag != _A+'pt' or type(point.attrib) is not dict or
                    len(point.attrib) != 2 or any(type(key) is not str for key in point.attrib) or
                    set(point.attrib) != {'x', 'y'} or len(point)):
                _fail('native_xml', 'Unsupported native point structure')
            _whitespace(point.text, budget)
            _whitespace(point.tail, budget)
            points.append(tuple(_integer(point.get(key), budget) for key in ('x', 'y')))
        commands.append((op, *points))
    return {'commands': commands, 'dimensions': [width, height], 'attributes': dict(path.attrib)}


def decode_native_cubic_path(path_element, *, max_commands=512, max_operations=1_000_000):
    """Decode existing XML integers without treating path dimensions as a clip.

    Negative or out-of-frame controls are retained under a signed-32-bit budget.
    Only the standard namespace and explicit M/L/C/Z path children are accepted.
    The caller parses XML; this API accepts an Element, never XML text or a file.
    """
    if type(max_operations) is not int or max_operations <= 0:
        raise ValueError('Native operation budget must be a positive integer')
    budget = _Arithmetic(max_operations)
    result = _decode(path_element, budget, max_commands)
    result['decode_operations'] = budget.used
    result['path_dimensions_are_not_a_clip'] = True
    return result


def _proof_mode(proof_mode, proof_split_depth):
    """Closed internal dispatch, selected before any proof; never a fallback."""
    if type(proof_mode) is not str or proof_mode not in ('isolated', 'endpoint_contact'):
        raise ValueError('Cubic proof mode must be isolated or endpoint_contact')
    if proof_mode == 'isolated':
        if proof_split_depth is not None:
            raise ValueError('Proof split depth is only applicable to endpoint_contact')
        return proof_mode, None
    depth = 1 if proof_split_depth is None else proof_split_depth
    if type(depth) is not int or depth not in (0, 1):
        raise ValueError('Contact proof depth must be built-in integer 0 or 1')
    return proof_mode, depth


def verify_native_cubic_path_encoding(original_path, candidate_path, *, slide_extents,
                                       max_slide_coordinate_error=F(1, 2),
                                       max_input_segments=128, max_commands=512,
                                       max_atomic_edges=2048, max_operations=1_000_000,
                                       max_probe_halvings=80, proof_mode='isolated',
                                       proof_split_depth=None):
    """Decode BOTH actual integer XML paths and reprove the proposed encoding.

    Default isolated behavior is unchanged. Explicit endpoint_contact uses only
    proved original-endpoint contacts and proof-only halves, with whole-C output.
    Identical path attributes and caller-bound positive slide extents are required.
    Only new L/L intersections may move, at most half a local grid unit AND the
    declared (<= half) slide EMU bound. Frame/paint/ancestor identity is external.
    """
    limits = _cubic._limits(max_input_segments, max_commands, max_atomic_edges,
                           max_operations, max_probe_halvings)
    budget = _Arithmetic(max_operations)
    return _verify_native_cubic_path_encoding_with_budget(
        original_path, candidate_path, limits, budget, slide_extents=slide_extents,
        max_slide_coordinate_error=max_slide_coordinate_error,
        proof_mode=proof_mode, proof_split_depth=proof_split_depth)


def _verify_native_cubic_path_encoding_with_budget(
        original_path, candidate_path, limits, budget, *, slide_extents,
        max_slide_coordinate_error=F(1, 2), proof_mode='isolated',
        proof_split_depth=None):
    """Private shared-counter entry; returned usage is this phase's delta.

    The caller must not spend the returned count again on the same counter.
    Source normalization is independently recomputed even if the caller already
    normalized before proposing the candidate. No caller receipt is consumed.
    """
    started = budget.used
    mode, depth = _proof_mode(proof_mode, proof_split_depth)
    original = _decode(original_path, budget, limits['commands'])
    candidate = _decode(candidate_path, budget, limits['atoms'] * 3 + 16)
    if original['attributes'] != candidate['attributes']:
        _fail('native_path_mapping', 'Candidate changed path dimensions or attributes')
    if type(slide_extents) not in (tuple, list) or len(slide_extents) != 2:
        _fail('native_frame', 'Two caller-bound slide extents are required')
    for extent in slide_extents:
        budget.spend()
        if type(extent) is not int or not 0 < extent <= _MAX_INTEGER:
            _fail('native_frame', 'Slide extents require positive bounded integers')
    maximum = _number(max_slide_coordinate_error, budget)
    if not 0 <= maximum <= F(1, 2):
        raise ValueError('Additional cubic encoding displacement may not exceed half a slide EMU')
    scales = [budget.div(F(v), F(d)) for v, d in zip(slide_extents, original['dimensions'])]
    local_bounds = [min(F(1, 2), budget.div(maximum, scale)) for scale in scales]
    if mode == 'isolated':
        proof = _cubic._verify_integer_encoding(
            original['commands'], candidate['commands'], limits, budget,
            max_coordinate_error=local_bounds,
            normalize_geometry=_cubic._normalize, candidate_geometry=_cubic._chord_rings)
    else:
        from .pdf_cubic_contact_winding import _verify_integer_with_budget
        proof = _verify_integer_with_budget(
            original['commands'], candidate['commands'], limits, budget,
            max_coordinate_error=local_bounds, proof_split_depth=depth)
    # These strings were produced by checked internal formatting, not supplied
    # by a caller. Their bounded conversion is charged as part of this receipt.
    budget.spend(2)
    errors = [budget.mul(F(v), scale) for v, scale in zip(proof['maximum_new_coordinate_error_local_units'], scales)]
    result = {'method': 'actual_integer_path_decode_and_strict_whole_cubic_reproof',
              'geometry': proof, 'path_attributes_and_dimensions_unchanged': True,
              'path_dimensions': original['dimensions'], 'provided_slide_extents': list(slide_extents),
              'slide_emu_per_local_unit': [budget.text(v) for v in scales],
              'maximum_new_coordinate_error_slide_emu': [budget.text(v) for v in errors],
              'declared_maximum_new_coordinate_error_slide_emu': budget.text(maximum),
              'path_dimensions_are_not_a_clip': True,
              'caller_must_bind_source_and_candidate_frame_and_paint': True,
              'parent_orientation_paint_and_source_identity_verified': False,
              'existing_source_to_initial_native_error_included': False,
              'rgb_alpha_error_bound': None}
    if mode == 'endpoint_contact':
        budget.spend(2)
        result.update(proof_mode=mode, proof_split_depth=depth)
    result['exact_predicate_operations_including_decode'] = budget.used - started
    return result
