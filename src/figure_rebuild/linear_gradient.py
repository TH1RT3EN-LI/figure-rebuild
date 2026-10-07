"""Bounded native linear fills for paths, without raster/color-band lowering."""
import math
import re
from xml.etree import ElementTree as ET

SVG = 'http://www.w3.org/2000/svg'


def validate_linear_gradient(style, kind='path', label='path'):
    """Validate an explicit, object-bounds-relative unscaled linear path fill.

    Stop offsets are normalized 0..1; colors are literal sRGB hex. The first
    and last stop must cover the complete axis. Repeated stops/hard edges,
    global coordinate axes and radial fills are unsupported.
    """
    if 'fill_gradient' not in style:
        return None
    gradient = style['fill_gradient']
    error = 'Invalid fill_gradient: ' + label
    if (kind != 'path' or not isinstance(gradient, dict)
            or set(gradient) != {'type', 'angle', 'stops'}
            or gradient['type'] != 'linear'
            or isinstance(gradient['angle'], bool)
            or not isinstance(gradient['angle'], (int, float))
            or not math.isfinite(gradient['angle']) or not 0 <= gradient['angle'] < 360
            or style.get('fill', 'none') != 'none'):
        raise ValueError(error)
    stops = gradient['stops']
    if not isinstance(stops, list) or not 2 <= len(stops) <= 64:
        raise ValueError(error)
    previous_units = -1
    for stop in stops:
        if not isinstance(stop, dict) or set(stop).difference({'offset', 'color', 'opacity'}):
            raise ValueError(error)
        offset, color, opacity = stop.get('offset'), stop.get('color'), stop.get('opacity', 1)
        if (isinstance(offset, bool) or not isinstance(offset, (int, float))
                or not math.isfinite(offset) or not 0 <= offset <= 1
                or not isinstance(color, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color)
                or isinstance(opacity, bool) or not isinstance(opacity, (int, float))
                or not math.isfinite(opacity) or not 0 <= opacity <= 1):
            raise ValueError(error)
        units = math.floor(offset * 100000 + .5)
        if units <= previous_units:
            raise ValueError('Gradient stops collapse or reverse at native precision: ' + label)
        previous_units = units
    if stops[0]['offset'] != 0 or stops[-1]['offset'] != 1:
        raise ValueError('Gradient must include offset 0 and 1: ' + label)
    return gradient


def gradient_axis(bounds, angle):
    """Native unscaled-angle axis: clockwise, projected across all box corners."""
    angle = (math.floor(angle * 60000 + .5) % 21600000) / 60000
    radians = math.radians(angle)
    dx, dy = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}.get(angle, (math.cos(radians), math.sin(radians)))
    width, height = max(.01, bounds['width']), max(.01, bounds['height'])
    length = abs(width * dx) + abs(height * dy)
    cx, cy = bounds['x'] + width/2, bounds['y'] + height/2
    return (cx-length*dx/2, cy-length*dy/2, cx+length*dx/2, cy+length*dy/2)


def native_path_frame(commands):
    """The authored native frame includes cubic controls, not curve extrema.

    Match powerpoint/curves.mjs:pathBounds: postprocessing keeps that frame
    while restoring exact cubic commands. Using SVG's geometry bounding box
    would change the gradient phase even when the paths themselves agree.
    """
    points = []
    for command in commands:
        for op, value in command.items():
            if op in ('moveTo', 'lineTo'):
                points.append((value['x'], value['y']))
            elif op == 'cubicTo':
                points.extend((value[x], value[y]) for x, y in (('x1', 'y1'), ('x2', 'y2'), ('x', 'y')))
            elif op != 'close':
                raise ValueError('Unsupported gradient path command')
    if not points:
        raise ValueError('Gradient path requires points')
    xs, ys = zip(*points)
    return {'x': min(xs), 'y': min(ys), 'width': max(.01, max(xs)-min(xs)),
            'height': max(.01, max(ys)-min(ys))}


