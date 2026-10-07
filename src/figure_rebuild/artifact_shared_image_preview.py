"""Explicit opaque integer windows sampled on one shared native media grid.

This preview policy is separate from fractional DrawingML crop semantics. The
caller supplies integer source windows. Their crop units and common affine must
agree with the actual delivered PPTX before any render allocation.
"""
import base64
import hashlib
import json
from fractions import Fraction as Q
from io import BytesIO

from PIL import Image
from . import artifact_image_preview as ordinary

POLICY = 'delivered-native-picture-mupdf-device-grid-v3'
MAX_GROUP_OBJECTS = 256
MAX_PAIR_CONSTRAINTS = 131072


def _axis(rows, dimensions, axis):
    lows, highs = [], []
    for row in rows:
        n, window = row['native'], row['window']
        count = window[axis + 2] - window[axis]
        lows.append(Q(2 * n[axis + 2] - 1, 2 * count))
        highs.append(Q(2 * n[axis + 2] + 1, 2 * count))
    for a in rows:
        for b in rows:
            coefficient = b['window'][axis] - a['window'][axis]
            rhs = b['native'][axis] - a['native'][axis] + 1
            if coefficient > 0:
                highs.append(Q(rhs, coefficient))
            elif coefficient < 0:
                lows.append(Q(rhs, coefficient))
            elif rhs < 0:
                raise ValueError('Shared media grid has incompatible native origins')
    lo, hi = max(lows), min(highs)
    if not 0 < lo <= hi:
        raise ValueError('Shared media grid has incompatible native extents')
    step = (lo + hi) / 2
    bounds = [(Q(2 * r['native'][axis] - 1, 2) - step * r['window'][axis],
               Q(2 * r['native'][axis] + 1, 2) - step * r['window'][axis]) for r in rows]
    lo, hi = max(a for a, b in bounds), min(b for a, b in bounds)
    if lo > hi:
        raise ValueError('Shared media grid has incompatible native offsets')
    origin = (lo + hi) / 2
    error = max(max(abs(origin + step * r['window'][axis] - r['native'][axis]),
                    abs(step * (r['window'][axis + 2] - r['window'][axis]) - r['native'][axis + 2]))
                for r in rows)
    if error > Q(1, 2):
        raise ValueError('Shared media grid exceeds native half-EMU quantization')
    return origin, step, {'axis': axis, 'origin_EMU': str(origin), 'source_pixel_step_EMU': str(step),
                          'maximum_left_or_extent_residual_EMU': str(error)}


