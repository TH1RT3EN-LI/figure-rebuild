"""Compare declared source evidence with the final, materialized scene.

Optional manifest schema (source coordinates, exact Unicode, no normalization)::

    source_evidence = {
        "schema_version": 1, "source_sha256": "<original source SHA256>",
        "literals": [{"id": "label-reading", "status": "confirmed",
                      "source_region": {"x": 1, "y": 2, "width": 30, "height": 12},
                      "object_ids": ["label-a", "label-b"], "text": "fsih"}],
        "connections": [{"id": "flow-reading", "status": "confirmed",
                         "source_region": {"x": 1, "y": 2, "width": 90, "height": 40},
                         "object_id": "flow", "from": {"id": "a", "site": "right"},
                         "to": {"id": "b", "site": "left"},
                         "arrow": {"start": "none", "end": "triangle"}}]
    }

Each record may instead have status ``unresolved``; its expected text/endpoints
can then be omitted. ``reason`` is optional. ``resolves`` lists previous unknown
IDs explicitly addressed by this evidence. Clearing recognition.unresolved does
not resolve a source-evidence record. With previous_manifest supplied, removing
old unknowns requires a currently matching confirmed record with ``resolves``.
Legacy string unknowns use their exact string as ID; records use their ``id``
or the deterministic ID returned by this audit.

Call after structural validation and compile_scene. This module reads only
resolved_scene.objects, never
replaces text, infers a reading, deletes duplicates, or grants visual acceptance.
The source SHA is bound to scene.source.sha256; validate() separately verifies
the actual source file. PASS covers declared invariants only, not unrecorded
source content or PPT rendering. No history preservation claim is made without
previous_manifest. The function does not mutate either input.
"""
import copy
import hashlib
import json
import math
import re


_SHA = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
_SITES = {'left', 'right', 'top', 'bottom'}
_ARROWS = {'none', 'triangle', 'stealth', 'arrow'}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _identity(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _unknown_id(value):
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get('id'), str) and value['id']:
        return value['id']
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return 'recognition:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _visible(obj):
    style = obj.get('style', {})
    if not isinstance(style, dict):
        return False
    opacity = style.get('opacity', 1)
    return (_finite(opacity) and opacity > 0 and obj.get('visible', True) is not False
            and obj.get('hidden', False) is not True
            and (obj.get('kind') != 'text' or style.get('fill', '#000000') != 'none'))


def _duplicates(objects, tolerance):
    """Review hints only: coincident text may intentionally be layered."""
    buckets, findings = {}, []
    cell = max(tolerance, 1e-9)
    for obj in objects:
        if obj.get('kind') != 'text' or not _visible(obj) or not isinstance(obj.get('text'), str):
            continue
        basis = 'box' if 'box' in obj else 'anchor'
        position = obj.get(basis)
        rotation = obj.get('rotation', 0)
        if (not isinstance(position, dict) or not all(_finite(position.get(k)) for k in ('x', 'y'))
                or not _finite(rotation)):
            continue
        x, y = position['x'], position['y']
        scaled_x, scaled_y = x / cell, y / cell
        if not _finite(scaled_x) or not _finite(scaled_y):
            findings.append({'code': 'duplicate_position_out_of_range', 'object_ids': [obj['id']],
                             'requires_review': True, 'automatic_action': 'none'})
            continue
        bx, by = math.floor(scaled_x), math.floor(scaled_y)
        prefix = (obj['text'], basis, rotation)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for old in buckets.get((*prefix, bx + dx, by + dy), []):
                    if max(abs(x - old[basis]['x']), abs(y - old[basis]['y'])) <= tolerance:
                        findings.append({'code': 'possible_duplicate_live_text',
                                         'object_ids': [old['id'], obj['id']], 'text': obj['text'],
                                         'requires_review': True, 'automatic_action': 'none'})
                        if len(findings) >= 100:
                            findings.append({'code': 'duplicate_diagnostic_limit',
                                             'requires_review': True, 'automatic_action': 'none'})
                            return findings
        buckets.setdefault((*prefix, bx, by), []).append(obj)
    return findings


