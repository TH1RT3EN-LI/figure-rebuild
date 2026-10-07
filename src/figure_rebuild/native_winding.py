"""Proved polygon fills for native consumers with even-odd path behavior.

The manifest and shape paint/frame stay unchanged. This module commits a new
single compound path only after exact final-grid topology/error validation.
"""
from fractions import Fraction as Q
import hashlib
import math
from xml.etree import ElementTree as ET

from .pdf_winding import (normalize_nonzero_polygons, verify_simple_loop_topology,
                          UnsupportedPdfWindingError)

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
EMU_PER_PX = 9525
MAX_NATIVE_INTEGER = 2**31 - 1  # Conservative implementation budget, not a schema claim.
POLICY = 'polygon_nonzero_half_ulp_and_1_over_1024_slide_emu_v1'


def _fail(code, message):
    raise UnsupportedPdfWindingError(code, message)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        _fail('invalid_number', 'Native polygon values must be finite numbers')
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        _fail('invalid_number', 'Native polygon values must be finite numbers')
    return Q(value)


def _text(value):
    try:
        return str(value)
    except ValueError as error:
        raise UnsupportedPdfWindingError('budget', 'Native polygon receipt exceeds runtime decimal formatting budget') from error


def _bound(value):
    try:
        result = float(value)
    except OverflowError as error:
        raise UnsupportedPdfWindingError('numeric_range', 'Native polygon error exceeds binary64 range') from error
    if not math.isfinite(result):
        _fail('numeric_range', 'Native polygon error exceeds binary64 range')
    if Q(result) < value:
        result = math.nextafter(result, math.inf)
    return result


def _integer(value, label, positive=False):
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise UnsupportedPdfWindingError('native_integer', 'Invalid native polygon ' + label) from error
    if str(result) != value or abs(result) > MAX_NATIVE_INTEGER or (positive and result <= 0):
        _fail('native_integer', 'Native polygon ' + label + ' exceeds the integer budget')
    return result


def _commands(obj):
    result = []
    for command in obj.get('commands', []):
        if not isinstance(command, dict) or len(command) != 1:
            _fail('invalid_commands', 'Invalid scene polygon command')
        op, values = next(iter(command.items()))
        if op == 'close' and values == {}:
            result.append(('Z',))
        elif op in ('moveTo', 'lineTo') and isinstance(values, dict) and set(values) == {'x', 'y'}:
            _finite(values['x']); _finite(values['y'])
            result.append(('M' if op == 'moveTo' else 'L', [values['x'], values['y']]))
        else:
            _fail('unsupported_commands', 'Only scene M/L/Z polygons are supported')
    return result


