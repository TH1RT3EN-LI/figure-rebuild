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
import math
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


def _json_record(path, *, strict_numbers_and_keys=False):
    def unique_record(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate JSON field: ' + key)
            result[key] = value
        return result

    def finite_number(token):
        value = float(token)
        _require(math.isfinite(value), 'Nonfinite JSON number: ' + token)
        return value

    def invalid_constant(token):
        raise ValueError('Nonstandard JSON numeric constant: ' + token)

    try:
        options = ({'object_pairs_hook': unique_record, 'parse_float': finite_number,
                    'parse_constant': invalid_constant} if strict_numbers_and_keys else {})
        value = json.loads(path.read_text(encoding='utf-8'), **options)
    except (OSError, ValueError, RecursionError) as exc:
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
        _require('artifact_stroke_preview_version' not in config, 'Native stroke preview requires preview provenance')
        _require('artifact_image_preview_version' not in config, 'Native picture preview requires preview provenance')
        if (run / 'render-audit.json').exists():
            legacy_audit = _json_record(run / 'render-audit.json')
            _require('artifact_stroke_preview' not in legacy_audit and
                     'stroke_preview_definition' not in legacy_audit.get('evidence', {}),
                     'Unrequested native stroke preview adapter')
            _require('artifact_image_preview' not in legacy_audit and
                     'image_preview_definition' not in legacy_audit.get('evidence', {}),
                     'Unrequested native picture preview adapter')
        return None  # Keep immutable legacy v1 review bindings unchanged.
    from .native_preview import validate_backend, validate_pdf_alpha_policy
    _require(type(config.get('preview_provenance_version')) is int and
             config['preview_provenance_version'] == 1, 'Unsupported preview provenance version')
    backend = validate_backend(config.get('preview_backend'))
    alpha_policy = validate_pdf_alpha_policy(config.get('pdf_alpha_derivation'), backend)
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
    if alpha_policy is None:
        _require('pdf_alpha_derivation' not in audit and
                 not {'native_pdf_derived', 'native_pdf_alpha_receipt'} & set(evidence),
                 'PDF derivation was not requested in the immutable config')
    if backend == 'artifact':
        _require(audit.get('renderer') == 'Codex Artifact Tool', 'Artifact preview has a different renderer identity')
        image_version = config.get('artifact_image_preview_version')
        if image_version is None:
            _require('artifact_image_preview' not in audit and 'image_preview_definition' not in evidence,
                     'Unrequested native picture preview adapter')
        else:
            _require(type(image_version) is int and image_version == 1 and not config.get('base'),
                     'Unsupported native picture preview provenance')
            _require('image_preview_definition' in evidence, 'Native picture preview definition is missing')
            from .artifact_image_preview import prepare_image_preview
            definition = _json_record(paths['image_preview_definition'], strict_numbers_and_keys=True)
            replay = prepare_image_preview(paths['pptx'], _json_record(paths['resolved_scene'], strict_numbers_and_keys=True))
            _require(_same_json(definition, replay), 'Native picture preview definition disagrees with actual delivered PPTX')
            expected_images = {'schema_version': 1, 'policy': replay['policy'], 'preview_only': True,
                               'native_delivery_modified': False, 'reference_pixels_used': False,
                               'renderer': replay['renderer'], 'renderer_version': replay['renderer_version'],
                               'mupdf_version': replay['mupdf_version'],
                               'applications': [{'scale': scale, 'applied_object_ids': [o['id'] for o in replay['objects']],
                                                 'complete_mixed_paint_order_preserved': True} for scale in (1, 2, 4)],
                               'unsupported': replay['unsupported'], 'source_pixel_equivalence': False,
                               'application_playback_verified': False}
            _require(_same_json(audit.get('artifact_image_preview'), expected_images),
                     'Native picture preview application/order receipt disagrees with actual native definition')
        stroke_version = config.get('artifact_stroke_preview_version')
        if stroke_version is None:
            _require('artifact_stroke_preview' not in audit and 'stroke_preview_definition' not in evidence,
                     'Unrequested native stroke preview adapter')
        else:
            _require(type(stroke_version) is int and stroke_version == 1 and not config.get('base'),
                     'Unsupported native stroke preview provenance')
            _require('stroke_preview_definition' in evidence, 'Native stroke preview definition is missing')
            from .artifact_stroke_preview import prepare_stroke_preview
            definition = _json_record(paths['stroke_preview_definition'], strict_numbers_and_keys=True)
            replay = prepare_stroke_preview(paths['pptx'], _json_record(paths['resolved_scene'], strict_numbers_and_keys=True))
            _require(_same_json(definition, replay), 'Native stroke preview definition disagrees with actual delivered PPTX')
            expected_strokes = {'schema_version': 1, 'policy': replay['policy'], 'preview_only': True,
                                'native_delivery_modified': False, 'applied_object_ids': [o['id'] for o in replay['objects']],
                                'unsupported': replay['unsupported'], 'complete_mixed_paint_order_preserved': True,
                                'source_pixel_equivalence': False, 'application_playback_verified': False}
            _require(_same_json(audit.get('artifact_stroke_preview'), expected_strokes),
                     'Native stroke preview application/order receipt disagrees with actual native definition')
    else:
        _require('artifact_image_preview_version' not in config and 'artifact_image_preview' not in audit and
                 'image_preview_definition' not in evidence, 'Native picture preview adapter requires Artifact')
        _require('artifact_stroke_preview_version' not in config and 'artifact_stroke_preview' not in audit and
                 'stroke_preview_definition' not in evidence, 'Native stroke preview adapter requires Artifact')
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
        if alpha_policy:
            from .pdf_binary_alpha import verify_binary_alpha_pdf
            _require({'native_pdf_derived', 'native_pdf_alpha_receipt'} <= set(evidence), 'Derived PDF evidence is incomplete')
            _require(paths['native_pdf_derived'] == directory / 'derived-pdf/binary-alpha-white-matte.pdf' and
                     paths['native_pdf_alpha_receipt'] == directory / 'derived-pdf/receipt.json', 'Invalid derived PDF paths')
            receipt = _json_record(paths['native_pdf_alpha_receipt'])
            verify_binary_alpha_pdf(paths['native_pdf'], paths['native_pdf_derived'], receipt)
            expected_derivation = {'policy': alpha_policy, 'source_role': 'native_pdf',
                                  'derived_role': 'native_pdf_derived', 'receipt_role': 'native_pdf_alpha_receipt',
                                  'transformed_masks': len(receipt['transformed_masks']),
                                  'retained_masks': len(receipt['retained_masks']),
                                  'raw_export_replaced': False, 'raw_previews_derived_from_pdf': False,
                                  'rgb_alpha_filtering_error_bound_proved': False}
            _require(_same_json(audit.get('pdf_alpha_derivation'), expected_derivation), 'Derived PDF declaration disagrees with sample replay')
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


def _same_json(left, right):
    # Preserve JSON value types: a boolean or 1.0 is not an integer count.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


_JS_TRIM_SPACE = frozenset('\t\v\f \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005'
                           '\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff')


def _text_fit_number(value, label, *, minimum=None, positive=False):
    try:
        good = type(value) in (int, float) and math.isfinite(value)
        number = float(value) if good else 0.
    except (OverflowError, ValueError):
        good, number = False, 0.
    _require(good and (minimum is None or number >= minimum) and (not positive or number > 0),
             'Text-fit invalid finite numeric evidence: ' + label)
    return number


def _text_fit_same(actual, expected, label):
    # Reproduce the writer's binary64 arithmetic, not an adjustable tolerance.
    # JSON's 1 and 1.0 are the same number; booleans are rejected before this.
    _require(_text_fit_number(actual, label) == _text_fit_number(expected, label),
             'Text-fit arithmetic disagrees with the writer: ' + label)


def _text_fit_round(value, label):
    value = _text_fit_number(value, label)
    lower = math.floor(value)
    # Math.round chooses +infinity on a tie. Adding .5 first can itself round
    # across a boundary (just below .5, or at large odd integers).
    return lower + (value - lower >= .5)


def _text_fit_wrapped_lines(text, lines):
    """Conserve Unicode and hard breaks, allowing only trimEnd at soft breaks.

    This does not rerun font measurement or select grapheme/word break points.
    Every line is an exact source substring. Only ECMAScript trailing space may
    disappear at a line boundary, and each hard newline consumes a boundary.
    The state budget keeps adversarial whitespace/empty-line ambiguity bounded.
    """
    limit = 1_000_000
    work = len(text)
    _require(work <= limit, 'Text-fit wrapped-line verification budget exceeded')
    # States are disjoint inclusive intervals of source positions. Compressing
    # their whitespace ambiguity avoids expanding the same suffix per position.
    space_end = list(range(len(text) + 1))
    for at in range(len(text) - 1, -1, -1):
        if text[at] in _JS_TRIM_SPACE:
            space_end[at] = space_end[at + 1]
    states = [(0, 0)]
    for index, line in enumerate(lines):
        _require(not line or line[-1] not in _JS_TRIM_SPACE,
                 'Text-fit wrapped line contradicts writer trimEnd')
        following = []
        for low, high in states:
            at = low
            while at <= high:
                work += 1
                _require(work <= limit, 'Text-fit wrapped-line verification budget exceeded')
                if line:
                    at = text.find(line, at, high + len(line))
                    if at < 0:
                        break
                end = at + len(line)
                stop = space_end[end]
                if index == len(lines) - 1:
                    if stop == len(text):
                        return True
                else:
                    # A soft break needs characters or consumed whitespace;
                    # a hard newline is consumed by precisely one boundary.
                    begin = end + (not line)
                    upper = stop - (stop == len(text) or text[stop] == '\n')
                    if begin <= upper:
                        following.append((begin, upper))
                    if stop < len(text) and text[stop] == '\n':
                        following.append((stop + 1, stop + 1))
                # For an empty line, all further states in this whitespace run
                # produce a subset of the interval already recorded above.
                at = max(at + 1, stop + 1) if not line else at + 1
        states = []
        for low, high in sorted(following):
            if states and low <= states[-1][1] + 1:
                states[-1] = (states[-1][0], max(states[-1][1], high))
            else:
                states.append((low, high))
        if not states:
            return False
    return False


def _verify_text_fit_measurements(resolved, measured):
    """Check current writer records, not independently remeasure actual fonts.

    Fit uses the writer's 0.001 px overflow/canvas allowance. Deterministic
    frame and layout arithmetic is checked without an added epsilon. Metrics
    such as glyph advance/ascent remain recorded evidence, not replayed facts.
    """
    live = {obj['id']: obj for obj in resolved['objects'] if obj.get('kind') == 'text'}
    for row in measured:
        oid = row['id']; obj = live[oid]
        label = 'object ' + oid
        _require(set(row) == {'id', 'box', 'layout'}, 'Text-fit incomplete measurement: ' + label)
        box, layout = row['box'], row['layout']

        def frame(value, name, *, content=False):
            _require(isinstance(value, dict) and set(value) == {'x', 'y', 'width', 'height'},
                     'Text-fit invalid frame: ' + name)
            return {k: _text_fit_number(value[k], name + '.' + k,
                                      positive=k == 'width' or (k == 'height' and not content))
                    for k in ('x', 'y', 'width', 'height')}

        box = frame(box, label + '.box')
        fields = {'lines', 'line_count', 'required_width', 'required_height', 'line_height', 'ascent',
                  'descent', 'native_baseline_ascent', 'default_native_baseline_ascent', 'baseline_basis',
                  'line_height_basis', 'measurement_basis', 'content_box', 'insets'}
        _require(isinstance(layout, dict) and fields <= set(layout) and
                 not set(layout) - fields - {'renderer_baseline', 'baseline_adjustment_px', 'character_spacing'},
                 'Text-fit incomplete layout: ' + label)
        from .text_spacing import validate_character_spacing
        spacing = validate_character_spacing(obj)
        if spacing is None:
            _require('character_spacing' not in layout, 'Text-fit undeclared character spacing: ' + label)
        else:
            recorded_spacing = layout.get('character_spacing')
            _require(isinstance(recorded_spacing, dict) and
                     set(recorded_spacing) == {'extra_advances_canvas_px', 'measurement_basis'} and
                     recorded_spacing['extra_advances_canvas_px'] == spacing and
                     all(_text_fit_number(v, label + '.character_spacing') == v
                         for v in recorded_spacing['extra_advances_canvas_px']) and
                     recorded_spacing['measurement_basis'] ==
                     'individual registered-font characters; native character spacing needs application verification',
                     'Text-fit character spacing differs from source: ' + label)
        lines, count = layout['lines'], layout['line_count']
        _require(isinstance(lines, list) and bool(lines) and all(isinstance(v, str) and '\n' not in v and '\r' not in v for v in lines) and
                 type(count) is int and count == len(lines), 'Text-fit invalid lines/count: ' + label)
        text = obj.get('text')
        _require(isinstance(text, str), 'Text-fit source text missing: ' + label)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        wrap = obj.get('wrap', 'none') if 'box' in obj else 'none'
        _require(wrap in ('none', 'square'), 'Text-fit unsupported wrapping: ' + label)
        _require(lines == text.split('\n') if wrap == 'none' else _text_fit_wrapped_lines(text, lines),
                 'Text-fit line content differs from source text: ' + label)
        values = {k: _text_fit_number(layout[k], label + '.' + k,
                  minimum=0 if k in ('required_width', 'ascent', 'descent') else None,
                  positive=k in ('required_height', 'line_height')) for k in
                  ('required_width', 'required_height', 'line_height', 'ascent', 'descent',
                   'native_baseline_ascent', 'default_native_baseline_ascent')}
        # The writer separately requires positive content width. Its content
        # height contract is only the recorded-height overflow check below.
        content = frame(layout['content_box'], label + '.content_box', content=True)
        insets = layout['insets']; declared = obj.get('insets', {})
        sides = ('left', 'right', 'top', 'bottom')
        _require(isinstance(insets, dict) and set(insets) == set(sides) and
                 isinstance(declared, dict) and not set(declared) - set(sides),
                 'Text-fit invalid insets: ' + label)
        for side in sides:
            _text_fit_same(_text_fit_number(insets[side], label + '.' + side, minimum=0),
                           _text_fit_number(declared.get(side, 0), label + '.' + side, minimum=0), label + '.insets.' + side)
        insets = {side: float(insets[side]) for side in sides}
        expected_content = {'x': box['x'] + insets['left'], 'y': box['y'] + insets['top'],
                            'width': box['width'] - insets['left'] - insets['right'],
                            'height': box['height'] - insets['top'] - insets['bottom']}
        for key in expected_content:
            _text_fit_same(content[key], expected_content[key], label + '.content_box.' + key)
        size = _text_fit_number(obj.get('font_size'), label + '.font_size', positive=True)
        explicit_baseline = 'baseline_offset' in obj
        baseline = (_text_fit_number(obj['baseline_offset'], label + '.baseline_offset', minimum=0)
                    if explicit_baseline else values['default_native_baseline_ascent'])
        _text_fit_same(values['native_baseline_ascent'], baseline, label + '.baseline')
        _require(layout['baseline_basis'] == ('explicit_calibrated_offset' if explicit_baseline else 'font_metrics_and_native_leading') and
                 layout['line_height_basis'] == ('explicit_pixel_value' if 'line_height' in obj else 'default_measured_leading') and
                 layout['measurement_basis'] == 'registered font; approximate PPT line layout; actual preview still required',
                 'Text-fit measurement basis disagrees with source: ' + label)
        expected_pitch = (_text_fit_number(obj['line_height'], label + '.line_height', positive=True)
                          if 'line_height' in obj else max(size * 1.2, values['ascent'] + values['descent']))
        if 'renderer_baseline' in layout:
            rb = layout['renderer_baseline']
            rb_fields = {'model', 'first_baseline_px', 'line_height_px', 'natural_line_height_px', 'font_ascent_px',
                         'paint_baseline_correction_px', 'rendered_font_size_px', 'scale', 'requested_line_height_px', 'spacing'}
            _require(isinstance(rb, dict) and rb_fields <= set(rb) and
                     not set(rb) - rb_fields - {'spacing_thousandths_percent'} and rb['model'] == 'artifact_presentation_v1',
                     'Text-fit invalid renderer metrics: ' + label)
            for key in rb_fields - {'model', 'spacing'}:
                _text_fit_number(rb[key], label + '.renderer.' + key,
                                 minimum=0 if key == 'first_baseline_px' else None,
                                 positive=key not in ('first_baseline_px', 'paint_baseline_correction_px'))
            scale = float(rb['scale']); px = _text_fit_round(size * scale * 75, label + '.rendered_font_size') / 75
            natural = px * 1.2
            _text_fit_number(natural, label + '.renderer.natural_height', positive=True)
            _text_fit_same(rb['rendered_font_size_px'], px / scale, label + '.rendered_font_size')
            _text_fit_same(rb['natural_line_height_px'], natural / scale, label + '.natural_line_height')
            requested = float(obj['line_height']) * scale if 'line_height' in obj else natural
            _text_fit_same(rb['requested_line_height_px'], float(obj['line_height']) if 'line_height' in obj else requested / scale,
                           label + '.requested_line_height')
            if 'line_height' in obj:
                percent = rb.get('spacing_thousandths_percent')
                _require(type(percent) is int and 0 < percent <= 9007199254740991 and rb['spacing'] == 'percent_of_natural_line' and
                         percent == _text_fit_round(requested / natural * 100000, label + '.spacing'), 'Text-fit invalid percentage spacing: ' + label)
                expected_pitch = natural * (percent / 100000) / scale
            else:
                _require(rb['spacing'] == 'default' and 'spacing_thousandths_percent' not in rb,
                         'Text-fit invalid default spacing: ' + label)
                expected_pitch = natural / scale
            _text_fit_same(rb['line_height_px'], expected_pitch, label + '.renderer.line_height')
            _text_fit_same(layout.get('baseline_adjustment_px'), baseline - rb['first_baseline_px'], label + '.baseline_adjustment')
        else:
            _require('baseline_adjustment_px' not in layout, 'Text-fit adjustment has no renderer metrics: ' + label)
        _text_fit_same(values['line_height'], expected_pitch, label + '.line_height')
        required_height = expected_pitch * count
        if explicit_baseline:
            required_height = max(required_height, baseline + values['descent'] + (count - 1) * expected_pitch)
        _text_fit_same(values['required_height'], required_height, label + '.required_height')
        if 'box' in obj:
            source_box = frame(obj['box'], label + '.source_box')
            for key in box:
                _text_fit_same(box[key], source_box[key], label + '.box.' + key)
        else:
            anchor = obj.get('anchor')
            _require(isinstance(anchor, dict) and set(anchor) == {'x', 'y'}, 'Text-fit source anchor missing: ' + label)
            ax, ay = (_text_fit_number(anchor[k], label + '.anchor.' + k) for k in ('x', 'y'))
            width = max(1, values['required_width'] + size * .12); height = required_height + size * .16
            alignment = obj.get('alignment', 'left')
            _require(alignment in ('left', 'center', 'right'), 'Text-fit invalid alignment: ' + label)
            expected_box = {'x': ax - insets['left'] - (width / 2 if alignment == 'center' else width if alignment == 'right' else 0),
                            'y': ay - insets['top'] - baseline, 'width': width + insets['left'] + insets['right'],
                            'height': height + insets['top'] + insets['bottom']}
            rotation = _text_fit_number(obj.get('rotation', 0), label + '.rotation')
            if rotation:
                # Replay the writer's center adjustment before checking the
                # receipt. DrawingML rotates the frame around its center, while
                # source anchors identify the baseline in slide coordinates.
                angle = rotation * math.pi / 180
                _text_fit_number(angle, label + '.rotation_radians')
                dx = expected_box['x'] + expected_box['width'] / 2 - ax
                dy = expected_box['y'] + expected_box['height'] / 2 - ay
                expected_box['x'] = ax + math.cos(angle) * dx - math.sin(angle) * dy - expected_box['width'] / 2
                expected_box['y'] = ay + math.sin(angle) * dx + math.cos(angle) * dy - expected_box['height'] / 2
            for key in box:
                _text_fit_same(box[key], expected_box[key], label + '.anchored_box.' + key)
        _require(values['required_width'] <= content['width'] + .001 and required_height <= content['height'] + .001,
                 'Text-fit recorded measurement overflows content frame: ' + label)
        canvas = resolved.get('canvas')
        _require(isinstance(canvas, dict), 'Text-fit source canvas missing: ' + label)
        width, height = (_text_fit_number(canvas.get(k), label + '.canvas.' + k, positive=True)
                         for k in ('width', 'height'))
        radians = _text_fit_number(obj.get('rotation', 0), label + '.rotation') * math.pi / 180
        _text_fit_number(radians, label + '.rotation_radians')
        co, si = math.cos(radians), math.sin(radians)
        cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        for dx in (-box['width'] / 2, box['width'] / 2):
            for dy in (-box['height'] / 2, box['height'] / 2):
                x, y = cx + co * dx - si * dy, cy + si * dx + co * dy
                _require(math.isfinite(x) and math.isfinite(y) and -.001 <= x <= width + .001 and -.001 <= y <= height + .001,
                         'Text-fit measured frame lies outside source canvas: ' + label)


def _diagnostic_provenance(run, config, paths, resolved, preview_audit):
    """Bind limited diagnostics, not source recognition or geometry replay."""
    if 'diagnostic_provenance_version' not in config:
        _require('source_inventory' not in _json_record(paths['manifest']),
                 'Source inventory requires diagnostic provenance')
        return None  # Existing run/review bindings remain unchanged.
    _require(type(config['diagnostic_provenance_version']) is int and config['diagnostic_provenance_version'] == 1,
             'Unsupported diagnostic provenance version')
    _require(isinstance(preview_audit, dict), 'Diagnostic provenance requires the final-PPT render audit')
    reports = {}
    for role, filename in (('source_content_audit', 'source-content-audit.json'),
                           ('semantic_audit', 'semantic-audit.json'), ('text_fit', 'text-fit.json')):
        path = _inside(run, run / filename)
        reports[role] = _json_record(path, strict_numbers_and_keys=role == 'text_fit')
        paths[role] = path
    objects = resolved.get('objects')
    _require(isinstance(objects, list) and all(isinstance(obj, dict) for obj in objects),
             'Diagnostic coverage requires actual resolved objects')
    source = reports['source_content_audit']
    semantic = reports['semantic_audit']
    text_fit = reports['text_fit']
    from .content_audit import audit_source_content
    # This rechecks the existing caller-declared constraints only. It does not
    # read source pixels/PDF, infer literals, or replay source paint geometry.
    _require(_same_json(source, audit_source_content(resolved)),
             'Source-content diagnostic disagrees with the resolved declared constraints')
    _require(_same_json(semantic.get('source_content'), source),
             'Semantic diagnostic contains different source-content evidence')
    expected_formulas = [obj.get('formula_asset') for obj in objects if obj.get('source_kind') == 'formula']
    expected_connections = [obj.get('connection_record') for obj in objects if obj.get('source_kind') == 'connector']
    _require(all(isinstance(item, dict) for item in expected_formulas + expected_connections) and
             _same_json(semantic.get('formulas'), expected_formulas) and
             _same_json(semantic.get('connections'), expected_connections),
             'Semantic records disagree with the resolved declared formula/connector objects')
    live_ids = [obj.get('id') for obj in objects if obj.get('kind') == 'text']
    measured = text_fit.get('objects')
    _require(isinstance(measured, list) and all(isinstance(item, dict) for item in measured),
             'Text-fit diagnostic must list its measured live text')
    measured_ids = [item.get('id') for item in measured]
    _require(all(isinstance(item, str) and item for item in live_ids + measured_ids) and
             len(set(measured_ids)) == len(measured_ids) and sorted(measured_ids) == sorted(live_ids),
             'Text-fit measured identities disagree with the actual live text objects')
    _verify_text_fit_measurements(resolved, measured)
    counts = {'resolved_objects': len(objects), 'live_text_objects': len(live_ids),
              'measured_live_text_objects': len(measured),
              'unmeasured_path_objects': sum(obj.get('kind') == 'path' for obj in objects),
              'unmeasured_image_objects': sum(obj.get('kind') == 'image' for obj in objects)}
    text_status = 'PASS' if live_ids else 'NOT_APPLICABLE'
    _require(type(text_fit.get('schema_version')) is int and text_fit['schema_version'] == 1 and
             text_fit.get('status') == text_status and text_fit.get('scope') == 'measured_live_text_only' and
             _same_json(text_fit.get('counts'), counts) and text_fit.get('visual_verification_required') is True,
             'Text-fit status, scope, or counts disagree with actual measured live text')
    expected = {'schema_version': 1,
                'inputs': {role: _binding(paths[role]) for role in ('source', 'manifest', 'resolved_scene', 'pptx')},
                'reports': {
                    'source_content_audit': {'artifact': _binding(paths['source_content_audit']),
                        'status': source['status'], 'scope': 'declared_invariants_only',
                        'counts': {key: source['coverage'][key] for key in ('literals_checked', 'connections_checked')}},
                    'semantic_audit': {'artifact': _binding(paths['semantic_audit']),
                        'status': 'RECORDED' if expected_formulas or expected_connections else 'NOT_PROVIDED',
                        'scope': 'declared_formula_connection_records_only',
                        'counts': {'formula_records': len(expected_formulas), 'connection_records': len(expected_connections)}},
                    'text_fit': {'artifact': _binding(paths['text_fit']), 'status': text_status,
                                 'scope': 'measured_live_text_only', 'counts': counts}},
                'semantic_recognition_performed': False, 'source_fidelity_evaluated': False,
                'visual_acceptance': 'pending'}
    manifest = _json_record(paths['manifest'])
    if 'source_inventory' in manifest:
        from .source_inventory import audit_source_inventory
        from .scene_compile import compile_scene
        path = _inside(run, run / 'source-inventory-audit.json')
        inventory = _json_record(path, strict_numbers_and_keys=True)
        paths['source_inventory_audit'] = path
        _require(_same_json(inventory, audit_source_inventory(manifest, config['asset_root'])),
                 'Source inventory diagnostic is stale or disagrees with frozen source evidence')
        _require(inventory['status'] != 'FAIL' and _same_json(semantic.get('source_inventory'), inventory),
                 'Source inventory failed or semantic diagnostics contain different evidence')
        compiled, _ = compile_scene(manifest, config.get('job', run), asset_root=config['asset_root'])
        _require(_same_json(resolved, compiled),
                 'Resolved scene disagrees with source-bound authoring objects')
        expected['reports']['source_inventory'] = {'artifact': _binding(path),
            'status': inventory['status'], 'scope': inventory['scope'], 'counts': inventory['coverage']}
    else:
        _require('source_inventory' not in semantic,
                 'Source inventory evidence lacks an authoring declaration')
    _require(_same_json(preview_audit.get('diagnostic_coverage'), expected),
             'Diagnostic coverage is missing, stale, or exceeds the actual declared/measured scope')
    return expected


def _canvas_clip_provenance(run, config, paths, manifest, resolved, preview_audit):
    """Recompute the narrow source/slide clipping proof from frozen inputs."""
    declared = 'source_canvas_clip' in manifest
    if not declared:
        _require('source_canvas_clip' not in resolved and
                 'source_canvas_clip_provenance_version' not in config and
                 (preview_audit is None or 'source_canvas_clip' not in preview_audit),
                 'Canvas clipping provenance has no source declaration')
        return None
    _require(type(config.get('source_canvas_clip_provenance_version')) is int and
             config['source_canvas_clip_provenance_version'] == 1 and not config.get('base'),
             'Canvas clipping requires standalone version-1 provenance')
    _require(_same_json(manifest['source_canvas_clip'], resolved.get('source_canvas_clip')),
             'Resolved canvas clipping declaration differs from the source manifest')
    _require(isinstance(preview_audit, dict), 'Canvas clipping requires final render provenance')
    source_report = _inside(run, run / 'source-canvas-clip.json')
    native_report = _inside(run, run / 'native-canvas-clip.json')
    asset_root = _inside(run, run / 'assets')
    try:
        from .source_canvas_clip import verify_source_canvas_clip, verify_native_canvas_clip
        replay = verify_source_canvas_clip(resolved, asset_root)
        _require(isinstance(replay, dict) and _same_json(_json_record(source_report), replay),
                 'Canvas clipping source proof is stale or disagrees with fresh replay')
        actual = verify_native_canvas_clip(paths['pptx'], resolved, replay)
        _require(isinstance(actual, dict) and _same_json(_json_record(native_report), actual),
                 'Canvas clipping native proof is stale or disagrees with the actual PPTX')
    except ImportError as error:
        raise ValueError('Canvas clipping verification requires its optional source dependencies') from error
    paths['source_canvas_clip'] = source_report
    paths['native_canvas_clip'] = native_report
    for role in ('source_pdf', 'source_svg'):
        record = manifest['source_canvas_clip'].get(role)
        _require(isinstance(record, dict) and isinstance(record.get('path'), str) and
                 not Path(record['path']).is_absolute(), 'Invalid canvas source evidence path')
        path = _inside(asset_root, asset_root / record['path'])
        _require(_binding(path)['sha256'] == record.get('sha256'), 'Canvas source evidence changed')
        paths['canvas_' + role] = path
    expected = {'schema_version': 1, 'source_receipt': _binding(source_report),
                'native_receipt': _binding(native_report),
                'resolved_scene': _binding(paths['resolved_scene']), 'pptx': _binding(paths['pptx'])}
    _require(_same_json(preview_audit.get('source_canvas_clip'), expected),
             'Render audit disagrees with verified canvas clipping evidence')
    return expected


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
    diagnostic_coverage = _diagnostic_provenance(run, config, paths, resolved, preview_audit)
    canvas_clip = _canvas_clip_provenance(run, config, paths, manifest, resolved, preview_audit)
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
    alpha = preview_audit.get('pdf_alpha_derivation') if preview_audit is not None else None
    if alpha is not None:
        _require(_same_json(delivery.get('pdf_alpha_derivation'), {
            **alpha, 'original_pdf': bindings['native_pdf'],
            'derived_pdf': bindings['native_pdf_derived'], 'receipt': bindings['native_pdf_alpha_receipt']}),
            'Delivery receipt disagrees with verified PDF derivation')
    else:
        _require('pdf_alpha_derivation' not in delivery,
                 'Delivery receipt contains an unrequested PDF derivation')
    if diagnostic_coverage is not None:
        _require(_same_json(delivery.get('diagnostic_coverage'), diagnostic_coverage),
                 'Delivery receipt disagrees with limited diagnostic coverage')
    _require((_same_json(delivery.get('source_canvas_clip'), canvas_clip) if canvas_clip is not None
              else 'source_canvas_clip' not in delivery),
             'Delivery receipt disagrees with verified canvas clipping evidence')
    return run, {'figure_id': manifest['id'], 'revision': manifest['revision']}, bindings, diagnostic_coverage


def prepare_output_review(run_dir):
    """Return an unperformed review template for exact, existing build artifacts.

    The template is not evidence of review.  Observe the listed source and both
    previews before filling it.  Snapshot identity includes any existing extra
    previews/comparison; their later removal, replacement or addition is stale.
    """
    run, identity, bindings, diagnostic_coverage = _build_bindings(run_dir)
    return {
        'schema_version': 1, 'kind': 'postbuild_visual_review',
        'run': str(run), 'build': identity, 'bindings': bindings,
        **({'diagnostic_coverage': diagnostic_coverage} if diagnostic_coverage is not None else {}),
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
                             'native_application_verification', 'recorded_at', 'diagnostic_coverage'},
             'Unknown output review fields; acceptance and verification must stay separate')
    _require(type(record.get('schema_version')) is int and record['schema_version'] == 1,
             'Unsupported output review schema')
    _require(record.get('kind') == 'postbuild_visual_review', 'Not a postbuild visual review')
    run, identity, bindings, diagnostic_coverage = _build_bindings(run_dir)
    _require((_same_json(record.get('diagnostic_coverage'), diagnostic_coverage) if diagnostic_coverage is not None
              else 'diagnostic_coverage' not in record), 'Output review diagnostic coverage is missing or stale')
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
