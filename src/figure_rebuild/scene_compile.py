"""Resolve audited semantic objects without changing the reviewed source scene."""
import argparse
import copy
import json
import math
from pathlib import Path


def validate_delivery_sampling(audit, scale=1):
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Formula delivery scale must be positive and finite')
    records = []
    for formula in audit['formulas']:
        placement = formula['placement']
        actual = placement['sampling_scale'] / scale
        minimum = placement['min_sampling_scale']
        if actual + 1e-9 < minimum:
            raise ValueError(f"Formula PNG fallback sampling below {minimum}x after slide placement: {formula['id']} ({actual:.6g}x)")
        records.append({'id': formula['id'], 'sampling_scale': actual, 'minimum': minimum, 'slide_scale': scale})
    return records


def compile_scene(manifest, job_dir, asset_root=None):
    from .formula_asset import resolve_formula_asset
    from .connections import resolve_scene
    scene = copy.deepcopy(manifest)
    if any(isinstance(obj, dict) and (obj.get('source_kind') in ('formula', 'connector') or
           any(key in obj for key in ('formula_asset', 'connection_record', 'source_attachment')))
           for obj in scene.get('objects', [])):
        raise ValueError('A resolved scene is a diagnostic, not an authoring manifest; use the reviewed semantic source')
    formulas, assets = [], {}
    for obj in scene.get('objects', []):
        if obj.get('kind') != 'formula':
            continue
        resolved = resolve_formula_asset(obj, job_dir, asset_root=asset_root)
        for file in resolved['hash_files']:
            existing = assets.get(file['path'])
            if existing and existing != file['sha256']:
                raise ValueError('Conflicting formula asset hashes: ' + file['path'])
            assets[file['path']] = file['sha256']
        png = resolved['png_path']
        checksum = assets[png]
        record = {'id': obj['id'], **copy.deepcopy(resolved)}
        formulas.append(record)
        if 'baseline_anchor' in obj and obj.get('attach_to'):
            box = resolved['placement']['box']
            anchor = obj['baseline_anchor']
            obj['source_requested_attachment'] = copy.deepcopy(obj['attach_to'])
            offset = obj['attach_to'].setdefault('offset', {'x': 0, 'y': 0})
            offset['x'] += box['x'] + box['width']/2 - anchor['x']
            offset['y'] += box['y'] + box['height']/2 - anchor['y']
            record['placement']['attachment_position_kind'] = 'baseline_anchor'
        obj.update(kind='image', source_kind='formula', path=png, sha256=checksum,
                   editable=False, box=resolved['placement']['box'],
                   formula_asset=record)
        obj.pop('baseline_anchor', None)
    scene, connections = resolve_scene(scene)
    # The source retains relationships. A materialized scene is deliberately
    # not another editable semantic source and must not be resolved twice.
    for obj in scene.get('objects', []):
        if obj.get('attach_to'):
            obj['source_attachment'] = obj.pop('attach_to')
        if obj.get('source_kind') == 'formula':
            record = next(r for r in formulas if r['id'] == obj['id'])
            initial = record['placement']['box']
            final = obj['box']
            record['requested_placement'] = copy.deepcopy(record['placement'])
            record['placement']['box'] = copy.deepcopy(final)
            anchor = record['placement'].get('baseline_anchor')
            if anchor:
                anchor['x'] += final['x'] - initial['x']
                anchor['y'] += final['y'] - initial['y']
            obj['formula_asset'] = copy.deepcopy(record)
    from .content_audit import audit_source_content
    return scene, {'formulas': formulas, 'connections': connections,
                   'source_content': audit_source_content(scene),
                   'hash_files': [{'path': p, 'sha256': h} for p, h in assets.items()]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--asset-root')
    parser.add_argument('--output', required=True)
    parser.add_argument('--audit', required=True)
    args = parser.parse_args()
    scene, audit = compile_scene(json.loads(Path(args.manifest).read_text()), args.job, args.asset_root)
    Path(args.audit).with_name('source-content-audit.json').write_text(
        json.dumps(audit['source_content'], ensure_ascii=False, indent=2) + '\n')
    if audit['source_content']['status'] == 'FAIL':
        raise ValueError('Source content has mismatches or unresolved evidence; inspect source-content-audit.json')
    Path(args.output).write_text(json.dumps(scene, ensure_ascii=False, indent=2) + '\n')
    Path(args.audit).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
