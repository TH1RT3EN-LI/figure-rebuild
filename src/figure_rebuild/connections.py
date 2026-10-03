"""Stable-ID relationships and transactional, local scene geometry edits.

This module resolves declared connections; it does not infer connections from
an image. Existing faithful path arrows remain paths. OOXML native connection
sites and safe grouping of attached labels are handled by the PPT exporter.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from .review import content_digest, REVIEW_METADATA


SITE_INDEX = {'top': 0, 'left': 1, 'bottom': 2, 'right': 3}
SITES = set(SITE_INDEX) | {'center'}
ARROWS = {'none', 'triangle', 'stealth', 'arrow'}
LABEL_KINDS = {'text', 'image', 'formula'}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value))


def _number(value, label):
    if not _finite(value):
        raise ValueError(label + ' must be finite')
    return value


def _point(value, label):
    if not isinstance(value, dict) or set(value) != {'x', 'y'}:
        raise ValueError(label + ' requires x and y')
    return {'x': _number(value['x'], label + '.x'), 'y': _number(value['y'], label + '.y')}


def _box(value, label):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'width', 'height'}:
        raise ValueError(label + ' requires x, y, width and height')
    box = {key: _number(value[key], label + '.' + key) for key in value}
    if box['width'] <= 0 or box['height'] <= 0:
        raise ValueError(label + ' requires positive width and height')
    return box


def _extrema(p0, p1, p2, p3):
    # Derivative of cubic Bernstein polynomial / 3.
    a, b, c = -p0 + 3*p1 - 3*p2 + p3, 2*(p0 - 2*p1 + p2), p1 - p0
    roots = []
    if abs(a) < 1e-14:
        if abs(b) > 1e-14:
            roots = [-c / b]
    else:
        discriminant = b*b - 4*a*c
        if discriminant >= 0:
            root = math.sqrt(discriminant)
            roots = [(-b + root)/(2*a), (-b - root)/(2*a)]
    return [t for t in roots if 0 < t < 1]


def path_bounds(obj):
    """Exact centerline bounds, including cubic extrema (not control hull)."""
    commands = obj.get('commands') if isinstance(obj, dict) else None
    if not isinstance(commands, list) or len(commands) < 2 or len(commands) > 200000:
        raise ValueError('Path requires a bounded commands list')
    points, previous, start = [], None, None
    for command in commands:
        if not isinstance(command, dict) or len(command) != 1:
            raise ValueError('Invalid path command')
        op, value = next(iter(command.items()))
        if op in ('moveTo', 'lineTo'):
            point = _point(value, 'Path point')
            if op == 'lineTo' and previous is None:
                raise ValueError('lineTo before moveTo')
            if op == 'moveTo':
                start = point
            previous = point
            points.append((point['x'], point['y']))
        elif op == 'cubicTo':
            if previous is None or not isinstance(value, dict) or set(value) != {'x1', 'y1', 'x2', 'y2', 'x', 'y'}:
                raise ValueError('Invalid cubicTo or cubicTo before moveTo')
            for key in value:
                _number(value[key], 'Cubic ' + key)
            p0, p1, p2, p3 = ((previous['x'], previous['y']),
                             (value['x1'], value['y1']), (value['x2'], value['y2']),
                             (value['x'], value['y']))
            ts = set([0., 1.] + _extrema(p0[0], p1[0], p2[0], p3[0]) +
                     _extrema(p0[1], p1[1], p2[1], p3[1]))
            for t in ts:
                u = 1-t
                points.append(tuple(u*u*u*p0[axis] + 3*u*u*t*p1[axis] +
                                    3*u*t*t*p2[axis] + t*t*t*p3[axis] for axis in (0, 1)))
            previous = {'x': value['x'], 'y': value['y']}
        elif op == 'close':
            if start is None or value != {}:
                raise ValueError('Invalid close or close before moveTo')
            previous = start
        else:
            raise ValueError('Unsupported path command: ' + str(op))
    if not points:
        raise ValueError('Path has no points')
    xs, ys = zip(*points)
    return {'x': min(xs), 'y': min(ys), 'width': max(xs)-min(xs), 'height': max(ys)-min(ys)}


def object_bounds(obj):
    if obj.get('kind') == 'path':
        return path_bounds(obj)
    if 'box' in obj:
        return _box(obj['box'], 'Object box')
    if obj.get('kind') == 'text' and 'anchor' in obj:
        point = _point(obj['anchor'], 'Text anchor')
        return dict(point, width=0, height=0)
    if obj.get('kind') == 'formula' and 'baseline_anchor' in obj:
        point = _point(obj['baseline_anchor'], 'Formula baseline anchor')
        return dict(point, width=0, height=0)
    raise ValueError('Object needs an explicit box or path bounds: ' + str(obj.get('id')))


def _site(obj, name):
    bounds = object_bounds(obj)
    x, y, w, h = (bounds[key] for key in ('x', 'y', 'width', 'height'))
    points = {'top': (x+w/2, y), 'left': (x, y+h/2), 'bottom': (x+w/2, y+h),
              'right': (x+w, y+h/2), 'center': (x+w/2, y+h/2)}
    px, py = points[name]
    return {'x': px, 'y': py}


def _objects(manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get('objects'), list) or len(manifest['objects']) > 10000:
        raise ValueError('Scene needs an objects list capped at 10000')
    objects = {}
    for obj in manifest['objects']:
        if not isinstance(obj, dict) or not _id(obj.get('id')):
            raise ValueError('Every object needs a stable id')
        if obj['id'] in objects:
            raise ValueError('Duplicate stable id: ' + obj['id'])
        objects[obj['id']] = obj
    return objects


def _attachment_order(objects):
    # Kahn's algorithm avoids recursion depth failures on long label chains.
    followers = {key: [] for key in objects}
    pending = {key: 0 for key in objects}
    for key, obj in objects.items():
        relation = obj.get('attach_to')
        if relation:
            followers[relation['id']].append(key)
            pending[key] += 1
    queue = [key for key in objects if pending[key] == 0]
    order, position = [], 0
    while position < len(queue):
        key = queue[position]; position += 1; order.append(key)
        for follower in followers[key]:
            pending[follower] -= 1
            if pending[follower] == 0:
                queue.append(follower)
    if len(order) != len(objects):
        raise ValueError('Attachment cycle: ' + ', '.join(key for key in objects if pending[key])[:300])
    return order


def locked_ids(manifest):
    objects = _objects(manifest)
    result = set()
    for key, obj in objects.items():
        if 'locked' in obj and not isinstance(obj['locked'], bool):
            raise ValueError('locked must be boolean: ' + key)
        if obj.get('locked'):
            result.add(key)
    locks = manifest.get('locks', [])
    if not isinstance(locks, list):
        raise ValueError('locks must be a list of stable-id records')
    for lock in locks:
        if not isinstance(lock, dict) or set(lock) - {'id', 'reason'} or not _id(lock.get('id')):
            raise ValueError('Lock requires a stable id; field/index locks are unsupported')
        if lock['id'] not in objects:
            raise ValueError('Dangling lock: ' + lock['id'])
        result.add(lock['id'])
    return result


def validate_connections(manifest):
    """Return errors without altering objects, order, or relationship metadata."""
    errors = []
    try:
        objects = _objects(manifest)
        locked_ids(manifest)
    except ValueError as error:
        return [str(error)]
    for key, obj in objects.items():
        try:
            if 'attach_to' in obj:
                if obj.get('kind') not in LABEL_KINDS:
                    raise ValueError('Only text/image/formula labels support attach_to: ' + key)
                relation = obj['attach_to']
                if not isinstance(relation, dict) or set(relation) != {'id', 'site', 'offset'}:
                    raise ValueError('attach_to requires id, site and offset: ' + key)
                target = relation['id']
                if not _id(target) or target not in objects:
                    raise ValueError('Dangling attachment target: ' + key)
                if objects[target].get('kind') == 'connector':
                    raise ValueError('A label cannot attach to a connector: ' + key)
                if not isinstance(relation['site'], str) or relation['site'] not in SITES:
                    raise ValueError('Unknown attachment site: ' + key)
                _point(relation['offset'], 'Attachment offset ' + key)
                object_bounds(obj); object_bounds(objects[target])
                if any(field in objects[target] for field in ('anchor', 'baseline_anchor')) and 'box' not in objects[target] and relation['site'] != 'center':
                    raise ValueError('Anchor-only attachment target requires center site: ' + key)
            if obj.get('kind') == 'connector':
                if any(field in obj for field in ('box', 'anchor', 'baseline_anchor', 'commands', 'attach_to')):
                    raise ValueError('Connector geometry is derived from endpoints, not stored commands/box: ' + key)
                if obj.get('route') not in ('straight', 'elbow'):
                    raise ValueError('Unsupported connector route: ' + key)
                for end in ('from', 'to'):
                    endpoint = obj.get(end)
                    if not isinstance(endpoint, dict) or set(endpoint) != {'id', 'site'}:
                        raise ValueError('Connector endpoint requires id and site: ' + key)
                    target = endpoint['id']
                    if not _id(target) or target not in objects:
                        raise ValueError('Dangling connector endpoint: ' + key)
                    if objects[target].get('kind') == 'connector' or ('box' not in objects[target] and objects[target].get('kind') != 'path'):
                        raise ValueError('Connector target requires explicit box/path: ' + key)
                    if not isinstance(endpoint['site'], str) or endpoint['site'] not in SITE_INDEX:
                        raise ValueError('Unknown connector site: ' + key)
                    bounds = object_bounds(objects[target])
                    if bounds['width'] <= 0 or bounds['height'] <= 0:
                        raise ValueError('Connector target requires positive-area compiled box/path: ' + key)
                arrow = obj.get('arrow')
                if not isinstance(arrow, dict) or set(arrow) != {'start', 'end'} or any(
                        not isinstance(arrow[end], str) or arrow[end] not in ARROWS for end in ('start', 'end')):
                    raise ValueError('Connector needs explicit supported start/end arrow types: ' + key)
                style = obj.get('style')
                if not isinstance(style, dict) or style.get('fill', 'none') != 'none' or not isinstance(style.get('stroke'), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', style['stroke']):
                    raise ValueError('Connector needs solid stroke and no fill: ' + key)
                if not _finite(style.get('stroke_width')) or style['stroke_width'] <= 0:
                    raise ValueError('Connector needs positive stroke_width: ' + key)
                if not _finite(style.get('opacity', 1)) or not 0 < style.get('opacity', 1) <= 1:
                    raise ValueError('Invalid connector opacity: ' + key)
        except (ValueError, TypeError) as error:
            errors.append(str(error))
    if not errors:
        try:
            _attachment_order(objects)
        except ValueError as error:
            errors.append(str(error))
    return errors


def _resolve_labels(objects):
    records = []
    for key in _attachment_order(objects):
        obj = objects[key]
        relation = obj.get('attach_to')
        if not relation:
            continue
        point = _site(objects[relation['id']], relation['site'])
        point = {axis: point[axis] + relation['offset'][axis] for axis in ('x', 'y')}
        if 'box' in obj:
            obj['box']['x'] = point['x'] - obj['box']['width']/2
            obj['box']['y'] = point['y'] - obj['box']['height']/2
        elif obj.get('kind') == 'formula' and 'baseline_anchor' in obj:
            obj['baseline_anchor'] = point
        else:
            obj['anchor'] = point
        records.append({'id': key, 'target_id': relation['id'], 'site': relation['site'],
                        'offset': copy.deepcopy(relation['offset']),
                        'position_kind': 'box_center' if 'box' in obj else 'baseline_anchor'})
    return records


def attachment_records(manifest):
    errors = validate_connections(manifest)
    if errors:
        raise ValueError('; '.join(errors[:10]))
    copy_manifest = copy.deepcopy(manifest)
    return _resolve_labels(_objects(copy_manifest))


def _connector_geometry(obj, objects):
    ends = {}
    for name in ('from', 'to'):
        endpoint = obj[name]
        ends[name] = dict(endpoint, site_index=SITE_INDEX[endpoint['site']],
                          point=_site(objects[endpoint['id']], endpoint['site']))
    first, last = ends['from']['point'], ends['to']['point']
    points = [first]
    if obj['route'] == 'elbow':
        horizontal_first = obj['from']['site'] in ('left', 'right')
        horizontal_last = obj['to']['site'] in ('left', 'right')
        if horizontal_first and horizontal_last:
            middle = (first['x'] + last['x'])/2
            points += [{'x': middle, 'y': first['y']}, {'x': middle, 'y': last['y']}]
        elif not horizontal_first and not horizontal_last:
            middle = (first['y'] + last['y'])/2
            points += [{'x': first['x'], 'y': middle}, {'x': last['x'], 'y': middle}]
        elif horizontal_first:
            points.append({'x': last['x'], 'y': first['y']})
        else:
            points.append({'x': first['x'], 'y': last['y']})
    points.append(last)
    compact = [points[0]]
    for point in points[1:]:
        if point != compact[-1]:
            compact.append(point)
    if len(compact) < 2:
        raise ValueError('Connector endpoints coincide: ' + obj['id'])
    commands = [{'moveTo': compact[0]}] + [{'lineTo': point} for point in compact[1:]]
    return {'id': obj['id'], **ends, 'route': obj['route'], 'arrow': copy.deepcopy(obj['arrow']),
            'style': copy.deepcopy(obj['style']), 'points': compact, 'commands': commands,
            'box': path_bounds({'commands': commands})}


def resolve_scene(manifest):
    """Return (materialized copy, connector records) for rendering/export.

    Same stable connector IDs become ordinary paths in the temporary copy.
    Attached labels resolve first; exporter records keep their IDs via
    attachment_records(). Original semantic input is never modified.
    """
    errors = validate_connections(manifest)
    if errors:
        raise ValueError('; '.join(errors[:10]))
    materialized = copy.deepcopy(manifest)
    objects = _objects(materialized)
    for record in _resolve_labels(objects):
        objects[record['id']]['attachment_record'] = copy.deepcopy(record)
    connections = []
    for obj in materialized['objects']:
        if obj.get('kind') != 'connector':
            continue
        record = _connector_geometry(obj, objects)
        connections.append(record)
        obj['kind'] = 'path'
        obj['source_kind'] = 'connector'
        obj['connection_record'] = copy.deepcopy(record)
        obj['commands'] = copy.deepcopy(record['commands'])
        for field in ('from', 'to', 'route', 'arrow'):
            obj.pop(field)
    return materialized, connections


def _translate(obj, dx, dy):
    if obj.get('kind') == 'connector':
        raise ValueError('Move a connector endpoint module, not the derived connector: ' + obj['id'])
    if 'attach_to' in obj:
        offset = obj['attach_to']['offset']
        offset['x'] += dx; offset['y'] += dy
    elif obj.get('kind') == 'path':
        for command in obj['commands']:
            operation, value = next(iter(command.items()))
            if operation == 'close':
                continue
            for xkey, ykey in [('x', 'y'), ('x1', 'y1'), ('x2', 'y2')]:
                if xkey in value:
                    value[xkey] += dx; value[ykey] += dy
    elif 'box' in obj:
        obj['box']['x'] += dx; obj['box']['y'] += dy
    elif 'anchor' in obj:
        obj['anchor']['x'] += dx; obj['anchor']['y'] += dy
    elif obj.get('kind') == 'formula' and 'baseline_anchor' in obj:
        obj['baseline_anchor']['x'] += dx; obj['baseline_anchor']['y'] += dy
    else:
        raise ValueError('Object has no editable geometry: ' + obj['id'])


def _setbox(obj, box, objects):
    if obj.get('kind') == 'connector':
        raise ValueError('Set a connector endpoint module box, not the derived connector: ' + obj['id'])
    if obj.get('kind') == 'path':
        old = path_bounds(obj)
        if old['width'] <= 0 or old['height'] <= 0:
            raise ValueError('Cannot resize a zero-area path: ' + obj['id'])
        sx, sy = box['width']/old['width'], box['height']/old['height']
        for command in obj['commands']:
            operation, value = next(iter(command.items()))
            if operation == 'close':
                continue
            for xkey, ykey in [('x', 'y'), ('x1', 'y1'), ('x2', 'y2')]:
                if xkey in value:
                    value[xkey] = box['x'] + (value[xkey]-old['x'])*sx
                    value[ykey] = box['y'] + (value[ykey]-old['y'])*sy
    elif 'box' in obj:
        obj['box'] = copy.deepcopy(box)
        if 'attach_to' in obj:
            relation = obj['attach_to']
            point = _site(objects[relation['id']], relation['site'])
            relation['offset'] = {'x': box['x']+box['width']/2-point['x'],
                                  'y': box['y']+box['height']/2-point['y']}
    else:
        raise ValueError('setbox needs an existing box or positive-area path: ' + obj['id'])


def _validate_authored(manifest, root):
    # Validate semantic authored input so frozen formula assets are verified;
    # compiled diagnostic paths/images are deliberately not accepted as input.
    from .validate import validate
    report = validate(manifest, root, require_review=False)
    if report['status'] != 'PASS':
        raise ValueError('Scene validation failed: ' + '; '.join(report['errors'][:12]))


def apply_scene_patch(manifest, patch, root):
    """Validate, edit an isolated copy, then return (new semantic scene, audit).

    All failed edits, stale bases, or locked dependent labels/connections leave
    the input object untouched. No files are written by this function.
    """
    if not isinstance(patch, dict) or set(patch) - {'base_revision', 'base_digest', 'reason', 'operations'}:
        raise ValueError('Patch supports only base_revision/base_digest/reason/operations')
    revision = manifest.get('revision') if isinstance(manifest, dict) else None
    if type(revision) is not int or revision < 1 or patch.get('base_revision') != revision or type(patch.get('base_revision')) is not int:
        raise ValueError('Stale base_revision')
    expected = content_digest(manifest)
    if patch.get('base_digest') != expected:
        raise ValueError('Stale base_digest')
    reason = patch.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Patch requires a nonempty modification reason')
    operations = patch.get('operations')
    if not isinstance(operations, list) or not 0 < len(operations) <= 1000:
        raise ValueError('Patch requires 1..1000 stable-id operations')
    _validate_authored(manifest, root)
    base_resolved, base_connections = resolve_scene(manifest)
    before = _objects(base_resolved)
    result = copy.deepcopy(manifest)
    objects = _objects(result)
    # Resolve after every operation so sequential edits to attachment chains
    # reference the already-updated geometry, rather than stale stored boxes.
    _resolve_labels(objects)
    for operation in operations:
        if not isinstance(operation, dict) or not _id(operation.get('id')) or operation['id'] not in objects:
            raise ValueError('Patch operation needs an existing stable id')
        obj = objects[operation['id']]
        if operation.get('op') == 'translate' and set(operation) == {'op', 'id', 'dx', 'dy'}:
            _translate(obj, _number(operation['dx'], 'dx'), _number(operation['dy'], 'dy'))
        elif operation.get('op') == 'setbox' and set(operation) == {'op', 'id', 'box'}:
            _setbox(obj, _box(operation['box'], 'Patch box'), objects)
        else:
            raise ValueError('Only exact translate and setbox operations are supported')
        _resolve_labels(objects)
    _validate_authored(result, root)
    after_resolved, after_connections = resolve_scene(result)
    after = _objects(after_resolved)
    for key in locked_ids(manifest):
        if before[key] != after[key]:
            raise ValueError('Patch would change locked object/dependent geometry: ' + key)
    changed = [key for key in before if before[key] != after[key]]
    if not changed:
        raise ValueError('Patch makes no geometric change')
    result['revision'] = revision + 1
    recognition = result['recognition']
    for field in REVIEW_METADATA:
        recognition.pop(field, None)
    recognition['status'] = 'needs_review'
    audit = {'schema_version': 1, 'job_id': result['id'], 'base_revision': revision,
             'base_digest': expected, 'revision': result['revision'], 'reason': reason.strip(),
             'operations': copy.deepcopy(operations), 'affected_ids': changed,
             'attachment_records': attachment_records(result),
             'connections_before': base_connections, 'connections_after': after_connections,
             'created_at': datetime.now(timezone.utc).isoformat(),
             'review_reset': True, 'input_preserved': True}
    return result, audit


def write_patch(input_path, patch, output_path):
    """Transactionally publish a fresh same-job manifest after a base snapshot."""
    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    if destination.parent != source.parent or destination == source:
        raise ValueError('Output must be a new filename in the same job directory')
    if destination.exists():
        raise ValueError('Output already exists; refusing to overwrite')
    original = source.read_bytes()
    manifest = json.loads(original.decode('utf-8'))
    result, audit = apply_scene_patch(manifest, patch, source.parent)
    encoded = (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8')
    snapshot_parent = source.parent / 'history' / 'scene-patches'
    snapshot = snapshot_parent / f'rev-{manifest["revision"]:04d}-{uuid.uuid4().hex[:12]}'
    history_existed = snapshot_parent.parent.exists()
    snapshot_parent_existed = snapshot_parent.exists()
    temporary = None
    committed = False
    owns_snapshot = False
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.scene-patch-', suffix='.json', dir=source.parent)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        if source.read_bytes() != original:
            raise ValueError('Source manifest changed during patch validation')
        snapshot.mkdir(parents=True, exist_ok=False)
        owns_snapshot = True
        with (snapshot / 'base-manifest.json').open('xb') as stream:
            stream.write(original); stream.flush(); os.fsync(stream.fileno())
        audit.update(base_file_sha256=hashlib.sha256(original).hexdigest(),
                     snapshot=str(snapshot / 'base-manifest.json'), output=str(destination))
        with (snapshot / 'patch-audit.json').open('x', encoding='utf-8') as stream:
            json.dump(audit, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        if source.read_bytes() != original:
            raise ValueError('Source manifest changed before patch commit')
        # Atomic exclusive publish: a competing writer never gets overwritten.
        os.link(temporary, destination)
        committed = True
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
        if not committed and owns_snapshot:
            for name in ('base-manifest.json', 'patch-audit.json'):
                try:
                    (snapshot / name).unlink()
                except FileNotFoundError:
                    pass
            snapshot.rmdir()
        if not committed:
            # Only remove empty parents created by this failed transaction.
            for directory, existed in ((snapshot_parent, snapshot_parent_existed),
                                       (snapshot_parent.parent, history_existed)):
                if not existed:
                    try:
                        directory.rmdir()
                    except (FileNotFoundError, OSError):
                        pass
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--patch', required=True, help='JSON patch with explicit base revision and content digest')
    parser.add_argument('--output', required=True, help='Fresh same-job manifest filename')
    args = parser.parse_args(argv)
    try:
        patch = json.loads(Path(args.patch).read_text(encoding='utf-8'))
        report = write_patch(args.manifest, patch, args.output)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, 'scene patch: ' + str(error) + '\n')
    print(json.dumps({'output': report['output'], 'revision': report['revision'],
                      'affected_ids': report['affected_ids'], 'snapshot': report['snapshot'],
                      'review_reset': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