def audit_source_content(resolved_scene, *, previous_manifest=None, duplicate_tolerance=1.0):
    """Return mismatches, unresolved readings and non-destructive diagnostics.

    FAIL means mismatches or unresolved evidence remain. REVIEW means only
    duplicate diagnostics remain. NOT_PROVIDED means no source constraints were
    supplied. All statuses leave model review and user acceptance independent.
    """
    report = {'schema_version': 1, 'status': 'NOT_PROVIDED', 'mismatches': [],
              'unresolved': [], 'diagnostics': [], 'source_binding': 'not_provided',
              'history_checked': previous_manifest is not None,
              'coverage': {'scope': 'declared_invariants_only', 'literals_checked': 0,
                           'connections_checked': 0},
              'structural_completion': 'not_evaluated', 'model_review': 'not_evaluated',
              'user_acceptance': 'pending',
              'limitations': ['No OCR or semantic inference; expected readings are caller-provided evidence.',
                              'Final PPT glyphs, occlusion, and painted arrowheads require visual inspection.']}

    def mismatch(code, **detail):
        report['mismatches'].append({'code': code, **copy.deepcopy(detail)})

    def finish():
        if report['mismatches'] or report['unresolved']:
            report['status'] = 'FAIL'
        elif report['diagnostics']:
            report['status'] = 'REVIEW'
        elif sum(report['coverage'][k] for k in ('literals_checked', 'connections_checked')):
            report['status'] = 'PASS'
        return report

    if not isinstance(resolved_scene, dict):
        mismatch('invalid_scene', detail='Expected materialized scene record')
        return finish()
    if not _finite(duplicate_tolerance) or duplicate_tolerance < 0:
        mismatch('invalid_duplicate_tolerance')
        return finish()
    raw_objects = resolved_scene.get('objects')
    if not isinstance(raw_objects, list):
        mismatch('invalid_objects')
        return finish()
    objects = {}
    for obj in raw_objects:
        if not isinstance(obj, dict) or not _identity(obj.get('id')):
            mismatch('invalid_object_id')
        elif obj['id'] in objects:
            mismatch('duplicate_object_id', object_id=obj['id'])
        else:
            objects[obj['id']] = obj
    report['diagnostics'] = _duplicates(list(objects.values()), duplicate_tolerance)
    source = resolved_scene.get('source', {})
    source_sha = source.get('sha256') if isinstance(source, dict) else None
    canvas = resolved_scene.get('canvas', {})

    def unknowns(manifest):
        recognition = manifest.get('recognition', {})
        if not isinstance(recognition, dict) or not isinstance(recognition.get('unresolved', []), list):
            mismatch('invalid_recognition_unresolved')
            return {}
        result = {}
        for value in recognition.get('unresolved', []):
            try:
                key = _unknown_id(value)
            except (TypeError, ValueError, UnicodeError):
                mismatch('invalid_recognition_unresolved_entry')
                continue
            if key in result:
                mismatch('duplicate_unresolved_id', unresolved_id=key)
            result[key] = value
        return result

    current_unknowns = unknowns(resolved_scene)
    report['unresolved'].extend({'id': key, 'code': 'recognition_unresolved', 'evidence': copy.deepcopy(value)}
                                for key, value in current_unknowns.items())
    evidence = resolved_scene.get('source_evidence')
    records, resolutions, seen = [], {}, set()
    if 'source_evidence' in resolved_scene:
        if (not isinstance(evidence, dict) or set(evidence) - {'schema_version', 'source_sha256', 'literals', 'connections'}
                or type(evidence.get('schema_version')) is not int or evidence.get('schema_version') != 1):
            mismatch('invalid_source_evidence')
            evidence = {}
        evidence_sha = evidence.get('source_sha256')
        if (not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha)
                or not isinstance(evidence_sha, str) or not _SHA.fullmatch(evidence_sha)
                or evidence_sha != source_sha):
            mismatch('source_sha_mismatch', expected=source_sha, actual=evidence_sha)
            report['source_binding'] = 'mismatch'
        else:
            report['source_binding'] = 'matched'
        for kind in ('literals', 'connections'):
            values = evidence.get(kind, [])
            if not isinstance(values, list):
                mismatch('invalid_evidence_list', kind=kind)
                continue
            records.extend((kind, record) for record in values)

    for kind, record in records:
        before = len(report['mismatches'])
        common = {'id', 'status', 'source_region', 'reason', 'resolves'}
        allowed = common | ({'object_ids', 'text'} if kind == 'literals' else {'object_id', 'from', 'to', 'arrow'})
        if not isinstance(record, dict) or set(record) - allowed or not _identity(record.get('id')):
            mismatch('invalid_evidence_record', kind=kind)
            continue
        rid = record['id']
        if rid in seen:
            mismatch('duplicate_evidence_id', evidence_id=rid)
        seen.add(rid)
        if record.get('status') not in ('confirmed', 'unresolved'):
            mismatch('unsupported_evidence_status', evidence_id=rid)
        if 'reason' in record and not isinstance(record['reason'], str):
            mismatch('invalid_evidence_reason', evidence_id=rid)
        resolves = record.get('resolves', [])
        if (not isinstance(resolves, list) or any(not isinstance(v, str) or not v for v in resolves)
                or len(resolves) != len(set(v for v in resolves if isinstance(v, str)))):
            mismatch('invalid_resolution_ids', evidence_id=rid)
            resolves = []
        region = record.get('source_region')
        if (not isinstance(canvas, dict) or not all(_finite(canvas.get(k)) and canvas[k] > 0 for k in ('width', 'height'))
                or not isinstance(region, dict) or set(region) != {'x', 'y', 'width', 'height'}
                or not all(_finite(region.get(k)) for k in ('x', 'y', 'width', 'height'))
                or min(region['x'], region['y']) < 0 or min(region['width'], region['height']) <= 0
                or region['x'] + region['width'] > canvas['width']
                or region['y'] + region['height'] > canvas['height']):
            mismatch('invalid_source_region', evidence_id=rid)
        targets = record.get('object_ids') if kind == 'literals' else [record.get('object_id')]
        if (not isinstance(targets, list) or not targets or any(not _identity(v) for v in targets)
                or len(set(v for v in targets if isinstance(v, str))) != len(targets)):
            mismatch('invalid_evidence_targets', evidence_id=rid)
            continue
        missing = [key for key in targets if key not in objects]
        if missing:
            mismatch('missing_object', evidence_id=rid, object_ids=missing)
        if kind == 'literals' and ('text' in record or record.get('status') == 'confirmed'):
            if not isinstance(record.get('text'), str) or not record['text']:
                mismatch('invalid_expected_text', evidence_id=rid)
        if record.get('status') == 'unresolved':
            report['unresolved'].append({'id': rid, 'code': 'source_reading_unresolved',
                                         'source_region': copy.deepcopy(region),
                                         'reason': record.get('reason', '')})
            continue
        if len(report['mismatches']) != before:
            continue
        if any(not _visible(objects[key]) for key in targets):
            mismatch('target_not_visible', evidence_id=rid, object_ids=targets)
            continue
        if kind == 'literals':
            if any(objects[key].get('kind') != 'text' or not isinstance(objects[key].get('text'), str) for key in targets):
                mismatch('literal_requires_live_text', evidence_id=rid, object_ids=targets)
                continue
            actual = ''.join(objects[key]['text'] for key in targets)
            report['coverage']['literals_checked'] += 1
            if actual != record['text']:
                mismatch('literal_mismatch', evidence_id=rid, object_ids=targets,
                         expected=record['text'], actual=actual)
        else:
            _check_connection(record, objects, mismatch)
            report['coverage']['connections_checked'] += 1
        if len(report['mismatches']) == before and report['source_binding'] == 'matched':
            for unresolved_id in resolves:
                resolutions.setdefault(unresolved_id, []).append((kind, record))

    if previous_manifest is not None:
        if not isinstance(previous_manifest, dict):
            mismatch('invalid_previous_manifest')
        else:
            old_source = previous_manifest.get('source', {})
            if not isinstance(old_source, dict) or old_source.get('sha256') != source_sha:
                mismatch('previous_source_sha_mismatch')
            old_unknowns = unknowns(previous_manifest)
            old_kinds = {}
            old_evidence = previous_manifest.get('source_evidence', {})
            if (not isinstance(old_evidence, dict) or ('source_evidence' in previous_manifest and (
                    set(old_evidence) - {'schema_version', 'source_sha256', 'literals', 'connections'}
                    or type(old_evidence.get('schema_version')) is not int
                    or old_evidence.get('schema_version') != 1
                    or old_evidence.get('source_sha256') != source_sha))):
                mismatch('invalid_previous_evidence')
            else:
                old_ids = set()
                for kind in ('literals', 'connections'):
                    old_records = old_evidence.get(kind, [])
                    if not isinstance(old_records, list):
                        mismatch('invalid_previous_evidence')
                        continue
                    for record in old_records:
                        if (not isinstance(record, dict) or not _identity(record.get('id'))
                                or record.get('status') not in ('confirmed', 'unresolved')
                                or record.get('id') in old_ids):
                            mismatch('invalid_previous_evidence')
                            continue
                        old_ids.add(record['id'])
                        if record['status'] == 'unresolved':
                            old_unknowns[record['id']] = record
                            old_kinds[record['id']] = kind
            current_ids = current_unknowns.keys() | {item['id'] for item in report['unresolved']}
            for key, value in old_unknowns.items():
                candidates = resolutions.get(key, [])
                if key in old_kinds:
                    # A checked reading in a different part of the source, or
                    # for a different target, cannot discharge this unknown.
                    # Stable target IDs and overlapping source scope survive
                    # the transition from unresolved to confirmed evidence.
                    previous_kind = old_kinds[key]
                    target_key = 'object_ids' if previous_kind == 'literals' else 'object_id'
                    candidates = [(kind, item) for kind, item in candidates
                                  if kind == previous_kind and item.get(target_key) == value.get(target_key)
                                  and _regions_overlap(item.get('source_region'), value.get('source_region'))]
                if key not in current_ids and not candidates:
                    report['unresolved'].append({'id': key, 'code': 'unknown_removed_without_evidence',
                                                 'previous_evidence': copy.deepcopy(value)})
    else:
        report['limitations'].append('No prior manifest supplied; previously removed unknowns cannot be detected.')
    return finish()


