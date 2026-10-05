"""Check caller-reviewed source components against an immutable reference scene.

This is a content gate, not recognition. Source artifacts and the independently
reviewed reference manifest must be retained in the job. Their hashes bind the
declared evidence; hashes do not authenticate its scientific interpretation.
Every reference object is compared, including objects outside named components.
Uncovered objects and outlined mathematics remain explicit review limitations.
Final PPT glyphs, compositing and visibility still require application review.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path


_SHA = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
_CATEGORIES = {'literal', 'math', 'connection', 'graphic'}
_REPRESENTATIONS = {'live_text', 'glyph_paths', 'mixed', 'raster', 'native_geometry'}


def _load_json(path, maximum):
    if path.stat().st_size > maximum:
        raise ValueError('Source inventory JSON exceeds its byte budget')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate source inventory JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Source inventory contains a nonfinite JSON number')
    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            constant(value)
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _region(value, canvas):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'width', 'height'}:
        return False
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value.values()):
        return False
    return (min(value['x'], value['y']) >= 0 and min(value['width'], value['height']) > 0
            and value['x'] + value['width'] <= canvas['width']
            and value['y'] + value['height'] <= canvas['height'])


def audit_source_inventory(manifest, asset_root):
    """Read an optional hash-bound inventory; never mutate inputs or grant acceptance.

    manifest.source_inventory = {path, sha256}
    The inventory requires schema_version=1, source_sha256, reference_manifest
    {path,sha256}, source_artifacts [{path,sha256}], components, and unresolved.
    Components require id, category, source_region, object_ids, representation,
    reading_status (verified/not_provided/unresolved), and limitations. A verified
    live-text component additionally requires reading, compared as exact Unicode.
    Source regions use the original manifest canvas. No OCR, math reconstruction
    or direction inference is performed. The independently reviewed reference
    is an authoring manifest, compared before semantic materialization.
    """
    report = {'schema_version': 1, 'status': 'NOT_PROVIDED',
              'scope': 'caller_reviewed_source_components_and_reference_objects',
              'source_binding': 'not_provided', 'mismatches': [], 'unresolved': [],
              'components': [], 'hash_files': [],
              'coverage': {'status': 'not_provided', 'reference_objects': 0,
                           'objects_compared': 0, 'components_checked': 0,
                           'named_component_objects': 0, 'uncovered_object_ids': [],
                           'category_counts': {}},
              'automatic_recognition_performed': False,
              'final_application_verified': False, 'user_acceptance': 'pending',
              'limitations': ['Source readings/reference correctness require independent caller review.',
                              'Matching authoring objects does not prove final PPT rendering or semantic editability.']}
    if not isinstance(manifest, dict) or 'source_inventory' not in manifest:
        return report
    root = Path(asset_root).resolve()
    files = {}

    def checked(record):
        if (not isinstance(record, dict) or set(record) != {'path', 'sha256'}
                or not isinstance(record.get('sha256'), str) or not _SHA.fullmatch(record['sha256'])):
            raise ValueError('Source inventory file needs a closed path/SHA256 binding')
        relative = record['path']
        if (not isinstance(relative, str) or not relative or any(c in relative for c in ('\\', ':', '\x00'))
                or Path(relative).is_absolute() or '..' in Path(relative).parts):
            raise ValueError('Source inventory file must be job-relative')
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 64 * 1024**2:
            raise ValueError('Source inventory file escapes the job, is missing or exceeds its budget')
        if relative in files and files[relative] != record['sha256']:
            raise ValueError('Conflicting source inventory file hashes')
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                if size > 64 * 1024**2:
                    raise ValueError('Source inventory file exceeds its byte budget')
                digest.update(chunk)
        if digest.hexdigest() != record['sha256']:
            raise ValueError('Source inventory file hash changed: ' + relative)
        files[relative] = record['sha256']
        return path

    def mismatch(code, **detail):
        report['mismatches'].append({'code': code, **copy.deepcopy(detail)})

    try:
        inventory = _load_json(checked(manifest['source_inventory']), 8 * 1024**2)
        required = {'schema_version', 'source_sha256', 'reference_manifest',
                    'source_artifacts', 'components', 'unresolved'}
        if not isinstance(inventory, dict) or set(inventory) != required or type(inventory['schema_version']) is not int or inventory['schema_version'] != 1:
            raise ValueError('Invalid closed source inventory schema')
        source = manifest.get('source', {})
        if (not isinstance(source, dict) or not isinstance(inventory['source_sha256'], str)
                or not _SHA.fullmatch(inventory['source_sha256']) or source.get('sha256') != inventory['source_sha256']):
            raise ValueError('Source inventory original-source hash mismatch')
        checked({'path': source['path'], 'sha256': source['sha256']})
        reference = _load_json(checked(inventory['reference_manifest']), 8 * 1024**2)
        if not isinstance(reference, dict) or reference.get('source', {}).get('sha256') != inventory['source_sha256']:
            raise ValueError('Reference manifest is bound to a different source')
        artifacts = inventory['source_artifacts']
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 128:
            raise ValueError('Source inventory needs bounded independent source artifacts')
        for artifact in artifacts:
            checked(artifact)
        if sum((root / p).stat().st_size for p in files) > 128 * 1024**2:
            raise ValueError('Source inventory evidence exceeds its total byte budget')
        report['source_binding'] = 'hashes_matched_caller_review_required'
        canvas = reference.get('canvas', {})
        if (not isinstance(canvas, dict) or any(type(canvas.get(k)) not in (int, float)
                or not math.isfinite(canvas[k]) or canvas[k] <= 0 for k in ('width', 'height'))):
            raise ValueError('Reference manifest needs a finite canvas')
        if manifest.get('canvas') != canvas:
            mismatch('source_canvas_mismatch')
        if manifest.get('groups', []) != reference.get('groups', []):
            mismatch('source_group_mismatch')
        def objects(scene):
            values = scene.get('objects')
            if (not isinstance(values, list) or not 1 <= len(values) <= 10000
                    or any(not isinstance(o, dict) or not _id(o.get('id')) for o in values)
                    or len({o['id'] for o in values}) != len(values)):
                raise ValueError('Source inventory scenes need bounded unique object IDs')
            return values, {o['id']: o for o in values}
        expected, by_id = objects(reference)
        actual, actual_ids = objects(manifest)
        report['coverage']['reference_objects'] = len(expected)
        if [o['id'] for o in actual] != [o['id'] for o in expected]:
            mismatch('source_object_order_or_inventory_mismatch')
        for oid, obj in by_id.items():
            report['coverage']['objects_compared'] += 1
            if oid not in actual_ids:
                mismatch('source_object_missing', object_id=oid)
            elif json.dumps(actual_ids[oid], sort_keys=True, allow_nan=False) != json.dumps(obj, sort_keys=True, allow_nan=False):
                mismatch('source_object_content_or_geometry_mismatch', object_id=oid)
        for oid in actual_ids.keys() - by_id.keys():
            mismatch('source_object_unexpected', object_id=oid)
        components, unknowns = inventory['components'], inventory['unresolved']
        if (not isinstance(components, list) or not 1 <= len(components) <= 10000
                or not isinstance(unknowns, list) or len(unknowns) > 10000):
            raise ValueError('Source inventory components/unknowns exceed their budgets')
        names, covered, counts, target_count = set(), set(), {}, 0
        for component in components:
            keys = {'id', 'category', 'source_region', 'object_ids', 'representation',
                    'reading_status', 'limitations'}
            if (not isinstance(component, dict) or not keys <= set(component) <= keys | {'reading'}
                    or not _id(component.get('id')) or component['id'] in names
                    or component['category'] not in _CATEGORIES or component['representation'] not in _REPRESENTATIONS
                    or component['reading_status'] not in {'verified', 'not_provided', 'unresolved'}
                    or not _region(component['source_region'], canvas)):
                raise ValueError('Invalid source component schema, identity or scope')
            names.add(component['id'])
            targets = component['object_ids']
            if (not isinstance(targets, list) or not 1 <= len(targets) <= 10000
                    or any(not _id(v) or v not in by_id for v in targets) or len(set(targets)) != len(targets)):
                raise ValueError('Source component targets are missing or duplicated')
            target_count += len(targets)
            if target_count > 200000:
                raise ValueError('Source component target references exceed their budget')
            limitations = component['limitations']
            if (not isinstance(limitations, list) or len(limitations) > 32
                    or any(not isinstance(v, str) or not 1 <= len(v) <= 4096 for v in limitations)
                    or ('reading' in component and (not isinstance(component['reading'], str)
                        or not 1 <= len(component['reading']) <= 65536))):
                raise ValueError('Invalid source reading/representation limitations')
            kinds = {by_id[v].get('kind') for v in targets}
            if (component['representation'] == 'live_text' and kinds != {'text'}
                    or component['representation'] == 'glyph_paths' and kinds != {'path'}
                    or component['representation'] == 'raster' and kinds != {'image'}):
                raise ValueError('Source component representation disagrees with reference objects')
            if component['representation'] in {'glyph_paths', 'mixed', 'raster'} and not limitations:
                raise ValueError('Nonsemantic source representations need explicit limitations')
            if component['reading_status'] == 'verified' and component['representation'] == 'live_text':
                if 'reading' not in component:
                    raise ValueError('Verified live text needs its source reading')
                text = ''.join(actual_ids.get(v, {}).get('text', '') for v in targets)
                if text != component['reading']:
                    mismatch('source_component_literal_mismatch', component_id=component['id'])
            if component['reading_status'] == 'unresolved':
                report['unresolved'].append({'id': component['id'], 'reason': 'source_reading_unresolved'})
            covered.update(targets)
            counts[component['category']] = counts.get(component['category'], 0) + 1
            report['components'].append(copy.deepcopy(component))
        for unknown in unknowns:
            if (not isinstance(unknown, dict) or set(unknown) != {'id', 'category', 'source_region', 'reason'}
                    or not _id(unknown.get('id')) or unknown['id'] in names
                    or unknown['category'] not in _CATEGORIES or not _region(unknown['source_region'], canvas)
                    or not isinstance(unknown['reason'], str) or not 1 <= len(unknown['reason']) <= 4096):
                raise ValueError('Invalid unresolved source component')
            names.add(unknown['id'])
            report['unresolved'].append(copy.deepcopy(unknown))
        uncovered = [o['id'] for o in expected if o['id'] not in covered]
        report['coverage'].update(status='partial' if uncovered or report['unresolved'] else 'complete',
            components_checked=len(components), named_component_objects=len(covered),
            uncovered_object_ids=uncovered, category_counts=counts)
        needs_review = uncovered or any(c['reading_status'] != 'verified' or c['limitations'] for c in components)
        report['status'] = 'FAIL' if report['mismatches'] or report['unresolved'] else 'REVIEW' if needs_review else 'PASS'
    except (ValueError, OSError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        mismatch('invalid_source_inventory', detail=str(error)[:400])
        report['status'] = 'FAIL'
    report['hash_files'] = [{'path': p, 'sha256': digest} for p, digest in sorted(files.items())]
    return report
