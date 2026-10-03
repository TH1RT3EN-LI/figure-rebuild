"""Read PDF image *occurrences*, preserving full placement before clipping.

PyMuPDF's text dictionary image bbox may already be clipped. It must never be
used to scale the full bitmap. This module cross-checks the image-info unit
transform, ordered fill-image paints and MuPDF's SVG painting tree. SVG is used
for occurrence-specific rectangular clips and decoded image/soft-mask pixels,
not as a whole-page raster fallback. Nothing writes to or edits the source PDF.

Only axis-aligned image placements, rectangular clips and co-registered raster
soft masks are supported. Unsupported geometry/compositing fails closed.
"""
import base64
from functools import lru_cache
import hashlib
import io
import math
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops


class UnsupportedPdfImageError(ValueError):
    """An image occurrence cannot be represented without losing source data."""


_IDENTITY = (1., 0., 0., 1., 0., 0.)
_NUMBER = r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
_XLINK = '{http://www.w3.org/1999/xlink}href'


def _numbers(value):
    tokens = re.findall(_NUMBER, value)
    if re.sub(_NUMBER, '', value).strip(' ,\t\r\n'):
        raise UnsupportedPdfImageError('Malformed numeric SVG value')
    values = [float(v) for v in tokens]
    if not all(math.isfinite(v) for v in values):
        raise UnsupportedPdfImageError('Non-finite SVG value')
    return values


def _multiply(a, b):
    """Column-vector affine composition: a after b."""
    return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])


def _transform(value):
    result = _IDENTITY
    matches = list(re.finditer(r'([A-Za-z]+)\s*\(([^)]*)\)', value or ''))
    if re.sub(r'[A-Za-z]+\s*\([^)]*\)', '', value or '').strip(' ,\t\r\n'):
        raise UnsupportedPdfImageError('Malformed SVG transform')
    for match in matches:
        kind, args = match.group(1), _numbers(match.group(2))
        if kind == 'matrix' and len(args) == 6:
            matrix = tuple(args)
        elif kind == 'translate' and len(args) in (1, 2):
            matrix = (1., 0., 0., 1., args[0], args[1] if len(args) == 2 else 0.)
        elif kind == 'scale' and len(args) in (1, 2):
            matrix = (args[0], 0., 0., args[-1], 0., 0.)
        else:
            raise UnsupportedPdfImageError('Unsupported SVG transform: ' + kind)
        result = _multiply(result, matrix)
    return result


def _axis_aligned(matrix):
    if not all(math.isfinite(v) for v in matrix) or abs(matrix[1]) > 1e-6 or abs(matrix[2]) > 1e-6:
        raise UnsupportedPdfImageError('Rotated or skewed image/clip transform is unsupported')
    if abs(matrix[0]) < 1e-12 or abs(matrix[3]) < 1e-12:
        raise UnsupportedPdfImageError('Degenerate image/clip transform')


def _mapped_rect(rect, matrix):
    _axis_aligned(matrix)
    if len(rect) != 4 or not all(math.isfinite(v) for v in rect):
        raise UnsupportedPdfImageError('Non-finite image/clip rectangle')
    x0, y0, x1, y1 = rect
    xs = [matrix[0]*x+matrix[4] for x in (x0, x1)]
    ys = [matrix[3]*y+matrix[5] for y in (y0, y1)]
    if not all(math.isfinite(value) for value in xs+ys):
        raise UnsupportedPdfImageError('Image/clip mapping overflows finite coordinates')
    return [min(xs), min(ys), max(xs), max(ys)]


