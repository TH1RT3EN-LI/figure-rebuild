"""Bounded, preview-only SVG strokes decoded from the delivered native PPTX.

The Artifact importer drops custom-path cap/join fields. This adapter covers
explicit, solid, unfilled, unrotated top-level paths. It reads the real native
coordinates and paint, never re-exports or changes the editable delivery.
Unsupported paths remain in the ordinary preview and are reported explicitly.
"""
import argparse
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile

from .package import XmlDocument, checked_part_name
from .placement import require_identity_shape_tree
from .stroke_style import CAPS, validate_stroke_style, _miter_units

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
EMU = 9525
POLICY = 'delivered-native-solid-unfilled-stroke-svg-v1'
MAX_BYTES = 128 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_COMMANDS = 200000
MAX_DECODE_PIXELS = 64 * 1024 * 1024  # Combined 8x SVG decode surfaces.


class UnsupportedStroke(ValueError):
    pass


def _one(node, path):
    rows = node.findall(path, NS)
    if len(rows) != 1:
        raise UnsupportedStroke('unsupported or missing native field: ' + path)
    return rows[0]


def _integer(value, label, positive=False):
    if not isinstance(value, str) or not value.lstrip('-').isdigit():
        raise UnsupportedStroke('invalid native integer: ' + label)
    result = int(value)
    if abs(result) > 2147483647 or (positive and result <= 0):
        raise UnsupportedStroke('native integer budget: ' + label)
    return result


def _number(value):
    if not math.isfinite(value):
        raise UnsupportedStroke('nonfinite native coordinate')
    return format(value, '.17g')


