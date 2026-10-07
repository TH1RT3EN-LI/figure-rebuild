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
import io
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .paths import PACKAGE_ROOT, config_path, python_environment
from .validate import confined, digest, load_and_validate, validate_placement
from .review import allocation_lock, allocate_run, check_revision_history, content_digest, verify_review

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


def font_profile(value, base, *, require_default_faces=True):
    """Normalize a portable profile; declared hashes bind configured font bytes."""
    if not isinstance(value, dict) or not isinstance(value.get('family'), str) or not value['family'].strip():
        raise ValueError('Font profile needs fonts.family and regular/bold records')
    if any(ord(character) < 32 or character in ('"', '\\') for character in value['family']):
        raise ValueError('Font family contains unsupported quote/control characters')
    if not any(role in value for role in ('regular', 'bold', 'italic', 'boldItalic')):
        raise ValueError('Font profile needs at least one real face: ' + value['family'])
    fonts = {'family': value['family'].strip()}
    for role in ('regular', 'bold', 'italic', 'boldItalic'):
        if role not in value and (not require_default_faces or role in ('italic', 'boldItalic')):
            continue
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
    additional = value.get('additional', [])
    if not isinstance(additional, list):
        raise ValueError('fonts.additional must be a list of explicit font profiles')
    if additional:
        fonts['additional'] = []
        names = {fonts['family'].casefold()}
        for extra in additional:
            if not isinstance(extra, dict) or extra.get('additional'):
                raise ValueError('Additional font profiles cannot be nested')
            normalized = font_profile(extra, base, require_default_faces=False)
            if normalized['family'].casefold() in names:
                raise ValueError('Duplicate configured font family: ' + normalized['family'])
            names.add(normalized['family'].casefold())
            fonts['additional'].append(normalized)
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
    if 'native_preview' in data:
        from .native_preview import validate_profile
        data['native_preview'] = validate_profile(data['native_preview'], base, check_executables=False)
    return data


def preflight(data):
    """The same backend dependency check is used by doctor and every build."""
    entry = PACKAGE_ROOT / 'powerpoint' / 'preflight.mjs'
    if not entry.is_file(): raise ValueError('Runtime preflight adapter is missing: ' + str(entry))
    with tempfile.TemporaryDirectory(prefix='figure-rebuild-doctor-') as temp:
        profile = Path(temp) / 'runtime.json'; save(profile, data)
        try:
            result = subprocess.run([data['node'], str(entry), str(profile)], capture_output=True,
                                    text=True, timeout=45, env=python_environment())
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
    if getattr(a, 'native_preview_profile', None):
        from .native_preview import validate_profile
        native_profile = Path(a.native_preview_profile).expanduser().resolve()
        data['native_preview'] = validate_profile(json.loads(native_profile.read_text(encoding='utf-8')), native_profile.parent)
    data = validate_runtime(data, config_path().parent)
    save(config_path(), data)
    print(json.dumps({'configured': True, 'profile': str(config_path()), 'font_family': data['fonts']['family']}))


def doctor(a):
    try:
        data = runtime(check_dependencies=False)
        report = preflight(data)
        report['vision'] = vision_capabilities(data['python'])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'FAIL', 'errors': [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


def vision_capabilities(python):
    """Probe the configured Python, which may differ from this CLI's interpreter."""
    probe = """import json
try:
    import cv2, numpy
    print(json.dumps({'status':'available','opencv':cv2.__version__,'numpy':numpy.__version__}))
except Exception as exc:
    print(json.dumps({'status':'unavailable','reason':str(exc),'install_hint':'Install requirements/vision.txt into the configured Python environment'}))
"""
    try:
        result = subprocess.run([python, '-B', '-c', probe], capture_output=True, text=True, timeout=15)
        if result.returncode: raise ValueError('Vision dependency probe failed')
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'status': 'unavailable', 'reason': str(exc)}


