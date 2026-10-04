"""Native object identity, contiguous grouping, and editability audit."""
import argparse
import hashlib
import json
import math
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main', 'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
for prefix, uri in NS.items(): ET.register_namespace(prefix, uri)

def native(node): return node.find('.//p:cNvPr', NS)

_CROP_KEYS = [('left', 'l'), ('top', 't'), ('right', 'r'), ('bottom', 'b')]
_EMU_PER_PX = 9525


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _crop_units(crop, label):
    if not isinstance(crop, dict) or set(crop) != {key for key, _ in _CROP_KEYS}:
        raise ValueError('Invalid normalized crop: ' + label)
    if not all(_number(v) and 0 <= v < 1 for v in crop.values()):
        raise ValueError('Invalid normalized crop: ' + label)
    if crop['left'] + crop['right'] >= 1 or crop['top'] + crop['bottom'] >= 1:
        raise ValueError('Crop has no remaining source area: ' + label)
    # Artifact Tool/OOXML uses JavaScript Math.round on nonnegative fractions.
    # Python round has a different tie rule at exactly half a unit.
    units = {key: math.floor(value * 100000 + .5) for key, value in crop.items()}
    if units['left'] + units['right'] >= 100000 or units['top'] + units['bottom'] >= 100000:
        raise ValueError('Crop quantization leaves no remaining source area: ' + label)
    return units


def _transform(element, label):
    node = element.find('p:spPr/a:xfrm', NS)
    if node is None:
        raise ValueError('Missing native transform: ' + label)
    off, extent = node.find('a:off', NS), node.find('a:ext', NS)
    if off is None or extent is None:
        raise ValueError('Incomplete native transform: ' + label)
    try:
        x, y = int(off.attrib['x']), int(off.attrib['y'])
        width, height = int(extent.attrib['cx']), int(extent.attrib['cy'])
        rotation = int(node.get('rot', '0'))
    except (KeyError, ValueError) as exc:
        raise ValueError('Invalid native transform: ' + label) from exc
    if width <= 0 or height <= 0:
        raise ValueError('Nonpositive native frame: ' + label)
    return node, (x, y, width, height), rotation


