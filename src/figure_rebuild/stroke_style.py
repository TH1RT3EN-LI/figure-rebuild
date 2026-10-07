"""Explicit path stroke geometry in SVG terms and native DrawingML.

Omitted properties leave the author's existing line untouched. An explicitly
selected miter join defaults to a limit of 4; a different limit requires an
explicit miter join. DrawingML percentages use 100000 units per ratio of 1.
Native XML preservation does not imply renderer support: the audited Artifact
Tool 2.8.59 importer ignores these properties and drops them on re-export.
"""
import math
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'a': A, 'p': P}
STROKE_PROPERTIES = frozenset({'stroke_linecap', 'stroke_linejoin', 'stroke_miterlimit', 'stroke_hairline'})
CAPS = {'butt': 'flat', 'round': 'rnd', 'square': 'sq'}
JOINS = frozenset({'miter', 'round', 'bevel'})


def _miter_units(value, label):
    try:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('Invalid stroke_miterlimit: ' + label)
    # Bound before multiplying so even enormous finite values fail cleanly.
    if value > 2147483647 / 100000:
        raise ValueError('stroke_miterlimit exceeds DrawingML range: ' + label)
    units = int((Decimal(str(value)) * 100000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if not 1 <= units <= 2147483647:
        raise ValueError('stroke_miterlimit vanishes at DrawingML precision: ' + label)
    return units


def validate_stroke_style(style, kind='path', label='path'):
    """Validate only the optional stroke geometry fields; return their values."""
    if not isinstance(style, dict):
        raise ValueError('Path style must be a record: ' + label)
    explicit = {key: style[key] for key in ('stroke_linecap', 'stroke_linejoin', 'stroke_miterlimit', 'stroke_hairline') if key in style}
    if explicit and kind != 'path':
        raise ValueError('Stroke cap/join properties require a path: ' + label)
    if 'stroke_hairline' in explicit:
        color, width, opacity = style.get('stroke'), style.get('stroke_width'), style.get('opacity', 1)
        fill = style.get('fill', 'none')
        if (explicit['stroke_hairline'] is not True or type(width) not in (int, float) or width != 0 or
                not isinstance(color, str) or len(color) != 7 or color[0] != '#' or
                any(c not in '0123456789abcdefABCDEF' for c in color[1:]) or
                (fill != 'none' and (not isinstance(fill, str) or len(fill) != 7 or fill[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in fill[1:]))) or 'fill_gradient' in style or
                isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or
                not 0 < opacity <= 1 or not math.isfinite(opacity)):
            raise ValueError('stroke_hairline requires true, zero width, solid-or-none fill and visible solid stroke: ' + label)
        if int((Decimal(str(opacity)) * 100000).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) < 1:
            raise ValueError('Device hairline opacity vanishes at native precision: ' + label)
    for key, allowed in [('stroke_linecap', CAPS), ('stroke_linejoin', JOINS)]:
        if key in explicit and (not isinstance(explicit[key], str) or explicit[key] not in allowed):
            raise ValueError('Invalid ' + key + ': ' + label)
    if 'stroke_miterlimit' in explicit:
        _miter_units(explicit['stroke_miterlimit'], label)
        if explicit.get('stroke_linejoin') != 'miter':
            raise ValueError('stroke_miterlimit requires stroke_linejoin=miter: ' + label)
    return explicit


def svg_stroke_attributes(style):
    """Only declared SVG attributes; omitted properties preserve old exports."""
    explicit = validate_stroke_style(style)
    attrs = {key.replace('_', '-'): str(value) for key, value in explicit.items() if key != 'stroke_hairline'}
    if explicit.get('stroke_hairline'):
        # SVG width zero is invisible. Retain the native-device intent rather
        # than inventing a fixed SVG width that changes with preview scale.
        attrs['data-figure-rebuild-device-hairline'] = 'native-zero-width'
    return attrs


def apply_stroke_style(element, obj):
    """Apply explicit line geometry without rewriting paint, width or paths."""
    label = str(obj.get('id', '(unknown)'))
    explicit = validate_stroke_style(obj.get('style', {}), obj.get('kind'), label)
    if not explicit:
        return None
    if element.tag != f'{{{P}}}sp' or element.find('p:spPr/a:custGeom', NS) is None:
        raise ValueError('Stroke geometry requires a native custom path: ' + label)
    line = element.find('p:spPr/a:ln', NS)
    if line is None:
        raise ValueError('Stroke geometry requires an authored native line: ' + label)
    paths = element.findall('p:spPr/a:custGeom/a:pathLst/a:path', NS)
    if explicit.get('stroke_hairline') and len(paths) != 1:
        raise ValueError('Device hairline requires one native custom path: ' + label)
    result = {'id': label, **explicit, 'native_geometry_written': True,
              'preview_renderer_support': 'not_verified_or_unsupported',
              'visual_verification_required': True}
    join = explicit.get('stroke_linejoin')
    miter_units = _miter_units(explicit.get('stroke_miterlimit', 4), label) if join == 'miter' else None
    # Validate before mutation. Ordinary requests change only cap and join;
    # explicit hairlines additionally restore zero-width paint and stroke state.
    if explicit.get('stroke_hairline'):
        line.set('w', '0')
        for child in list(line):
            if child.tag in {f'{{{A}}}{name}' for name in ('noFill', 'solidFill', 'gradFill', 'pattFill', 'blipFill')}:
                line.remove(child)
        paint = ET.Element(f'{{{A}}}solidFill')
        color = ET.SubElement(paint, f'{{{A}}}srgbClr', {'val': obj['style']['stroke'][1:].upper()})
        opacity = obj['style'].get('opacity', 1)
        if opacity != 1:
            units = int((Decimal(str(opacity)) * 100000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            ET.SubElement(color, f'{{{A}}}alpha', {'val': str(units)})
        line.insert(0, paint)
        paths[0].set('stroke', '1')
        result.update(native_stroke_width_emu=0, native_path_stroke_enabled=True,
                      native_zero_width_written=True, svg_device_hairline_rendering='unsupported',
                      pdf_linewidth_and_pixel_verification='required',
                      application_minimum_width_behavior='not_verified')
    if 'stroke_linecap' in explicit:
        cap = CAPS[explicit['stroke_linecap']]
        line.set('cap', cap)
        result['native_cap'] = cap
    if join is not None:
        for child in list(line):
            if child.tag in {f'{{{A}}}{name}' for name in JOINS}:
                line.remove(child)
        node = ET.Element(f'{{{A}}}{join}', {'lim': str(miter_units)} if join == 'miter' else {})
        # CT_LineProperties places the join after dash and before line ends.
        at = next((i for i, child in enumerate(line) if child.tag in {
            f'{{{A}}}headEnd', f'{{{A}}}tailEnd', f'{{{A}}}extLst'}), len(line))
        line.insert(at, node)
        result['native_join'] = join
        if miter_units is not None:
            result['native_miter_limit_units'] = miter_units
    return result


def verify_hairline_xml(element, obj):
    """Read actual zero-width paint independently of the mutation routine."""
    style = obj.get('style', {})
    if style.get('stroke_hairline') is not True:
        return None
    validate_stroke_style(style, obj.get('kind'), obj['id'])
    label = obj['id']
    expected = int((Decimal(str(style.get('opacity', 1))) * 100000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    props = element.find('p:spPr', NS)
    if element.tag != f'{{{P}}}sp' or props is None:
        raise ValueError('Device hairline must remain a native path: ' + label)
    if any(props.find('a:' + k, NS) is not None for k in ('gradFill', 'pattFill', 'blipFill', 'effectLst', 'effectDag', 'scene3d', 'sp3d')):
        raise ValueError('Unsupported device hairline native fill or effect: ' + label)
    fills = [c for c in props if c.tag in {f'{{{A}}}noFill', f'{{{A}}}solidFill'}]
    fill = style.get('fill', 'none')
    if len(fills) != 1 or (fill == 'none' and (fills[0].tag != f'{{{A}}}noFill' or fills[0].attrib or len(fills[0]))):
        raise ValueError('Actual native hairline shape fill disagrees: ' + label)
    if fill != 'none':
        colors = fills[0].findall('a:srgbClr', NS)
        if fills[0].tag != f'{{{A}}}solidFill' or fills[0].attrib or len(fills[0]) != 1 or len(colors) != 1 or colors[0].get('val') != fill[1:].upper():
            raise ValueError('Actual native hairline shape color disagrees: ' + label)
        alpha = colors[0].findall('a:alpha', NS)
        if colors[0].attrib != {'val': fill[1:].upper()} or len(alpha) > 1 or any(c.tag != f'{{{A}}}alpha' or len(c) for c in colors[0]) or (alpha and alpha[0].attrib != {'val': str(expected)}) or (not alpha and expected != 100000):
            raise ValueError('Actual native hairline shape alpha disagrees: ' + label)
    paths = props.findall('a:custGeom/a:pathLst/a:path', NS)
    lines = props.findall('a:ln', NS)
    if len(paths) != 1 or paths[0].get('stroke', '1') not in ('1', 'true') or len(lines) != 1 or lines[0].get('w') != '0':
        raise ValueError('Actual native device hairline width/stroke state disagrees: ' + label)
    line = lines[0]
    colors = line.findall('a:solidFill/a:srgbClr', NS)
    paints = [c for c in line if c.tag in {f'{{{A}}}' + k for k in ('noFill', 'solidFill', 'gradFill', 'pattFill', 'blipFill')}]
    if len(paints) != 1 or paints[0].attrib or len(paints[0]) != 1 or len(colors) != 1 or colors[0].attrib != {'val': style['stroke'][1:].upper()}:
        raise ValueError('Actual native hairline color disagrees: ' + label)
    color = colors[0]; alpha = color.findall('a:alpha', NS)
    if len(alpha) > 1 or any(c.tag != f'{{{A}}}alpha' or len(c) for c in color) or (alpha and alpha[0].attrib != {'val': str(expected)}) or (not alpha and expected != 100000):
        raise ValueError('Actual native hairline alpha disagrees: ' + label)
    if 'stroke_linecap' in style and line.get('cap', 'flat') != CAPS[style['stroke_linecap']]:
        raise ValueError('Actual native hairline cap disagrees: ' + label)
    if 'stroke_linejoin' in style:
        joins = [c for c in line if c.tag in {f'{{{A}}}' + k for k in JOINS}]
        join = style['stroke_linejoin']
        expected_attrs = {'lim': str(_miter_units(style.get('stroke_miterlimit', 4), label))} if join == 'miter' else {}
        if len(joins) != 1 or joins[0].tag != f'{{{A}}}{join}' or joins[0].attrib != expected_attrs or len(joins[0]):
            raise ValueError('Actual native hairline join disagrees: ' + label)
    return {'id': label, 'native_line_width_emu': 0, 'native_path_stroke_enabled': True,
            'native_color': style['stroke'].upper(), 'native_alpha_units': expected,
            'native_shape_xml_sha256': hashlib.sha256(ET.canonicalize(ET.tostring(element), rewrite_prefixes=True).encode()).hexdigest(),
            'pdf_linewidth_verified': False, 'application_playback_verified': False}


def verify_native_hairlines(pptx, manifest):
    """Verify declared hairlines in the actual ungrouped standalone slide."""
    if type(manifest) is not dict or type(manifest.get('objects')) is not list or len(manifest['objects']) > 10000 or any(type(o) is not dict for o in manifest['objects']):
        raise ValueError('Invalid native hairline manifest or object budget')
    requested = [o for o in manifest['objects'] if o.get('style', {}).get('stroke_hairline') is True]
    if not requested:
        return []
    from pathlib import Path
    from zipfile import ZipFile
    from .package import XmlDocument, checked_part_name
    from .placement import require_identity_shape_tree
    import re
    path = Path(pptx)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Device hairline package byte budget')
    with ZipFile(path) as package:
        entries = package.infolist()
        if len(entries) > 10000 or len({i.filename for i in entries}) != len(entries) or sum(i.file_size for i in entries) > 128 * 1024 * 1024:
            raise ValueError('Device hairline package expansion budget')
        for item in entries: checked_part_name(item.filename)
        slides = [i for i in entries if re.fullmatch(r'ppt/slides/[^/]+\.xml', i.filename)]
        if len(slides) != 1 or slides[0].file_size > 8 * 1024 * 1024:
            raise ValueError('Device hairline requires one bounded native slide')
        root = XmlDocument.parse(package.read(slides[0]), 'hairline slide').root
    tree = root.find('p:cSld/p:spTree', NS)
    require_identity_shape_tree(tree, 'hairline slide')
    shapes = {}
    for element in tree.findall('p:sp', NS):
        prop = element.find('p:nvSpPr/p:cNvPr', NS)
        if prop is None or prop.get('name') in shapes:
            raise ValueError('Missing or duplicate native hairline object identity')
        shapes[prop.get('name')] = element
    records = []
    for obj in requested:
        if obj['id'] not in shapes:
            raise ValueError('Device hairline must be a top-level native path: ' + obj['id'])
        records.append(verify_hairline_xml(shapes[obj['id']], obj))
    return records