def svg_linear_gradient(defs, gradient_id, style, bounds=None):
    """Write the same native-quantized stops to the inspectable SVG master."""
    gradient = validate_linear_gradient(style)
    if gradient is None:
        return None
    angle = (math.floor(gradient['angle'] * 60000 + .5) % 21600000) / 60000
    if bounds is None:
        raise ValueError('SVG gradient requires the authored native path frame')
    x1, y1, x2, y2 = map(str, gradient_axis(bounds, angle))
    units = 'userSpaceOnUse'
    node = ET.SubElement(defs, f'{{{SVG}}}linearGradient', {
        'id': gradient_id, 'gradientUnits': units,
        'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
        'color-interpolation': 'sRGB', 'spreadMethod': 'pad'})
    for stop in gradient['stops']:
        offset = math.floor(stop['offset'] * 100000 + .5) / 100000
        ET.SubElement(node, f'{{{SVG}}}stop', {
            'offset': str(offset), 'stop-color': stop['color'],
            'stop-opacity': str(stop.get('opacity', 1))})
    return 'url(#' + gradient_id + ')'


def apply_gradient_angle_precision(element, obj):
    """Correct only one-unit float serialization loss in an authored angle.

    Artifact receives degrees and can truncate one 1/60000-degree unit while
    serializing a fractional double. Write the requested native integer before
    full verification, and retain the correction in the editability receipt.
    Larger or structurally different changes are left for verification to reject.
    """
    gradient = validate_linear_gradient(obj.get('style', {}), obj.get('kind'), obj.get('id', 'path'))
    if gradient is None:
        return None
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}
    line = element.find('p:spPr/a:gradFill/a:lin', ns)
    if line is None:
        return None
    try:
        actual = int(line.get('ang', '0'))
    except (TypeError, ValueError):
        return None
    expected = math.floor(gradient['angle'] * 60000 + .5) % 21600000
    if actual != expected and abs(actual-expected) == 1:
        line.set('ang', str(expected))
        return {'exported_angle_units': actual, 'requested_angle_units': expected,
                'correction_units': expected-actual, 'unit_degrees': 1/60000}
    return None


def verify_native_gradient(element, obj):
    """Reject an exporter which drops or changes the requested native fill."""
    gradient = validate_linear_gradient(obj.get('style', {}), obj.get('kind'), obj.get('id', 'path'))
    if gradient is None:
        return None
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
          'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}
    failure = 'Exporter changed native linear gradient: ' + obj['id']
    sppr = element.find('p:spPr', ns)
    if sppr is None or sppr.find('a:custGeom', ns) is None:
        raise ValueError(failure)
    fills = [node for node in sppr if node.tag in {f'{{{ns["a"]}}}{tag}' for tag in
             ('noFill', 'solidFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill')}]
    if len(fills) != 1 or fills[0].tag != f'{{{ns["a"]}}}gradFill':
        raise ValueError(failure)
    fill = fills[0]
    if fill.get('flip', 'none') != 'none':
        raise ValueError(failure)
    expected_children = [f'{{{ns["a"]}}}gsLst', f'{{{ns["a"]}}}lin']
    if [child.tag for child in fill] != expected_children:
        raise ValueError(failure)
    line = fill.find('a:lin', ns)
    angle = math.floor(gradient['angle'] * 60000 + .5) % 21600000
    try:
        if int(line.get('ang', '0')) != angle or line.get('scaled', '0') not in ('0', 'false'):
            raise ValueError(failure)
        native_stops = list(fill.find('a:gsLst', ns))
        if len(native_stops) != len(gradient['stops']):
            raise ValueError(failure)
        for actual, expected in zip(native_stops, gradient['stops']):
            if actual.tag != f'{{{ns["a"]}}}gs' or int(actual.get('pos', '-1')) != math.floor(expected['offset']*100000+.5):
                raise ValueError(failure)
            colors = list(actual)
            if len(colors) != 1 or colors[0].tag != f'{{{ns["a"]}}}srgbClr' or colors[0].get('val', '').upper() != expected['color'][1:].upper():
                raise ValueError(failure)
            transforms = list(colors[0])
            if len(transforms) > 1 or any(t.tag != f'{{{ns["a"]}}}alpha' for t in transforms):
                raise ValueError(failure)
            opacity = int(transforms[0].get('val', '-1')) if transforms else 100000
            expected_opacity = math.floor(expected.get('opacity', 1)*obj.get('style', {}).get('opacity', 1)*100000+.5)
            if opacity != expected_opacity:
                raise ValueError(failure)
    except (TypeError, OverflowError) as exc:
        raise ValueError(failure) from exc
    return {'id': obj['id'], 'native_fill_verified': True, 'angle_units': angle,
            'scaled': False, 'stop_count': len(native_stops),
            'stop_precision': 100000, 'alpha_composition_verified': True,
            'visual_verification_required': True}
