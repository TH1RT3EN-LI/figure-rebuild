"""Target-grid previews of explicitly declared source image-only intervals.

Each recipe must reproduce the actual delivered picture bytes at its declared
4x/8x delivery scale before sampling 1x/2x/4x on the common canvas grid. Original
PDF image handles and contexts generate only the declared picture assets. The
complete scene still imports the delivered editable PPTX.
"""
import base64
import hashlib
import json
import math
import re
from io import BytesIO
from pathlib import Path

from . import artifact_image_preview as ordinary
from .pdf_image_render import render_native_pdf_image_interval
from .validate import confined

POLICY = 'delivered-native-picture-mupdf-device-grid-v4'
MAX_OBJECTS = 256
MAX_SOURCE_PAINTS = 100000


def source_request_assets(request):
    """Validate the explicit recipe and return its one immutable PDF input."""
    if (type(request) is not dict or set(request) != {
            'schema_version', 'source_pdf', 'page_index', 'source_transform', 'user_clip_pdf', 'objects'} or
            type(request['schema_version']) is not int or request['schema_version'] != 1 or
            type(request['page_index']) is not int or not 0 <= request['page_index'] < 4096 or
            type(request['objects']) is not list or not 1 <= len(request['objects']) <= MAX_OBJECTS):
        raise ValueError('Invalid explicit source image sampling request')
    source = request['source_pdf']
    if (type(source) is not dict or set(source) != {'path', 'sha256'} or
            type(source['path']) is not str or not source['path'] or
            type(source['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', source['sha256'])):
        raise ValueError('Source image sampling requires a bound PDF asset')
    path = Path(source['path'])
    if path.is_absolute() or '..' in path.parts or '\\' in source['path']:
        raise ValueError('Source image sampling asset must stay inside the asset root')
    def finite(values, length):
        return (type(values) is list and len(values) == length and
                all(type(v) in (int, float) and math.isfinite(v) for v in values))
    transform, clip = request['source_transform'], request['user_clip_pdf']
    if (not finite(transform, 6) or transform[1] != 0 or transform[2] != 0 or
            transform[0] <= 0 or transform[3] <= 0 or not finite(clip, 4) or
            clip[2] <= clip[0] or clip[3] <= clip[1]):
        raise ValueError('Source image sampling needs a positive diagonal mapping and finite clip')
    ids, previous = set(), -1
    for obj in request['objects']:
        if (type(obj) is not dict or set(obj) != {'id', 'paint_seqnos', 'source_bounds', 'delivery_sampling_scale'} or
                type(obj['id']) is not str or not obj['id'] or obj['id'] in ids or
                type(obj['delivery_sampling_scale']) is not int or obj['delivery_sampling_scale'] not in (4, 8)):
            raise ValueError('Invalid source image sampling identity or delivery scale')
        seqnos, bounds = obj['paint_seqnos'], obj['source_bounds']
        if (type(seqnos) is not list or not 1 <= len(seqnos) <= 64 or
                any(type(v) is not int or not 0 <= v < MAX_SOURCE_PAINTS for v in seqnos) or
                seqnos != list(range(seqnos[0], seqnos[-1] + 1)) or seqnos[0] <= previous):
            raise ValueError('Source image intervals must be consecutive, distinct and in native paint order')
        if (type(bounds) is not list or len(bounds) != 4 or any(type(v) is not int for v in bounds) or
                not 0 <= bounds[0] < bounds[2] <= 32768 or not 0 <= bounds[1] < bounds[3] <= 32768):
            raise ValueError('Source image sampling requires integral positive canvas storage bounds')
        ids.add(obj['id']); previous = seqnos[-1]
    return [dict(source)]


def prepare_source_image_preview(pptx, manifest, request, asset_root):
    source_request_assets(request)
    if asset_root is None:
        raise ValueError('Source image preview requires its immutable asset root')
    source = confined(Path(asset_root).resolve(), request['source_pdf']['path'])
    if not source.is_file() or not 0 < source.stat().st_size <= ordinary.MAX_BYTES:
        raise ValueError('Missing or oversized source image PDF')
    with source.open('rb') as stream:
        source_bytes = stream.read(ordinary.MAX_BYTES+1)
    if (len(source_bytes) > ordinary.MAX_BYTES or not source_bytes.startswith(b'%PDF-') or
            hashlib.sha256(source_bytes).hexdigest() != request['source_pdf']['sha256']):
        raise ValueError('Source image PDF bytes disagree with their frozen checksum')
    pptx, payload, xml, names, package, relationships, pictures, order = ordinary._native_picture_inputs(pptx, manifest)
    canvas = manifest.get('canvas')
    if (type(canvas) is not dict or not {'width', 'height'} <= set(canvas) or
            any(type(canvas[k]) not in (int, float) or not math.isfinite(canvas[k]) or
                not 0 < canvas[k] <= 32768 or int(canvas[k]) != canvas[k] for k in ('width', 'height'))):
        raise ValueError('Source image sampling requires integral canvas dimensions')
    width, height = int(canvas['width']), int(canvas['height'])
    if any(max(width*scale, height*scale) > 32768 or width*height*scale**2 > ordinary.MAX_SURFACE_PIXELS
           for scale in ordinary.SCALES):
        raise ValueError('Source image sampling canvas render surface budget')
    with ordinary.ZipFile(BytesIO(payload)) as archive:
        part = 'ppt/presentation.xml'
        if part not in archive.namelist() or archive.getinfo(part).file_size > ordinary.MAX_XML:
            raise ValueError('Missing or oversized native canvas part')
        presentation = ordinary.XmlDocument.parse(archive.read(part), part).root
        size = ordinary._one(presentation, 'p:sldSz')
        if [ordinary._integer(size.get(k), k, True) for k in ('cx', 'cy')] != [width*ordinary.EMU, height*ordinary.EMU]:
            raise ValueError('Source sampling canvas differs from actual native slide dimensions')
    t, clip = request['source_transform'], request['user_clip_pdf']
    if [clip[0]*t[0]+t[4], clip[1]*t[3]+t[5], clip[2]*t[0]+t[4], clip[3]*t[3]+t[5]] != [0, 0, width, height]:
        raise ValueError('Source clip and mapping must cover exactly the native canvas')
    if [o['id'] for o in request['objects']] != list(pictures):
        raise ValueError('Source sampling request must cover all actual native pictures once in paint order')
    planned, pixels = [], 0
    for obj in request['objects']:
        try:
            native, encoded, media, dimensions, crop = ordinary._plain_picture(pictures[obj['id']], package, relationships, 2)
        except ordinary.UnsupportedPicture as exc:
            raise ValueError('Unsupported declared source-sampled picture: '+str(exc)) from exc
        bounds = obj['source_bounds']; x, y, x1, y1 = bounds; w, h = x1-x, y1-y
        if (native != [v*ordinary.EMU for v in (x, y, w, h)] or x1 > width or y1 > height or any(crop.values())):
            raise ValueError('Source storage bounds disagree with actual native frame or source crop')
        delivery_scale = obj['delivery_sampling_scale']
        if dimensions != [w*delivery_scale, h*delivery_scale]:
            raise ValueError('Actual native media dimensions disagree with declared source delivery sampling')
        for scale in (delivery_scale, *ordinary.SCALES):
            if max(w*scale, h*scale) > 32768 or w*h*scale**2 > ordinary.MAX_SURFACE_PIXELS:
                raise ValueError('Source image sampling render surface budget')
            pixels += w*h*scale**2
        if pixels > ordinary.MAX_COMBINED_PIXELS:
            raise ValueError('Combined source image sampling pixel budget')
        planned.append(dict(recipe=obj, native=native, data=encoded, media=media, dimensions=dimensions, crop=crop))
    import pymupdf as fitz
    # Open from the same bounded frozen bytes for every replay. No cached image
    # decoder state or mutable job source can enter later target-scale renders.
    def sample(recipe, scale, global_grid):
        with fitz.open(stream=source_bytes, filetype='pdf') as doc:
            if request['page_index'] >= len(doc) or len(doc) > 4096:
                raise ValueError('Source image sampling page budget or missing page')
            page = doc[request['page_index']]
            bboxlog = page.get_bboxlog()
            if len(bboxlog) > MAX_SOURCE_PAINTS:
                raise ValueError('Source image sampling paint budget')
            return render_native_pdf_image_interval(page, bboxlog, recipe['paint_seqnos'],
                source_transform=t, source_bounds=recipe['source_bounds'], user_clip_pdf=clip,
                sampling_scale=scale, global_device_grid=global_grid)
    objects, proofs = [], []
    for item in planned:
        recipe = item['recipe']; id_ = recipe['id']
        original = sample(recipe, recipe['delivery_sampling_scale'], False)
        if original['asset_bytes'] != item['data']:
            raise ValueError('Source image recipe does not reproduce actual delivered media bytes: '+id_)
        x, y, x1, y1 = recipe['source_bounds']; w, h = x1-x, y1-y
        previews, receipts = [], []
        for scale in ordinary.SCALES:
            result = sample(recipe, scale, True); data = result['asset_bytes']
            previews.append({'scale': scale, 'position': dict(left=x, top=y, width=w, height=h),
                             'width': w*scale, 'height': h*scale, 'png_base64': base64.b64encode(data).decode('ascii'),
                             'png_sha256': hashlib.sha256(data).hexdigest()})
            receipts.append(result['receipt'])
        pic = pictures[id_]
        objects.append({'id': id_, 'native_position': dict(zip(('left', 'top', 'width', 'height'),
                          (v/ordinary.EMU for v in item['native']))),
                        'native_picture_xml_sha256': hashlib.sha256(ordinary.E.canonicalize(ordinary.E.tostring(pic), rewrite_prefixes=True).encode()).hexdigest(),
                        'native_media_part': item['media'], 'native_media_sha256': hashlib.sha256(item['data']).hexdigest(),
                        'native_media_dimensions': item['dimensions'], 'native_source_crop_units': item['crop'],
                        'previews': previews})
        proofs.append({'id': id_, 'delivery_media_replayed_byte_exactly': True,
                       'delivery_replay': original['receipt'], 'target_grid_replays': receipts})
    result = {'schema_version': 4, 'policy': POLICY, 'input_pptx': {'path': str(pptx), 'sha256': hashlib.sha256(payload).hexdigest()},
              'slide_part': names[0], 'native_slide_xml_sha256': hashlib.sha256(xml).hexdigest(), 'paint_order': order,
              'objects': objects, 'unsupported': [], 'scales': list(ordinary.SCALES),
              'source_sampling_request': request, 'source_sampling_proofs': proofs,
              'renderer': 'PyMuPDF', 'renderer_version': fitz.VersionBind, 'mupdf_version': fitz.VersionFitz,
              'sampling': 'source_image_only_intervals_on_global_target_device_grid', 'render_pixels': pixels,
              'preview_only': True, 'native_delivery_modified': False, 'reference_pixels_used': False,
              'source_pixel_equivalence': False, 'application_playback_verified': False}
    if len(json.dumps(result, separators=(',', ':')).encode()) > ordinary.MAX_DEFINITION_BYTES:
        raise ValueError('Native source picture definition byte budget')
    return result