def publish_diagnostic(report, output, preview_source=None, preview_output=None):
    """Publish complete diagnostic files without replacing any existing input/output."""
    from .publish import stage
    output = Path(output).resolve()
    destinations = [output]
    contents = [(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')]
    if preview_output is not None:
        destinations.append(Path(preview_output).resolve())
        contents.append(Path(preview_source).read_bytes())
    if len(set(destinations)) != len(destinations):
        raise ValueError('Diagnostic report and preview must use different paths')
    staged, owned = [], []
    try:
        for destination, content in zip(destinations, contents):
            staged.append(stage(destination, content))
        for temporary, destination in zip(staged, destinations):
            os.link(temporary, destination)
            owned.append((temporary, destination))
    except BaseException:
        for temporary, destination in reversed(owned):
            if destination.exists() and os.path.samestat(temporary.stat(), destination.stat()):
                destination.unlink()
        raise
    finally:
        for temporary in staged: temporary.unlink(missing_ok=True)


def refine_crop_command(a):
    from .crop_refine import refine_crop, save_crop_preview
    source, output = Path(a.input).resolve(), Path(a.output).resolve()
    preview = Path(a.preview).resolve() if a.preview else None
    if output == source or preview == source:
        raise ValueError('Diagnostic output must not replace the source image')
    for destination in (output, preview):
        if destination is not None and destination.exists():
            raise ValueError('Diagnostic output already exists: '+str(destination))
    report = refine_crop(source, a.region, background=a.background,
                         tolerance=a.tolerance, padding=a.padding, min_area=a.min_area)
    with tempfile.TemporaryDirectory(prefix='figure-rebuild-crop-') as directory:
        temporary = Path(directory)/'preview.png'
        if preview is not None: save_crop_preview(source, report, temporary)
        publish_diagnostic(report, output, temporary if preview else None, preview)
    print(json.dumps({'report': str(output), 'preview': str(preview) if preview else None,
                      'status': report['status'], 'manifest_changed': False}, ensure_ascii=False))


def diagnose_command(a):
    from PIL import Image
    from .registration import diagnose_registration
    reference, rebuilt, output = map(lambda p: Path(p).resolve(), (a.reference, a.rebuilt, a.output))
    if output in (reference, rebuilt): raise ValueError('Report must not replace an input image')
    if output.exists(): raise ValueError('Diagnostic output already exists: '+str(output))
    source_bytes, target_bytes = reference.read_bytes(), rebuilt.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as source, Image.open(io.BytesIO(target_bytes)) as target:
        report = diagnose_registration(source, target, max_shift_px=a.max_shift)
    report['inputs'] = {'reference': {'path': str(reference), 'sha256': hashlib.sha256(source_bytes).hexdigest()},
                        'rebuilt': {'path': str(rebuilt), 'sha256': hashlib.sha256(target_bytes).hexdigest()}}
    publish_diagnostic(report, output)
    print(json.dumps({'report': str(output), 'status': report['status'], 'images_changed': False}))
    return 1 if report['status'] in ('unavailable', 'failure') else 0


def freeze_assets(job, run, manifest, *, extra_sources=()):
    """Archive every declared source byte for this run without altering originals."""
    asset_root = Path(run) / 'assets'
    asset_root.mkdir()
    source = manifest['source']
    assets = {source['path']: source['sha256']}
    if 'authoring' in manifest:
        from .authoring import verify_creation_inputs
        verify_creation_inputs(manifest, job)
        for role in ('spec', 'audit'):
            entry = manifest['authoring'][role]
            if entry['path'] in assets and assets[entry['path']] != entry['sha256']:
                raise ValueError('Conflicting creation asset checksums')
            assets[entry['path']] = entry['sha256']
    if 'source_canvas_clip' in manifest:
        declaration = manifest['source_canvas_clip']
        if not isinstance(declaration, dict):
            raise ValueError('Source canvas clipping needs an explicit declaration')
        for role in ('source_pdf', 'source_svg'):
            record = declaration.get(role)
            if not isinstance(record, dict) or not isinstance(record.get('path'), str) or not isinstance(record.get('sha256'), str):
                raise ValueError('Source canvas clipping needs a bound ' + role)
            previous = assets.get(record['path'])
            if previous is not None and previous != record['sha256']:
                raise ValueError('Conflicting checksums for canvas evidence: ' + record['path'])
            assets[record['path']] = record['sha256']
    from .scene_compile import compile_scene
    _, semantic = compile_scene(manifest, job)
    if semantic.get('source_inventory', {}).get('status') == 'FAIL':
        raise ValueError('Source inventory has mismatches or unresolved source evidence')
    for record in semantic['hash_files']:
        assets[record['path']] = record['sha256']
    for item in manifest['objects']:
        if item['kind'] == 'image':
            previous = assets.get(item['path'])
            if previous is not None and previous != item['sha256']:
                raise ValueError('Conflicting checksums for asset: ' + item['path'])
            assets[item['path']] = item['sha256']
    for item in extra_sources:
        previous = assets.get(item['path'])
        if previous is not None and previous != item['sha256']:
            raise ValueError('Conflicting checksums for preview source asset: '+item['path'])
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
        from .svg_import import import_svg
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

def create(a):
    from .authoring import create_job
    fonts = read_font_profile(a.font_profile) if a.font_profile else runtime(check_dependencies=False)['fonts']
    print(json.dumps(create_job(a.spec, a.job, fonts), ensure_ascii=False, indent=2))

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

def review_output(a):
    """Record observations only after their original artifact bindings match."""
    from .output_review import prepare_output_review, record_output_review, verify_output_review
    if a.output and not a.record:
        raise ValueError('--output is only used with --record')
    if a.require_no_observed_issues and not a.verify:
        raise ValueError('--require-no-observed-issues is only used with --verify')
    if a.template:
        result = prepare_output_review(a.run)
        destination = Path(a.template).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'template': str(destination), 'review_performed': False}))
    else:
        record = json.loads(Path(a.record or a.verify).read_text(encoding='utf-8'))
        result = (record_output_review(a.run, record, output=a.output) if a.record else
                  verify_output_review(a.run, record, require_no_observed_issues=a.require_no_observed_issues))
        print(json.dumps(result, ensure_ascii=False, indent=2))