def _paint(element, obj):
    style = obj.get('style', {})
    if not isinstance(style, dict):
        _fail('invalid_paint', 'Scene path style must be a record')
    # The scene path model has nonzero semantics; do not infer equivalence for
    # an explicitly different rule in an unvalidated caller's object.
    for container in (obj, style):
        if any(container.get(key, 'nonzero') != 'nonzero' for key in ('fill_rule', 'fill-rule')):
            _fail('unsupported_fill_rule', 'Explicit nonzero scene fill is required')
    fill = style.get('fill', 'none')
    if not isinstance(fill, str) or len(fill) != 7 or fill[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in fill[1:]):
        _fail('unsupported_fill', 'Only a declared solid scene fill is supported')
    if style.get('fill_gradient') is not None or style.get('stroke', 'none') != 'none':
        _fail('unsupported_paint', 'Polygon normalization requires a solid fill and no source stroke')
    opacity = _finite(style.get('opacity', 1))
    if not 0 < opacity <= 1:
        _fail('unsupported_opacity', 'Polygon normalization requires a visible finite opacity')
    props = element.find('p:spPr', NS)
    if props is None:
        _fail('missing_shape', 'Missing native shape properties')
    if any(props.find('a:' + tag, NS) is not None for tag in ('effectLst', 'effectDag', 'scene3d', 'sp3d')):
        _fail('unsupported_effect', 'Native polygon effects have not been proved')
    fill_nodes = [c for c in props if c.tag.rsplit('}', 1)[-1] in ('solidFill', 'noFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill')]
    if len(fill_nodes) != 1 or fill_nodes[0].tag != '{' + NS['a'] + '}solidFill' or len(fill_nodes[0]) != 1:
        _fail('native_paint_mismatch', 'Native polygon fill is not the declared solid fill')
    color = fill_nodes[0][0]
    if color.tag != '{' + NS['a'] + '}srgbClr' or color.get('val', '').lower() != fill[1:].lower():
        _fail('native_paint_mismatch', 'Native polygon color differs from the scene')
    if any(c.tag != '{' + NS['a'] + '}alpha' for c in color) or len(color) > 1:
        _fail('native_paint_mismatch', 'Native polygon color contains unsupported transforms')
    actual_alpha = 100000 if not len(color) else _integer(color[0].get('val'), 'alpha')
    expected_alpha = math.floor(float(opacity) * 100000 + .5)
    if actual_alpha != expected_alpha or not 0 <= actual_alpha <= 100000:
        _fail('native_paint_mismatch', 'Native polygon opacity differs from the scene serialization')
    line = props.find('a:ln', NS)
    if line is None or line.find('a:noFill', NS) is None:
        _fail('native_stroke_ambiguous', 'Native polygon must explicitly declare no line fill')
    if any(line.find('a:' + tag, NS) is not None for tag in ('solidFill', 'gradFill', 'pattFill')):
        _fail('native_stroke_ambiguous', 'Native polygon line has conflicting paint')
    return {'scene_srgb': fill.lower(), 'scene_opacity_exact': _text(opacity),
            'native_alpha_units': actual_alpha, 'opacity_serialization_unit': 100000,
            'existing_alpha_error_exact': _text(abs(opacity - Q(actual_alpha, 100000))),
            'fill_opacity_and_stroke_unchanged': True}


def root_group_is_identity(tree):
    """Require the slide's parent transform to be absent or exactly identity."""
    props = tree.find('p:grpSpPr', NS)
    if props is None or not len(props):
        return True
    if len(props) != 1 or props[0].tag != '{' + NS['a'] + '}xfrm':
        return False
    transform = props[0]
    # The authoring exporter emits this parameter-free root transform. It has
    # no coordinate remapping; actual native empty-vs-absent regression covers
    # this representation independently of nonempty group transforms.
    if not len(transform) and not transform.attrib:
        return True
    if transform.get('rot', '0') != '0' or any(transform.get(k, '0') not in ('0', 'false') for k in ('flipH', 'flipV')):
        return False
    if len(transform) != 4:
        return False
    try:
        values = {}
        for tag, keys in [('off', ('x', 'y')), ('ext', ('cx', 'cy')),
                          ('chOff', ('x', 'y')), ('chExt', ('cx', 'cy'))]:
            node = transform.find('a:' + tag, NS)
            if node is None:
                return False
            values[tag] = [_integer(node.get(key), 'parent ' + key, positive=tag in ('ext', 'chExt')) for key in keys]
        return values['off'] == values['chOff'] and values['ext'] == values['chExt']
    except UnsupportedPdfWindingError:
        return False