def _intersection(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _box(rect):
    return dict(x=rect[0], y=rect[1], width=rect[2]-rect[0], height=rect[3]-rect[1])


def _close(a, b, tolerance=0.002):
    return len(a) == len(b) and all(abs(x-y) <= tolerance for x, y in zip(a, b))


def _paint_order(infos, paints, selection):
    """Unique ordered exact-bbox correspondence; no nearest-bbox guessing.

    MuPDF may expose synthetic image-info entries without a fill-image paint.
    Match paints as a subsequence, and reject any selected occurrence which
    could be skipped or paired differently in another valid correspondence.
    Repeated xrefs/bboxes with equal occurrence counts still map by order.
    """
    # Drivers may isolate unsupported occurrences by partitioning selections.
    # Cache only this pure geometric correspondence, never mutable source data.
    correspondences = _paint_correspondences(tuple(tuple(info['bbox']) for info in infos),
                                             tuple(tuple(paint[1]) for paint in paints))
    result = {}
    for i, (candidates, could_skip) in enumerate(correspondences):
        if selection is not None and i not in selection:
            continue
        if could_skip or len(candidates) != 1:
            raise UnsupportedPdfImageError(f'Image occurrence {i}: missing or ambiguous fill-image paint')
        result[i] = paints[candidates[0]]
    return result


@lru_cache(maxsize=2)
def _paint_correspondences(info_boxes, paint_boxes):
    count, painted = len(info_boxes), len(paint_boxes)
    if count == painted and all(_close(a, b) for a, b in zip(info_boxes, paint_boxes)):
        return tuple(((i,), False) for i in range(count))
    same = [[_close(info, paint) for paint in paint_boxes] for info in info_boxes]
    prefix = [bytearray(painted+1) for _ in range(count+1)]
    prefix[0][0] = 1
    for i in range(count):
        prefix[i+1][0] = 1
        for j in range(1, painted+1):
            prefix[i+1][j] = prefix[i][j] or (prefix[i][j-1] and same[i][j-1])
    if not prefix[count][painted]:
        raise UnsupportedPdfImageError('fill-image paints cannot be matched in image-info order')
    suffix = [bytearray(painted+1) for _ in range(count+1)]
    suffix[count][painted] = 1
    for i in range(count-1, -1, -1):
        suffix[i][painted] = 1
        for j in range(painted-1, -1, -1):
            suffix[i][j] = suffix[i+1][j] or (same[i][j] and suffix[i+1][j+1])
    result = []
    for i in range(count):
        candidates = tuple(j for j in range(painted) if prefix[i][j] and same[i][j] and suffix[i+1][j+1])
        could_skip = any(prefix[i][j] and suffix[i+1][j] for j in range(painted+1))
        result.append((candidates, could_skip))
    return tuple(result)


def _style(node):
    style = dict(node.attrib)
    for declaration in node.get('style', '').split(';'):
        if ':' in declaration:
            key, value = declaration.split(':', 1)
            style[key.strip()] = value.strip()
    return style


def _reference(value):
    match = re.fullmatch(r'url\(\s*#([^\s)]+)\s*\)', value or '')
    if not match:
        raise UnsupportedPdfImageError('Only local SVG clip/mask references are supported')
    return match.group(1)


def _rect_path(data):
    """Accept exactly one axis-aligned rectangle, not its bounding-box proxy."""
    tokens = re.findall(r'[A-Za-z]|' + _NUMBER, data)
    if re.sub(r'[A-Za-z]|' + _NUMBER, '', data).strip(' ,\t\r\n'):
        raise UnsupportedPdfImageError('Malformed clip path')
    points, current, command, index = [], (0., 0.), None, 0
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command not in ('M', 'm', 'L', 'l', 'H', 'h', 'V', 'v', 'Z', 'z'):
            raise UnsupportedPdfImageError('Non-rectangular clip path')
        if command in ('Z', 'z'):
            if index != len(tokens):
                raise UnsupportedPdfImageError('Compound clip paths are unsupported')
            break
        count = 2 if command.upper() in ('M', 'L') else 1
        try:
            args = [float(v) for v in tokens[index:index+count]]
        except ValueError as error:
            raise UnsupportedPdfImageError('Malformed clip path coordinates') from error
        if len(args) != count or not all(math.isfinite(v) for v in args):
            raise UnsupportedPdfImageError('Malformed clip path coordinates')
        index += count
        if command.upper() == 'M' and points:
            raise UnsupportedPdfImageError('Compound clip paths are unsupported')
        relative = command.islower()
        if command.upper() in ('M', 'L'):
            current = (args[0] + (current[0] if relative else 0),
                       args[1] + (current[1] if relative else 0))
        elif command.upper() == 'H':
            current = (args[0] + (current[0] if relative else 0), current[1])
        else:
            current = (current[0], args[0] + (current[1] if relative else 0))
        points.append(current)
        if command in ('M', 'm'):
            command = 'l' if relative else 'L'
    if len(points) == 5 and points[-1] == points[0]:
        points.pop()
    # SVG clipping uses filled geometry: an open subpath is implicitly closed.
    if len(points) != 4:
        raise UnsupportedPdfImageError('Clip must be exactly one rectangle')
    xs, ys = sorted({p[0] for p in points}), sorted({p[1] for p in points})
    if len(xs) != 2 or len(ys) != 2 or len(set(points)) != 4 or any(
            a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:]+points[:1])):
        raise UnsupportedPdfImageError('Clip must be an axis-aligned rectangle')
    return [xs[0], ys[0], xs[1], ys[1]]