def _svg(shape, obj):
    style = obj.get('style', {})
    explicit = validate_stroke_style(style, 'path', obj['id'])
    sp = _one(shape, 'p:spPr')
    if _one(shape, 'p:nvSpPr/p:cNvPr').get('hidden', '0') not in ('0', 'false') or any(
            sp.find('a:' + name, NS) is not None for name in ('effectLst', 'effectDag', 'scene3d', 'sp3d')):
        raise UnsupportedStroke('hidden path or unsupported native effect')
    if sp.find('a:noFill', NS) is None or sp.find('a:solidFill', NS) is not None or sp.find('a:gradFill', NS) is not None:
        raise UnsupportedStroke('only unfilled source paths are supported')
    xf = _one(sp, 'a:xfrm')
    if _integer(xf.get('rot', '0'), 'rotation') % 21600000 or xf.get('flipH', '0') not in ('0', 'false') or xf.get('flipV', '0') not in ('0', 'false'):
        raise UnsupportedStroke('rotated or reflected path')
    off, ext = _one(xf, 'a:off'), _one(xf, 'a:ext')
    ox, oy = [_integer(off.get(k), k) / EMU for k in ('x', 'y')]
    ew, eh = [_integer(ext.get(k), k, True) / EMU for k in ('cx', 'cy')]
    paths = _one(sp, 'a:custGeom/a:pathLst')
    if len(paths) != 1 or paths[0].tag != '{' + NS['a'] + '}path':
        raise UnsupportedStroke('multiple native paths')
    native = paths[0]
    if native.get('stroke', '1') not in ('1', 'true') or set(native.attrib) - {'w', 'h', 'fill', 'stroke', 'extrusionOk'}:
        raise UnsupportedStroke('unsupported native path properties')
    pw, ph = [_integer(native.get(k), k, True) for k in ('w', 'h')]
    ln = _one(sp, 'a:ln')
    if set(ln.attrib) - {'w', 'cap', 'cmpd', 'algn'} or ln.get('cmpd', 'sng') != 'sng' or ln.get('algn', 'ctr') != 'ctr':
        raise UnsupportedStroke('unsupported compound/aligned stroke')
    width = _integer(ln.get('w'), 'stroke width', True) / EMU
    cap_native = ln.get('cap', 'flat')
    if cap_native not in CAPS.values():
        raise UnsupportedStroke('unsupported native cap')
    cap = next(k for k, v in CAPS.items() if v == cap_native)
    joins = [c for c in ln if c.tag.rsplit('}', 1)[-1] in ('round', 'bevel', 'miter')]
    if len(joins) != 1:
        raise UnsupportedStroke('explicit native join is required')
    join = joins[0].tag.rsplit('}', 1)[-1]
    miter = _integer(joins[0].get('lim', '800000'), 'miter limit', True) / 100000 if join == 'miter' else 1
    if 'stroke_linecap' in explicit and cap != explicit['stroke_linecap']:
        raise ValueError('Native cap disagrees with source declaration: ' + obj['id'])
    if 'stroke_linejoin' in explicit and join != explicit['stroke_linejoin']:
        raise ValueError('Native join disagrees with source declaration: ' + obj['id'])
    if join == 'miter' and 'stroke_linejoin' in explicit and int(round(miter * 100000)) != _miter_units(explicit.get('stroke_miterlimit', 4), obj['id']):
        raise ValueError('Native miter disagrees with source declaration: ' + obj['id'])
    allowed = {'solidFill', 'prstDash', 'round', 'bevel', 'miter', 'headEnd', 'tailEnd'}
    if any(c.tag.rsplit('}', 1)[-1] not in allowed for c in ln):
        raise UnsupportedStroke('unsupported native stroke paint/effect')
    dash = _one(ln, 'a:prstDash')
    if dash.get('val') != 'solid':
        raise UnsupportedStroke('native dashed stroke requires a separate adapter')
    for end in ('headEnd', 'tailEnd'):
        if any(c.get('type', 'none') != 'none' for c in ln.findall('a:' + end, NS)):
            raise UnsupportedStroke('native line end is not expanded geometry')
    color = _one(ln, 'a:solidFill/a:srgbClr')
    rgb = color.get('val', '')
    if len(rgb) != 6 or any(c not in '0123456789abcdefABCDEF' for c in rgb):
        raise UnsupportedStroke('unsupported native RGB color')
    alpha_nodes = color.findall('a:alpha', NS)
    if len(alpha_nodes) > 1 or any(c.tag != '{' + NS['a'] + '}alpha' for c in color):
        raise UnsupportedStroke('unsupported native color transform')
    alpha = _integer(alpha_nodes[0].get('val'), 'alpha') if alpha_nodes else 100000
    if not 0 <= alpha <= 100000:
        raise UnsupportedStroke('native alpha range')
    points, commands = [], []
    for node in native:
        op = node.tag.rsplit('}', 1)[-1]
        count = {'moveTo': 1, 'lnTo': 1, 'cubicBezTo': 3, 'close': 0}.get(op)
        if count is None or len(node) != count:
            raise UnsupportedStroke('unsupported native path command')
        coords = []
        for point in node:
            if point.tag != '{' + NS['a'] + '}pt' or set(point.attrib) != {'x', 'y'}:
                raise UnsupportedStroke('unsupported native point')
            x = _integer(point.get('x'), 'point x') * ew / pw
            y = _integer(point.get('y'), 'point y') * eh / ph
            coords.extend((x, y)); points.append((x, y))
        commands.append({'moveTo': 'M', 'lnTo': 'L', 'cubicBezTo': 'C', 'close': 'Z'}[op] + ' '.join(map(_number, coords)))
    if not points or native[0].tag != '{' + NS['a'] + '}moveTo':
        raise UnsupportedStroke('empty native stroke')
    pad = width * max(1, miter) / 2 + 1
    left, top = min(x for x, _ in points) - pad, min(y for _, y in points) - pad
    w, h = max(x for x, _ in points) + pad - left, max(y for _, y in points) + pad - top
    if max(w, h) * 8 > 16384 or math.ceil(w * 8) * math.ceil(h * 8) > MAX_DECODE_PIXELS:
        raise UnsupportedStroke('SVG decode surface budget')
    attributes = f'width="{_number(w)}" height="{_number(h)}" viewBox="{_number(left)} {_number(top)} {_number(w)} {_number(h)}"'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" {attributes}><path d="{" ".join(commands)}" fill="none" stroke="#{rgb}" stroke-width="{_number(width)}" stroke-opacity="{_number(alpha / 100000)}" stroke-linecap="{cap}" stroke-linejoin="{join}" stroke-miterlimit="{_number(miter)}"/></svg>'
    return {'id': obj['id'], 'svg': svg, 'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
            'position': {'left': ox + left, 'top': oy + top, 'width': w, 'height': h},
            'native_cap': cap_native, 'native_join': join, 'native_stroke_width_emu': round(width * EMU),
            'native_shape_xml_sha256': hashlib.sha256(E.canonicalize(E.tostring(shape), rewrite_prefixes=True).encode()).hexdigest(),
            'command_count': len(native), 'decode_pixels_8x': math.ceil(w * 8) * math.ceil(h * 8)}