def normalize_native_polygon_fill(element, obj, entry, *, parent_identity=False):
    """Return a per-paint receipt; unsupported input leaves geometry untouched."""
    label = obj.get('id')
    base = {'id': label, 'policy': POLICY, 'manifest_modified': False,
            'geometry_preserved': True, 'visual_review_required': False}
    commands = obj.get('commands', [])
    if not isinstance(commands, (tuple, list)) or len(commands) > 1040:
        return {**base, 'status': 'rejected', 'reason_code': 'budget',
                'reason': 'Native polygon input exceeds the 1040-command budget', 'visual_review_required': True}
    if any(isinstance(c, dict) and 'cubicTo' in c for c in commands):
        return {**base, 'status': 'not_applicable', 'reason_code': 'curves_not_supported',
                'reason': 'Cubic geometry is retained; no curve flattening is performed'}
    try:
        if not parent_identity:
            _fail('unsupported_parent_transform', 'Native polygon parent transform is not proved identity')
        paint = _paint(element, obj)
        source = _commands(obj)
        if not isinstance(entry, dict) or entry.get('kind') != 'path':
            _fail('missing_map', 'Native polygon normalization requires a mapped path frame')
        context = entry.get('source_to_slide')
        if not isinstance(context, dict):
            _fail('missing_map', 'Native polygon normalization requires the declared placement mapping')
        tx, ty = _finite(context.get('translate_x')), _finite(context.get('translate_y'))
        numerator, denominator = _finite(context.get('scale_numerator')), _finite(context.get('scale_denominator'))
        if numerator <= 0 or denominator <= 0:
            _fail('invalid_map', 'Native polygon placement scale must be positive')
        scale = numerator / denominator
        box = entry.get('source_box', {})
        if not isinstance(box, dict):
            _fail('invalid_map', 'Native polygon source frame must be a record')
        bx, by, bw, bh = [_finite(box.get(k)) for k in ('x', 'y', 'width', 'height')]
        if bw <= 0 or bh <= 0:
            _fail('invalid_map', 'Native polygon source frame must be positive')
        props = element.find('p:spPr', NS)
        transform = props.find('a:xfrm', NS)
        if transform is None:
            _fail('missing_frame', 'Missing native polygon transform')
        if transform.get('rot', '0') != '0' or transform.get('flipH', '0') not in ('0', 'false') or transform.get('flipV', '0') not in ('0', 'false'):
            _fail('unsupported_transform', 'Native polygon orientation is not axis aligned')
        off, ext = transform.find('a:off', NS), transform.find('a:ext', NS)
        if off is None or ext is None:
            _fail('missing_frame', 'Missing native polygon frame components')
        ox, oy = [_integer(off.get(k), k) for k in ('x', 'y')]
        ex, ey = [_integer(ext.get(k), k, positive=True) for k in ('cx', 'cy')]
        actual_frame = [ox, oy, ex, ey]
        ideal_frame = [(tx + bx*scale)*EMU_PER_PX, (ty + by*scale)*EMU_PER_PX,
                       bw*scale*EMU_PER_PX, bh*scale*EMU_PER_PX]
        frame_errors = [abs(Q(a)-b) for a, b in zip(actual_frame, ideal_frame)]
        if max(frame_errors) > 2:
            _fail('frame_mismatch', 'Native polygon frame differs from the declared mapping by more than 2 EMU')
        geometry = props.find('a:custGeom', NS)
        paths = geometry.find('a:pathLst', NS) if geometry is not None else None
        if paths is None or len(paths) != 1:
            _fail('native_path_count', 'Exactly one native compound path is required')
        original = paths[0]
        if original.tag != '{' + NS['a'] + '}path' or original.get('fill', 'norm') != 'norm':
            _fail('native_fill_mode', 'Native path must use the full shape fill')
        _integer(original.get('w'), 'path width', positive=True)
        _integer(original.get('h'), 'path height', positive=True)
        coordinates = [abs(float(v)) for c in source if len(c) > 1 for v in c[1]]
        magnitude = max([1.0] + coordinates)
        half_ulp = Q(math.ulp(magnitude)) / 2
        cap = Q(1, EMU_PER_PX * 1024) / scale
        tolerance = min(half_ulp, cap)
        normalized = normalize_nonzero_polygons(source, max_coordinate_error=tolerance)
        if not normalized['commands']:
            return {**base, 'status': 'not_applicable', 'reason_code': 'empty_nonzero_fill',
                    'reason': 'Original empty fill retained', 'normalization': normalized['proof']}
        attrs = dict(original.attrib)
        attrs.update(w=str(ex), h=str(ey))
        replacement = ET.Element(original.tag, attrs)
        for command in normalized['commands']:
            if command[0] == 'Z':
                ET.SubElement(replacement, '{' + NS['a'] + '}close')
                continue
            x, y = (Q(v) for v in command[1])
            # Actual off/ext remain fixed; w/h=ext makes local units 1 EMU.
            # Subtracting the actual integer offset compensates its rounding.
            points = [round((tx+x*scale)*EMU_PER_PX-ox),
                      round((ty+y*scale)*EMU_PER_PX-oy)]
            if any(abs(v) > MAX_NATIVE_INTEGER for v in points):
                _fail('native_integer', 'Native polygon point exceeds the integer budget')
            node = ET.SubElement(replacement, '{' + NS['a'] + '}' + ('moveTo' if command[0] == 'M' else 'lnTo'))
            ET.SubElement(node, '{' + NS['a'] + '}pt', {'x': str(points[0]), 'y': str(points[1])})
        # Decode the real XML values, including dimensions, after serialization.
        replacement = ET.fromstring(ET.tostring(replacement))
        pw, ph = [_integer(replacement.get(k), 'final path ' + k, positive=True) for k in ('w', 'h')]
        reflected, local_points = [], []
        for node in replacement:
            if node.tag == '{' + NS['a'] + '}close':
                reflected.append(('Z',)); continue
            p = node.find('a:pt', NS)
            px, py = [_integer(p.get(k), 'final point ' + k) for k in ('x', 'y')]
            local_points.append([px, py])
            x = ((Q(ox)+Q(px*ex, pw))/EMU_PER_PX-tx)/scale
            y = ((Q(oy)+Q(py*ey, ph))/EMU_PER_PX-ty)/scale
            reflected.append(('M' if node.tag == '{' + NS['a'] + '}moveTo' else 'L', [x, y]))
        topology = verify_simple_loop_topology(normalized['commands'], reflected)
        native_error = Q(topology['maximum_vertex_coordinate_delta_exact'])*scale*EMU_PER_PX
        if native_error > Q(1, 2):
            _fail('native_coordinate_error', 'Final native polygon coordinates exceed 0.5 EMU')
        float_error = Q(normalized['proof']['coordinate_export']['maximum_coordinate_error_exact'])
        total_error = float_error*scale*EMU_PER_PX + native_error
        record = {**base, 'status': 'applied', 'geometry_preserved': False, 'visual_review_required': True,
                  'normalization': normalized['proof'], 'paint_verification': paint,
                  'original_geometry_sha256': hashlib.sha256(ET.tostring(original)).hexdigest(),
                  'final_geometry_sha256': hashlib.sha256(ET.tostring(replacement)).hexdigest(),
                  'compound_native_path_count': 1, 'native_frame_unchanged': True,
                  'parent_transform_verified_identity': True,
                  'source_to_slide_exact': {'translate_x': _text(tx), 'translate_y': _text(ty), 'scale': _text(scale)},
                  'roundoff_policy': {'half_ulp_scene_px_exact': _text(half_ulp),
                      'hard_cap_scene_px_exact': _text(cap), 'permitted_scene_px_exact': _text(tolerance),
                      'hard_cap_slide_emu_exact': '1/1024', 'native_point_cap_slide_emu_exact': '1/2'},
                  'native_grid': {'actual_frame_emu': actual_frame, 'path_dimensions': [pw, ph],
                      'original_frame_errors_emu_exact': [_text(v) for v in frame_errors],
                      'original_frame_tolerance_emu': 2, 'frame_roundoff_compensated_by_local_points': True,
                      'negative_local_point_count': sum(any(v < 0 for v in p) for p in local_points),
                      'topology_verification': topology, 'maximum_point_error_emu_exact': _text(native_error),
                      'maximum_point_error_emu_bound': _bound(native_error),
                      'maximum_total_geometry_error_emu_exact': _text(total_error),
                      'maximum_total_geometry_error_emu_bound': _bound(total_error)},
                  'pointwise_geometry_identical': total_error == 0, 'rgb_alpha_error_bound': None}
        # The only mutation occurs after every proof and receipt is complete.
        paths.remove(original)
        paths.append(replacement)
        return record
    except UnsupportedPdfWindingError as error:
        return {**base, 'status': 'rejected', 'reason_code': error.code, 'reason': str(error),
                'details': error.details, 'visual_review_required': True}
