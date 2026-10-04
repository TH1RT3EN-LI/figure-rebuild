"""Opt-in, hash-bound LibreOffice previews of a finalized single-slide PPTX.

LibreOffice and fonts are supplied by the caller, never installed by this module.
Rendering is not visual acceptance or native application playback verification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


BACKENDS = ('artifact', 'libreoffice')
PROVENANCE_VERSION = 1


def binding(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    if not data:
        raise ValueError('Empty preview evidence: ' + str(path))
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def validate_backend(value):
    if not isinstance(value, str) or value not in BACKENDS:
        raise ValueError('Unknown preview backend: ' + repr(value))
    return value


def validate_pdf_alpha_policy(value, backend):
    from .pdf_binary_alpha import POLICY
    if value is not None and (type(value) is not str or value != POLICY or backend != 'libreoffice'):
        raise ValueError('PDF alpha derivation requires the explicit binary-alpha policy and LibreOffice backend')
    return value


def validate_profile(value, base, *, check_executables=True):
    """Normalize the optional external command profile; do not invoke it."""
    allowed = {'command', 'environment', 'fc_match', 'timeout_seconds'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Native preview profile has unknown fields or is not an object')
    command = value.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s or '\0' in s for s in command):
        raise ValueError('Native preview command must be a nonempty argv list')
    command = list(command)
    for argument in command[1:]:
        if argument.lower().startswith(('-env:userinstallation', '--convert-to', '--outdir')):
            raise ValueError('Native preview adapter owns profile and output arguments')
    env = value.get('environment', {})
    protected = {'HOME', 'XDG_CONFIG_HOME', 'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT'}
    if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
                                      or k in protected or not isinstance(v, str) or '\0' in v for k, v in env.items()):
        raise ValueError('Invalid or protected native preview environment override')
    timeout = value.get('timeout_seconds', 120)
    if type(timeout) not in (int, float) or not 0 < timeout <= 600 or not math.isfinite(timeout):
        raise ValueError('Native preview timeout_seconds must be in (0, 600]')

    def executable(raw, label):
        if not isinstance(raw, str) or not raw or '\0' in raw:
            raise ValueError('Native preview requires ' + label)
        path = Path(raw).expanduser()
        path = Path(os.path.abspath(path if path.is_absolute() else Path(base) / path))
        if check_executables and (not path.is_file() or not os.access(path, os.X_OK)):
            raise ValueError('Native preview executable is missing: ' + label)
        return str(path)

    command[0] = executable(command[0], 'command[0]')
    return {'command': command, 'environment': dict(env),
            'fc_match': executable(value.get('fc_match'), 'fc_match'), 'timeout_seconds': timeout}


def preflight(runtime):
    """Check only dependencies of an explicitly selected native backend."""
    profile = validate_profile(runtime.get('native_preview'), Path.cwd())
    if os.name != 'posix':
        raise ValueError('LibreOffice preview currently requires POSIX and Fontconfig')
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('LibreOffice preview requires PyMuPDF in the configured Python (install the source extra)') from exc
    return profile, pymupdf


def _execute(command, environment, timeout, commands):
    record = {'argv': command, 'environment_overrides': environment,
              'environment_unset': ['FONTCONFIG_SYSROOT'],
              'started_at': datetime.now(timezone.utc).isoformat()}
    inherited = os.environ.copy()
    inherited.pop('FONTCONFIG_SYSROOT', None)
    inherited.update(environment)
    process = subprocess.Popen(command, env=inherited, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        record['timed_out'] = True
    record.update(returncode=process.returncode,
                  stdout=stdout.decode('utf-8', errors='replace'),
                  stderr=stderr.decode('utf-8', errors='replace'),
                  finished_at=datetime.now(timezone.utc).isoformat())
    commands.append(record)
    if record.get('timed_out'):
        raise ValueError('Native preview command timed out: ' + command[0])
    if process.returncode:
        raise ValueError('Native preview command failed: ' + command[0] + ': ' + record['stderr'][-2000:])
    return record['stdout']


def _pattern_family(family):
    return ''.join('\\' + c if c in '\\,:-' else c for c in family)


def _font_environment(run, directory, profile, commands):
    audit_path = run / 'font-audit.json'
    if not audit_path.resolve().is_relative_to(run):
        raise ValueError('Font audit escapes the immutable build run')
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    if not isinstance(audit, list) or not audit:
        raise ValueError('Native preview requires a nonempty font audit')
    fonts = directory / 'fonts'
    fonts.mkdir()
    cache = directory / 'font-cache'
    cache.mkdir()
    config_path = directory / 'fonts.conf'
    config = ET.Element('fontconfig')
    ET.SubElement(config, 'reset-dirs')
    ET.SubElement(config, 'dir').text = str(fonts)
    ET.SubElement(config, 'cachedir').text = str(cache)
    config_path.write_bytes(b'<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n' + ET.tostring(config))
    environment = {**profile['environment'], 'FONTCONFIG_FILE': str(config_path),
                   'FONTCONFIG_PATH': str(directory), 'SAL_USE_VCLPLUGIN': 'svp'}
    evidence = {'font_audit': binding(audit_path), 'native_fontconfig': binding(config_path)}
    prepared = []
    for i, face in enumerate(audit):
        if not isinstance(face, dict) or not isinstance(face.get('family'), str) or face.get('role') not in ('regular', 'bold', 'italic', 'boldItalic'):
            raise ValueError('Invalid registered font record for native preview')
        source = Path(face['renderer']).resolve()
        if not source.is_relative_to(run) or binding(source)['sha256'] != face.get('renderer_sha256'):
            raise ValueError('Registered renderer font is outside the run or its SHA256 changed')
        copied = fonts / (str(i) + source.suffix)
        copied.write_bytes(source.read_bytes())
        if binding(copied)['sha256'] != face['renderer_sha256']:
            raise ValueError('Font changed while preparing native preview')
        evidence['native_font_' + str(i)] = binding(copied)
        prepared.append((face, copied))
    resolutions = []
    for face, copied in prepared:
        weight = 200 if face.get('bold') else 80
        slant = 110 if face.get('style_class') == 'oblique' else (100 if face.get('italic') else 0)
        pattern = f"{_pattern_family(face['family'])}:weight={weight}:slant={slant}"
        output = _execute([profile['fc_match'], '-f', '%{file}\n%{index}\n', pattern],
                          environment, profile['timeout_seconds'], commands).splitlines()
        if len(output) != 2 or output[1] != '0' or Path(output[0]).resolve() != copied.resolve() or binding(output[0])['sha256'] != face['renderer_sha256']:
            raise ValueError('Fontconfig substituted the registered face: ' + face['family'] + ' ' + face['role'])
        resolutions.append({'family': face['family'], 'role': face['role'], 'pattern': pattern,
                            'selected': binding(copied), 'face_index': 0,
                            'postscript_names': face.get('postscript_names', [])})
    return environment, evidence, resolutions


def _pdf_font_name(name):
    return re.sub(r'[^a-z0-9]', '', re.sub(r'^[A-Z]{6}\+', '', name).lower())


def _image_evidence(source, pdf, page):
    """Record actual exported encodings; reject new lossy compressed streams."""
    import io
    from PIL import Image
    media, original_streams = [], set()
    with zipfile.ZipFile(source) as package:
        for name in package.namelist():
            if not name.startswith('ppt/media/'):
                continue
            data = package.read(name)
            digest = hashlib.sha256(data).hexdigest()
            try:
                with Image.open(io.BytesIO(data)) as img:
                    media.append({'part': name, 'sha256': digest, 'format': img.format,
                                  'pixel_dimensions': list(img.size)})
                    if img.format in ('JPEG', 'JPEG2000'):
                        original_streams.add(digest)
            except (OSError, ValueError):
                media.append({'part': name, 'sha256': digest, 'format': 'non-raster-or-unsupported'})
    images = []
    for item in page.get_images(full=True):
        xref, smask, width, height = item[:4]
        actual_filter = pdf.xref_get_key(xref, 'Filter')[1]
        raw_hash = hashlib.sha256(pdf.xref_stream_raw(xref)).hexdigest()
        lossy = 'DCTDecode' in actual_filter or 'JPXDecode' in actual_filter
        if lossy and raw_hash not in original_streams:
            raise ValueError('LibreOffice PDF introduced an unproven lossy image stream: ' + actual_filter)
        placements = []
        for rectangle, matrix in page.get_image_rects(xref, transform=True):
            physical_width = math.hypot(matrix.a, matrix.b)
            physical_height = math.hypot(matrix.c, matrix.d)
            placements.append({'rect_points': list(rectangle),
                               'transform': list(matrix),
                               'effective_dpi': [width * 72 / physical_width if physical_width else None,
                                                 height * 72 / physical_height if physical_height else None]})
        images.append({'xref': xref, 'soft_mask_xref': smask, 'filter': actual_filter,
                       'pixel_dimensions': [width, height], 'stream_sha256': raw_hash,
                       'lossy_source_bytes_preserved': lossy and raw_hash in original_streams,
                       'placements': placements})
        if smask:
            mask_filter = pdf.xref_get_key(smask, 'Filter')[1]
            if 'DCTDecode' in mask_filter or 'JPXDecode' in mask_filter:
                raise ValueError('LibreOffice PDF introduced an unproven compressed alpha mask')
            images[-1]['soft_mask'] = {'filter': mask_filter,
                                      'pixel_dimensions': [int(pdf.xref_get_key(smask, key)[1]) for key in ('Width', 'Height')],
                                      'stream_sha256': hashlib.sha256(pdf.xref_stream_raw(smask)).hexdigest()}
    return {'ppt_media': media, 'pdf_images': images,
            'limitation': 'Pixel sizes, encodings and placement DPI are evidence; renderer-generated images are not automatically mapped one-to-one to source assets.'}


def pdf_export_options():
    return {name: {'type': 'boolean', 'value': value} for name, value in (
        ('ExportHiddenSlides', True), ('ExportNotesPages', False), ('ExportOnlyNotesPages', False),
        ('UseLosslessCompression', True), ('ReduceImageResolution', False))}


def png_export_options(width, height):
    # Impress accepts explicit long strings. The output is kept byte-for-byte.
    return {'PixelWidth': {'type': 'long', 'value': str(width)},
            'PixelHeight': {'type': 'long', 'value': str(height)},
            'Translucent': {'type': 'boolean', 'value': False},
            'Compression': {'type': 'long', 'value': '6'}}


def conversion_command(profile, directory, token, format_name, options, source):
    return [*profile['command'], '-env:UserInstallation=' + (directory / ('profile-' + token)).as_uri(),
            '--headless', '--convert-to', format_name + ':' + json.dumps(options, separators=(',', ':')),
            '--outdir', str(directory / token), str(source)]


def render(config):
    """Export direct native PNGs; inspect a separate PDF for font/image evidence."""
    if config.get('base'):
        raise ValueError('LibreOffice preview does not yet support base-deck slide mapping')
    if validate_backend(config.get('preview_backend')) != 'libreoffice':
        raise ValueError('Native renderer requires explicit libreoffice preview backend')
    alpha_policy = validate_pdf_alpha_policy(config.get('pdf_alpha_derivation'), 'libreoffice')
    profile, fitz = preflight(config['runtime'])
    run = Path(config['run']).resolve()
    source = run / 'validated-output' / 'reconstruction.pptx'
    if not source.resolve().is_relative_to(run):
        raise ValueError('Finalized PPTX escapes the immutable build run')
    before = binding(source)
    from .package import inspect_pptx
    deck = inspect_pptx(source)
    if len(deck['slides']) != 1:
        raise ValueError('LibreOffice preview currently supports exactly one slide')
    for scale in (1, 2, 4):
        if (run / f'preview-{scale}x.png').exists():
            raise ValueError('Native preview destination already exists')
    directory = run / 'native-preview'
    directory.mkdir()  # never reuse a previous conversion/profile/cache
    commands = []
    commands_path = directory / 'commands.json'
    try:
        environment, evidence, resolutions = _font_environment(run, directory, profile, commands)
        command_file = Path(profile['command'][0])
        command_snapshot = directory / 'command-executable.bin'
        command_identity = {'path': str(command_file), 'sha256': binding(command_file)['sha256']}
        command_snapshot.write_bytes(command_file.read_bytes())
        evidence['native_command_executable'] = binding(command_snapshot)
        if evidence['native_command_executable']['sha256'] != command_identity['sha256']:
            raise ValueError('Native renderer executable changed while snapshotting')
        profile_uri = (directory / 'profile-version').as_uri()
        prefix = [*profile['command'], '-env:UserInstallation=' + profile_uri, '--headless']
        version = _execute([*prefix, '--version'], environment, profile['timeout_seconds'], commands).strip()
        if not re.search(r'LibreOffice\s+\d+\.\d+', version):
            raise ValueError('Native preview command did not identify a LibreOffice version')
        pdf_dir = directory / 'pdf'
        pdf_dir.mkdir()
        export_options = pdf_export_options()
        _execute(conversion_command(profile, directory, 'pdf', 'pdf:impress_pdf_Export', export_options, source),
                 environment, profile['timeout_seconds'], commands)
        if binding(source) != before:
            raise ValueError('Finalized PPTX changed during native preview conversion')
        pdf_path = pdf_dir / 'reconstruction.pdf'
        if not pdf_path.resolve().is_relative_to(directory):
            raise ValueError('Native PDF escapes the immutable build run')
        evidence['native_pdf'] = binding(pdf_path)
        dimensions = deck['slide_size_emu']
        width, height = dimensions['cx'] / 12700, dimensions['cy'] / 12700
        expected_names = {_pdf_font_name(name) for item in resolutions for name in item['postscript_names']}
        with fitz.open(pdf_path) as pdf:
            if not pdf.is_pdf or pdf.is_encrypted or len(pdf) != 1:
                raise ValueError('Native preview PDF must contain exactly one unencrypted page')
            page = pdf[0]
            if page.rotation or abs(page.rect.width - width) > .02 or abs(page.rect.height - height) > .02:
                raise ValueError('Native preview PDF page geometry does not match the final PPTX')
            actual_fonts = sorted({record[3] for record in page.get_fonts(full=True)})
            if any(_pdf_font_name(name) not in expected_names for name in actual_fonts):
                raise ValueError('LibreOffice PDF contains an unregistered/substituted font: ' + ', '.join(actual_fonts))
            image_audit = _image_evidence(source, pdf, page)
            page_geometry = list(page.rect)
        derivation = {}
        if alpha_policy:
            from .pdf_binary_alpha import derive_binary_alpha_pdf
            derived_directory = directory / 'derived-pdf'
            derived_directory.mkdir()
            derived_pdf = derived_directory / 'binary-alpha-white-matte.pdf'
            receipt_path = derived_directory / 'receipt.json'
            receipt = derive_binary_alpha_pdf(pdf_path, derived_pdf)
            _save(receipt_path, receipt)
            evidence['native_pdf_derived'] = binding(derived_pdf)
            evidence['native_pdf_alpha_receipt'] = binding(receipt_path)
            derivation['pdf_alpha_derivation'] = {
                'policy': alpha_policy, 'source_role': 'native_pdf',
                'derived_role': 'native_pdf_derived', 'receipt_role': 'native_pdf_alpha_receipt',
                'transformed_masks': len(receipt['transformed_masks']),
                'retained_masks': len(receipt['retained_masks']),
                'raw_export_replaced': False, 'raw_previews_derived_from_pdf': False,
                'rgb_alpha_filtering_error_bound_proved': False}
        from PIL import Image
        sizes, png_exports = {}, {}
        for scale in (1, 2, 4):
            expected = (round(dimensions['cx'] / 9525 * scale), round(dimensions['cy'] / 9525 * scale))
            token = f'png-{scale}x'
            png_dir = directory / token
            png_dir.mkdir()
            options = png_export_options(*expected)
            command_index = len(commands)
            _execute(conversion_command(profile, directory, token, 'png:impress_png_Export', options, source),
                     environment, profile['timeout_seconds'], commands)
            if binding(source) != before:
                raise ValueError('Finalized PPTX changed during direct PNG export')
            png_path = png_dir / 'reconstruction.png'
            if not png_path.resolve().is_relative_to(png_dir):
                raise ValueError('Native PNG escapes its export directory')
            exported = binding(png_path)
            with Image.open(png_path) as png:
                if png.format != 'PNG' or png.size != expected:
                    raise ValueError(f'Native preview pixel dimensions differ from the PPTX: {png.size}, expected {expected}')
                png.verify()
            destination = run / f'preview-{scale}x.png'
            destination.write_bytes(png_path.read_bytes())
            if binding(destination)['sha256'] != exported['sha256']:
                raise ValueError('Native preview differs from actual exported PNG bytes')
            role = f'native_png_{scale}x'
            evidence[role] = exported
            sizes[str(scale)] = list(expected)
            png_exports[str(scale)] = {'evidence_role': role, 'input_pptx': before,
                                       'fontconfig_sha256': evidence['native_fontconfig']['sha256'],
                                       'options': options, 'command_index': command_index}
        if binding(source) != before or binding(pdf_path) != evidence['native_pdf']:
            raise ValueError('Native preview input changed during export')
        if binding(command_file)['sha256'] != command_identity['sha256']:
            raise ValueError('Native renderer executable changed during conversion')
        for item in evidence.values():
            if binding(item['path']) != item:
                raise ValueError('Native preview evidence changed during rendering')
        _save(commands_path, commands)
        return {**derivation, 'renderer': 'LibreOffice Impress', 'renderer_backend': 'headless_direct_png',
                'libreoffice_version': version, 'libreoffice_command': profile['command'],
                'pdf_export_options': export_options,
                'command_executable': command_identity,
                'raw_preview_format': 'impress_png_Export',
                'pdf_inspector': 'PyMuPDF', 'pdf_inspector_version': fitz.__version__,
                'pdf_role': 'font_and_image_evidence_only', 'pixel_density_dpi': [96, 192, 384], 'source_media_bytes_modified': False,
                'preview_scales': [1, 2, 4], 'raw_diagnostic_scale': 1,
                'application_playback_verified': False,
                'native_render': {'input_before': before, 'input_after': binding(source),
                                  'slide_id': deck['slides'][0]['slide_id'], 'page_index': 0,
                                  'page_count': 1, 'page_rect_points': page_geometry,
                                  'png_exports': png_exports,
                                  'slide_size_emu': dimensions, 'pixel_dimensions': sizes,
                                  'pdf_fonts': actual_fonts, 'font_resolutions': resolutions},
                'image_audit': image_audit,
                'evidence': {**evidence, 'native_commands': binding(commands_path)},
                'preview_limitations': [{'code': 'native_application_specific_rendering', 'status': 'needs_review',
                                        'detail': 'This is LibreOffice direct PNG export appearance, not PowerPoint/WPS verification. PDF evidence is a separate export and is not the source of raw previews. Native miter limits, default joins, text spacing and other application differences remain subject to actual visual review.'}]}
    finally:
        _save(commands_path, commands)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        if args.preflight:
            profile, fitz = preflight(config)
            result = {'status': 'PASS', 'profile': profile, 'pymupdf': fitz.__version__}
        else:
            result = render(config)
            result['evidence']['native_commands'] = binding(result['evidence']['native_commands']['path'])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, 'LibreOffice preview: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