def _source_fidelity_json(path, record_type):
    """Read one bounded, unambiguous JSON record for the typed replay API."""
    from dataclasses import fields, MISSING
    import math
    if not isinstance(path, str) or not path.strip():
        raise ValueError('Source-fidelity JSON path must be nonempty')
    path = Path(path).expanduser().resolve()
    with path.open('rb') as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError('Source-fidelity JSON exceeds 1 MiB: ' + str(path))
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite JSON number is unsupported: ' + value)
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            constant(value)
        return parsed
    def integer(value):
        parsed = int(value)
        try:
            finite = math.isfinite(parsed)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError('JSON integer exceeds finite numeric range')
        return parsed
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=number, parse_int=integer)
    except RecursionError as exc:
        raise ValueError('Source-fidelity JSON nesting is too deep') from exc
    if not isinstance(data, dict):
        raise ValueError('Source-fidelity JSON must be an object: ' + str(path))
    schema = {field.name: field for field in fields(record_type)}
    unknown = sorted(set(data) - set(schema))
    if unknown:
        raise ValueError('Unknown ' + record_type.__name__ + ' fields: ' + ', '.join(unknown))
    missing = [name for name, field in schema.items()
               if name not in data and field.default is MISSING and field.default_factory is MISSING]
    if missing:
        raise ValueError('Missing ' + record_type.__name__ + ' fields: ' + ', '.join(missing))
    return data, path


def verify_source_fidelity(a):
    """Expose the bounded source replay without changing semantic/visual review."""
    from .source_fidelity import (PdfSourceDescriptor, SourceReplayPolicy,
                                  ReplayLimits, audit_source_fidelity)
    data, descriptor = _source_fidelity_json(a.source_descriptor, PdfSourceDescriptor)
    for key in ('pdf_path', 'reference_png_path'):
        value = data.get(key)
        if key == 'reference_png_path' and value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(key + ' must be a nonempty filesystem path')
        path = Path(value).expanduser()
        data[key] = str((descriptor.parent / path).resolve() if not path.is_absolute() else path.resolve())
    policy = SourceReplayPolicy(**_source_fidelity_json(a.policy, SourceReplayPolicy)[0]) if a.policy is not None else SourceReplayPolicy()
    limits = ReplayLimits(**_source_fidelity_json(a.limits, ReplayLimits)[0]) if a.limits is not None else ReplayLimits()
    paths = {}
    for name in ('manifest', 'resolved_scene', 'asset_root', 'pptx', 'evidence_dir'):
        value = getattr(a, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('--' + name.replace('_', '-') + ' must be a nonempty filesystem path')
        paths[name] = Path(value).expanduser().resolve()
    try:
        result = audit_source_fidelity(
            PdfSourceDescriptor(**data), manifest_path=paths['manifest'],
            resolved_scene_path=paths['resolved_scene'], asset_root=paths['asset_root'],
            pptx_path=paths['pptx'], evidence_dir=paths['evidence_dir'],
            policy=policy, limits=limits,
        )
    except OverflowError as exc:
        raise ValueError('Source-fidelity numeric input exceeds the supported range') from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.get('status') == 'VERIFIED_IN_DECLARED_SCOPE' else 1


def insert(a):
    """Place an already generated native slide without an authoring runtime."""
    from .package import merge_overlay
    base = Path(a.base).expanduser().resolve()
    checksum = a.base_sha256 or digest(base)
    receipt = merge_overlay(base=base, overlay=a.input, output=a.output,
                            slide_id=a.slide_id, base_sha256=checksum,
                            placement=a.placement, replace_ids=a.replace_id or [],
                            prefix=a.prefix, receipt_path=a.receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))

