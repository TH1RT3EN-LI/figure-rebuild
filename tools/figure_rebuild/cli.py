#!/usr/bin/env python3
"""Image-to-editable-PPT workflow; recognition is performed by the calling host."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from validate import confined, digest, load_and_validate, validate_placement
from review import allocation_lock, allocate_run, check_revision_history, content_digest, verify_review

def save(path, data):
    """Replace JSON only after the complete, flushed document is available."""
    path = Path(path)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def config_path():
    configured = os.environ.get('FIGURE_REBUILD_CONFIG')
    return Path(configured).expanduser().resolve() if configured else REPO / '.local/figure-rebuild/runtime.json'


def font_profile(value, base):
    """Normalize a portable profile; declared hashes bind configured font bytes."""
    if not isinstance(value, dict) or not isinstance(value.get('family'), str) or not value['family'].strip():
        raise ValueError('Font profile needs fonts.family and regular/bold records')
    if any(ord(character) < 32 or character in ('"', '\\') for character in value['family']):
        raise ValueError('Font family contains unsupported quote/control characters')
    fonts = {'family': value['family'].strip()}
    for role in ('regular', 'bold'):
        face = value.get(role)
        if not isinstance(face, dict) or not isinstance(face.get('path'), str) or not face['path']:
            raise ValueError('Font profile needs ' + role + '.path')
        index = face.get('face_index')
        if type(index) is not int or index < 0:
            raise ValueError('Font profile needs a nonnegative integer ' + role + '.face_index')
        path = Path(face['path']).expanduser()
        path = (Path(base) / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file(): raise ValueError('Font file does not exist: ' + str(path))
        expected = face.get('sha256')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected)):
            raise ValueError('Font SHA256 must be 64 hexadecimal characters: ' + role)
        actual = digest(path)
        if expected is not None and actual != expected.lower():
            raise ValueError('Font hash mismatch: ' + role)
        fonts[role] = {'path': str(path), 'face_index': index, 'sha256': actual}
    return fonts


def read_font_profile(path):
    path = Path(path).expanduser().resolve()
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc: raise ValueError('Cannot read font profile: ' + str(exc)) from exc
    if not isinstance(data, dict) or 'fonts' not in data:
        raise ValueError('Font profile JSON must contain a fonts record')
    return font_profile(data['fonts'], path.parent)


def validate_runtime(data, base):
    if not isinstance(data, dict): raise ValueError('Runtime configuration must be a JSON record')
    data = dict(data)
    for key in ('node', 'python', 'node_modules', 'presentation_skill'):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f'Missing runtime {key}; run configure')
        path = Path(value).expanduser()
        absolute = path if path.is_absolute() else Path(base) / path
        # Python uses the invoked executable's location to discover pyvenv.cfg.
        # Dereferencing .venv/bin/python silently discards that environment.
        path = Path(os.path.abspath(absolute)) if key in ('node', 'python') else absolute.resolve()
        if key in ('node', 'python'):
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError('Runtime executable is missing or not executable: ' + key)
        elif not path.is_dir(): raise ValueError('Runtime directory is missing: ' + key)
        data[key] = str(path)
    data['fonts'] = font_profile(data.get('fonts'), base)
    return data


def preflight(data):
    """The same backend dependency check is used by doctor and every build."""
    entry = HERE / 'preflight.mjs'
    if not entry.is_file(): raise ValueError('Runtime preflight adapter is missing: ' + str(entry))
    with tempfile.TemporaryDirectory(prefix='figure-rebuild-doctor-') as temp:
        profile = Path(temp) / 'runtime.json'; save(profile, data)
        try:
            result = subprocess.run([data['node'], str(entry), str(profile)], capture_output=True,
                                    text=True, timeout=45)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Runtime dependency check timed out') from exc
    try:
        report = json.loads(result.stdout)
    except (TypeError, ValueError):
        detail = (result.stderr or result.stdout or 'no diagnostics').strip()
        raise ValueError('Runtime dependency check failed: ' + detail)
    if result.returncode != 0:
        if isinstance(report, dict):
            raise ValueError('Runtime dependency check failed: ' + json.dumps(report, ensure_ascii=False))
        raise ValueError('Runtime dependency check failed')
    return report

def runtime(check_dependencies=True):
    config = config_path()
    data = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
    if not isinstance(data, dict): raise ValueError('Runtime configuration must be a JSON record')
    for key, env in [('node', 'RUNTIME_NODE'), ('python', 'RUNTIME_PYTHON'), ('node_modules', 'RUNTIME_NODE_MODULES'), ('presentation_skill', 'PRESENTATION_SKILL_DIR')]:
        if os.environ.get(env): data[key] = os.environ[env]
    override = os.environ.get('FIGURE_REBUILD_FONT_PROFILE')
    if override: data['fonts'] = read_font_profile(override)
    data = validate_runtime(data, config.parent)
    if check_dependencies: preflight(data)
    return data

def configure(a):
    data = {key: str(Path(os.path.abspath(Path(getattr(a, key)).expanduser())))
            if key in ('node', 'python') else str(Path(getattr(a, key)).expanduser().resolve())
            for key in ('node', 'python', 'node_modules', 'presentation_skill')}
    profile = Path(a.font_profile).expanduser().resolve()
    data.update(font_profile=str(profile), fonts=read_font_profile(profile))
    data = validate_runtime(data, config_path().parent)
    save(config_path(), data)
    print(json.dumps({'configured': True, 'profile': str(config_path()), 'font_family': data['fonts']['family']}))


def doctor(a):
    try:
        data = runtime(check_dependencies=False)
        report = preflight(data)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'FAIL', 'errors': [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


def freeze_assets(job, run, manifest):
    """Archive every declared source byte for this run without altering originals."""
    asset_root = Path(run) / 'assets'
    asset_root.mkdir()
    source = manifest['source']
    assets = {source['path']: source['sha256']}
    for item in manifest['objects']:
        if item['kind'] == 'image':
            previous = assets.get(item['path'])
            if previous is not None and previous != item['sha256']:
                raise ValueError('Conflicting checksums for asset: ' + item['path'])
            assets[item['path']] = item['sha256']
    records = []
    for relative, expected in sorted(assets.items()):
        original = confined(job, relative)
        target = (asset_root / relative).resolve()
        if not target.is_relative_to(asset_root.resolve()):
            raise ValueError('Asset snapshot path escapes run: ' + relative)
        if digest(original) != expected:
            raise ValueError('Asset changed before snapshot: ' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if digest(target) != expected:
            raise ValueError('Asset changed during snapshot: ' + relative)
        records.append({'path': relative, 'sha256': expected, 'snapshot': str(target)})
    save(Path(run) / 'asset-snapshot.json', {'asset_root': str(asset_root), 'assets': records})
    return asset_root

def prepare(a):
    src, job = Path(a.input).resolve(), Path(a.job).resolve()
    if job.exists(): raise ValueError('A new job directory is required; old revisions are preserved')
    if src.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}: raise ValueError('Supported inputs: PNG, JPEG, WebP, GIF, SVG')
    if not src.is_file(): raise ValueError('Input does not exist')
    original = job / 'sources' / ('original' + src.suffix.lower())
    imported = None
    if src.suffix.lower() == '.svg':
        from svg_import import import_svg
        imported = import_svg(src, tolerance=a.tolerance)
        width, height = imported['canvas']['width'], imported['canvas']['height']
    else:
        from PIL import Image
        with Image.open(src) as image:
            width, height = image.size
            image.verify()
    original.parent.mkdir(parents=True)
    shutil.copy2(src, original)
    manifest = {
        'schema_version': 1, 'id': a.id or job.name, 'revision': 1,
        'source': {'path': str(original.relative_to(job)), 'sha256': digest(original), 'width': width, 'height': height, 'kind': a.kind, 'uri': a.source_uri or ''},
        'canvas': {'width': width, 'height': height, 'background': '#FFFFFF'},
        'recognition': {'provider': 'svg_import' if imported else 'calling_host', 'status': 'needs_review', 'notes': '', 'unresolved': []},
        'objects': imported['objects'] if imported else [],
    }
    if imported: manifest['recognition']['curve_tolerance_px'] = a.tolerance
    save(job / 'manifest.json', manifest)
    print(json.dumps({'job': str(job), 'manifest': str(job / 'manifest.json'), 'source_sha256': manifest['source']['sha256'], 'next': 'Inspect the original, fill/review objects and text; then review and build. No external recognition API is called.'}, ensure_ascii=False))

def review(a):
    p = Path(a.manifest).resolve()
    data, report = load_and_validate(p, require_review=False)
    data['recognition'].update(status='reviewed', review_note=a.note,
                               reviewed_at=datetime.now(timezone.utc).isoformat(),
                               acceptance='caller_reviewed; not_user_visual_acceptance',
                               reviewed_revision=data['revision'])
    data['recognition']['reviewed_digest'] = content_digest(data)
    save(p, data)
    print(json.dumps({'status': 'reviewed', 'counts': report['object_counts']}))

def build(a):
    m = Path(a.manifest).resolve()
    data, report = load_and_validate(m)
    verify_review(data)
    job = m.parent
    out = Path(a.output).resolve() if a.output else job / 'exports' / f"{data['id']}-r{data['revision']}.pptx"
    if out.exists(): raise ValueError('Output already exists; use a new revision or output name')
    rt = runtime()
    base_config = None
    if a.placement and not a.base: raise ValueError('placement requires an existing base deck')
    if (a.slide_id or a.base_sha256 or a.replace_id) and not a.base: raise ValueError('Base-specific arguments require --base')
    if a.base:
        from package import inspect_pptx as inspect_deck
        base = Path(a.base).resolve()
        original_sha = digest(base)
        if a.base_sha256 and a.base_sha256 != original_sha: raise ValueError('Base checksum no longer matches')
        if not a.slide_id: raise ValueError('Existing deck needs a stable native slide-id from inspect-base')
        if not a.placement: raise ValueError('Existing deck needs explicit placement x y width height')
        info = inspect_deck(base)
        size = info.get('slide_size', info.get('slide_size_emu'))
        if size is None: raise ValueError('inspect_deck must report slide_size_emu')
        if isinstance(size, dict): size = [size.get('cx', size.get('width')), size.get('cy', size.get('height'))]
        if not any(str(s.get('slide_id', s.get('id'))) == str(a.slide_id) for s in info['slides']): raise ValueError('Stable slide-id is absent from the base deck')
        page_canvas = {'width': size[0] / 9525, 'height': size[1] / 9525}
        validate_placement(a.placement, page_canvas)
        base_config = {'sha256': original_sha, 'slide_id': str(a.slide_id), 'replace_ids': a.replace_id or [], 'canvas': page_canvas, 'placement': a.placement}
    # Serialize only history validation and complete snapshot reservation.
    # Rendering runs independently after releasing the OS lock.
    with allocation_lock(job / 'build'):
        check_revision_history(job / 'build', data)
        run = allocate_run(job / 'build')
        assets = freeze_assets(job, run, data)
        snapshot = run / 'manifest-snapshot.json'
        save(snapshot, data)
        save(run / 'manifest-validation.json', report)
        config = {'manifest': str(snapshot), 'manifest_source': str(m), 'job': str(job), 'asset_root': str(assets), 'run': str(run), 'output': str(out), 'repo': str(REPO), 'runtime': rt}
        if base_config:
            snap = run / 'base-snapshot.pptx'
            shutil.copy2(base, snap)
            if digest(snap) != original_sha: raise ValueError('Base changed during snapshot; rebuild from the current base')
            config['base'] = dict(base_config, path=str(snap))
        save(run / 'build-config.json', config)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not a.marker_already_started:
        subprocess.run([rt['node'], str(Path(rt['presentation_skill']) / 'container_tools/mark_artifact_operation_started.mjs'), '--operation-kind', 'edit' if a.base else 'create', '--expected-output-count', '1', '--output-format', 'pptx'], cwd=rt['presentation_skill'], check=True)
    subprocess.run([rt['node'], str(HERE / 'build.mjs'), str(run / 'build-config.json')], check=True)
    print(json.dumps({'pptx': str(out), 'run': str(run), 'preview': str(run / 'preview-1x.png'), 'comparison': str(run / 'comparison.png'), 'native_and_raster_counts': report['object_counts']}, ensure_ascii=False))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version='figure-rebuild 0.2.0')
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('configure')
    for k in ['node', 'python', 'node_modules', 'presentation_skill']: c.add_argument('--' + k.replace('_', '-'), required=True)
    c.add_argument('--font-profile', required=True)
    c.set_defaults(func=configure)
    c = sub.add_parser('doctor'); c.set_defaults(func=doctor)
    c = sub.add_parser('prepare')
    c.add_argument('--input', required=True); c.add_argument('--job', required=True); c.add_argument('--id')
    c.add_argument('--kind', required=True, choices=['research_original', 'user_original', 'retrieved_original', 'generated_diagram'])
    c.add_argument('--source-uri'); c.add_argument('--tolerance', type=float, default=.35); c.set_defaults(func=prepare)
    c = sub.add_parser('review')
    c.add_argument('--manifest', required=True); c.add_argument('--note', required=True); c.set_defaults(func=review)
    c = sub.add_parser('validate')
    c.add_argument('--manifest', required=True)
    def validate_reviewed(a):
        data, report = load_and_validate(a.manifest)
        verify_review(data)
        print(json.dumps(report, ensure_ascii=False))
    c.set_defaults(func=validate_reviewed)
    c = sub.add_parser('build')
    c.add_argument('--manifest', required=True); c.add_argument('--output'); c.add_argument('--base'); c.add_argument('--base-sha256'); c.add_argument('--slide-id'); c.add_argument('--placement', type=float, nargs=4); c.add_argument('--replace-id', action='append'); c.add_argument('--marker-already-started', action='store_true'); c.set_defaults(func=build)
    c = sub.add_parser('inspect-base')
    c.add_argument('base')
    def inspect(a):
        from package import inspect_pptx as inspect_deck
        print(json.dumps(inspect_deck(a.base), ensure_ascii=False, indent=2))
    c.set_defaults(func=inspect)
    a = p.parse_args()
    try:
        result = a.func(a)
        return result if isinstance(result, int) else 0
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(str(e), file=sys.stderr); return 1
    return 0

if __name__ == '__main__': sys.exit(main())
