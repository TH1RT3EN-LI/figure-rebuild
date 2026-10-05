"""Validate a host-recognized figure before native PPT authoring (stdlib)."""
import hashlib
import json
import math
import re
from pathlib import Path

KINDS = {'path', 'text', 'image'}
SOURCE_KINDS = {'research_original', 'user_original', 'retrieved_original', 'generated_diagram'}

def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()

def confined(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Asset paths must be relative to the manifest directory')
    p = (Path(root) / relative).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError('Asset path escapes the job directory: ' + relative)
    if not p.is_file():
        raise ValueError('Missing asset: ' + relative)
    return p

def finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False

def validate_placement(placement, canvas):
    """Reject invalid requested placement before authoring or writing run files."""
    if not isinstance(placement, (list, tuple)) or len(placement) != 4 or not all(finite(v) for v in placement):
        raise ValueError('Placement must contain four finite numbers: x y width height')
    if not isinstance(canvas, dict) or not all(finite(canvas.get(k)) and canvas[k] > 0 for k in ('width', 'height')):
        raise ValueError('Placement requires a positive finite page canvas')
    x, y, width, height = placement
    if width <= 0 or height <= 0:
        raise ValueError('Placement width and height must be positive')
    if x < 0 or y < 0 or x + width > canvas['width'] + .001 or y + height > canvas['height'] + .001:
        raise ValueError('Requested placement is outside the page; clipping is unsupported')
    return placement


def validate(manifest, root, require_review=True, _materialized=False):
    if not _materialized and isinstance(manifest, dict) and isinstance(manifest.get('objects'), list) and any(
            isinstance(o, dict) and (o.get('kind') in ('formula', 'connector') or 'attach_to' in o or
                                    o.get('source_kind') in ('formula', 'connector') or
                                    any(k in o for k in ('formula_asset', 'connection_record', 'source_attachment')))
            for o in manifest['objects']):
        try:
            from .scene_compile import compile_scene
            scene, audit = compile_scene(manifest, root)
            result = validate(scene, root, require_review, _materialized=True)
            result['semantic_counts'] = {'formula': len(audit['formulas']), 'connector': len(audit['connections'])}
            if 'source_inventory' in audit:
                result['source_inventory'] = audit['source_inventory']
                if audit['source_inventory']['status'] == 'FAIL':
                    result['errors'].extend('Source inventory: ' + item.get('code', 'unresolved')
                        for item in audit['source_inventory']['mismatches'] + audit['source_inventory']['unresolved'])
                    result['status'] = 'FAIL'
                elif audit['source_inventory']['status'] == 'REVIEW':
                    result['warnings'].append('Source component coverage or representation needs review; see source_inventory')
            return result
        except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
            return {'status': 'FAIL', 'errors': [str(exc)], 'warnings': [], 'object_counts': {},
                    'native_editable_count': 0, 'raster_count': 0, 'fully_native': False}
    errors, warnings = [], []
    canvas_clip_proof = None
    canvas_clip_ids = set()
    def check(ok, message):
        if not ok:
            errors.append(message)
    def record(value, label):
        if not isinstance(value, dict):
            errors.append(label + ' must be a record')
            return {}
        return value
    def valid_id(value):
        return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value))
    def enum(value, options):
        return isinstance(value, str) and value in options
    def solid(value, allow_none=True):
        return isinstance(value, str) and ((allow_none and value == 'none') or bool(re.fullmatch(r'#[0-9A-Fa-f]{6}', value)))
    count = {k: 0 for k in KINDS}
    def report():
        result = {'status': 'PASS' if not errors else 'FAIL', 'errors': errors, 'warnings': warnings,
                'object_counts': count, 'native_editable_count': count['path'] + count['text'],
                'raster_count': count['image'], 'fully_native': count['image'] == 0}
        if canvas_clip_proof is not None:
            result['source_canvas_clip'] = canvas_clip_proof
        return result
    if not isinstance(manifest, dict):
        errors.append('Manifest must be a record')
        return report()
    check(type(manifest.get('schema_version')) is int and manifest.get('schema_version') == 1, 'schema_version must be integer 1')
    check(valid_id(manifest.get('id')), 'Invalid job id')
    check(type(manifest.get('revision')) is int and manifest.get('revision', 0) > 0, 'revision must be a positive integer')
    canvas = record(manifest.get('canvas'), 'Canvas')
    canvas_ok = all(finite(canvas.get(k)) and 0 < canvas[k] <= 20000 for k in ('width', 'height'))
    for key in ('width', 'height'):
        check(finite(canvas.get(key)) and 0 < canvas[key] <= 20000, 'Invalid canvas ' + key)
    if 'background' in canvas:
        check(solid(canvas['background']), 'Canvas background must be a solid hex color or none')
    def in_canvas(x, y, label, object_id=None):
        if canvas_ok and (not isinstance(object_id, str) or object_id not in canvas_clip_ids):
            check(-.001 <= x <= canvas['width'] + .001 and -.001 <= y <= canvas['height'] + .001,
                  label + ' is outside the canvas; clipping is unsupported')
    source = record(manifest.get('source'), 'Source')
    check(enum(source.get('kind'), SOURCE_KINDS), 'Unknown source classification')
    for key in ('width', 'height'):
        check(finite(source.get(key)) and 0 < source[key] <= 20000, 'Invalid source ' + key)
    check(isinstance(source.get('sha256'), str) and bool(re.fullmatch(r'[0-9a-f]{64}', source.get('sha256', ''))), 'Original source needs a lowercase SHA256 checksum')
    try:
        original = confined(root, source.get('path'))
        check(digest(original) == source.get('sha256'), 'Original source hash changed')
        if original.suffix.lower() != '.svg':
            from PIL import Image
            try:
                with Image.open(original) as image:
                    check(list(image.size) == [source.get('width'), source.get('height')], 'Original dimensions changed')
            except Image.DecompressionBombError as exc:
                raise ValueError('Original raster exceeds safe image dimensions') from exc
        check(source.get('width') == canvas.get('width') and source.get('height') == canvas.get('height'), 'Source/canvas coordinates differ')
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        errors.append(str(exc))
    recognition = record(manifest.get('recognition'), 'Recognition')
    if require_review:
        check(recognition.get('status') == 'reviewed', 'Recognition needs visual review before authoring')
    check(enum(recognition.get('provider'), {'calling_host', 'svg_import'}), 'Recognition provider must be explicit')
    unresolved = recognition.get('unresolved', [])
    check(isinstance(unresolved, list), 'Recognition unresolved must be a list')
    check(not unresolved, 'Resolve uncertain text/relationships before authoring')
    objects = manifest.get('objects')
    check(isinstance(objects, list) and 0 < len(objects) <= 10000, 'Objects must be a nonempty list, capped at 10000')
    if not isinstance(objects, list) or len(objects) > 10000:
        return report()
    if 'source_canvas_clip' in manifest:
        try:
            from .source_canvas_clip import verify_source_canvas_clip
            canvas_clip_proof = verify_source_canvas_clip(manifest, root)
            canvas_clip_ids = set(canvas_clip_proof['object_ids'])
            warnings.append('Explicit source canvas clipping: verified glyph outlines require standalone slide bounds and visual review')
        except (ValueError, OSError, ImportError, RuntimeError, KeyError, TypeError, OverflowError) as exc:
            errors.append(str(exc))
    ids, groups, total_vertices = set(), set(), 0
    for index, raw in enumerate(objects):
        if not isinstance(raw, dict):
            errors.append(f'Object {index} is not a record'); continue
        obj = raw
        oid = obj.get('id')
        check(valid_id(oid), f'Invalid object id at {index}')
        if isinstance(oid, str):
            check(oid not in ids, 'Duplicate stable id: ' + oid); ids.add(oid)
        label = str(oid)
        kind = obj.get('kind')
        from .text_spacing import validate_character_spacing
        try:
            validate_character_spacing(obj)
        except ValueError as exc:
            errors.append(str(exc))
        check(enum(kind, KINDS), 'Unsupported object kind: ' + str(kind))
        if isinstance(kind, str) and kind in count:
            count[kind] += 1
        if 'group_id' in obj:
            group = obj['group_id']; check(valid_id(group), 'Invalid group id: ' + label)
            if isinstance(group, str): groups.add(group)
        if 'z_index' in obj: check(finite(obj['z_index']), f'Invalid depth: {label}')
        if 'confidence' in obj:
            confidence = obj['confidence']
            check(finite(confidence) and 0 <= confidence <= 1, f'Invalid confidence: {label}')
            if finite(confidence) and confidence < .8: warnings.append(f'Approximate recognition: {label}')
        style = record(obj.get('style', {}), 'Style for ' + label)
        from .stroke_style import STROKE_PROPERTIES, validate_stroke_style
        check(not set(style).difference({'fill', 'fill_gradient', 'stroke', 'stroke_width', 'opacity'} | STROKE_PROPERTIES), 'Unsupported style property: ' + label)
        try:
            validate_stroke_style(style, kind, label)
            from .linear_gradient import validate_linear_gradient
            validate_linear_gradient(style, kind, label)
        except ValueError as exc:
            errors.append(str(exc))
        for channel in ('fill', 'stroke'):
            color = style.get(channel, '#000000' if kind == 'text' and channel == 'fill' else 'none')
            check(solid(color), f'Use a solid hex color or none: {label}.{channel}')
        stroke_width, opacity = style.get('stroke_width', 0), style.get('opacity', 1)
        check(finite(stroke_width) and 0 <= stroke_width <= 20000, 'Invalid stroke width: ' + label)
        check(finite(opacity) and 0 <= opacity <= 1, 'Invalid opacity: ' + label)
        if kind == 'path':
            check('box' not in obj and 'anchor' not in obj and obj.get('rotation', 0) == 0,
                  'Paths use global command coordinates; box/anchor/rotation overrides are unsupported: ' + label)
            commands = obj.get('commands')
            check(isinstance(commands, list) and 1 < len(commands) <= 200000, 'Invalid path command count: ' + label)
            if not isinstance(commands, list) or len(commands) > 200000: continue
            total_vertices += len(commands)
            check(total_vertices <= 200000, 'Total path command limit is 200000')
            if total_vertices > 200000: continue
            active, points, drawable, previous, subpath_start = False, [], False, None, None
            for command in commands:
                check(isinstance(command, dict) and len(command) == 1, 'Invalid path command: ' + label)
                if not isinstance(command, dict) or len(command) != 1: continue
                op, point = next(iter(command.items()))
                check(enum(op, {'moveTo', 'lineTo', 'cubicTo', 'close'}), 'Unsupported path command: ' + label)
                if op in ('moveTo', 'lineTo'):
                    good = isinstance(point, dict) and set(point) == {'x', 'y'} and finite(point.get('x')) and finite(point.get('y'))
                    check(good, 'Invalid path point: ' + label)
                    if good:
                        in_canvas(point['x'], point['y'], 'Path geometry ' + label, oid)
                        points.append(point)
                        if op == 'lineTo' and previous is not None and point != previous: drawable = True
                        previous = point
                    if op == 'moveTo':
                        active = True
                        subpath_start = point if good else None
                    else: check(active, 'lineTo before moveTo: ' + label)
                elif op == 'cubicTo':
                    good = isinstance(point, dict) and set(point) == {'x1', 'y1', 'x2', 'y2', 'x', 'y'} and all(finite(point.get(key)) for key in ('x1', 'y1', 'x2', 'y2', 'x', 'y'))
                    check(good, 'Invalid cubic control points: ' + label)
                    check(active and previous is not None, 'cubicTo before moveTo: ' + label)
                    if good:
                        for xkey, ykey in [('x1', 'y1'), ('x2', 'y2'), ('x', 'y')]:
                            in_canvas(point[xkey], point[ykey], 'Cubic control hull ' + label, oid)
                        points.append({'x': point['x'], 'y': point['y']})
                        if previous is not None and any({'x': point[xkey], 'y': point[ykey]} != previous for xkey, ykey in [('x1', 'y1'), ('x2', 'y2'), ('x', 'y')]):
                            drawable = True
                        previous = {'x': point['x'], 'y': point['y']}
                elif op == 'close':
                    check(active and point == {}, 'close without subpath: ' + label)
                    # A subsequent cubic begins at the closed subpath origin.
                    if active:
                        previous = subpath_start
            check(bool(points) and drawable, 'Path needs a drawable segment: ' + label)
            if opacity == 0 or (style.get('fill', 'none') == 'none' and 'fill_gradient' not in style and (style.get('stroke', 'none') == 'none' or stroke_width == 0)):
                warnings.append('Path has no visible paint: ' + label)
        elif kind == 'text':
            if 'font_family' in obj:
                family = obj['font_family']
                check(isinstance(family, str) and bool(family.strip()) and all(ord(ch) >= 32 and ch not in {'\"', "'", '\\'} for ch in family),
                      'Invalid text font_family: ' + label)
            check(isinstance(obj.get('text'), str) and bool(obj.get('text', '').strip()), 'Text is empty: ' + label)
            if isinstance(obj.get('text'), str):
                check(all(ch in '\t\n\r' or 0x20 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF for ch in obj['text']),
                      'Text contains a character forbidden by XML 1.0: ' + label)
            check(finite(obj.get('font_size')) and 0 < obj['font_size'] <= 1000, 'Invalid text font_size: ' + label)
            check(enum(obj.get('alignment', 'left'), {'left', 'center', 'right'}), 'Invalid text alignment: ' + label)
            check(enum(obj.get('vertical_alignment', 'top'), {'top', 'middle', 'bottom'}), 'Invalid vertical alignment: ' + label)
            check(enum(obj.get('wrap', 'none'), {'none', 'square'}), 'Invalid text wrap: ' + label)
            for flag in ('bold', 'italic'):
                if flag in obj: check(isinstance(obj[flag], bool), 'Invalid text ' + flag + ': ' + label)
            for key in ('line_height', 'baseline_offset'):
                if key in obj: check(finite(obj[key]) and (obj[key] >= 0 if key == 'baseline_offset' else obj[key] > 0), 'Invalid text ' + key + ': ' + label)
            if 'insets' in obj:
                inset = obj['insets']
                check(isinstance(inset, dict) and not set(inset).difference({'left', 'right', 'top', 'bottom'}) and
                      all(finite(v) and v >= 0 for v in inset.values()), 'Invalid text insets: ' + label)
            check(finite(obj.get('rotation', 0)), 'Invalid text rotation: ' + label)
            check(solid(style.get('fill', '#000000'), allow_none=False) and finite(opacity) and opacity > 0,
                  'Text must have visible solid fill and positive opacity: ' + label)
            check(style.get('stroke', 'none') == 'none' or stroke_width == 0, 'Editable text strokes are unsupported: ' + label)
            check(('anchor' in obj) != ('box' in obj), 'Text needs exactly one box or baseline anchor: ' + label)
            if 'anchor' in obj:
                anchor = obj['anchor']
                good = isinstance(anchor, dict) and set(anchor) == {'x', 'y'} and finite(anchor.get('x')) and finite(anchor.get('y'))
                check(good, 'Invalid text baseline anchor: ' + label)
                if good: in_canvas(anchor['x'], anchor['y'], 'Text baseline ' + label)
                check(obj.get('vertical_alignment', 'top') == 'top', 'Baseline text requires top vertical alignment: ' + label)
        elif kind == 'image':
            check(enum(obj.get('fit', 'contain'), {'contain', 'stretch'}), 'Invalid image fit: ' + label)
            check(obj.get('editable') is False, 'Raster assets must be marked editable=false')
            check(opacity == 1, 'Raster opacity is unsupported; preserve it in the raster asset: ' + label)
            check(style.get('fill', 'none') == 'none' and style.get('stroke', 'none') == 'none' and stroke_width == 0,
                  'Raster fill and stroke styles are unsupported; use a separate native path: ' + label)
            check(obj.get('rotation', 0) == 0, 'Raster rotation is unsupported; prepare the raster asset first: ' + label)
            try:
                asset = confined(root, obj.get('path'))
                check(asset.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}, 'Unsupported raster file type: ' + label)
                check(digest(asset) == obj.get('sha256'), 'Raster asset hash changed: ' + label)
                from PIL import Image
                try:
                    with Image.open(asset) as image:
                        image.verify()
                except Image.DecompressionBombError as exc:
                    raise ValueError('Raster asset exceeds safe image dimensions: ' + label) from exc
            except (ValueError, OSError, ImportError) as exc: errors.append(str(exc))
            check('box' in obj, 'Raster asset needs a box')
            if 'crop' in obj:
                crop = obj['crop']
                good = isinstance(crop, dict) and set(crop) == {'left', 'top', 'right', 'bottom'} and all(finite(v) and 0 <= v < 1 for v in crop.values())
                check(good and crop['left'] + crop['right'] < 1 and crop['top'] + crop['bottom'] < 1, 'Invalid raster crop fractions')
                if good:
                    # DrawingML stores crop in 1/100000 units, using half-up
                    # rounding. A positive floating area can round to nothing.
                    q = {k: math.floor(value * 100000 + .5) for k, value in crop.items()}
                    check(q['left'] + q['right'] < 100000 and q['top'] + q['bottom'] < 100000,
                          'Raster crop becomes empty after DrawingML quantization: ' + label)
        if 'box' in obj:
            box = obj['box']
            good = isinstance(box, dict) and set(box) == {'x', 'y', 'width', 'height'} and all(finite(box.get(k)) for k in ('x', 'y', 'width', 'height'))
            check(good, 'Invalid object box: ' + label)
            if good:
                check(box['width'] > 0 and box['height'] > 0, 'Object box must have positive dimensions: ' + label)
                if finite(obj.get('rotation', 0)):
                    # PPT rotates a text box about its center; validate its visible
                    # footprint rather than only its unrotated editing rectangle.
                    cx, cy = box['x']+box['width']/2, box['y']+box['height']/2
                    theta = math.radians(obj.get('rotation', 0))
                    co, si = math.cos(theta), math.sin(theta)
                    for dx, dy in ((-box['width']/2, -box['height']/2), (-box['width']/2, box['height']/2), (box['width']/2, -box['height']/2), (box['width']/2, box['height']/2)):
                        in_canvas(cx+co*dx-si*dy, cy+si*dx+co*dy, 'Object box ' + label)
    check(not ids.intersection(groups), 'Group ids must differ from member ids')
    if 'authoring' in manifest and not errors:
        try:
            from .authoring import verify_creation_inputs
            verify_creation_inputs(manifest, root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append('Creation inputs: ' + str(exc))
    result = report()
    if not errors:
        from .content_audit import audit_source_content
        content = audit_source_content(manifest)
        result['source_content'] = content
        if content['status'] == 'FAIL':
            errors.extend('Source content: ' + item.get('code', 'unresolved')
                          for item in content['mismatches'] + content['unresolved'])
            result['status'] = 'FAIL'
        if content['diagnostics']:
            warnings.append('Possible duplicate live text requires source review; see source_content diagnostics')
        if 'source_inventory' in manifest and not _materialized:
            from .source_inventory import audit_source_inventory
            inventory = audit_source_inventory(manifest, root)
            result['source_inventory'] = inventory
            if inventory['status'] == 'FAIL':
                errors.extend('Source inventory: ' + item.get('code', 'unresolved')
                              for item in inventory['mismatches'] + inventory['unresolved'])
                result['status'] = 'FAIL'
            elif inventory['status'] == 'REVIEW':
                warnings.append('Source component coverage or representation needs review; see source_inventory')
    return result

def load_and_validate(path, require_review=True):
    path = Path(path)
    data = json.loads(path.read_text())
    report = validate(data, path.parent, require_review)
    if report['errors']:
        raise ValueError('\n'.join(report['errors']))
    return data, report
