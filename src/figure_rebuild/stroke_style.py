"""Explicit path stroke geometry in SVG terms and native DrawingML.

Omitted properties leave the author's existing line untouched. An explicitly
selected miter join defaults to a limit of 4; a different limit requires an
explicit miter join. DrawingML percentages use 100000 units per ratio of 1.
Native XML preservation does not imply renderer support: the audited Artifact
Tool 2.8.59 importer ignores these properties and drops them on re-export.
"""
import math
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'a': A, 'p': P}
STROKE_PROPERTIES = frozenset({'stroke_linecap', 'stroke_linejoin', 'stroke_miterlimit'})
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
    explicit = {key: style[key] for key in ('stroke_linecap', 'stroke_linejoin', 'stroke_miterlimit') if key in style}
    if explicit and kind != 'path':
        raise ValueError('Stroke cap/join properties require a path: ' + label)
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
    return {key.replace('_', '-'): str(value) for key, value in explicit.items()}


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
    result = {'id': label, **explicit, 'native_geometry_written': True,
              'preview_renderer_support': 'not_verified_or_unsupported',
              'visual_verification_required': True}
    join = explicit.get('stroke_linejoin')
    miter_units = _miter_units(explicit.get('stroke_miterlimit', 4), label) if join == 'miter' else None
    # All validation is complete before modifying XML. Keep fill/dash/endpoints
    # and the path/frame byte values unchanged; only cap and join are selected.
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