def _rect_clip(node, matrix):
    matrix = _multiply(matrix, _transform(node.get('transform')))
    if node.get('clipPathUnits', 'userSpaceOnUse') != 'userSpaceOnUse':
        raise UnsupportedPdfImageError('Object-bounding-box clips are unsupported')
    children = list(node)
    if len(children) != 1:
        raise UnsupportedPdfImageError('Compound clip geometry is unsupported')
    shape = children[0]
    if shape.get('clip-path') or shape.get('mask'):
        raise UnsupportedPdfImageError('Nested effects inside clip geometry are unsupported')
    matrix = _multiply(matrix, _transform(shape.get('transform')))
    tag = shape.tag.rsplit('}', 1)[-1]
    if tag == 'path':
        rect = _rect_path(shape.get('d', ''))
    elif tag == 'rect' and not shape.get('rx') and not shape.get('ry'):
        x, y = float(shape.get('x', 0)), float(shape.get('y', 0))
        rect = [x, y, x+float(shape.get('width', 0)), y+float(shape.get('height', 0))]
    else:
        raise UnsupportedPdfImageError('Non-rectangular clipping is unsupported')
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise UnsupportedPdfImageError('Degenerate clipping rectangle')
    return _mapped_rect(rect, matrix)


def _svg_images(svg, selection=None):
    root = ET.fromstring(svg)
    ids = {node.get('id'): node for node in root.iter() if node.get('id')}

    def has_image(node, seen=(), definition=False):
        tag = node.tag.rsplit('}', 1)[-1]
        if tag == 'image':
            return True
        if tag in ('defs', 'clipPath', 'mask', 'symbol') and not definition:
            return False
        if tag == 'use':
            href = node.get('href', node.get(_XLINK, ''))
            if href.startswith('#') and href[1:] in ids and href[1:] not in seen:
                return has_image(ids[href[1:]], seen+(href[1:],), True)
            return False
        return any(has_image(child, seen) for child in node)

    def image_only_count(node, seen=()):
        """Reject distributing one group mask over multiple/other paints."""
        tag = node.tag.rsplit('}', 1)[-1]
        if tag == 'image':
            return 1
        if tag == 'defs':
            return 0
        if tag == 'use':
            href = node.get('href', node.get(_XLINK, ''))
            if href.startswith('#') and href[1:] in ids and href[1:] not in seen:
                return image_only_count(ids[href[1:]], seen+(href[1:],))
            return 2
        if tag not in ('svg', 'g', 'mask', 'symbol'):
            return 2
        return sum(image_only_count(child, seen) for child in node)

    def walk(node, matrix=_IDENTITY, clips=(), masks=(), stack=(), definition=False, errors=()):
        tag = node.tag.rsplit('}', 1)[-1]
        if tag in ('defs', 'clipPath', 'mask', 'symbol') and not definition:
            return []
        if not has_image(node, definition=definition):
            return []
        style = _style(node)
        if style.get('display') == 'none' or style.get('visibility') == 'hidden':
            return []
        matrix = _multiply(matrix, _transform(node.get('transform')))
        # Carry effects with their subtree so an unrelated unsupported image
        # cannot block a selected occurrence. Shared group effects still reject
        # every affected selected child; they are never flattened into leaf alpha.
        for key in ('opacity', 'fill-opacity', 'stroke-opacity'):
            if key in style and float(style[key]) != 1:
                errors += ('Group/image opacity requires explicit compositing: ' + key,)
        for key in ('filter', 'mix-blend-mode', 'isolation'):
            if style.get(key, 'none') not in ('none', 'normal', 'auto'):
                errors += ('Unsupported image group effect: ' + key,)
        for key in ('clip-path', 'mask'):
            if style.get(key, 'none') != 'none':
                ref = _reference(style[key])
                if ref not in ids:
                    raise UnsupportedPdfImageError('Unresolved SVG reference: ' + ref)
                if key == 'clip-path':
                    clips += ({'id': ref, 'transform': list(matrix)},)
                else:
                    if image_only_count(node) != 1:
                        errors += ('Group mask over multiple/other paints requires compositing',)
                    masks += ({'id': ref, 'transform': matrix},)
        if tag == 'use':
            href = node.get('href', node.get(_XLINK, ''))
            if not href.startswith('#') or href[1:] not in ids or href[1:] in stack:
                raise UnsupportedPdfImageError('Unresolved or recursive SVG use')
            offset = (1., 0., 0., 1., float(node.get('x', 0)), float(node.get('y', 0)))
            return walk(ids[href[1:]], _multiply(matrix, offset), clips, masks, stack+(href[1:],), True, errors)
        if tag == 'image':
            href = node.get('href', node.get(_XLINK, ''))
            match = re.fullmatch(r'data:image/[\w.+-]+;base64,([\s\S]+)', href)
            if not match:
                raise UnsupportedPdfImageError('Only embedded raster image data is supported')
            data = base64.b64decode(re.sub(r'\s', '', match.group(1)), validate=True)
            with Image.open(io.BytesIO(data)) as image:
                pixels = image.convert('RGBA')
            width, height = float(node.get('width', 0)), float(node.get('height', 0))
            if width <= 0 or height <= 0 or not _close([width/height], [pixels.width/pixels.height]):
                raise UnsupportedPdfImageError('SVG image viewport aspect-ratio mapping is unsupported')
            unit = _multiply(matrix, (width, 0., 0., height, float(node.get('x', 0)), float(node.get('y', 0))))
            return [{'id': node.get('id'), 'transform': unit, 'clips': clips, 'masks': masks,
                     'pixels': pixels, 'encoded_sha256': hashlib.sha256(data).hexdigest(), 'errors': errors}]
        # Text / paths cannot contain painted images; their own effects are irrelevant.
        if tag not in ('svg', 'g', 'mask', 'symbol'):
            return []
        result = []
        for child in node:
            result.extend(walk(child, matrix, clips, masks, stack, errors=errors))
        return result

    occurrences = walk(root)
    for index, occurrence in enumerate(occurrences):
        if selection is not None and index not in selection:
            continue
        if occurrence['errors']:
            raise UnsupportedPdfImageError(f'Image occurrence {index}: ' + occurrence['errors'][0])
        occurrence['clips'] = [dict(clip, rect=_rect_clip(ids[clip['id']], clip['transform']))
                               for clip in occurrence['clips']]
        for context in occurrence['masks']:
            mask = ids[context['id']]
            # Only the SVG form emitted for a co-registered PDF image SMask is lowered.
            if mask.get('maskUnits') or mask.get('maskContentUnits') not in (None, 'userSpaceOnUse'):
                raise UnsupportedPdfImageError('Nonstandard mask coordinate units are unsupported')
            mask_images = walk(mask, context['transform'], definition=True)
            if len(mask_images) != 1:
                raise UnsupportedPdfImageError('Mask must contain one co-registered raster image')
            item = mask_images[0]
            if item['errors']:
                raise UnsupportedPdfImageError('Soft mask: ' + item['errors'][0])
            if item['masks'] or item['clips'] or not _close(item['transform'], occurrence['transform']):
                raise UnsupportedPdfImageError('Complex or non-co-registered soft mask')
            pixels = item['pixels']
            if pixels.size != occurrence['pixels'].size:
                raise UnsupportedPdfImageError('Soft mask dimensions differ from image')
            red, green, blue, alpha = pixels.split()
            if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
                raise UnsupportedPdfImageError('Non-grayscale luminosity mask is unsupported')
            mask_alpha = ImageChops.multiply(red, alpha)
            occurrence['pixels'].putalpha(ImageChops.multiply(occurrence['pixels'].getchannel('A'), mask_alpha))
    return occurrences