def prepare_stroke_preview(pptx, manifest):
    """Return a deterministic definition; never modify source or native files."""
    pptx = Path(pptx).resolve()
    if not isinstance(manifest, dict) or not isinstance(manifest.get('objects'), list) or len(manifest['objects']) > 10000:
        raise ValueError('Invalid stroke preview source object budget')
    if pptx.stat().st_size > MAX_BYTES:
        raise ValueError('Stroke preview PPTX byte budget')
    payload = pptx.read_bytes()
    part = 'ppt/slides/slide1.xml'
    with ZipFile(BytesIO(payload)) as z:
        infos = z.infolist()
        if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos) or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError('Stroke preview package expansion budget or duplicate member')
        for info in infos:
            checked_part_name(info.filename)
        if z.getinfo(part).file_size > MAX_XML:
            raise ValueError('Stroke preview XML byte budget')
        xml = z.read(part)
    root = XmlDocument.parse(xml, part).root
    tree = _one(root, 'p:cSld/p:spTree')
    require_identity_shape_tree(tree, 'stroke preview slide')
    # Nested group transforms are intentionally outside this adapter scope.
    native = {}
    for shape in tree.findall('p:sp', NS):
        name = _one(shape, 'p:nvSpPr/p:cNvPr').get('name')
        if name in native:
            raise ValueError('Duplicate native stroke name')
        native[name] = shape
    if any(not isinstance(obj, dict) for obj in manifest['objects']):
        raise ValueError('Invalid source stroke object')
    ids = [obj.get('id') for obj in manifest['objects']]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid source stroke identities')
    objects, skipped, commands, decoded = [], [], 0, 0
    for obj in manifest['objects']:
        if obj.get('kind') != 'path' or not validate_stroke_style(obj.get('style', {}), 'path', obj['id']):
            continue
        if obj.get('style', {}).get('stroke', 'none') == 'none' or not obj['style'].get('stroke_width', 0):
            continue
        try:
            if obj['id'] not in native:
                raise UnsupportedStroke('only top-level native custom paths are supported')
            record = _svg(native[obj['id']], obj)
        except UnsupportedStroke as exc:
            skipped.append({'id': obj['id'], 'reason': str(exc)}); continue
        commands += record['command_count']; decoded += record['decode_pixels_8x']
        if commands > MAX_COMMANDS or decoded > MAX_DECODE_PIXELS:
            raise ValueError('Combined stroke preview command/decode budget exceeded')
        objects.append(record)
    return {'schema_version': 1, 'policy': POLICY, 'input_pptx': {'path': str(pptx), 'sha256': hashlib.sha256(payload).hexdigest()},
            'slide_part': part, 'native_slide_xml_sha256': hashlib.sha256(xml).hexdigest(),
            'objects': objects, 'unsupported': skipped, 'command_count': commands,
            'decode_pixels_8x': decoded, 'preview_only': True, 'native_delivery_modified': False,
            'source_pixel_equivalence': False, 'application_playback_verified': False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pptx', required=True); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True)
    a = p.parse_args(argv)
    source = Path(a.manifest)
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Stroke preview manifest byte budget')
    # Reuse the bounded, duplicate-key/nonfinite rejecting source reader.
    from .source_inventory import _load_json
    result = prepare_stroke_preview(a.pptx, _load_json(source, 32 * 1024 * 1024))
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
