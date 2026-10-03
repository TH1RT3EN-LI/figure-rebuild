"""Validate frozen LaTeX formula assets and resolve faithful display placement.

The figure manifest pins its audit SHA256. Build consumes only job-confined
assets; it never reruns TeX, recognizes a source image, or needs source fonts.
The frozen audit retains the engine/font provenance and actual dependencies.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from PIL import Image

from .formula_render import (_embedded_base, _font_metadata_digest, _metrics, _source,
                             validate_expression, validate_outlined_svg)


HASH = re.compile(r'[0-9a-f]{64}\Z')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    if positive and value <= 0:
        raise ValueError(label + ' must be positive')
    return float(value)


def _sequence(value, size, label, positive=False):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f'{label} must have {size} numbers')
    return [_number(item, label, positive) for item in value]


def _box(value, label):
    if isinstance(value, dict):
        if set(value) != {'x', 'y', 'width', 'height'}:
            raise ValueError(label + ' must contain x, y, width and height')
        value = [value[key] for key in ('x', 'y', 'width', 'height')]
    return _sequence(value, 4, label)


def _box_record(values):
    return dict(zip(('x', 'y', 'width', 'height'), values))


def _relative(job, declared, base=None):
    if not isinstance(declared, str) or not declared:
        raise ValueError('Formula asset path is missing')
    path = Path(declared)
    resolved = path.resolve() if path.is_absolute() else ((base or job) / path).resolve()
    if not resolved.is_relative_to(job) or resolved == job:
        raise ValueError('Formula asset escapes the job directory')
    return resolved.relative_to(job).as_posix()


def _frozen(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Missing or escaping frozen formula asset: ' + relative)
    return path


def _fonts_metadata(audit, dependencies):
    """Check provenance relationships without opening external font sources."""
    if audit.get('engine') not in ('tectonic', 'pdflatex') or audit.get('shell_escape') is not False:
        raise ValueError('Formula audit must name a safe real TeX engine')
    if not isinstance(audit.get('engine_version'), str) or not HASH.fullmatch(audit.get('engine_sha256', '')):
        raise ValueError('Formula audit needs the engine version and hash')
    fonts, alphabets = audit.get('font_registry'), audit.get('alphabet_font_registry')
    if not isinstance(fonts, dict) or fonts.get('family') != 'Computer Modern':
        raise ValueError('Formula audit needs registered Computer Modern font metadata')
    names = set()
    programs = set()
    for registry in (fonts, alphabets):
        if registry is None:
            continue
        if not isinstance(registry, dict) or not HASH.fullmatch(registry.get('registry_sha256', '')):
            raise ValueError('Formula font registry hash is invalid')
        if registry is fonts and not HASH.fullmatch(registry.get('font_manifest_sha256', '')):
            raise ValueError('Formula math font manifest hash is invalid')
        if not isinstance(registry.get('files'), list) or not registry['files']:
            raise ValueError('Formula audit font files are missing')
        for record in registry['files']:
            if not isinstance(record, dict) or not HASH.fullmatch(record.get('sha256', '')):
                raise ValueError('Formula audit contains an invalid font hash')
            name = record.get('name')
            if not isinstance(name, str) or Path(name).name != name or name in names:
                raise ValueError('Formula audit contains invalid or duplicate font names')
            names.add(name)
            if name.lower().endswith('.pfb'):
                programs.add(name[:-4].upper())
            elif registry is alphabets:
                if not isinstance(record.get('postscript_name'), str) or name != record['postscript_name'] + '.otf':
                    raise ValueError('Formula OpenType identity is inconsistent')
                programs.add(record['postscript_name'])
    used = audit.get('registered_font_dependencies')
    used_alphabets = audit.get('registered_alphabet_font_dependencies', [])
    if not isinstance(used, list) or not used or not isinstance(used_alphabets, list):
        raise ValueError('Formula registered font dependencies are missing')
    for name in used + used_alphabets:
        if name not in names or (dependencies is not None and name not in dependencies):
            raise ValueError('Formula dependency is missing from its registered font metadata/log')
    embedded = audit.get('embedded_fonts')
    if not isinstance(embedded, list) or not embedded:
        raise ValueError('Formula embedded font audit is missing')
    used_programs = {name[:-4].upper() for name in used if name.endswith('.pfb')}
    used_programs.update(name[:-4] for name in used_alphabets if name.endswith('.otf'))
    for name in embedded:
        if not isinstance(name, str):
            raise ValueError('Formula embedded font name is invalid')
        base = _embedded_base(name)
        if base not in programs or base not in used_programs:
            raise ValueError('Formula embedded font has no registered dependency: ' + name)
    if audit.get('schema_version') == '2':
        if audit.get('font_metadata_sha256') != _font_metadata_digest(fonts, alphabets):
            raise ValueError('Formula font metadata hash changed')


def _pdf_structure(data):
    """Check PDF framing/xref without requiring a TeX/PDF tool at build time."""
    matches = list(re.finditer(br'startxref\s+(\d+)\s+%%EOF', data))
    if not data.startswith(b'%PDF-') or not matches or data[matches[-1].end():].strip():
        raise ValueError('Formula PDF has invalid framing or no final cross-reference')
    offset = int(matches[-1].group(1))
    if offset >= len(data) or not (data[offset:].startswith(b'xref') or re.match(br'\d+\s+\d+\s+obj\b', data[offset:])):
        raise ValueError('Formula PDF cross-reference offset is invalid')


def _geometry_metadata(audit, log):
    """Validate baseline and frame evidence from real TeX metrics and ink crop."""
    if audit.get('rotation_deg') not in (0, 90, 180, 270) or isinstance(audit.get('rotation_deg'), bool):
        raise ValueError('Formula audit rotation is invalid')
    if audit.get('schema_version') != '2':
        return
    metrics = _metrics(log)
    recorded = audit.get('tex_box')
    if not isinstance(recorded, dict) or set(recorded) != set(metrics) or any(
            abs(_number(recorded[key], 'Formula TeX metric') - metrics[key]) > 1e-8 for key in metrics):
        raise ValueError('Formula TeX metrics differ from its actual engine log')
    bbox = _sequence(audit.get('alpha_bbox_px'), 4, 'Formula generated ink bbox')
    page = _sequence(audit.get('rendered_page_pixels'), 2, 'Formula generated page size', True)
    if not all(float(value).is_integer() for value in bbox + page) or not (
            0 <= bbox[0] < bbox[2] <= page[0] and 0 <= bbox[1] < bbox[3] <= page[1]):
        raise ValueError('Formula ink bbox lies outside its generated page')
    expected_pixels = [bbox[2] - bbox[0], bbox[3] - bbox[1]]
    if audit['rotation_deg'] in (90, 270):
        expected_pixels.reverse()
    if expected_pixels != audit.get('png_pixels'):
        raise ValueError('Formula generated ink bbox/rotation differs from PNG pixels')
    dpi = _number(audit.get('dpi'), 'Formula DPI', True)
    padding = _number(audit.get('padding_pt'), 'Formula TeX padding')
    if not 0 <= padding <= 20:
        raise ValueError('Formula TeX padding is out of bounds')
    expected_origin = [padding * dpi / 72.27 - bbox[0],
                       (padding + metrics['height_pt']) * dpi / 72.27 - bbox[1]]
    origin = _sequence(audit.get('baseline_origin_ink_unrotated_px'), 2, 'Formula TeX origin')
    if any(abs(actual - expected) > 1e-6 for actual, expected in zip(origin, expected_origin)):
        raise ValueError('Formula baseline differs from TeX metrics and generated crop')
    if abs(_number(audit.get('baseline_ink_unrotated_px'), 'Formula baseline') - origin[1]) > 1e-6:
        raise ValueError('Formula baseline metrics are inconsistent')


def place_formula_asset(resolved, element=None):
    """Resolve contain or exact-em placement and check the PNG fallback sampling."""
    element = element or resolved['element']
    audit = resolved['audit']
    natural = _sequence(audit.get('natural_display_size_px'), 2, 'Formula natural size', True)
    pixels = _sequence(audit.get('png_pixels'), 2, 'Formula PNG size', True)
    em = _number(audit.get('font_size_px'), 'Formula em', True)
    font_size = _number(element.get('font_size', em), 'Formula font_size', True)
    scale = font_size / em
    anchor = element.get('baseline_anchor')
    if ('box' in element) == ('baseline_anchor' in element):
        raise ValueError('Formula needs exactly one box or baseline_anchor')
    if anchor is not None:
        if audit.get('rotation_deg') != 0:
            raise ValueError('Rotated formulas require box placement; baseline_anchor supports 0 degrees only')
        if not isinstance(anchor, dict) or set(anchor) != {'x', 'y'}:
            raise ValueError('Formula baseline_anchor must contain x and y')
        x = _number(anchor['x'], 'Formula baseline x')
        y = _number(anchor['y'], 'Formula baseline y')
        origin = audit.get('baseline_origin_ink_unrotated_px')
        if origin is None:
            # Legacy audit did not retain horizontal TeX origin. Never guess it.
            raise ValueError('Formula baseline placement needs a v2 audit with TeX origin metrics')
        origin = _sequence(origin, 2, 'Formula TeX origin')
        dpi = _number(audit.get('dpi'), 'Formula DPI', True)
        box = [x - origin[0] * 96 / dpi * scale,
               y - origin[1] * 96 / dpi * scale, natural[0] * scale, natural[1] * scale]
        baseline = {'x': x, 'y': y}
        allocation = None
    else:
        allocation = _box(element['box'], 'Formula box')
        if allocation[2] <= 0 or allocation[3] <= 0:
            raise ValueError('Formula box width and height must be positive')
        if 'font_size' not in element:
            scale = min(allocation[2] / natural[0], allocation[3] / natural[1])
            font_size = em * scale
        width, height = natural[0] * scale, natural[1] * scale
        if width > allocation[2] + 1e-6 or height > allocation[3] + 1e-6:
            raise ValueError('Formula font_size exceeds its box; increase the box explicitly')
        box = [allocation[0] + (allocation[2] - width) / 2,
               allocation[1] + (allocation[3] - height) / 2, width, height]
        baseline = None
        if audit.get('rotation_deg') == 0 and audit.get('baseline_origin_ink_unrotated_px') is not None:
            origin = _sequence(audit['baseline_origin_ink_unrotated_px'], 2, 'Formula TeX origin')
            factor = 96 / _number(audit.get('dpi'), 'Formula DPI', True) * scale
            baseline = {'x': box[0] + origin[0] * factor, 'y': box[1] + origin[1] * factor}
    sampling = min(pixels[0] / box[2], pixels[1] / box[3])
    minimum = max(8.0, _number(audit.get('min_sampling_scale', 8), 'Minimum formula sampling', True))
    if sampling + 1e-9 < minimum:
        raise ValueError(f'Formula final PNG fallback sampling {sampling:.6g}x is below required {minimum:.6g}x; rerender at higher DPI')
    return {'box': _box_record(box), 'allocation_box': _box_record(allocation) if allocation else None, 'font_size': font_size,
            'scale': scale, 'sampling_scale': sampling, 'min_sampling_scale': minimum,
            'baseline_anchor': baseline, 'aspect_ratio_preserved': True}


def resolve_formula_asset(element, job_dir, asset_root=None):
    """Verify a pinned formula audit and all job-confined frozen output bytes.

    Absolute legacy output paths are normalized against ``job_dir`` before any
    read. ``asset_root`` can name a frozen job mirror, so mutated originals are
    never consumed after the build snapshot has been established.
    """
    if not isinstance(element, dict) or element.get('kind') != 'formula':
        raise ValueError('Formula resolver expects a kind:formula element')
    job = Path(job_dir).expanduser().resolve()
    root = Path(asset_root or job).expanduser().resolve()
    declared = element.get('audit')
    if not isinstance(declared, str) or Path(declared).is_absolute():
        raise ValueError('Formula audit must be relative to the job directory')
    audit_relative = _relative(job, declared)
    audit_path = _frozen(root, audit_relative)
    expected = element.get('audit_sha256')
    if not isinstance(expected, str) or not HASH.fullmatch(expected) or _sha(audit_path) != expected:
        raise ValueError('Formula audit hash changed or is not pinned')
    try:
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError('Formula audit is malformed') from error
    if not isinstance(audit, dict) or audit.get('schema_version') not in ('1', '2') or audit.get('kind') != 'generated_latex':
        raise ValueError('Unsupported formula audit schema or source type')
    expression = validate_expression(audit.get('latex'), confirmed=audit.get('transcription_confirmed'))
    if audit.get('reference_crop_used') is not False:
        raise ValueError('Formula audit must explicitly exclude reference image crops')
    if 'latex' in element and element['latex'] != expression:
        raise ValueError('Formula element transcription disagrees with the frozen audit')
    representation = element.get('representation', 'svg')
    if representation not in ('svg', 'png'):
        raise ValueError('Formula representation must be svg or png')
    assets = audit.get('assets')
    required = {'tex', 'pdf', 'png'}
    legacy = audit['schema_version'] == '1'
    if not legacy:
        required.update(('svg', 'log', 'dependencies.txt'))
        if audit.get('asset_path_base') != 'audit_directory':
            raise ValueError('Formula v2 audit paths must be relative to its audit directory')
    elif representation == 'svg':
        raise ValueError('Legacy formula audits require explicit png representation; rerender for SVG')
    if not isinstance(assets, dict) or not required.issubset(assets):
        raise ValueError('Formula audit is missing required output hashes')
    hash_files = [{'path': audit_relative, 'sha256': expected}]
    paths = {}
    audit_original_parent = (job / audit_relative).parent
    for extension, record in assets.items():
        if extension not in ('tex', 'pdf', 'png', 'svg', 'log', 'dependencies.txt'):
            raise ValueError('Unsupported formula audit asset type: ' + str(extension))
        if not isinstance(record, dict) or not HASH.fullmatch(record.get('sha256', '')):
            raise ValueError('Formula output hash is invalid')
        if not legacy and (not isinstance(record.get('path'), str) or Path(record['path']).is_absolute()):
            raise ValueError('Formula v2 output path must be relative to its audit directory')
        relative = _relative(job, record.get('path'), audit_original_parent)
        path = _frozen(root, relative)
        if _sha(path) != record['sha256']:
            raise ValueError('Formula asset hash changed: ' + relative)
        paths[extension] = relative
        hash_files.append({'path': relative, 'sha256': record['sha256']})
    if len(set(paths.values())) != len(paths):
        raise ValueError('Formula outputs must have distinct paths')
    tex = _frozen(root, paths['tex']).read_text(encoding='utf-8')
    parameters = (expression, audit.get('font_size_px'), audit.get('color'), audit.get('padding_pt', 1),
                  audit.get('design_size'), audit.get('stroke_width_px', 0), audit.get('engine'),
                  audit.get('alphabet_font_registry') is not None)
    try:
        canonical = _source(*parameters)
    except (TypeError, ValueError, IndexError) as error:
        raise ValueError('Formula audit TeX parameters are invalid') from error
    # Old generated TeX retained lowercase hex color spelling while its audit
    # normalized it. Color hex case changes no rendering and is not code.
    normalized_tex = re.sub(r'(\\definecolor\{ink\}\{HTML\}\{)([0-9a-fA-F]{6})(\})',
                            lambda match: match[1] + match[2].upper() + match[3], tex)
    if normalized_tex != canonical:
        raise ValueError('Formula TeX no longer matches the audited expression and font parameters')
    _pdf_structure(_frozen(root, paths['pdf']).read_bytes())
    pixels = _sequence(audit.get('png_pixels'), 2, 'Formula PNG size', True)
    natural = _sequence(audit.get('natural_display_size_px'), 2, 'Formula natural size', True)
    dpi = _number(audit.get('dpi'), 'Formula DPI', True)
    if any(abs(pixels[index] * 96 / dpi - natural[index]) > 1e-6 for index in (0, 1)):
        raise ValueError('Formula natural frame disagrees with PNG DPI/geometry')
    try:
        with Image.open(_frozen(root, paths['png'])) as image:
            image.load()
            if image.format != 'PNG' or image.mode != 'RGBA' or list(image.size) != pixels:
                raise ValueError('Formula PNG bytes/dimensions/alpha mode disagree with the audit')
            if image.getchannel('A').getbbox() != (0, 0, image.width, image.height):
                raise ValueError('Formula PNG is not ink-tight')
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('Formula PNG is invalid') from error
    dependencies = _frozen(root, paths['dependencies.txt']).read_text(encoding='utf-8') if 'dependencies.txt' in paths else None
    log = _frozen(root, paths['log']).read_text(encoding='utf-8') if 'log' in paths else None
    _geometry_metadata(audit, log)
    _fonts_metadata(audit, dependencies)
    vector = None
    if 'svg' in paths:
        vector = validate_outlined_svg(_frozen(root, paths['svg']).read_bytes())
        expected_viewbox = audit.get('vector_effective_viewbox')
        if not isinstance(expected_viewbox, list) or len(expected_viewbox) != 4 or any(
                abs(_number(actual, 'SVG viewBox') - _number(expected_value, 'Audited SVG viewBox')) > 1e-7
                for actual, expected_value in zip(vector['viewbox'], expected_viewbox)):
            raise ValueError('Formula SVG viewBox differs from its audit')
        if vector['viewbox'][:2] != [0, 0] or any(abs(vector['viewbox'][index + 2] - natural[index]) > 1e-7 for index in (0, 1)):
            raise ValueError('Formula SVG and PNG ink frames differ')
        geometry = audit.get('vector_geometry')
        if not isinstance(geometry, dict) or geometry.get('embeddedfont_outlines') is not True or geometry.get('external_references') is not False:
            raise ValueError('Formula SVG needs an explicit embedded font outline audit')
        if geometry.get('rotation_deg') != audit.get('rotation_deg') or geometry.get('viewbox') != expected_viewbox:
            raise ValueError('Formula SVG geometry disagrees with the audited rotation/frame')
        if 'svg_intrinsic_pixels' in audit or geometry.get('intrinsic_pixel_policy'):
            intrinsic = vector['intrinsic_pixels']
            if intrinsic != pixels or intrinsic != audit.get('svg_intrinsic_pixels') or intrinsic != geometry.get('intrinsic_pixels'):
                raise ValueError('Formula SVG intrinsic dimensions disagree with PNG fallback sampling')
            recorded = _number(audit.get('svg_intrinsic_rasterization_scale'), 'Formula SVG intrinsic sampling', True)
            if abs(recorded - vector['intrinsic_rasterization_scale']) > 1e-7 or abs(recorded - _number(geometry.get('intrinsic_rasterization_scale'), 'Formula SVG geometry sampling', True)) > 1e-7:
                raise ValueError('Formula SVG intrinsic sampling differs from its audit')
            if recorded + 1e-7 < 8 or geometry.get('intrinsic_pixel_policy') != 'match_png_fallback':
                raise ValueError('Formula SVG intrinsic sampling must match the high-density PNG fallback')
    result = {'element': element, 'audit': audit, 'audit_path': audit_relative,
              'representation': representation, 'selected_path': paths[representation],
              'png_path': paths['png'], 'svg_path': paths.get('svg'), 'paths': paths,
              'hash_files': hash_files, 'legacy_audit': legacy, 'vector_geometry': vector}
    result['placement'] = place_formula_asset(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--asset-root', help='Optional frozen job mirror root')
    parser.add_argument('--input', required=True, help='JSON array of formula elements')
    args = parser.parse_args()
    try:
        elements = json.loads(Path(args.input).read_text(encoding='utf-8'))
        if not isinstance(elements, list):
            raise ValueError('Formula input must be an array')
        results = [resolve_formula_asset(element, args.job, args.asset_root) for element in elements]
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(2, 'Formula asset error: ' + str(error) + '\n')
    print(json.dumps({'formulas': results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