def _mapped_frames(object_map, manifest):
    if object_map is None:
        return None
    data = object_map if isinstance(object_map, dict) else json.loads(Path(object_map).read_text())
    if not isinstance(data, dict):
        raise ValueError('Object map must be a record')
    placement = data.get('placement')
    canvas = manifest.get('canvas')
    if not isinstance(placement, list) or len(placement) != 4 or not all(_number(v) for v in placement):
        raise ValueError('Object map placement needs four finite numbers')
    if placement[2] <= 0 or placement[3] <= 0 or not isinstance(canvas, dict) or not all(_number(canvas.get(k)) and canvas[k] > 0 for k in ('width', 'height')):
        raise ValueError('Invalid object map canvas or placement')
    scale = placement[2] / canvas['width']
    if not math.isclose(scale, placement[3] / canvas['height'], rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError('Object map placement is not uniformly scaled')
    entries = data.get('objects')
    if not isinstance(entries, list):
        raise ValueError('Object map objects must be a list')
    frames = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ValueError('Invalid object map entry')
        if entry['id'] in frames:
            raise ValueError('Duplicate object map id: ' + entry['id'])
        box = entry.get('box')
        if not isinstance(box, dict) or not all(_number(box.get(k)) for k in ('x', 'y', 'width', 'height')) or box['width'] <= 0 or box['height'] <= 0:
            raise ValueError('Invalid mapped frame: ' + entry['id'])
        frame = ((placement[0] + box['x'] * scale) * _EMU_PER_PX,
                 (placement[1] + box['y'] * scale) * _EMU_PER_PX,
                 box['width'] * scale * _EMU_PER_PX,
                 box['height'] * scale * _EMU_PER_PX)
        if not all(math.isfinite(value) for value in frame):
            raise ValueError('Mapped frame exceeds numeric range: ' + entry['id'])
        frames[entry['id']] = {**entry, 'frame': frame, 'source_box': box, 'scale': scale,
                              'source_to_slide': {'translate_x': placement[0], 'translate_y': placement[1],
                                                  'scale_numerator': placement[2], 'scale_denominator': canvas['width']}}
    return frames


def _restore_cubic_path(element, obj, mapped_frames):
    """Restore exact editable cubics after the author's flattened intermediate.

    Keep the native transform and paint unchanged. DrawingML path coordinates
    use source-pixel EMU units; xfrm supplies the requested uniform placement.
    Multiple moveTo/close subpaths retain their nonzero-winding fill topology.
    """
    label = obj['id']
    if mapped_frames is None or label not in mapped_frames or mapped_frames[label]['kind'] != 'path':
        raise ValueError('Native cubic restoration requires mapped path frame: ' + label)
    entry = mapped_frames[label]
    transform, actual_frame, rotation = _transform(element, label)
    if rotation % 21600000 or transform.get('flipH', '0') in ('1', 'true') or transform.get('flipV', '0') in ('1', 'true'):
        raise ValueError('Exporter changed cubic path orientation: ' + label)
    if any(abs(actual - expected) > 2 for actual, expected in zip(actual_frame, entry['frame'])):
        raise ValueError('Exporter changed cubic path frame: ' + label)
    box = entry['source_box']
    geometry = element.find('p:spPr/a:custGeom', NS)
    paths = geometry.find('a:pathLst', NS)
    if paths is None or len(paths) != 1:
        raise ValueError('Cubic restoration expects one authored custom path: ' + label)
    original_path = paths[0]
    attrs = dict(original_path.attrib)
    attrs.update(w=str(max(1, math.floor(box['width'] * _EMU_PER_PX + .5))),
                 h=str(max(1, math.floor(box['height'] * _EMU_PER_PX + .5))))
    restored = ET.Element(f"{{{NS['a']}}}path", attrs)
    def point(node, x, y):
        if not _number(x) or not _number(y):
            raise ValueError('Nonfinite cubic path coordinate: ' + label)
        if not box['x'] - .000001 <= x <= box['x'] + box['width'] + .000001 or not box['y'] - .000001 <= y <= box['y'] + box['height'] + .000001:
            raise ValueError('Cubic path control hull exceeds mapped frame: ' + label)
        ET.SubElement(node, f"{{{NS['a']}}}pt", {
            'x': str(math.floor((x - box['x']) * _EMU_PER_PX + .5)),
            'y': str(math.floor((y - box['y']) * _EMU_PER_PX + .5))})
    cubic_count = 0
    active = False
    for command in obj['commands']:
        if not isinstance(command, dict) or len(command) != 1:
            raise ValueError('Invalid native path command: ' + label)
        op, value = next(iter(command.items()))
        if op in ('moveTo', 'lineTo'):
            if not isinstance(value, dict) or set(value) != {'x', 'y'} or (op == 'lineTo' and not active):
                raise ValueError('Invalid native path point: ' + label)
            node = ET.SubElement(restored, f"{{{NS['a']}}}{'lnTo' if op == 'lineTo' else 'moveTo'}")
            point(node, value['x'], value['y'])
            active = True
        elif op == 'cubicTo':
            if not active or not isinstance(value, dict) or set(value) != {'x1', 'y1', 'x2', 'y2', 'x', 'y'}:
                raise ValueError('Invalid native cubic control points: ' + label)
            node = ET.SubElement(restored, f"{{{NS['a']}}}cubicBezTo")
            for xkey, ykey in [('x1', 'y1'), ('x2', 'y2'), ('x', 'y')]:
                point(node, value[xkey], value[ykey])
            cubic_count += 1
        elif op == 'close':
            if not active or value != {}:
                raise ValueError('Invalid native path close: ' + label)
            ET.SubElement(restored, f"{{{NS['a']}}}close")
        else:
            raise ValueError('Unsupported native path command: ' + label)
    paths.remove(original_path)
    paths.append(restored)
    return {'id': label, 'native_cubic_segments': cubic_count,
            'coordinate_unit': 'source_pixel_emu', 'path_dimensions': [int(attrs['w']), int(attrs['h'])],
            'frame_verified': True, 'intermediate_flattening_only': True,
            'subpath_count': sum('moveTo' in command for command in obj['commands'])}


def _visual_bounds(element, label):
    """Conservative visual union; children stay in their original coordinates."""
    _, (x, y, width, height), rotation = _transform(element, label)
    margin = 0
    line = element.find('p:spPr/a:ln', NS)
    if line is not None and line.find('a:noFill', NS) is None:
        try:
            stroke = int(line.get('w', '9525'))
        except ValueError as exc:
            raise ValueError('Invalid native stroke width: ' + label) from exc
        if stroke < 0:
            raise ValueError('Invalid native stroke width: ' + label)
        alpha = line.find('.//a:alpha', NS)
        if alpha is None or alpha.get('val') != '0':
            margin = stroke / 2
            # A custom polygon can have acute joins outside its editing box.
            # Use the miter bound as a conservative envelope, not an assertion
            # that every member actually occupies this whole rectangle.
            custom = element.find('p:spPr/a:custGeom', NS) is not None
            miter = line.find('a:miter', NS)
            if custom and line.find('a:round', NS) is None and line.find('a:bevel', NS) is None:
                limit = 4.0
                if miter is not None:
                    try:
                        limit = max(1, int(miter.get('lim', '400000')) / 100000)
                    except ValueError as exc:
                        raise ValueError('Invalid native miter limit: ' + label) from exc
                margin *= limit
    radians = math.radians((rotation % 21600000) / 60000)
    co, si = math.cos(radians), math.sin(radians)
    cx, cy = x + width / 2, y + height / 2
    corners = [(cx + co * dx - si * dy, cy + si * dx + co * dy)
               for dx in (-width / 2 - margin, width / 2 + margin)
               for dy in (-height / 2 - margin, height / 2 + margin)]
    return (math.floor(min(p[0] for p in corners)), math.floor(min(p[1] for p in corners)),
            math.ceil(max(p[0] for p in corners)), math.ceil(max(p[1] for p in corners)))


def process(source, output, manifest_path, receipt, object_map=None, asset_root=None,
            occupied_placement=None):
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError('Refusing to overwrite a PPTX')
    if Path(receipt).exists(): raise ValueError('Refusing to overwrite an editability receipt')
    manifest = json.loads(Path(manifest_path).read_text())
    object_map_data = json.loads(Path(object_map).read_text()) if object_map is not None else None
    if object_map is not None and not isinstance(object_map_data, dict):
        raise ValueError('Object map must be a record')
    mapped_frames = _mapped_frames(object_map_data, manifest)
    canvas_clip_receipt = None
    canvas_clip_ids = set()
    if 'source_canvas_clip' in manifest:
        if asset_root is None or mapped_frames is None:
            raise ValueError('Source canvas clipping needs frozen assets and mapped standalone frames')
        from .source_canvas_clip import verify_source_canvas_clip
        canvas_clip_receipt = verify_source_canvas_clip(manifest, asset_root)
        canvas_clip_ids = set(canvas_clip_receipt['object_ids'])
        for label in canvas_clip_ids:
            entry = mapped_frames.get(label, {})
            placement = entry.get('source_to_slide', {})
            if (placement.get('translate_x') != 0 or placement.get('translate_y') != 0 or
                    placement.get('scale_numerator') != placement.get('scale_denominator')):
                raise ValueError('Source canvas clipping requires identity placement: ' + label)
    objects = sorted(enumerate(manifest['objects']), key=lambda row: (row[1].get('z_index', row[0]), row[0]))
    objects = [o for _, o in objects]
    with zipfile.ZipFile(source) as z:
        payloads = {i.filename: z.read(i.filename) for i in z.infolist()}
        infos = z.infolist()
    from .package import XmlDocument
    page_document = XmlDocument.parse(payloads['ppt/slides/slide1.xml'], 'ppt/slides/slide1.xml')
    has_formulas = any(o.get('source_kind') == 'formula' for o in objects)
    rels_document = XmlDocument.parse(payloads['ppt/slides/_rels/slide1.xml.rels'], 'slide1.xml.rels') if has_formulas else None
    types_document = XmlDocument.parse(payloads['[Content_Types].xml'], '[Content_Types].xml') if has_formulas else None
    formula_records, text_layout_records, gradient_records = [], [], []
    page = page_document.root
    tree = page.find('p:cSld/p:spTree', NS)
    elements = [e for e in tree if e.tag in {f"{{{NS['p']}}}sp", f"{{{NS['p']}}}pic", f"{{{NS['p']}}}grpSp"}]
    if len(elements) != len(objects): raise ValueError(f'Exported object count mismatch: {len(elements)} != {len(objects)}')
    mapping, raster_crops, cubic_paths, stroke_styles, winding_fills = [], [], [], [], []
    from .native_winding import normalize_native_polygon_fill, root_group_is_identity
    parent_identity = root_group_is_identity(tree)
    for element, obj in zip(elements, objects):
        pr = native(element)
        if pr is None: raise ValueError('Missing native identity')
        if obj['kind'] != 'image' and pr.get('name') != obj['id']:
            raise ValueError('Exporter reordered named objects: ' + obj['id'])
        pr.set('name', obj['id'])
        pr.set('descr', 'source_id=' + obj['id'] + ('; semantic_group=' + obj['group_id'] if obj.get('group_id') else ''))
        if obj['id'] in canvas_clip_ids:
            pr.set('descr', pr.get('descr') + '; source_canvas_clip_required=true')
        if obj['kind'] == 'text' and element.find('p:txBody', NS) is None: raise ValueError('Text was flattened')
        text_entry = mapped_frames.get(obj['id']) if mapped_frames and obj['kind'] == 'text' else None
        has_renderer_baseline = isinstance(text_entry, dict) and isinstance(text_entry.get('text_layout'), dict) and \
            text_entry['text_layout'].get('renderer_baseline') is not None
        if obj['kind'] == 'text' and (obj.get('line_height') is not None or obj.get('baseline_offset') is not None or has_renderer_baseline):
            from .semantic_ooxml import apply_text_layout
            entry = text_entry
            if not entry: raise ValueError('Text layout requires a mapped frame: ' + obj['id'])
            text_layout_records.append(apply_text_layout(element, obj, entry, entry['scale']))
        if obj['kind'] == 'path' and element.find('p:spPr/a:custGeom', NS) is None: raise ValueError('Vector path was flattened')
        if obj['kind'] == 'path':
            from .linear_gradient import verify_native_gradient, apply_gradient_angle_precision
            angle_correction = apply_gradient_angle_precision(element, obj)
            gradient_record = verify_native_gradient(element, obj)
            if gradient_record is not None:
                if angle_correction is not None:
                    gradient_record['angle_serialization_correction'] = angle_correction
                gradient_records.append(gradient_record)
            from .stroke_style import apply_stroke_style
            stroke_record = apply_stroke_style(element, obj)
            if stroke_record is not None:
                stroke_styles.append(stroke_record)
        if obj['kind'] == 'path' and (obj['id'] in canvas_clip_ids or any('cubicTo' in command for command in obj.get('commands', []))):
            cubic_paths.append(_restore_cubic_path(element, obj, mapped_frames))
        if obj['kind'] == 'path' and obj['id'] in canvas_clip_ids:
            winding_fills.append({'id': obj['id'], 'status': 'not_applicable',
                                  'reason_code': 'keep_original_canvas_clip_geometry',
                                  'reason': 'Verified source viewport glyph retains complete original commands',
                                  'geometry_preserved': True, 'manifest_modified': False,
                                  'visual_review_required': True})
        elif obj['kind'] == 'path':
            if any('cubicTo' in command for command in obj.get('commands', [])):
                from .native_cubic_winding import normalize_native_cubic_fill
                winding_fills.append(normalize_native_cubic_fill(
                    page, object_id=obj['id'], manifest=manifest,
                    object_map=object_map_data, placement=occupied_placement,
                    proof_mode='endpoint_contact', proof_split_depth=1))
            else:
                winding_fills.append(normalize_native_polygon_fill(
                    element, obj, mapped_frames.get(obj['id']) if mapped_frames else None,
                    parent_identity=parent_identity))
        if obj['kind'] == 'image' and element.tag != f"{{{NS['p']}}}pic": raise ValueError('Raster asset classification mismatch')
        if obj['kind'] == 'image':
            rect = element.find('p:blipFill/a:srcRect', NS)
            try:
                actual_units = {key: int(rect.get(short, '0')) if rect is not None else 0 for key, short in _CROP_KEYS}
            except ValueError as exc:
                raise ValueError('Invalid exported source crop: ' + obj['id']) from exc
            if any(value < 0 or value >= 100000 for value in actual_units.values()) or actual_units['left'] + actual_units['right'] >= 100000 or actual_units['top'] + actual_units['bottom'] >= 100000:
                raise ValueError('Exported source crop has no valid remaining area: ' + obj['id'])
            expected = obj.get('crop', {key: 0 for key, _ in _CROP_KEYS})
            expected_units = _crop_units(expected, obj['id'])
            if actual_units != expected_units:
                raise ValueError('Exporter changed or discarded source crop: ' + obj['id'])
            actual = {key: value / 100000 for key, value in actual_units.items()}
            frame_audit = None
            if mapped_frames is not None:
                entry = mapped_frames.get(obj['id'])
                if entry is None or entry['kind'] != 'image':
                    raise ValueError('Missing mapped image frame: ' + obj['id'])
                transform, actual_frame, rotation = _transform(element, obj['id'])
                if rotation % 21600000 or transform.get('flipH', '0') in ('1', 'true') or transform.get('flipV', '0') in ('1', 'true'):
                    raise ValueError('Exporter changed image orientation: ' + obj['id'])
                if any(abs(actual_value - expected_value) > 2 for actual_value, expected_value in zip(actual_frame, entry['frame'])):
                    raise ValueError('Exporter changed fitted image frame: ' + obj['id'])
                frame_audit = {'actual_emu': list(actual_frame), 'expected_emu': list(entry['frame']), 'tolerance_emu': 2, 'verified': True}
            blip = element.find('p:blipFill/a:blip', NS)
            rid = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed') if blip is not None else None
            rels = ET.fromstring(payloads['ppt/slides/_rels/slide1.xml.rels'])
            matches = [rel for rel in rels if rel.get('Id') == rid]
            if len(matches) != 1 or matches[0].get('TargetMode') == 'External' or matches[0].get('Type') != 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image':
                raise ValueError('Missing embedded original image: ' + obj['id'])
            target = matches[0].get('Target', '')
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt/slides', target))
            if member not in payloads:
                raise ValueError('Missing embedded original image bytes: ' + obj['id'])
            actual_sha = hashlib.sha256(payloads[member]).hexdigest()
            if actual_sha != obj['sha256']:
                raise ValueError('Exporter changed original raster bytes: ' + obj['id'])
            raster_crops.append({'id': obj['id'], 'crop': actual, 'requested_crop': expected, 'crop_units': actual_units, 'original_sha256': actual_sha, 'embedded_member': member, 'source_bytes_preserved': True, 'frame_audit': frame_audit})
            if obj.get('source_kind') == 'formula':
                from .semantic_ooxml import add_formula_svg
                formula_records.append(add_formula_svg(element, obj, payloads, rels_document, types_document, asset_root or Path(manifest_path).parent))
                if mapped_frames:
                    formula_records[-1]['delivery_sampling_scale'] = obj['formula_asset']['placement']['sampling_scale'] / mapped_frames[obj['id']]['scale']
        mapping.append({'id': obj['id'], 'kind': obj['kind'], 'native_id': pr.get('id'), 'group_id': obj.get('group_id')})
    max_id = max(int(e.get('id')) for e in page.findall('.//p:cNvPr', NS))
    from .native_connections import connect_objects, attachment_groups
    connectors = connect_objects(tree, elements, objects, mapped_frames) if any(o.get('source_kind') == 'connector' for o in objects) else []
    attached, taken, max_id = attachment_groups(tree, elements, objects, max_id, _visual_bounds)
    group_members = {}
    for i, obj in enumerate(objects):
        if obj.get('group_id') and i not in taken: group_members.setdefault(obj['group_id'], []).append(i)
    grouped, warnings = attached, []
    for group, slots in group_members.items():
        if slots != list(range(slots[0], slots[-1] + 1)):
            warnings.append('Logical-only group retained to preserve paint order: ' + group)
            continue
        if len(slots) == 1: continue
        members = [elements[i] for i in slots]
        bounds = [_visual_bounds(member, objects[i]['id']) for member, i in zip(members, slots)]
        left, top = min(b[0] for b in bounds), min(b[1] for b in bounds)
        right, bottom = max(b[2] for b in bounds), max(b[3] for b in bounds)
        g = ET.Element(f"{{{NS['p']}}}grpSp")
        ng = ET.SubElement(g, f"{{{NS['p']}}}nvGrpSpPr")
        max_id += 1
        ET.SubElement(ng, f"{{{NS['p']}}}cNvPr", {'id': str(max_id), 'name': group})
        ET.SubElement(ng, f"{{{NS['p']}}}cNvGrpSpPr"); ET.SubElement(ng, f"{{{NS['p']}}}nvPr")
        gp = ET.SubElement(g, f"{{{NS['p']}}}grpSpPr")
        xf = ET.SubElement(gp, f"{{{NS['a']}}}xfrm")
        for name, attrs in [('off', {'x': left, 'y': top}), ('ext', {'cx': max(1, right-left), 'cy': max(1, bottom-top)}), ('chOff', {'x': left, 'y': top}), ('chExt', {'cx': max(1, right-left), 'cy': max(1, bottom-top)})]:
            ET.SubElement(xf, f"{{{NS['a']}}}{name}", {k: str(v) for k, v in attrs.items()})
        at = list(tree).index(members[0])
        for member in members: tree.remove(member); g.append(member)
        tree.insert(at, g)
        grouped.append({'id': group, 'members': [objects[i]['id'] for i in slots], 'visual_bounds_emu': [left, top, right, bottom], 'identity_transform': True, 'bounds_policy': 'conservative_rotation_and_stroke_envelope'})
    payloads['ppt/slides/slide1.xml'] = page_document.bytes()
    if has_formulas:
        payloads['ppt/slides/_rels/slide1.xml.rels'] = rels_document.bytes()
        payloads['[Content_Types].xml'] = types_document.bytes()
    with zipfile.ZipFile(output, 'w') as z:
        for info in infos: z.writestr(info, payloads[info.filename])
        for name in payloads.keys() - {info.filename for info in infos}: z.writestr(name, payloads[name])
    data = {'native_objects': mapping, 'native_groups': grouped, 'raster_crops': raster_crops, 'native_cubic_paths': cubic_paths, 'native_cubic_segment_count': sum(path['native_cubic_segments'] for path in cubic_paths), 'warnings': warnings, 'path_count': sum(o['kind'] == 'path' for o in objects), 'text_count': sum(o['kind'] == 'text' for o in objects), 'raster_count': sum(o['kind'] == 'image' for o in objects), 'fully_native': not any(o['kind'] == 'image' for o in objects)}
    data['stroke_styles'] = stroke_styles
    data['native_winding_fills'] = winding_fills
    if canvas_clip_receipt is not None:
        data['source_canvas_clip'] = canvas_clip_receipt
    data['native_gradients'] = gradient_records
    data.update(formula_assets=formula_records, formula_count=len(formula_records),
                svg_formula_count=sum(row.get('representation') == 'svg' for row in formula_records),
                native_connectors=connectors, text_layout=text_layout_records)
    Path(receipt).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return data

def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument('--input', required=True); p.add_argument('--output', required=True); p.add_argument('--manifest', required=True); p.add_argument('--receipt', required=True); p.add_argument('--object-map')
    p.add_argument('--asset-root')
    p.add_argument('--placement', type=float, nargs=4, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'))
    p.add_argument('--quiet', action='store_true',
                   help='Write the complete receipt file without repeating it on stdout')
    a = p.parse_args(argv)
    result = process(a.input, a.output, a.manifest, a.receipt,
                     object_map=a.object_map, asset_root=a.asset_root, occupied_placement=a.placement)
    if not a.quiet:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