def build(a):
    m = Path(a.manifest).resolve()
    data, report = load_and_validate(m)
    verify_review(data)
    if 'source_canvas_clip' in data and (a.base or a.placement):
        raise ValueError('Source canvas clipping requires a standalone slide; base decks and placement are unsupported')
    job = m.parent
    out = Path(a.output).resolve() if a.output else job / 'exports' / f"{data['id']}-r{data['revision']}.pptx"
    if out.exists(): raise ValueError('Output already exists; use a new revision or output name')
    from .native_preview import validate_backend, validate_pdf_alpha_policy
    preview_backend = validate_backend(getattr(a, 'preview_backend', 'artifact'))
    pdf_alpha_policy = validate_pdf_alpha_policy(getattr(a, 'pdf_alpha_derivation', None), preview_backend)
    if preview_backend == 'native-svg' and (a.base or 'source_canvas_clip' in data or
            any(o.get('kind') != 'path' for o in data['objects'])):
        raise ValueError('Native SVG preview requires a standalone flat path-only slide without source clipping')
    shared_grid_path = getattr(a, 'artifact_image_shared_grid', None)
    shared_grid = None
    if shared_grid_path:
        from .source_inventory import _load_json
        from .artifact_image_preview import MAX_DEFINITION_BYTES
        shared_grid = _load_json(Path(shared_grid_path).resolve(), MAX_DEFINITION_BYTES)
    source_sampling_path = getattr(a, 'artifact_image_source_sampling', None)
    source_rgb_path = getattr(a, 'artifact_image_source_rgb_groups', None)
    source_sampling, preview_sources = None, []
    if source_sampling_path or source_rgb_path:
        if shared_grid_path or (source_sampling_path and source_rgb_path):
            raise ValueError('Choose one explicit native image sampling policy')
        from .source_inventory import _load_json
        from .artifact_image_preview import MAX_DEFINITION_BYTES
        from .artifact_source_image_preview import source_request_assets
        source_sampling = _load_json(Path(source_sampling_path or source_rgb_path).resolve(), MAX_DEFINITION_BYTES)
        preview_sources = source_request_assets(source_sampling, rgb_groups=bool(source_rgb_path))
    image_preview = (getattr(a, 'artifact_image_preview', False) or shared_grid_path is not None or
                     source_sampling_path is not None or source_rgb_path is not None)
    path_prefix_path = getattr(a, 'artifact_path_prefix_grid', None)
    path_prefix = None
    if path_prefix_path:
        if preview_backend != 'artifact' or a.base or image_preview or 'source_canvas_clip' in data:
            raise ValueError('Native path-prefix grid requires a standalone Artifact build without another image sampling policy or source clipping')
        from .source_inventory import _load_json
        from .artifact_path_prefix_preview import validate_request
        from .artifact_image_preview import MAX_DEFINITION_BYTES
        path_prefix = _load_json(Path(path_prefix_path).resolve(), MAX_DEFINITION_BYTES)
        validate_request(path_prefix)
    if image_preview and (preview_backend != 'artifact' or a.base):
        raise ValueError('Native picture preview requires a standalone Artifact build')
    if preview_backend in ('libreoffice', 'libreoffice-pdf', 'libreoffice-pdf-rgb', 'libreoffice-pdf-photos') and a.base:
        raise ValueError('LibreOffice preview does not yet support base-deck slide mapping')
    if any(o.get('style', {}).get('stroke_hairline') is True for o in data['objects']) and preview_backend != 'libreoffice-pdf':
        raise ValueError('Explicit device hairlines require the libreoffice-pdf preview backend')
    rt = runtime()
    if preview_backend == 'native-svg':
        try:
            subprocess.run([rt['python'], '-B', str(PACKAGE_ROOT / '_bootstrap.py'), 'native_svg_preview', '--preflight'],
                           check=True, capture_output=True, text=True, timeout=45, env=python_environment())
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Native SVG dependency check timed out') from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError('Native SVG dependency check failed: ' + (exc.stderr or '').strip()) from exc
    if image_preview or path_prefix_path:
        try:
            subprocess.run([rt['python'], '-B', str(PACKAGE_ROOT / '_bootstrap.py'), 'artifact_image_preview',
                            '--preflight'], check=True, capture_output=True, text=True, timeout=45,
                           env=python_environment())
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Native picture preview dependency check timed out') from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError('Native picture preview dependency check failed: ' + (exc.stderr or '').strip()) from exc
    if preview_backend in ('libreoffice', 'libreoffice-pdf', 'libreoffice-pdf-rgb', 'libreoffice-pdf-photos'):
        # Probe the configured interpreter, not the interpreter running this CLI.
        with tempfile.TemporaryDirectory(prefix='figure-rebuild-native-check-') as temp:
            native_config = Path(temp) / 'runtime.json'; save(native_config, rt)
            try:
                subprocess.run([rt['python'], '-B', str(PACKAGE_ROOT / '_bootstrap.py'), 'native_preview',
                                '--config', str(native_config), '--preflight'], check=True,
                               capture_output=True, text=True, timeout=45, env=python_environment())
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Native preview dependency check timed out') from exc
            except subprocess.CalledProcessError as exc:
                raise ValueError('Native preview dependency check failed: ' + (exc.stderr or '').strip()) from exc
    base_config = None
    if a.placement and not a.base: raise ValueError('placement requires an existing base deck')
    if (a.slide_id or a.base_sha256 or a.replace_id) and not a.base: raise ValueError('Base-specific arguments require --base')
    if a.base:
        from .package import inspect_pptx as inspect_deck
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
        from .placement import fit_placement
        placement_audit = fit_placement(data['canvas'], page_canvas, a.placement)
        base_config = {'sha256': original_sha, 'slide_id': str(a.slide_id), 'replace_ids': a.replace_id or [], 'canvas': page_canvas, 'placement': a.placement, 'placement_audit': placement_audit}
    from .scene_compile import compile_scene, validate_delivery_sampling
    _, semantic = compile_scene(data, job)
    placement_scale = base_config['placement_audit']['scale'] if base_config else 1
    validate_delivery_sampling(semantic, placement_scale)
    # Serialize only history validation and complete snapshot reservation.
    # Rendering runs independently after releasing the OS lock.
    with allocation_lock(job / 'build'):
        check_revision_history(job / 'build', data)
        run = allocate_run(job / 'build')
        assets = freeze_assets(job, run, data, extra_sources=preview_sources) if preview_sources else freeze_assets(job, run, data)
        snapshot = run / 'manifest-snapshot.json'
        save(snapshot, data)
        save(run / 'manifest-validation.json', report)
        config = {'manifest': str(snapshot), 'manifest_source': str(m), 'job': str(job), 'asset_root': str(assets), 'run': str(run), 'output': str(out), 'package_root': str(PACKAGE_ROOT), 'runtime': rt,
                  'preview_backend': preview_backend, 'preview_provenance_version': 1,
                  'diagnostic_provenance_version': 1}
        if preview_backend == 'artifact' and not base_config:
            config['artifact_stroke_preview_version'] = 1
        if image_preview:
            config['artifact_image_preview_version'] = 5 if source_rgb_path else 4 if source_sampling_path else 3 if shared_grid_path else 2
            if shared_grid_path:
                config['artifact_image_shared_grid'] = shared_grid
            if source_sampling_path:
                config['artifact_image_source_sampling'] = source_sampling
            if source_rgb_path:
                config['artifact_image_source_rgb_groups'] = source_sampling
        if path_prefix_path:
            config['artifact_path_prefix_preview_version'] = 1
            config['artifact_path_prefix_grid'] = path_prefix
        if preview_backend == 'libreoffice-pdf':
            config['native_pdf_preview_version'] = 1
        if preview_backend == 'native-svg':
            config['native_svg_preview_version'] = 1
        if preview_backend in ('libreoffice-pdf-rgb', 'libreoffice-pdf-photos'):
            from .pdf_zero_alpha_rgb import POLICY
            config['native_pdf_preview_version'] = 2
            config['pdf_rgb_derivation'] = POLICY
        if preview_backend == 'libreoffice-pdf-photos':
            from .pdf_native_photos import POLICY
            config['native_pdf_preview_version'] = 3
            config['pdf_native_photo_placement'] = POLICY
        if pdf_alpha_policy:
            config['pdf_alpha_derivation'] = pdf_alpha_policy
        if 'source_canvas_clip' in data:
            config['source_canvas_clip_provenance_version'] = 1
        if base_config:
            snap = run / 'base-snapshot.pptx'
            shutil.copy2(base, snap)
            if digest(snap) != original_sha: raise ValueError('Base changed during snapshot; rebuild from the current base')
            config['base'] = dict(base_config, path=str(snap))
        save(run / 'build-config.json', config)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not a.marker_already_started:
        subprocess.run([rt['node'], str(Path(rt['presentation_skill']) / 'container_tools/mark_artifact_operation_started.mjs'), '--operation-kind', 'edit' if a.base else 'create', '--expected-output-count', '1', '--output-format', 'pptx'], cwd=rt['presentation_skill'], check=True)
    subprocess.run([rt['node'], str(PACKAGE_ROOT / 'powerpoint' / 'build.mjs'), str(run / 'build-config.json')], check=True, env=python_environment())
    print(json.dumps({'pptx': str(out), 'run': str(run), 'preview': str(run / 'preview-1x.png'), 'comparison': str(run / 'comparison.png'), 'native_and_raster_counts': report['object_counts']}, ensure_ascii=False))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version='figure-rebuild ' + __version__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('configure')
    for k in ['node', 'python', 'node_modules', 'presentation_skill']: c.add_argument('--' + k.replace('_', '-'), required=True)
    c.add_argument('--font-profile', required=True)
    c.add_argument('--native-preview-profile', help='Optional external LibreOffice/Fontconfig command profile JSON')
    c.set_defaults(func=configure)
    c = sub.add_parser('doctor'); c.set_defaults(func=doctor)
    c = sub.add_parser('refine-crop', help='Propose a source-pixel crop inside a selected region; never edits the manifest')
    c.add_argument('--input', required=True); c.add_argument('--region', type=float, nargs=4, required=True)
    c.add_argument('--output', required=True); c.add_argument('--preview')
    c.add_argument('--background', type=int, nargs=3); c.add_argument('--tolerance', type=float, default=18)
    c.add_argument('--padding', type=int, default=1); c.add_argument('--min-area', type=int, default=1)
    c.set_defaults(func=refine_crop_command)
    c = sub.add_parser('diagnose', help='Measure translation and unaligned edge error without changing images')
    c.add_argument('--reference', required=True); c.add_argument('--rebuilt', required=True)
    c.add_argument('--output', required=True); c.add_argument('--max-shift', type=float, default=32)
    c.set_defaults(func=diagnose_command)
    c = sub.add_parser('create', help='Author an original academic figure from an explicit graph and saved brief')
    c.add_argument('--spec', required=True, help='Creation spec JSON: brief, nodes, stages/lanes and directed edges')
    c.add_argument('--job', required=True, help='New job directory; existing jobs are preserved')
    c.add_argument('--font-profile', help='Portable font profile; defaults to configured runtime fonts')
    c.set_defaults(func=create)
    c = sub.add_parser('prepare')
    c.add_argument('--input', required=True); c.add_argument('--job', required=True); c.add_argument('--id')
    c.add_argument('--kind', required=True, choices=['research_original', 'user_original', 'retrieved_original', 'generated_diagram'])
    c.add_argument('--source-uri'); c.add_argument('--tolerance', type=float, default=.35); c.set_defaults(func=prepare)
    c = sub.add_parser('review')
    c.add_argument('--manifest', required=True); c.add_argument('--note', required=True); c.set_defaults(func=review)
    c = sub.add_parser('review-output', help='Bind supplied visual observations to exact built artifacts')
    c.add_argument('--run', required=True)
    action = c.add_mutually_exclusive_group(required=True)
    action.add_argument('--template', help='Write an unperformed review template to a new JSON file')
    action.add_argument('--record', help='Record a filled template without refreshing its artifact bindings')
    action.add_argument('--verify', help='Verify a recorded review against the current artifact bytes')
    c.add_argument('--output', help='New JSON destination for --record')
    c.add_argument('--require-no-observed-issues', action='store_true')
    c.set_defaults(func=review_output)
    c = sub.add_parser('verify-source-fidelity', help='Replay source geometry and bind the actual PPTX; does not recognize semantics or perform visual acceptance')
    c.add_argument('--source-descriptor', required=True, help='Strict PDF descriptor JSON; embedded relative paths resolve against its directory')
    for name in ('manifest', 'resolved-scene', 'asset-root', 'pptx', 'evidence-dir'):
        c.add_argument('--' + name, required=True, help='Filesystem path relative to the current directory; evidence directory must be new' if name == 'evidence-dir' else 'Filesystem path relative to the current directory')
    c.add_argument('--policy', help='Optional strict SourceReplayPolicy JSON file; omitted fields use API defaults')
    c.add_argument('--limits', help='Optional strict ReplayLimits JSON file; omitted fields use API defaults')
    c.set_defaults(func=verify_source_fidelity)
    c = sub.add_parser('validate')
    c.add_argument('--manifest', required=True)
    def validate_reviewed(a):
        data, report = load_and_validate(a.manifest)
        verify_review(data)
        print(json.dumps(report, ensure_ascii=False))
    c.set_defaults(func=validate_reviewed)
    c = sub.add_parser('build', help='Generate a figure, optionally placing it in an existing deck')
    c.add_argument('--preview-backend', choices=['artifact', 'libreoffice', 'libreoffice-pdf', 'libreoffice-pdf-rgb', 'libreoffice-pdf-photos', 'native-svg'], default='artifact', help='Renderer for the finalized PPTX; native-svg reads flat solid native paths; LibreOffice PDF backends retain all native exports and support standalone slides')
    c.add_argument('--artifact-image-preview', action='store_true', help='Sample supported actual native picture media with MuPDF for Artifact previews; requires optional source dependencies and a standalone slide')
    c.add_argument('--artifact-image-shared-grid', help='JSON request declaring opaque integer source windows sampled together on a common native media grid; standalone Artifact preview only')
    c.add_argument('--artifact-image-source-sampling', help='JSON source image-only interval recipes which byte-replay delivered picture media before target-grid previews; standalone Artifact only')
    c.add_argument('--artifact-path-prefix-grid', help='JSON request for a filled native-path prefix sampled with the actual opaque background on a finite grid; standalone Artifact only; editable delivery unchanged')
    c.add_argument('--artifact-image-source-rgb-groups', help='JSON single-image recipes using the existing explicit RGB source-group policy; finite full-figure review required')
    c.add_argument('--pdf-alpha-derivation', choices=['binary-alpha-white-matte-v1'], help='Also deliver a separately named PDF with exact binary-alpha sample re-encoding; requires LibreOffice')
    c.add_argument('--manifest', required=True); c.add_argument('--output'); c.add_argument('--base'); c.add_argument('--base-sha256'); c.add_argument('--slide-id'); c.add_argument('--placement', type=float, nargs=4, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'), help='Target region in CSS pixels; fit uniformly and center'); c.add_argument('--replace-id', action='append'); c.add_argument('--marker-already-started', action='store_true'); c.set_defaults(func=build)
    c = sub.add_parser('insert', help='Fit an existing single-slide figure into a target deck; no authoring runtime needed')
    c.add_argument('--input', required=True, help='Generated single-slide PPTX')
    c.add_argument('--base', required=True, help='Existing target PPTX')
    c.add_argument('--base-sha256', help='Expected base checksum from inspect-base')
    c.add_argument('--slide-id', type=int, required=True, help='Stable native slide ID from inspect-base, not a page number')
    c.add_argument('--placement', type=float, nargs=4, required=True, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'), help='Target region in CSS pixels; fit uniformly and center')
    c.add_argument('--output', required=True, help='New PPTX path; existing files are never overwritten')
    c.add_argument('--replace-id', action='append', help='Replace a top-level object by its unique native name')
    c.add_argument('--prefix', help='Prefix imported object names to avoid collisions')
    c.add_argument('--receipt', help='Optional merge audit JSON path')
    c.set_defaults(func=insert)
    c = sub.add_parser('inspect-base')
    c.add_argument('base')
    def inspect(a):
        from .package import inspect_pptx as inspect_deck
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
