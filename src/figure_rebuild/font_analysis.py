"""Read author PDF text metrics and verify supplied font-registry identities.

PyMuPDF is optional. This module never changes the PDF, font binaries, scene,
or registration; any PDF-to-source transform must be supplied explicitly.
Vector outlines have no reliable font metadata and are never guessed here.
"""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path


def _name(value):
    """Remove a PDF subset prefix without treating similar families as equal."""
    return re.sub(r'^[A-Z]{6}\+', '', value or '').casefold()


def _style(value):
    canonical = re.sub(r'[\s_-]+', '', value.casefold())
    return 'regular' if canonical in ('normal', 'roman', 'regular') else canonical


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    return float(value)


def _matrix(transform):
    if transform is None:
        return None
    if not isinstance(transform, (list, tuple)) or len(transform) != 2 or any(
            not isinstance(row, (list, tuple)) or len(row) != 3 for row in transform):
        raise ValueError('source_transform must be a 2 x 3 PDF-point-to-source-pixel matrix')
    rows = [[_finite(value, 'source_transform coefficient') for value in row]
            for row in transform]
    if abs(rows[0][0] * rows[1][1] - rows[0][1] * rows[1][0]) < 1e-12:
        raise ValueError('source_transform must be invertible')
    return rows


def _point(point, transform):
    x, y = point
    return [transform[0][0] * x + transform[0][1] * y + transform[0][2],
            transform[1][0] * x + transform[1][1] * y + transform[1][2]]


