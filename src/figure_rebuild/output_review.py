"""Caller-supplied visual observations bound to one completed build.

Call ``prepare_output_review`` *before* inspecting its artifact paths, fill the
``model_review`` section, then call ``record_output_review``.  The original
bindings must accompany the observations; recording never refreshes them.  This
module checks evidence identity, not whether a person/model really looked at it,
and never infers visual quality from successful builds or numerical metrics.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .publish import stage


_SHA256 = re.compile(r'[0-9a-f]{64}')
_VISUAL_ROLES = {'source', 'preview_1x', 'preview_2x'}
_OPTIONAL_IMAGES = {
    'preview_4x': 'preview-4x.png',
    'preview_smooth_1x': 'preview-smooth-1x.png',
    'comparison': 'comparison.png',
    'source_raster': 'svg-reference.png',
}
_STATUSES = {'no_observed_issue', 'issues_found', 'needs_further_review'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label + ' is required')


def _json_record(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read build record {path}: {exc}') from exc
    _require(isinstance(value, dict), f'Build record must be an object: {path}')
    return value


def _binding(path):
    """Hash actual bytes, rejecting missing/empty files and in-flight writes."""
    path = Path(path).resolve()
    _require(path.is_file(), f'Missing or non-file review artifact: {path}')
    try:
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except OSError as exc:
        raise ValueError(f'Missing or unreadable review artifact {path}: {exc}') from exc
    signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    _require(signature(before) == signature(after) == signature(current),
             f'Review artifact changed while hashing: {path}')
    _require(after.st_size > 0, f'Review artifact is empty: {path}')
    return {'path': str(path), 'sha256': digest.hexdigest()}


def _inside(root, path):
    path = Path(path).resolve()
    _require(path.is_relative_to(root), f'Build artifact escapes immutable run: {path}')
    return path


def _preview_provenance(run, config, paths):
    """New runs bind renderer identity and its actual input/output evidence."""
    if 'preview_provenance_version' not in config and 'preview_backend' not in config:
        return None  # Keep immutable legacy v1 review bindings unchanged.
    from .native_preview import validate_backend
    _require(type(config.get('preview_provenance_version')) is int and
             config['preview_provenance_version'] == 1, 'Unsupported preview provenance version')
    backend = validate_backend(config.get('preview_backend'))
    audit_path = _inside(run, run / 'render-audit.json')
    audit = _json_record(audit_path)
    _require(type(audit.get('schema_version')) is int and audit['schema_version'] == 1 and
             audit.get('preview_backend') == backend, 'Render audit backend/schema disagrees with build config')
    _require(audit.get('input_pptx') == _binding(paths['pptx']), 'Render audit identifies a different final PPTX')
    _require(audit.get('application_playback_verified') is False,
             'Preview generation cannot grant native application playback verification')
    previews = audit.get('previews')
    expected = {'preview_1x': ('preview-1x.png', 1), 'preview_2x': ('preview-2x.png', 2),
                'preview_4x': ('preview-4x.png', 4), 'preview_smooth_1x': ('preview-smooth-1x.png', 1)}
    _require(isinstance(previews, dict) and set(previews) == set(expected), 'Render audit must bind all preview scales')
    from PIL import Image
    for role, (filename, scale) in expected.items():
        path = _inside(run, run / filename)
        record = previews[role]
        fields = {'path', 'sha256', 'width', 'height', 'scale'} | ({'derivation'} if role == 'preview_smooth_1x' else set())
        _require(isinstance(record, dict) and set(record) == fields,
                 'Invalid preview binding: ' + role)
        _require({k: record[k] for k in ('path', 'sha256')} == _binding(path), 'Preview bytes disagree with render audit: ' + role)
        _require(all(type(record[k]) is int and record[k] > 0 for k in ('width', 'height', 'scale')),
                 'Preview dimensions and scale must be positive integers')
        with Image.open(path) as png:
            _require(png.format == 'PNG' and list(png.size) == [record['width'], record['height']] and record['scale'] == scale,
                     'Preview dimensions or scale disagree with render audit: ' + role)
            png.verify()
        paths[role] = path
    smooth = previews['preview_smooth_1x']
    _require(smooth['derivation'] == {'source_role': 'preview_4x', 'source_sha256': previews['preview_4x']['sha256'],
                                     'kernel': 'lanczos3', 'target_size': [smooth['width'], smooth['height']], 'is_raw_preview': False}
             and smooth['derivation'].get('is_raw_preview') is False,
             'Smooth preview derivation is missing, stale, or falsely identifies a raw preview')
    paths['render_audit'] = audit_path
    evidence = audit.get('evidence')
    _require(isinstance(evidence, dict) and 'font_audit' in evidence, 'Renderer evidence must include audited fonts')
    for role, item in evidence.items():
        _require(isinstance(role, str) and re.fullmatch(r'[a-z][a-z0-9_]*', role) and role not in paths and role != 'delivered_pptx',
                 'Invalid or colliding renderer evidence role')
        _require(isinstance(item, dict) and set(item) == {'path', 'sha256'} and isinstance(item.get('path'), str),
                 'Invalid renderer evidence binding')
        path = _inside(run, Path(item['path']))
        _require(item == _binding(path), 'Renderer evidence changed: ' + role)
        paths[role] = path
    if backend == 'artifact':
        _require(audit.get('renderer') == 'Codex Artifact Tool', 'Artifact preview has a different renderer identity')
    else:
        _require(not config.get('base') and audit.get('renderer') == 'LibreOffice Impress' and
                 audit.get('renderer_backend') == 'headless_direct_png' and audit.get('raw_preview_format') == 'impress_png_Export' and
                 audit.get('pdf_inspector') == 'PyMuPDF' and audit.get('pdf_role') == 'font_and_image_evidence_only' and
                 'rasterizer' not in audit, 'Invalid native preview renderer identity')
        _require({'native_pdf', 'native_commands', 'native_fontconfig', 'native_command_executable', 'native_png_1x', 'native_png_2x', 'native_png_4x'} <= set(evidence), 'Native preview evidence is incomplete')
        _text(audit.get('libreoffice_version'), 'LibreOffice version')
        _text(audit.get('pdf_inspector_version'), 'PDF inspector version')
        native = audit.get('native_render')
        _require(isinstance(native, dict) and native.get('input_before') == audit['input_pptx'] == native.get('input_after'),
                 'Native preview input PPTX binding changed')
        _require(native.get('page_count') == 1 and native.get('page_index') == 0,
                 'Native preview must identify the single supported page')
        _require(audit.get('libreoffice_command') == config.get('runtime', {}).get('native_preview', {}).get('command'),
                 'Native command disagrees with the frozen runtime configuration')
        _require(audit.get('command_executable') == {'path': audit['libreoffice_command'][0],
                                                   'sha256': evidence['native_command_executable']['sha256']},
                 'Native executable declaration disagrees with the frozen command snapshot')
        _require(audit.get('pixel_density_dpi') == [96, 192, 384], 'Native pixel density must be 96/192/384')
        from .native_preview import pdf_export_options, png_export_options, conversion_command
        options = audit.get('pdf_export_options', {})
        _require(json.dumps(options, sort_keys=True) == json.dumps(pdf_export_options(), sort_keys=True), 'Native preview requires lossless PDF evidence without image downsampling or notes')
        commands = json.loads(paths['native_commands'].read_text(encoding='utf-8'))
        _require(isinstance(commands, list) and bool(commands) and all(isinstance(c, dict) and
                 c.get('returncode') == 0 and not c.get('timed_out') and isinstance(c.get('argv'), list) and
                 all(isinstance(arg, str) for arg in c['argv']) for c in commands), 'Native rendering commands did not all succeed')
        directory = run / 'native-preview'
        profile = config['runtime']['native_preview']
        environment = {**profile.get('environment', {}), 'FONTCONFIG_FILE': str(paths['native_fontconfig']),
                       'FONTCONFIG_PATH': str(directory), 'SAL_USE_VCLPLUGIN': 'svp'}
        _require(all(c.get('environment_overrides') == environment and c.get('environment_unset') == ['FONTCONFIG_SYSROOT']
                     for c in commands), 'Native commands must share the isolated registered font environment')
        version_argv = [*profile['command'], '-env:UserInstallation=' + (directory / 'profile-version').as_uri(), '--headless', '--version']
        _require(any(c['argv'] == version_argv and c.get('stdout', '').strip() == audit['libreoffice_version'] for c in commands),
                 'Native renderer version is not bound to the executed command')
        converts = [(i, c) for i, c in enumerate(commands) if '--convert-to' in c['argv']]
        pdf_argv = conversion_command(profile, directory, 'pdf', 'pdf:impress_pdf_Export', options, paths['pptx'])
        _require(len(converts) == 4 and sum(c['argv'] == pdf_argv for _, c in converts) == 1 and
                 paths['native_pdf'] == directory / 'pdf/reconstruction.pdf',
                 'Native exports must identify one PDF evidence export and three direct PNG exports of the final PPTX')
        exports = native.get('png_exports')
        _require(isinstance(exports, dict) and set(exports) == {'1', '2', '4'}, 'Direct PNG export evidence is incomplete')
        for scale in (1, 2, 4):
            item = exports[str(scale)]
            role = f'native_png_{scale}x'
            preview = previews[f'preview_{scale}x']
            expected_options = png_export_options(preview['width'], preview['height'])
            _require(isinstance(item, dict) and set(item) == {'evidence_role', 'input_pptx', 'fontconfig_sha256', 'options', 'command_index'} and
                     item['evidence_role'] == role and item['input_pptx'] == audit['input_pptx'] and
                     item['fontconfig_sha256'] == evidence['native_fontconfig']['sha256'] and
                     json.dumps(item['options'], sort_keys=True) == json.dumps(expected_options, sort_keys=True),
                     'Direct PNG export identity or size options disagree with the actual preview')
            index = item['command_index']
            _require(type(index) is int and 0 <= index < len(commands) and commands[index]['argv'] ==
                     conversion_command(profile, directory, f'png-{scale}x', 'png:impress_png_Export', expected_options, paths['pptx']),
                     'Direct PNG command disagrees with the final PPTX, scale, filter, or output directory')
            _require(paths[role] == directory / f'png-{scale}x/reconstruction.png' and
                     evidence[role]['sha256'] == preview['sha256'], 'Raw preview must retain the exact direct-export PNG bytes')
        from .package import inspect_pptx
        deck = inspect_pptx(paths['pptx'])
        _require(len(deck['slides']) == 1 and native.get('slide_id') == deck['slides'][0]['slide_id'] and
                 native.get('slide_size_emu') == deck['slide_size_emu'], 'Native page identity disagrees with final PPTX')
        try:
            import pymupdf
        except ImportError as exc:
            raise ValueError('Verifying a native preview requires PyMuPDF') from exc
        from .native_preview import _image_evidence, _pdf_font_name
        with pymupdf.open(paths['native_pdf']) as pdf:
            _require(pdf.is_pdf and not pdf.is_encrypted and len(pdf) == 1 and
                     not pdf[0].rotation and list(pdf[0].rect) == native.get('page_rect_points') and
                     abs(pdf[0].rect.width - deck['slide_size_emu']['cx'] / 12700) <= .02 and
                     abs(pdf[0].rect.height - deck['slide_size_emu']['cy'] / 12700) <= .02,
                     'Native PDF geometry disagrees with render audit or final PPTX')
            _require(audit.get('image_audit') == _image_evidence(paths['pptx'], pdf, pdf[0]),
                     'Native PDF image encodings or resolutions disagree with render audit')
            actual_fonts = sorted({item[3] for item in pdf[0].get_fonts(full=True)})
            _require(native.get('pdf_fonts') == actual_fonts, 'Native PDF font declarations disagree with actual resources')
        dimensions = native.get('pixel_dimensions')
        _require(isinstance(dimensions, dict) and dimensions == {str(s): [previews[f'preview_{s}x']['width'], previews[f'preview_{s}x']['height']] for s in (1, 2, 4)},
                 'Native preview sizes disagree with direct export evidence')
        font_resolutions = native.get('font_resolutions')
        registered = json.loads(paths['font_audit'].read_text(encoding='utf-8'))
        font_keys = {role for role in evidence if re.fullmatch(r'native_font_[0-9]+', role)}
        _require(isinstance(registered, list) and bool(registered) and isinstance(font_resolutions, list) and
                 len(font_resolutions) == len(registered) and font_keys == {f'native_font_{i}' for i in range(len(registered))},
                 'Native font resolution evidence is incomplete')
        allowed_names = set()
        for i, (resolution, face) in enumerate(zip(font_resolutions, registered)):
            expected_font = evidence[f'native_font_{i}']
            _require(isinstance(face, dict) and isinstance(resolution, dict) and
                     resolution.get('selected') == expected_font and expected_font['sha256'] == face.get('renderer_sha256') and
                     resolution.get('family') == face.get('family') and resolution.get('role') == face.get('role') and
                     resolution.get('postscript_names') == face.get('postscript_names') and type(resolution.get('face_index')) is int and resolution['face_index'] == 0,
                     'Native font resolution does not uniquely match its registered face')
            allowed_names.update(_pdf_font_name(name) for name in face.get('postscript_names', []))
        _require(all(_pdf_font_name(name) in allowed_names for name in actual_fonts), 'Native PDF contains an unregistered font')
        emu = deck['slide_size_emu']
        _require(all(dimensions[str(s)] == [round(emu['cx'] / 9525 * s), round(emu['cy'] / 9525 * s)] for s in (1, 2, 4)),
                 'Native pixel dimensions do not correspond to final PPTX and declared DPI')
    return audit


def _build_bindings(run_dir):
    run = Path(run_dir).resolve()
    _require(run.is_dir(), f'Build run does not exist: {run}')
    config_path = _inside(run, run / 'build-config.json')
    config = _json_record(config_path)
    manifest_path = _inside(run, run / 'manifest-snapshot.json')
    _require(config.get('run') == str(run), 'Build config identifies a different run')
    _require(config.get('manifest') == str(manifest_path),
             'Build config must identify the immutable manifest snapshot')
    asset_root = _inside(run, run / 'assets')
    _require(config.get('asset_root') == str(asset_root),
             'Build config must identify the frozen asset directory')
    manifest = _json_record(manifest_path)
    resolved_path = _inside(run, run / 'resolved-scene.json')
    resolved = _json_record(resolved_path)
    _text(manifest.get('id'), 'Manifest id')
    _require(type(manifest.get('revision')) is int and manifest['revision'] > 0,
             'Manifest revision must be a positive integer')
    _require(all(resolved.get(k) == manifest.get(k) for k in ('id', 'revision', 'source')),
             'Resolved scene does not identify the same source, figure and revision')
    source = manifest.get('source')
    _require(isinstance(source, dict), 'Manifest source is required')
    _text(source.get('path'), 'Source path')
    _require(not Path(source['path']).is_absolute(), 'Source path must be snapshot-relative')
    _require(isinstance(source.get('sha256'), str) and _SHA256.fullmatch(source['sha256']),
             'Manifest source SHA256 is invalid')
    paths = {
        'build_config': config_path,
        'manifest': manifest_path,
        'resolved_scene': resolved_path,
        'source': _inside(asset_root, asset_root / source['path']),
        'pptx': _inside(run, run / 'validated-output' / 'reconstruction.pptx'),
        'preview_1x': _inside(run, run / 'preview-1x.png'),
        'preview_2x': _inside(run, run / 'preview-2x.png'),
        'delivery': _inside(run, run / 'delivery.json'),
    }
    for role, filename in _OPTIONAL_IMAGES.items():
        path = run / filename
        if path.exists() or path.is_symlink():
            paths[role] = _inside(run, path)
    preview_audit = _preview_provenance(run, config, paths)
    _text(config.get('output'), 'Delivered PPTX path')
    _require(Path(config['output']).is_absolute(), 'Delivered PPTX path must be absolute')
    paths['delivered_pptx'] = Path(config['output']).resolve()
    _require(len(set(paths.values())) == len(paths), 'Artifact paths must be distinct')
    bindings = {role: _binding(path) for role, path in paths.items()}
    _require(bindings['source']['sha256'] == source['sha256'], 'Frozen source hash changed')
    delivery = _json_record(paths['delivery'])
    _require(delivery.get('output') == str(paths['delivered_pptx']),
             'Delivery receipt identifies a different output path')
    _require(delivery.get('sha256') == bindings['pptx']['sha256'] ==
             bindings['delivered_pptx']['sha256'],
             'Delivered PPTX, immutable validated PPTX and receipt do not match')
    _require(delivery.get('source_sha256') == source['sha256'],
             'Delivery receipt identifies a different source')
    if preview_audit is not None:
        _require(delivery.get('preview_backend') == preview_audit['preview_backend'] and
                 delivery.get('render_audit_sha256') == bindings['render_audit']['sha256'],
                 'Delivery receipt disagrees with preview renderer provenance')
    return run, {'figure_id': manifest['id'], 'revision': manifest['revision']}, bindings


def prepare_output_review(run_dir):
    """Return an unperformed review template for exact, existing build artifacts.

    The template is not evidence of review.  Observe the listed source and both
    previews before filling it.  Snapshot identity includes any existing extra
    previews/comparison; their later removal, replacement or addition is stale.
    """
    run, identity, bindings = _build_bindings(run_dir)
    return {
        'schema_version': 1, 'kind': 'postbuild_visual_review',
        'run': str(run), 'build': identity, 'bindings': bindings,
        'model_review': {
            'performed': False, 'reviewer': '', 'method': '',
            'status': 'needs_further_review', 'inspected': [],
            'findings': [], 'limitations': [],
        },
        'user_acceptance': {'status': 'pending'},
        'native_application_verification': {'status': 'not_verified'},
    }


def _validate_observations(record, bindings):
    model = record.get('model_review')
    _require(isinstance(model, dict) and model.get('performed') is True,
             'Caller must explicitly report an actually performed visual inspection')
    _text(model.get('reviewer'), 'Visual reviewer')
    _text(model.get('method'), 'Visual inspection method')
    _require(isinstance(model.get('status'), str) and model['status'] in _STATUSES,
             'Invalid model visual review status')
    _require(set(model) == {'performed', 'reviewer', 'method', 'status', 'inspected',
                            'findings', 'limitations'}, 'Unknown or missing model review fields')
    inspected = model.get('inspected')
    _require(isinstance(inspected, list), 'Inspected artifacts must be a list')
    seen = set()
    for item in inspected:
        _require(isinstance(item, dict), 'Each inspected artifact must be a record')
        role = item.get('artifact')
        _require(isinstance(role, str) and role in bindings, 'Unknown inspected artifact')
        _require(item.get('sha256') == bindings[role]['sha256'],
                 f'Inspected artifact hash is missing or stale: {role}')
        regions = item.get('regions')
        _require(isinstance(regions, list) and bool(regions), 'Inspected regions are required')
        for region in regions:
            _text(region, 'Inspected region description')
        seen.add(role)
    _require(_VISUAL_ROLES <= seen, 'Inspect source, preview_1x and preview_2x explicitly')
    findings = model.get('findings')
    limitations = model.get('limitations')
    _require(isinstance(findings, list), 'Visual findings must be a list')
    _require(isinstance(limitations, list), 'Visual limitations must be a list')
    for limitation in limitations:
        _text(limitation, 'Visual limitation')
    ids, unresolved = set(), []
    for finding in findings:
        _require(isinstance(finding, dict), 'Each visual finding must be a record')
        _text(finding.get('id'), 'Finding id')
        _require(finding['id'] not in ids, 'Duplicate finding id: ' + finding['id'])
        ids.add(finding['id'])
        _text(finding.get('description'), 'Finding description')
        _text(finding.get('region'), 'Finding region')
        _require(isinstance(finding.get('severity'), str) and
                 finding['severity'] in {'critical', 'major', 'minor'},
                 'Invalid finding severity')
        _require(isinstance(finding.get('status'), str) and
                 finding['status'] in {'open', 'needs_review', 'resolved'},
                 'Invalid finding status')
        evidence = finding.get('artifacts')
        _require(isinstance(evidence, list) and bool(evidence) and
                 all(isinstance(role, str) and role in seen for role in evidence),
                 'Findings must identify inspected artifact evidence')
        if finding['status'] == 'resolved':
            _text(finding.get('resolution'), 'Resolved finding explanation')
        else:
            unresolved.append(finding['id'])
    if model['status'] == 'no_observed_issue':
        _require(not unresolved, 'No-observed-issue status contradicts unresolved findings')
    if model['status'] == 'issues_found':
        _require(bool(unresolved), 'Issues-found status needs an unresolved finding')
    if model['status'] == 'needs_further_review':
        _require(bool(unresolved or limitations), 'Further review needs an explicit reason')
    _require(record.get('user_acceptance') == {'status': 'pending'},
             'Model output review cannot grant user acceptance; keep it pending')
    native = record.get('native_application_verification')
    _require(isinstance(native, dict), 'Native application verification must be separate')
    _require(isinstance(native.get('status'), str) and
             native['status'] in {'not_verified', 'verified'},
             'Invalid native application verification status')
    if native['status'] == 'not_verified':
        _require(native == {'status': 'not_verified'},
                 'Unverified native application status cannot contain verification claims')
    if native['status'] == 'verified':
        for field in ('application', 'version', 'reviewer', 'method'):
            _text(native.get(field), 'Native application ' + field)
        _require(native.get('pptx_sha256') == bindings['pptx']['sha256'],
                 'Native application verification is for a different PPTX')
        evidence = native.get('evidence')
        _require(isinstance(evidence, list) and bool(evidence),
                 'Native application verification requires its own evidence files')
        for item in evidence:
            _require(isinstance(item, dict) and isinstance(item.get('path'), str),
                     'Native application evidence needs a file binding')
            _require(item == _binding(item['path']), 'Native application evidence is stale')
            _require(item['sha256'] not in {artifact['sha256'] for artifact in bindings.values()},
                     'Build artifacts alone are not native application inspection evidence')
    return unresolved


def verify_output_review(run_dir, record, *, require_no_observed_issues=False):
    """Validate bindings/observations; optionally gate on a clear model review.

    Integrity errors always raise ValueError.  Open findings may be saved and
    inspected normally; callers requiring a clear review opt into the gate.
    Even a clear review leaves user acceptance pending and does not imply native
    application playback or semantic correctness outside the inspected scope.
    """
    _require(isinstance(record, dict), 'Output review must be a record')
    _require(set(record) <= {'schema_version', 'kind', 'run', 'build', 'bindings',
                             'model_review', 'user_acceptance',
                             'native_application_verification', 'recorded_at'},
             'Unknown output review fields; acceptance and verification must stay separate')
    _require(type(record.get('schema_version')) is int and record['schema_version'] == 1,
             'Unsupported output review schema')
    _require(record.get('kind') == 'postbuild_visual_review', 'Not a postbuild visual review')
    run, identity, bindings = _build_bindings(run_dir)
    _require(record.get('run') == str(run) and record.get('build') == identity,
             'Output review belongs to a different build')
    supplied = record.get('bindings')
    _require(isinstance(supplied, dict) and supplied == bindings,
             'Output review bindings are missing or stale: artifact paths or bytes changed')
    unresolved = _validate_observations(record, bindings)
    status = record['model_review']['status']
    if require_no_observed_issues:
        _require(status == 'no_observed_issue' and not unresolved,
                 'Output review has unresolved findings or still needs further review')
    return {'status': status, 'unresolved_findings': unresolved,
            'user_acceptance': 'pending',
            'native_application_verification': record['native_application_verification']['status']}


def record_output_review(run_dir, record, *, output=None):
    """Save supplied observations exclusively, without rebinding or acceptance.

    Returns the saved record.  Use a new report name for a later inspection;
    existing reviews are never silently overwritten.
    """
    try:
        saved = json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError('Output review must be finite JSON data') from exc
    verify_output_review(run_dir, saved)
    destination = Path(output) if output is not None else Path(run_dir) / 'output-review.json'
    destination = destination.resolve()
    _require(str(destination) not in {a['path'] for a in saved['bindings'].values()},
             'Output review must not replace an artifact')
    _require(destination.suffix.lower() == '.json', 'Output review must use a JSON report path')
    saved['recorded_at'] = datetime.now(timezone.utc).isoformat()
    content = (json.dumps(saved, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    temporary = stage(destination, content)
    linked = False
    try:
        verify_output_review(run_dir, saved)
        os.link(temporary, destination)
        linked = True
        verify_output_review(run_dir, saved)
    except BaseException:
        if linked and destination.exists() and os.path.samestat(temporary.stat(), destination.stat()):
            destination.unlink()
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return saved