def _regions_overlap(first, second):
    for region in (first, second):
        if (not isinstance(region, dict) or set(region) != {'x', 'y', 'width', 'height'}
                or not all(_finite(region.get(k)) for k in ('x', 'y', 'width', 'height'))
                or min(region['width'], region['height']) <= 0):
            return False
    return (max(first['x'], second['x']) < min(first['x'] + first['width'], second['x'] + second['width'])
            and max(first['y'], second['y']) < min(first['y'] + first['height'], second['y'] + second['height']))


def _check_connection(record, objects, mismatch):
    """Check both declared topology and actual materialized path consistency."""
    rid, oid = record['id'], record['object_id']
    for end in ('from', 'to'):
        target = record.get(end)
        if (not isinstance(target, dict) or set(target) != {'id', 'site'}
                or not _identity(target.get('id')) or not isinstance(target.get('site'), str)
                or target['site'] not in _SITES):
            mismatch('invalid_expected_connection', evidence_id=rid, endpoint=end)
            return
        if target['id'] not in objects:
            mismatch('missing_connection_target', evidence_id=rid, object_id=target['id'])
            return
        if not _visible(objects[target['id']]):
            mismatch('connection_target_not_visible', evidence_id=rid, object_id=target['id'])
            return
    arrow = record.get('arrow')
    if (not isinstance(arrow, dict) or set(arrow) != {'start', 'end'}
            or any(not isinstance(arrow.get(k), str) or arrow[k] not in _ARROWS for k in ('start', 'end'))):
        mismatch('invalid_expected_arrow', evidence_id=rid)
        return
    obj = objects[oid]
    actual = obj.get('connection_record')
    if obj.get('kind') != 'path' or obj.get('source_kind') != 'connector' or not isinstance(actual, dict):
        mismatch('connection_requires_materialized_record', evidence_id=rid, object_id=oid)
        return
    topology = {end: {key: actual.get(end, {}).get(key) for key in ('id', 'site')}
                for end in ('from', 'to') if isinstance(actual.get(end), dict)}
    expected = {end: record[end] for end in ('from', 'to')}
    if topology != expected or actual.get('arrow') != arrow:
        mismatch('connection_mismatch', evidence_id=rid, expected={**expected, 'arrow': arrow},
                 actual={**topology, 'arrow': actual.get('arrow')})
        return
    if (actual.get('id') != oid or obj.get('commands') != actual.get('commands')
            or obj.get('style') != actual.get('style')):
        mismatch('connection_materialization_mismatch', evidence_id=rid, object_id=oid)
        return
    # Regenerate only the declared edge against *current* endpoint objects. This
    # detects a stale record even when its commands were copied into the path.
    from .connections import resolve_scene
    semantic = {'id': oid, 'kind': 'connector', **expected, 'arrow': arrow,
                'route': actual.get('route'), 'style': copy.deepcopy(obj.get('style'))}
    endpoint_ids = {record[end]['id'] for end in ('from', 'to')}
    try:
        _, rebuilt = resolve_scene({'objects': [copy.deepcopy(objects[key]) for key in endpoint_ids] + [semantic]})
    except (ValueError, TypeError, KeyError) as error:
        mismatch('invalid_connection_materialization', evidence_id=rid, detail=str(error))
        return
    if obj.get('commands') != rebuilt[0]['commands']:
        mismatch('connection_geometry_mismatch', evidence_id=rid, object_id=oid)