def _box(box, transform):
    x0, y0, x1, y1 = box
    points = [_point(point, transform) for point in
              ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def read_font_registry(path):
    """Verify SFNT faces using their actual names, hash, and TTC face index.

    Registry ``fonts`` entries use id/path/family/style/face_index/sha256.
    Relative font paths are resolved against the registry file's directory.
    Invalid entries remain explicit errors and cannot produce a match.
    """
    from fontTools.ttLib import TTFont, TTLibError
    path = Path(path).resolve()
    registry = json.loads(path.read_text(encoding='utf-8'))
    definitions = registry.get('fonts')
    if not isinstance(definitions, list):
        raise ValueError('Font registry requires a fonts list')
    result = []
    for entry in definitions:
        report = {'id': entry.get('id') if isinstance(entry, dict) else None,
                  'status': 'invalid'}
        try:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                raise ValueError('Registry face requires a path')
            if not isinstance(entry.get('id'), str) or not entry['id'].strip():
                raise ValueError('Registry face requires a stable nonempty id')
            for field in ('family', 'style'):
                if field in entry and (not isinstance(entry[field], str) or not entry[field].strip()):
                    raise ValueError('Registry ' + field + ' must be a nonempty string')
            source = Path(entry['path'])
            source = (path.parent / source).resolve() if not source.is_absolute() else source.resolve()
            data = source.read_bytes()
            collection = data[:4] == b'ttcf'
            if collection and 'face_index' not in entry:
                raise ValueError('TTC/OTC requires an explicit face_index')
            index = entry.get('face_index', 0)
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError('face_index must be a nonnegative integer')
            if not collection and index != 0:
                raise ValueError('Noncollection face_index must be zero')
            digest = hashlib.sha256(data).hexdigest()
            expected = entry.get('sha256')
            if expected is not None and (not isinstance(expected, str) or
                    not re.fullmatch('[a-fA-F0-9]{64}', expected) or expected.lower() != digest):
                raise ValueError('Font SHA256 mismatch or invalid digest')
            with TTFont(str(source), fontNumber=index if collection else -1) as font:
                names = {}
                for name_id in (1, 2, 4, 6, 16, 17):
                    values = set()
                    for record in font['name'].names:
                        if record.nameID == name_id:
                            try:
                                values.add(record.toUnicode().strip())
                            except (UnicodeError, LookupError):
                                continue
                    names[name_id] = sorted(value for value in values if value)
                families = names[16] or names[1]
                styles = names[17] or names[2]
                if not families:
                    raise ValueError('Font has no family metadata')
                requested_family = entry.get('family')
                if requested_family and requested_family.casefold() not in {
                        value.casefold() for value in families + names[1]}:
                    raise ValueError('Registry family does not match font binary')
                requested_style = entry.get('style')
                if requested_style and _style(requested_style) not in {_style(value) for value in styles}:
                    raise ValueError('Registry style does not match font binary')
                report.update(status='verified', fontfile=str(source), face_index=index,
                              sha256=digest, expected_hash_verified=expected is not None,
                              family=requested_family or families[0],
                              style=requested_style or (styles[0] if styles else 'unknown'),
                              binary_family_names=families, binary_style_names=styles,
                              postscript_names=names[6], full_names=names[4])
        except (OSError, ValueError, KeyError, IndexError, TypeError, TTLibError) as error:
            report['reason'] = str(error)
        result.append(report)
    return result


def match_font(pdf_name, registry_faces):
    verified = [face for face in registry_faces if face['status'] == 'verified']
    for field, method in (('postscript_names', 'exact_postscript_name'),
                          ('full_names', 'exact_full_name')):
        matches = [face for face in verified if _name(pdf_name) in {
            _name(value) for value in face[field]}]
        if len(matches) == 1:
            return {'status': 'verified_match', 'method': method, 'face': matches[0]}
        if len(matches) > 1:
            return {'status': 'ambiguous', 'method': method,
                    'candidate_ids': [face['id'] for face in matches]}
    return {'status': 'unresolved', 'reason': 'No exact verified PostScript/full-name match; no fallback guessed'}


def analyze_pdf(pdf_path, *, page=1, region=None, source_transform=None, font_registry=None):
    """Return PDF metrics in points and optional explicitly mapped source pixels.

    ``page`` is one-based. ``region`` is x0/y0/x1/y1 in PDF page coordinates.
    Span bboxes use font metrics, not ink bounds. Origin is the real baseline
    anchor; line_direction preserves rotated text. Vector-path counts are not
    proof of glyph classification and their fonts remain unknown.
    """
    try:
        import pymupdf
    except ImportError as error:
        raise ValueError('PDF font analysis requires optional requirements-source.txt') from error
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError('page must be a positive one-based integer')
    transform = _matrix(source_transform)
    if region is not None:
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError('region requires x0 y0 x1 y1 in PDF points')
        region = [_finite(value, 'region coordinate') for value in region]
        if region[2] <= region[0] or region[3] <= region[1]:
            raise ValueError('region must have positive width and height')
    path = Path(pdf_path).resolve()
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    faces = read_font_registry(font_registry) if font_registry else []
    with pymupdf.open(path) as document:
        if page > len(document):
            raise ValueError('page exceeds PDF page count')
        sheet = document[page - 1]
        clip = pymupdf.Rect(region) if region else sheet.rect
        records = []
        for block_index, block in enumerate(sheet.get_text('dict', clip=clip)['blocks']):
            for line_index, line in enumerate(block.get('lines', [])):
                for span_index, span in enumerate(line['spans']):
                    text = span.get('text', '')
                    if not text:
                        continue
                    record = {'id': f'p{page}-b{block_index}-l{line_index}-s{span_index}',
                              'text': text, 'pdf_fontname': span['font'],
                              'pdf_font_size_pt': span['size'],
                              'pdf_baseline_anchor_pt': list(span['origin']),
                              'pdf_metric_bbox_pt': list(span['bbox']),
                              'bbox_kind': 'font_metric_not_ink',
                              'line_direction': list(line.get('dir', (1, 0))),
                              'font_flags': span.get('flags'),
                              'ascender_em': span.get('ascender'), 'descender_em': span.get('descender'),
                              'font_registry_match': match_font(span['font'], faces)}
                    if transform:
                        record['source_baseline_anchor_px'] = _point(span['origin'], transform)
                        record['source_metric_bbox_px'] = _box(span['bbox'], transform)
                        # Direction-specific em vectors also handle anisotropic scale/rotation.
                        dx, dy = line.get('dir', (1, 0))
                        size = span['size']
                        record['source_em_horizontal_vector_px'] = [
                            size * (transform[0][0] * dx + transform[0][1] * dy),
                            size * (transform[1][0] * dx + transform[1][1] * dy)]
                        record['source_em_vertical_vector_px'] = [
                            size * (-transform[0][0] * dy + transform[0][1] * dx),
                            size * (-transform[1][0] * dy + transform[1][1] * dx)]
                    records.append(record)
        drawing_count = sum(bool(pymupdf.Rect(item['rect']).intersects(clip))
                            for item in sheet.get_drawings())
        image_count = sum(bool(pymupdf.Rect(item['bbox']).intersects(clip))
                          for item in sheet.get_image_info())
        resource_fonts = [{'xref': value[0], 'font_type': value[2], 'basefont': value[3],
                           'resource_name': value[4], 'scope': 'page_resource_not_region_usage'}
                          for value in sheet.get_fonts(full=True)]
        report = {'schema_version': '1', 'source': {'pdf': str(path), 'sha256': original_hash,
                   'page': page, 'page_size_pt': [sheet.rect.width, sheet.rect.height],
                   'region_pdf_pt': list(clip), 'original_preserved': True},
                  'source_transform': transform, 'transform_origin': 'explicit_caller_parameters' if transform else 'not_supplied',
                  'font_registry': str(Path(font_registry).resolve()) if font_registry else None,
                  'registry_faces': faces, 'text_spans': records,
                  'region_observations': {'text_span_count': len(records),
                      'vector_drawing_count': drawing_count, 'raster_image_count': image_count,
                      'glyph_outline_font_status': 'not_available_from_text_metadata',
                      'vector_paths_classified_as_glyphs': False,
                      'note': 'Vector paths may include outlined glyphs. Their font/size cannot be asserted from this analysis; inspect or compare separately.'},
                  'page_resource_fonts': resource_fonts}
    if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
        raise ValueError('Source PDF changed during read-only font analysis')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--page', type=int, default=1, help='One-based PDF page number')
    parser.add_argument('--region', nargs=4, type=float, metavar=('X0', 'Y0', 'X1', 'Y1'))
    parser.add_argument('--font-registry')
    parser.add_argument('--scale', type=float, help='Explicit uniform PDF-pt-to-source-px scale')
    parser.add_argument('--offset', nargs=2, type=float, metavar=('X', 'Y'))
    parser.add_argument('--matrix', nargs=6, type=float, metavar=('A', 'B', 'TX', 'C', 'D', 'TY'),
                        help='Explicit affine transform, row-major 2 x 3')
    parser.add_argument('--output', required=True, help='Fresh JSON report path; never overwrite a report')
    args = parser.parse_args(argv)
    if args.matrix is not None and (args.scale is not None or args.offset is not None):
        parser.error('--matrix cannot be combined with --scale/--offset')
    if (args.scale is None) != (args.offset is None):
        parser.error('--scale and --offset must be provided together')
    transform = ([args.matrix[:3], args.matrix[3:]] if args.matrix is not None else
                 [[args.scale, 0, args.offset[0]], [0, args.scale, args.offset[1]]]
                 if args.scale is not None else None)
    try:
        report = analyze_pdf(args.pdf, page=args.page, region=args.region,
                             source_transform=transform, font_registry=args.font_registry)
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
    except (OSError, ValueError) as error:
        parser.exit(2, f'font analysis: {error}\n')
    print(json.dumps({'report': str(output), 'text_span_count': len(report['text_spans']),
                      'verified_matches': sum(span['font_registry_match']['status'] == 'verified_match'
                                              for span in report['text_spans'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