def extract_pdf_images(pdf_path, *, page=1, region=None, source_transform=None, image_indices=None):
    """Return visible image occurrences with PNG bytes and explicit placement.

    ``page`` is one-based; ``region`` is x0/y0/x1/y1 in unrotated top-left PDF
    points. ``source_transform`` is a row-major 2x3 point-to-source-pixel matrix,
    defaulting to identity. Only nonzero diagonal scale/translation is supported.

    Each result's ``asset_bytes`` is the full oriented RGBA PNG, never stretched
    into the text dictionary's clipped bbox. ``full_source_box`` is its complete
    positive-size placement, ``visible_frame`` (also ``box``) is the intersection
    with active clips/page/region, and ``crop`` contains removed edge fractions
    of that full oriented bitmap. Apply crop then fit='stretch' to ``box``.
    ``paint_seqno`` is the actual bboxlog index, including intervening non-image
    paints. Repeated xrefs are separate occurrences in painting order.
    ``xref`` is explicitly a content-digest candidate from get_image_info, not
    a proven resource identity: identical RGB resources with different SMasks
    can be assigned the same candidate by MuPDF. Asset pixels/alpha come from
    the actual SVG occurrence, and are never obtained via that candidate xref.

    ``image_indices`` optionally selects zero-based get_image_info occurrence
    indices (never xrefs). Any selected unsupported occurrence fails the whole
    operation, including one outside the region. No approximate fallback is
    returned. With no selection, all occurrences must be supported.
    """
    try:
        import pymupdf
    except ImportError as error:
        raise ValueError('PDF image extraction requires the optional source dependencies') from error
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError('page must be a positive one-based integer')
    if image_indices is not None and (not isinstance(image_indices, (list, tuple, set)) or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in image_indices)):
        raise ValueError('image_indices must contain nonnegative occurrence indices')
    selection = set(image_indices) if image_indices is not None else None
    if source_transform is None:
        mapping = _IDENTITY
    else:
        if not isinstance(source_transform, (list, tuple)) or len(source_transform) != 2 or any(
                not isinstance(row, (list, tuple)) or len(row) != 3 for row in source_transform):
            raise ValueError('source_transform must be a 2 x 3 matrix')
        vals = [value for row in source_transform for value in row]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vals):
            raise ValueError('source_transform coefficients must be finite numbers')
        mapping = (vals[0], vals[3], vals[1], vals[4], vals[2], vals[5])
        _axis_aligned(mapping)
    if region is not None and (not isinstance(region, (list, tuple)) or len(region) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in region)
            or region[2] <= region[0] or region[3] <= region[1]):
        raise ValueError('region must be a finite positive x0/y0/x1/y1 rectangle')
    path = Path(pdf_path).resolve()
    source_bytes = path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    with pymupdf.open(stream=source_bytes, filetype='pdf') as document:
        if page > len(document):
            raise ValueError('page exceeds PDF page count')
        sheet = document[page-1]
        if sheet.rotation:
            raise UnsupportedPdfImageError('Rotated PDF pages are unsupported')
        if selection == set():
            return []
        infos = sheet.get_image_info(xrefs=True)
        paints = [(index, bbox) for index, (kind, bbox, *_) in enumerate(sheet.get_bboxlog()) if kind == 'fill-image']
        svg = sheet.get_svg_image(text_as_path=True)
        if selection and max(selection) >= len(infos):
            raise ValueError('image index exceeds occurrence count')
        occurrences = _svg_images(svg, selection)
        if len(infos) != len(occurrences):
            raise UnsupportedPdfImageError('Image-info and SVG occurrence counts disagree')
        paint_order = _paint_order(infos, paints, selection)
        blocks = {block['number']: block for block in sheet.get_text('dict')['blocks'] if block['type'] == 1}
        results = []
        for index, (info, occurrence) in enumerate(zip(infos, occurrences)):
            if selection is not None and index not in selection:
                continue
            paint = paint_order[index]
            transform = tuple(info['transform'])
            _axis_aligned(transform)
            full = _mapped_rect([0, 0, 1, 1], transform)
            if not _close(transform, occurrence['transform']) or not _close(full, info['bbox']) or not _close(full, paint[1]):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: transform/paint identity mismatch')
            if occurrence['pixels'].size != (info['width'], info['height']):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: intrinsic dimensions disagree')
            visible = _intersection(full, list(sheet.rect))
            if region is not None:
                visible = _intersection(visible, region)
            for clip in occurrence['clips']:
                visible = _intersection(visible, clip['rect'])
            if visible[2] <= visible[0] or visible[3] <= visible[1]:
                continue
            normalized = _mapped_rect(full, mapping)
            frame = _mapped_rect(visible, mapping)
            combined = _multiply(mapping, transform)
            pixels = occurrence['pixels']
            flips = {'horizontal': combined[0] < 0, 'vertical': combined[3] < 0}
            if flips['horizontal']:
                pixels = pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if flips['vertical']:
                pixels = pixels.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            encoded = io.BytesIO()
            pixels.save(encoded, format='PNG')
            asset_bytes = encoded.getvalue()
            crop = dict(left=(frame[0]-normalized[0])/(normalized[2]-normalized[0]),
                        top=(frame[1]-normalized[1])/(normalized[3]-normalized[1]),
                        right=(normalized[2]-frame[2])/(normalized[2]-normalized[0]),
                        bottom=(normalized[3]-frame[3])/(normalized[3]-normalized[1]))
            block = blocks.get(info['number'])
            if block is not None and not _close(block['transform'], transform):
                raise UnsupportedPdfImageError('Text image block identity mismatch; bbox-nearest matching is forbidden')
            smask = document.xref_get_key(info['xref'], 'SMask') if info.get('xref') else ('null', 'null')
            results.append({'image_index': index, 'xref': info.get('xref', 0),
                            'xref_identity': 'image_info_content_digest_candidate_not_occurrence_identity',
                            'paint_seqno': paint[0],
                            'asset_bytes': asset_bytes, 'asset_sha256': hashlib.sha256(asset_bytes).hexdigest(),
                            'asset_size': list(pixels.size), 'full_bbox_pdf_pt': full,
                            'full_source_box': _box(normalized), 'visible_frame': _box(frame), 'box': _box(frame),
                            'crop': crop, 'fit': 'stretch', 'editable': False,
                            'provenance': {'source_pdf': str(path), 'source_pdf_sha256': source_sha, 'page': page,
                                           'image_info_number': info['number'], 'image_info_digest': info['digest'].hex(),
                                           'pdf_unit_transform': list(transform), 'source_transform': list(mapping),
                                           'svg_image_id': occurrence['id'], 'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
                                           'encoded_image_sha256': occurrence['encoded_sha256'],
                                           'text_dict_bbox_pdf_pt': list(block['bbox']) if block else None,
                                           'text_dict_bbox_used_for_placement': False, 'active_rectangular_clips': list(occurrence['clips']),
                                           'soft_mask_xref_candidate': int(smask[1].split()[0]) if smask[0] == 'xref' else None,
                                           'soft_mask_resource_identity_verified': False,
                                           'svg_soft_mask_ids': [mask['id'] for mask in occurrence['masks']],
                                           'asset_flips': flips, 'asset_basis': 'MuPDF SVG occurrence raster with co-registered soft mask',
                                           'paint_order_basis': 'ordered image-info + SVG painting-tree + fill-image bboxlog cross-check'}})
    return results