def prepare_shared_image_preview(pptx, manifest, request):
    if (type(request) is not dict or set(request) != {'schema_version', 'groups'} or
            type(request['schema_version']) is not int or request['schema_version'] != 1 or
            type(request['groups']) is not list or not 1 <= len(request['groups']) <= MAX_GROUP_OBJECTS):
        raise ValueError('Invalid explicit shared media grid request')
    pptx, payload, xml, names, package, relationships, pictures, order = ordinary._native_picture_inputs(pptx, manifest)
    canvas = manifest.get('canvas')
    if (type(canvas) is not dict or not {'width', 'height'} <= set(canvas) or
            any(type(canvas[k]) not in (int, float) or not 0 < canvas[k] <= 32768 for k in ('width', 'height'))):
        raise ValueError('Invalid shared native grid canvas')
    # The grid has integral device dimensions. Fractional slide dimensions are
    # deliberately outside this policy rather than silently changing scale.
    if any(int(canvas[k]) != canvas[k] for k in ('width', 'height')):
        raise ValueError('Shared native grid requires integral canvas dimensions')
    width, height = int(canvas['width']), int(canvas['height'])
    with ordinary.ZipFile(BytesIO(payload)) as archive:
        part = 'ppt/presentation.xml'
        if part not in archive.namelist() or archive.getinfo(part).file_size > ordinary.MAX_XML:
            raise ValueError('Missing or oversized shared native canvas part')
        presentation = ordinary.XmlDocument.parse(archive.read(part), part).root
        size = ordinary._one(presentation, 'p:sldSz')
        if [ordinary._integer(size.get(k), k, True) for k in ('cx', 'cy')] != [width * ordinary.EMU, height * ordinary.EMU]:
            raise ValueError('Shared canvas differs from actual native slide dimensions')
    parsed, identities, pixels, pairs = [], set(), 0, 0
    for group in request['groups']:
        if (type(group) is not dict or set(group) != {'objects'} or type(group['objects']) is not list or
                not 1 <= len(group['objects']) <= MAX_GROUP_OBJECTS):
            raise ValueError('Invalid shared media group object budget')
        pairs += 2 * len(group['objects'])**2
        if pairs > MAX_PAIR_CONSTRAINTS:
            raise ValueError('Shared media pair constraint budget')
        rows, data = [], None
        for item in group['objects']:
            if (type(item) is not dict or set(item) != {'id', 'window'} or type(item['id']) is not str or
                    item['id'] not in pictures or item['id'] in identities or type(item['window']) is not list or
                    len(item['window']) != 4 or any(type(v) is not int for v in item['window'])):
                raise ValueError('Invalid shared media integer window or identity')
            identities.add(item['id'])
            try:
                native, encoded, media, dimensions, crop = ordinary._plain_picture(pictures[item['id']], package, relationships, 2)
            except ordinary.UnsupportedPicture as exc:
                raise ValueError('Unsupported declared shared native picture: ' + str(exc)) from exc
            if data is not None and encoded != data:
                raise ValueError('Shared media group has different actual image bytes')
            data = encoded
            x0, y0, x1, y1 = window = item['window']
            if not (0 <= x0 < x1 <= dimensions[0] and 0 <= y0 < y1 <= dimensions[1]):
                raise ValueError('Shared integer window escapes actual media')
            margins = [x0, y0, dimensions[0] - x1, dimensions[1] - y1]
            if any(round(Q(v * 100000, dimensions[i % 2])) != crop[k] for i, (v, k) in enumerate(zip(margins, 'ltrb'))):
                raise ValueError('Explicit integer window disagrees with actual native source crop')
            x, y, w, h = native
            if not (0 <= x < x + w <= width * ordinary.EMU and 0 <= y < y + h <= height * ordinary.EMU):
                raise ValueError('Shared native window escapes canvas')
            rows.append({'id': item['id'], 'native': native, 'window': window, 'dimensions': dimensions,
                         'media': media, 'crop': crop})
        x, sx, xp = _axis(rows, dimensions, 0)
        y, sy, yp = _axis(rows, dimensions, 1)
        frame = [x / ordinary.EMU, y / ordinary.EMU, sx * dimensions[0] / ordinary.EMU, sy * dimensions[1] / ordinary.EMU]
        for scale in ordinary.SCALES:
            full_w, full_h = frame[2] * scale, frame[3] * scale
            if (max(width * scale, height * scale, full_w, full_h) > 32768 or
                    max(width * height * scale**2, full_w * full_h) > ordinary.MAX_SURFACE_PIXELS):
                raise ValueError('Shared media grid render surface or transform budget')
            pixels += width * height * scale**2
            for row in rows:
                n = row['native']
                box = [round(Q(v * scale, ordinary.EMU)) for v in (n[0], n[1], n[0] + n[2], n[1] + n[3])]
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError('Shared native window has an empty device footprint')
                pixels += (box[2] - box[0]) * (box[3] - box[1])
        if pixels > ordinary.MAX_COMBINED_PIXELS:
            raise ValueError('Combined shared native picture render pixel budget')
        parsed.append({'rows': rows, 'data': data, 'frame': frame, 'proofs': [xp, yp]})
    if identities != set(pictures):
        raise ValueError('Shared media request must cover every actual native picture exactly once')
    # Every plan, transform and combined pixel cost is checked before opening
    # a MuPDF render surface. Opaque windows avoid undefined alpha compositing.
    for group in parsed:
        with Image.open(BytesIO(group['data'])) as image:
            rgba = image.convert('RGBA')
            for row in group['rows']:
                if rgba.crop(tuple(row['window'])).getchannel('A').getextrema() != (255, 255):
                    raise ValueError('Shared native source window is not opaque')
    import pymupdf as fitz
    objects, proofs = {}, []
    for group in parsed:
        for row in group['rows']:
            pic = pictures[row['id']]
            objects[row['id']] = {'id': row['id'], 'native_position': dict(zip(('left', 'top', 'width', 'height'), (v / ordinary.EMU for v in row['native']))),
                'native_picture_xml_sha256': hashlib.sha256(ordinary.E.canonicalize(ordinary.E.tostring(pic), rewrite_prefixes=True).encode()).hexdigest(),
                'native_media_part': row['media'], 'native_media_sha256': hashlib.sha256(group['data']).hexdigest(),
                'native_media_dimensions': row['dimensions'], 'native_source_crop_units': row['crop'],
                'explicit_integer_source_window': row['window'], 'previews': []}
        frame = list(map(float, group['frame']))
        for scale in ordinary.SCALES:
            with fitz.open() as doc:
                page = doc.new_page(width=width, height=height)
                xr = page.insert_image(fitz.Rect(frame[0], frame[1], frame[0] + frame[2], frame[1] + frame[3]), stream=group['data'], keep_proportion=False)
                doc.xref_set_key(xr, 'Interpolate', 'false')
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
                if (pix.width, pix.height) != (width * scale, height * scale):
                    raise ValueError('Shared media renderer changed device grid dimensions')
                full = Image.frombytes('RGBA', (pix.width, pix.height), pix.samples)
                for row in group['rows']:
                    n = row['native']
                    x0, y0, x1, y1 = [round(Q(v * scale, ordinary.EMU)) for v in (n[0], n[1], n[0] + n[2], n[1] + n[3])]
                    sample = full.crop((x0, y0, x1, y1))
                    if sample.getchannel('A').getextrema() != (255, 255):
                        raise ValueError('Shared native device window is not opaque')
                    buffer = BytesIO(); sample.save(buffer, format='PNG'); data = buffer.getvalue()
                    objects[row['id']]['previews'].append({'scale': scale, 'position': {'left': x0 / scale, 'top': y0 / scale, 'width': (x1 - x0) / scale, 'height': (y1 - y0) / scale},
                        'width': x1 - x0, 'height': y1 - y0, 'png_base64': base64.b64encode(data).decode('ascii'), 'png_sha256': hashlib.sha256(data).hexdigest()})
        proofs.append({'object_ids': [r['id'] for r in group['rows']], 'native_full_media_frame_canvas_px': frame,
                       'quantization_proofs': group['proofs']})
    result = {'schema_version': 3, 'policy': POLICY, 'input_pptx': {'path': str(pptx), 'sha256': hashlib.sha256(payload).hexdigest()},
              'slide_part': names[0], 'native_slide_xml_sha256': hashlib.sha256(xml).hexdigest(), 'paint_order': order,
              'objects': [objects[name] for name in pictures], 'unsupported': [], 'scales': list(ordinary.SCALES),
              'shared_grid_request': request, 'shared_grid_proofs': proofs, 'renderer': 'PyMuPDF',
              'renderer_version': fitz.VersionBind, 'mupdf_version': fitz.VersionFitz, 'PDF_image_Interpolate': False,
              'sampling': 'explicit_opaque_integer_windows_on_shared_native_media_device_grid', 'render_pixels': pixels,
              'preview_only': True, 'native_delivery_modified': False, 'reference_pixels_used': False,
              'source_pixel_equivalence': False, 'application_playback_verified': False}
    if len(json.dumps(result, separators=(',', ':')).encode()) > ordinary.MAX_DEFINITION_BYTES:
        raise ValueError('Native picture definition byte budget')
    return result
