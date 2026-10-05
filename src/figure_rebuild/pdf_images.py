"""Read PDF image *occurrences*, preserving full placement before clipping.

PyMuPDF's text dictionary image bbox may already be clipped. It must never be
used to scale the full bitmap. This module cross-checks the image-info unit
transform, ordered fill-image paints and MuPDF's SVG painting tree. SVG is used
for occurrence-specific clip geometry. Pixels and soft masks are decoded from
the actual native fill-image event, never an SVG re-encoding or candidate xref. Nothing writes to or edits the source PDF.

Axis-aligned image placement is the default. Explicit affine rasterization
preserves a raster image's complete matrix in an isolated native PDF paint. Rectangular clipping keeps the full bitmap/crop contract. Other
validated path clips are applied to this embedded image alone in an in-memory
PDF, preserving native cubic curves and winding before rasterizing at 8x source
pixel sampling. Unsupported geometry/compositing fails closed.
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

from .pdf_image_clips import PdfImageClipError, parse_image_clip
from .pdf_image_native import PdfImageNativeError, capture_native_pdf_images
from .pdf_image_render import render_native_pdf_image


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
    if not all(math.isfinite(v) for v in matrix) or matrix[1] != 0 or matrix[2] != 0:
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


def _image_bounds(matrix, allow_affine):
    if not allow_affine:
        _axis_aligned(matrix)
    if (len(matrix) != 6 or not all(math.isfinite(v) for v in matrix) or
            abs(matrix[0]*matrix[3]-matrix[1]*matrix[2]) < 1e-12):
        raise UnsupportedPdfImageError('Degenerate or non-finite image affine transform')
    points = [(matrix[0]*u+matrix[2]*v+matrix[4], matrix[1]*u+matrix[3]*v+matrix[5])
              for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _intersection(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _box(rect):
    return dict(x=rect[0], y=rect[1], width=rect[2]-rect[0], height=rect[3]-rect[1])


def _auxiliary_text_block(info, block):
    """Keep text-page numbering as an unbound diagnostic candidate.

    Extraction flags and clipping may give the text dictionary a different
    namespace. Primary native/SVG occurrence checks have already succeeded.
    Even equal dimensions and transforms cannot bind its pixels or soft mask.
    """
    if block is None:
        return {'status': 'no_same_number_text_block', 'candidate_is_unbound': True,
                'same_number_is_identity': False, 'used_for_placement': False}
    same = (block.get('type') == 1 and
            (block.get('width'), block.get('height')) == (info['width'], info['height']) and
            _close(block.get('transform', ()), info['transform']))
    return {'status': 'auxiliary_geometry_consistent' if same else 'unrelated_text_namespace_number_collision',
            'image_info_number': info['number'], 'text_block_number': block.get('number'),
            'text_block_dimensions': [block.get('width'), block.get('height')],
            'text_block_transform': list(block.get('transform', ())),
            'candidate_bbox': list(block.get('bbox', ())),
            'candidate_is_unbound': True, 'same_number_is_identity': False,
            'primary_native_svg_identity_still_required': True, 'used_for_placement': False}


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


def _clip_geometry(node, matrix):
    """Validate the actual filled clip, retaining curves and winding, not a bbox proxy."""
    try:
        geometry = parse_image_clip(node, matrix)
    except PdfImageClipError as error:
        raise UnsupportedPdfImageError(str(error)) from error
    try:
        rectangle = _rect_clip(node, matrix)
    except UnsupportedPdfImageError:
        rectangle = None
    return {**geometry, 'rect': rectangle or geometry['bounds'],
            'rectangular': rectangle is not None}


def _clipped_image_pixels(pixels, transform, full, visible, mapping, clips):
    """Render exactly one embedded raster with PDF-native W/W* clip geometry.

    MuPDF's SVG importer can ignore clipPath, so it is not a clipping oracle.
    The temporary PDF contains this image, its co-registered alpha and the
    validated clip operators only. No page, path paint or text is rasterized.
    """
    import pymupdf
    sampling = 8
    width_pt, height_pt = visible[2]-visible[0], visible[3]-visible[1]
    width = max(1, math.ceil(width_pt*abs(mapping[0])*sampling))
    height = max(1, math.ceil(height_pt*abs(mapping[3])*sampling))
    if width*height > 64_000_000 or max(width, height) > 32768:
        raise UnsupportedPdfImageError('Complex image clip exceeds the explicit 8x raster sampling budget')
    # MuPDF normalizes PDF pages with a sub-point axis to a 1x1-point box.
    # Express the isolated page in integer sample coordinates instead, so a
    # 0.2-point-wide, 200-point-tall source strip never collapses to 1x1.
    sx, sy = width/width_pt, height/height_pt
    image_stream = io.BytesIO()
    pixels.save(image_stream, format='PNG')

    def number(value):
        if not math.isfinite(value):
            raise UnsupportedPdfImageError('Non-finite isolated image PDF coordinate')
        # PDF numbers do not allow exponential notation. Retain source precision.
        return format(value, '.15f').rstrip('0').rstrip('.') or '0'

    def point(value):
        return number((value[0]-visible[0])*sx)+' '+number((visible[3]-value[1])*sy)

    content = ['q']
    for clip in clips:
        for command in clip['commands']:
            op = command[0]
            if op in ('M', 'L'):
                content.append(point(command[1]) + (' m' if op == 'M' else ' l'))
            elif op == 'C':
                content.append(' '.join(point(p) for p in command[1:])+' c')
            elif op == 'Z':
                content.append('h')
            else:
                raise UnsupportedPdfImageError('Unsupported validated PDF clip command')
        content.append(('W*' if clip['rule'] == 'evenodd' else 'W')+' n')
    with pymupdf.open() as document:
        sheet = document.new_page(width=width, height=height)
        sheet.insert_image(pymupdf.Rect((full[0]-visible[0])*sx, (full[1]-visible[1])*sy,
                                       (full[2]-visible[0])*sx, (full[3]-visible[1])*sy),
                           stream=image_stream.getvalue(), keep_proportion=False)
        stream = sheet.get_contents()[-1]
        image_content = document.xref_stream(stream)
        # insert_image can serialize its transform with less precision. Bind the
        # complete occurrence frame directly, independent of the clipped bbox.
        # image-info maps top-left image unit coordinates into top-left PDF
        # points; PDF Do uses a bottom-left unit square. Compose both changes
        # of basis directly, retaining shear/reflection without decomposition.
        a, b, c, d, e, f = transform
        matrix = [a*sx, -b*sy, -c*sx, d*sy,
                  (e+c-visible[0])*sx, (visible[3]-f-d)*sy]
        image_content, substitutions = re.subn(rb'[-+\d.eE ]+ cm',
            (' '.join(number(v) for v in matrix)+' cm').encode(), image_content)
        if substitutions != 1:
            raise UnsupportedPdfImageError('Cannot bind isolated image transform unambiguously')
        document.update_stream(stream, ('\n'.join(content)+'\n').encode()+image_content+b'\nQ')
        isolated_bytes = document.tobytes()
        pixmap = sheet.get_pixmap(alpha=True)
        if (pixmap.width, pixmap.height) != (width, height):
            raise UnsupportedPdfImageError('Isolated image renderer changed the required sampling dimensions')
        with Image.open(io.BytesIO(pixmap.tobytes('png'))) as decoded:
            result = decoded.convert('RGBA')
    pitch = [width_pt*abs(mapping[0])/result.width, height_pt*abs(mapping[3])/result.height]
    receipt = {'method': 'isolated_embedded_image_with_native_pdf_clip_operators',
               'renderer': 'MuPDF', 'renderer_version': pymupdf.VersionBind,
               'requested_sampling_scale': sampling, 'actual_sampling_pitch_source_px': pitch,
               'boundary_sampling_cell_diagonal_source_px': math.hypot(*pitch),
               'sampling_bound_scope': 'raster grid spacing; not a bound on RGB error or renderer numerical accuracy',
               'clip_geometry_approximated': False, 'clip_curves_preserved_as_native_pdf_cubic': True,
               'source_path_or_text_paints_rasterized': False, 'derived_image_pixels_resampled': True,
               'isolated_pdf_sha256': hashlib.sha256(isolated_bytes).hexdigest(),
               'isolated_image_rgba_png_sha256': hashlib.sha256(image_stream.getvalue()).hexdigest(),
               'raster_size': list(result.size), 'visible_bbox_pdf_pt': list(visible),
               'isolated_pdf_coordinate_scale': [sx, sy],
               'exact_source_unit_transform': list(transform),
               'isolated_image_pdf_matrix': matrix,
               'affine_matrix_decomposed': False,
               'derived_affine_rasterization': bool(transform[1] or transform[2]),
               'original_encoded_image_resource_preserved_in_source_pdf': True,
               'svg_image_encoding_is_not_original_pdf_resource_bytes': True}
    return result, receipt


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
        occurrence['clips'] = [dict(clip, **_clip_geometry(ids[clip['id']], clip['transform']))
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


def extract_pdf_images(pdf_path, *, page=1, region=None, source_transform=None, image_indices=None,
                       allow_affine_rasterization=False, native_occurrence_rendering=False,
                       allow_native_rgb_group_sampling=False, native_sampling_scale=8,
                       allow_native_matte_sampling=False, allow_device_rgb_page_wrapper=False,
                       preserve_straight_mask_colors=False):
    """Return visible image occurrences with PNG bytes and explicit placement.

    ``page`` is one-based; ``region`` is x0/y0/x1/y1 in unrotated top-left PDF
    points. ``source_transform`` is a row-major 2x3 point-to-source-pixel matrix,
    defaulting to identity. Only nonzero diagonal scale/translation is supported.

    For rectangular clips, ``asset_bytes`` is the full oriented RGBA PNG, never
    stretched into the text dictionary's clipped bbox. ``full_source_box`` is its complete
    positive-size placement, ``visible_frame`` (also ``box``) is the intersection
    with active clips/page/region, and ``crop`` contains removed edge fractions
    of that full oriented bitmap. Complex clips instead create a derived 8x
    sampled PNG of this embedded-image occurrence only, with explicit alpha,
    ``asset_source_box`` equal to its visible frame and zero crop. The original
    full placement and exact clip curves remain in provenance. Apply crop then
    fit='stretch' to ``box`` in both cases.
    ``allow_affine_rasterization=True`` additionally permits image rotation and
    shear: the original raster, native decoded alpha, full affine matrix and
    clips are painted into an isolated 8x sampled PNG. Text and vector paints
    are not rasterized. The default rejects these transforms. The derived PNG
    uses its visible axis-aligned frame and zero crop; provenance retains the
    exact source matrix and sampling limits.
    ``native_occurrence_rendering=True`` explicitly renders each selected real
    image through its original MuPDF image handle, clips, attached mask, default
    colorspaces and supported Normal RGB group callbacks. All independent
    text/path/shading/other-image paints are omitted. This preserves native image
    filtering rather than first reconstructing a decoded RGBA image. The derived
    frame is rounded outward to integer source pixels (less than one transparent
    pixel of padding per edge) and sampled at ``native_sampling_scale`` (8 by
    default; explicitly 4 is also supported). This option only affects native
    occurrence rendering; 4 without that mode is rejected. The scale describes
    grid spacing, not a bound on RGB/alpha error. Different scales can change
    filtering at every output size and require source/output visual comparison.
    The original tight visible box
    remains in its receipt. ``box``, ``visible_frame`` and ``asset_source_box``
    consistently describe the derived frame, with zero crop. Unsupported group
    effects fail closed. Affine placement still requires its separate opt-in.
    Metadata extraction uses a separate PDF document. Each native occurrence
    starts from the original source bytes in a fresh document, so decoding for
    SVG, image hashes, or a preceding occurrence cannot prime its image cache.
    Pixel-based receipts are captured only after the native PNG is complete.
    ``allow_native_matte_sampling=True`` requires native occurrence rendering.
    It additionally admits 8-bit DeviceRGB images and same-size 8-bit attached
    Matte masks without Decode changes. The actual native image, bound mask and
    clips are forwarded unchanged; no decoded-alpha recomposition occurs.
    This opt-in remains sampled, requires whole-figure visual review and has no
    RGB/alpha error bound. Default decoded and native Matte rejection remain.
    ``allow_native_rgb_group_sampling=True`` requires native occurrence rendering
    and additionally admits one actual RGB child transparency group (including
    ICC / isolated groups) under the neutral page root. Original colorspaces and
    callbacks remain unchanged. Shared-group splitting has no RGB/alpha error
    bound; every such opt-in result requires full-figure visual review and is
    explicitly sampled, never a claim of exact compositing equivalence.
    ``paint_seqno`` is the actual bboxlog index, including intervening non-image
    paints. Repeated xrefs are separate occurrences in painting order.
    ``xref`` is explicitly a content-digest candidate from get_image_info, not
    a proven resource identity: identical RGB resources with different SMasks
    can be assigned the same candidate by MuPDF. Asset pixels/alpha come from
    the actual native PDF fill-image event, including its ColorSpace, Decode and
    bound soft mask. They are never obtained via that candidate xref.

    ``image_indices`` optionally selects zero-based get_image_info occurrence
    indices (never xrefs). Any selected unsupported occurrence fails the whole
    operation, including one outside the region. No approximate fallback is
    returned. With no selection, all occurrences must be supported.

    For intrinsic decoding, ``allow_device_rgb_page_wrapper=True`` explicitly
    admits only the full-page canonical DeviceRGB isolated Normal alpha-one
    wrapper with canonical default RGB, without child groups. The default
    remains strict. ``preserve_straight_mask_colors=True`` retains converted
    RGB bytes and the co-registered attached mask without a premultiplied byte
    round trip. Both require intrinsic decoding and reject native occurrence
    rendering. Placement, clipping, source resources and budgets are unchanged;
    this does not establish renderer or shared-group pixel equivalence.
    """
    try:
        import pymupdf
    except ImportError as error:
        raise ValueError('PDF image extraction requires the optional source dependencies') from error
    if not isinstance(native_occurrence_rendering, bool):
        raise ValueError('native_occurrence_rendering must be a boolean')
    for value, name in ((allow_device_rgb_page_wrapper, 'allow_device_rgb_page_wrapper'),
                        (preserve_straight_mask_colors, 'preserve_straight_mask_colors')):
        if type(value) is not bool:
            raise ValueError(name + ' must be a boolean')
        if value and native_occurrence_rendering:
            raise ValueError(name + ' requires intrinsic image decoding')
    if (isinstance(native_sampling_scale, bool) or not isinstance(native_sampling_scale, int)
            or native_sampling_scale not in (4, 8)):
        raise ValueError('native_sampling_scale must be the integer 4 or 8')
    if native_sampling_scale != 8 and not native_occurrence_rendering:
        raise ValueError('native_sampling_scale=4 requires native_occurrence_rendering=True')
    if not isinstance(allow_native_rgb_group_sampling, bool):
        raise ValueError('allow_native_rgb_group_sampling must be a boolean')
    if allow_native_rgb_group_sampling and not native_occurrence_rendering:
        raise ValueError('allow_native_rgb_group_sampling requires native_occurrence_rendering=True')
    if not isinstance(allow_native_matte_sampling, bool):
        raise ValueError('allow_native_matte_sampling must be a boolean')
    if allow_native_matte_sampling and not native_occurrence_rendering:
        raise ValueError('allow_native_matte_sampling requires native_occurrence_rendering=True')
    if not isinstance(allow_affine_rasterization, bool):
        raise ValueError('allow_affine_rasterization must be a boolean')
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
        bboxlog = sheet.get_bboxlog()
        paints = [(index, bbox) for index, (kind, bbox, *_) in enumerate(bboxlog) if kind == 'fill-image']
        svg = sheet.get_svg_image(text_as_path=True)
        if selection and max(selection) >= len(infos):
            raise ValueError('image index exceeds occurrence count')
        occurrences = _svg_images(svg, selection)
        if len(infos) != len(occurrences):
            raise UnsupportedPdfImageError('Image-info and SVG occurrence counts disagree')
        paint_order = _paint_order(infos, paints, selection)
        try:
            native_images = {}
            if not native_occurrence_rendering:
                # SVG/image-info/text extraction decodes and caches images.
                # Keep original pixel decoding independent of those APIs too.
                with pymupdf.open(stream=source_bytes, filetype='pdf') as pixel_document:
                    native_images = capture_native_pdf_images(
                        pixel_document[page-1], bboxlog, {paint[0] for paint in paint_order.values()},
                        allow_device_rgb_page_wrapper=allow_device_rgb_page_wrapper,
                        preserve_straight_mask_colors=preserve_straight_mask_colors)
        except PdfImageNativeError as error:
            raise UnsupportedPdfImageError(str(error)) from error
        blocks = {block['number']: block for block in sheet.get_text('dict')['blocks'] if block['type'] == 1}
        results = []
        for index, (info, occurrence) in enumerate(zip(infos, occurrences)):
            if selection is not None and index not in selection:
                continue
            paint = paint_order[index]
            transform = tuple(info['transform'])
            full = _image_bounds(transform, allow_affine_rasterization)
            affine_rasterization = allow_affine_rasterization and bool(transform[1] or transform[2])
            if not _close(transform, occurrence['transform']) or not _close(full, info['bbox']) or not _close(full, paint[1]):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: transform/paint identity mismatch')
            if occurrence['pixels'].size != (info['width'], info['height']):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: intrinsic dimensions disagree')
            visible = _intersection(full, list(sheet.rect))
            if region is not None:
                visible = _intersection(visible, region)
            for clip in occurrence['clips']:
                visible = _intersection(visible, clip['rect'])
            outside = visible[2] <= visible[0] or visible[3] <= visible[1]
            normalized = _mapped_rect(full, mapping)
            frame = [0, 0, 1, 1] if outside else _mapped_rect(visible, mapping)
            if native_occurrence_rendering:
                # Even selected occurrences outside the ROI must pass identity
                # and compositing validation. Their temporary 1px frame is
                # discarded, never returned as a source asset.
                user_clip = _intersection(list(sheet.rect), region) if region is not None else list(sheet.rect)
                if user_clip[2] <= user_clip[0] or user_clip[3] <= user_clip[1]:
                    user_clip = list(sheet.rect)
                try:
                    # A separate document per occurrence prevents metadata,
                    # earlier renders and receipt decoding from seeding this
                    # draw's image/mask cache. Reuse the same immutable bytes;
                    # native paint order/bounds and resource identity are still
                    # checked against the independently extracted metadata.
                    with pymupdf.open(stream=source_bytes, filetype='pdf') as render_document:
                        native = render_native_pdf_image(
                            render_document[page-1], bboxlog, paint[0], source_transform=mapping,
                            source_bounds=frame, user_clip_pdf=user_clip,
                            allow_native_rgb_group_sampling=allow_native_rgb_group_sampling,
                            native_sampling_scale=native_sampling_scale,
                            allow_native_matte_sampling=allow_native_matte_sampling)
                    native['receipt']['render_document_state'] = 'fresh_source_bytes_per_occurrence'
                    native['receipt']['metadata_and_render_documents_separated'] = True
                    if allow_native_rgb_group_sampling:
                        native['receipt'].update({'source_pdf_sha256': source_sha, 'source_pdf_page': page})
                except PdfImageNativeError as error:
                    raise UnsupportedPdfImageError(str(error)) from error
            else:
                native = native_images[paint[0]]
            if native['native_digest'] != info['digest'].hex():
                raise UnsupportedPdfImageError(f'Image occurrence {index}: native image digest disagrees with image-info')
            if not _close(native['transform'], transform):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: native image transform disagrees with image-info')
            if (native['width'], native['height']) != (info['width'], info['height']):
                raise UnsupportedPdfImageError(f'Image occurrence {index}: native intrinsic dimensions disagree')
            if outside:
                continue
            combined = _multiply(mapping, transform)
            flips = None if affine_rasterization else {'horizontal': combined[0] < 0, 'vertical': combined[3] < 0}
            complex_clip = any(not clip['rectangular'] for clip in occurrence['clips'])
            derived_raster = complex_clip or affine_rasterization or native_occurrence_rendering
            clip_rasterization = None
            if native_occurrence_rendering:
                asset_bytes = native['asset_bytes']
                frame = native['frame']
                asset_size = native['receipt']['raster_size']
                clip_rasterization = native['receipt']
                # All source and image transforms are already in the native
                # draw-device matrix. Never apply an additional image flip.
                pixel_flips = {'horizontal': False, 'vertical': False}
            else:
                pixels = native['pixels']
                if derived_raster:
                    pixels, clip_rasterization = _clipped_image_pixels(
                        pixels, transform, full, visible, mapping, occurrence['clips'])
                    pixel_flips = {'horizontal': mapping[0] < 0, 'vertical': mapping[3] < 0}
                else:
                    pixel_flips = flips
                if pixel_flips['horizontal']:
                    pixels = pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if pixel_flips['vertical']:
                    pixels = pixels.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                encoded = io.BytesIO()
                pixels.save(encoded, format='PNG')
                asset_bytes = encoded.getvalue()
                asset_size = list(pixels.size)
            crop = dict(left=(frame[0]-normalized[0])/(normalized[2]-normalized[0]),
                        top=(frame[1]-normalized[1])/(normalized[3]-normalized[1]),
                        right=(normalized[2]-frame[2])/(normalized[2]-normalized[0]),
                        bottom=(normalized[3]-frame[3])/(normalized[3]-normalized[1]))
            if derived_raster:
                crop = dict(left=0., top=0., right=0., bottom=0.)
            auxiliary_receipt = _auxiliary_text_block(info, blocks.get(info['number']))
            smask = document.xref_get_key(info['xref'], 'SMask') if info.get('xref') else ('null', 'null')
            results.append({'image_index': index, 'xref': info.get('xref', 0),
                            'xref_identity': 'image_info_content_digest_candidate_not_occurrence_identity',
                            'paint_seqno': paint[0],
                            'asset_bytes': asset_bytes, 'asset_sha256': hashlib.sha256(asset_bytes).hexdigest(),
                            'asset_size': asset_size, 'full_bbox_pdf_pt': full,
                            'full_source_box': _box(normalized), 'visible_frame': _box(frame), 'box': _box(frame),
                            'asset_source_box': _box(frame if derived_raster else normalized),
                            'crop': crop, 'fit': 'stretch', 'editable': False,
                            'provenance': {'source_pdf': str(path), 'source_pdf_sha256': source_sha, 'page': page,
                                           'image_info_number': info['number'], 'image_info_digest': info['digest'].hex(),
                                           'pdf_unit_transform': list(transform), 'source_transform': list(mapping),
                                           'svg_image_id': occurrence['id'], 'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
                                           'encoded_image_sha256': occurrence['encoded_sha256'],
                                           'encoded_image_sha256_basis': 'MuPDF exported SVG payload, not original PDF resource bytes',
                                           'original_resource_retained_in_source_pdf': True,
                                           'pixel_decode_basis': ('native_source_image_context_forwarding' if native_occurrence_rendering else
                                                                 'actual_native_pdf_fill_image_with_bound_colorspace_decode_and_mask'),
                                           'native_occurrence_rendering': native_occurrence_rendering,
                                           'native_image': native['receipt'],
                                           'svg_encoded_image_used_for_pixels': False,
                                           'text_dict_bbox_pdf_pt': None,
                                           'text_dict_bbox_used_for_placement': False,
                                           'auxiliary_text_block': auxiliary_receipt,
                                           'active_rectangular_clips': [clip for clip in occurrence['clips'] if clip['rectangular']],
                                           'active_image_clips': list(occurrence['clips']),
                                           'clip_rasterization': clip_rasterization,
                                           'soft_mask_xref_candidate': int(smask[1].split()[0]) if smask[0] == 'xref' else None,
                                           'soft_mask_resource_identity_verified': False,
                                           'native_image_and_mask_occurrence_binding_verified': True,
                                           'svg_soft_mask_ids': [mask['id'] for mask in occurrence['masks']],
                                           'asset_flips': flips, 'derived_asset_final_axis_flips': pixel_flips,
                                           'asset_basis': ('sampled native image occurrence with original clip/group/mask context' if native_occurrence_rendering else
                                                           'derived isolated embedded-image PDF affine/clip raster' if derived_raster else
                                                           'MuPDF native fill-image occurrence raster with bound soft mask'),
                                           'paint_order_basis': 'ordered native device paints + image-info + SVG painting-tree + fill-image bboxlog cross-check'}})
    return results
