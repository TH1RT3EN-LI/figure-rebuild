"""Sample an explicit native filled-path prefix on a finite opaque pixel grid.

The prefix starts at the first actual paint and is composited over the actual
opaque slide background. Only previews change; no reference pixels, fonts or
editable native objects are replaced. This is a finite rasterization policy,
not a source/Office appearance equivalence claim.
"""
import argparse
import base64
import hashlib
import json
import math
from fractions import Fraction as Q
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from .artifact_image_preview import (EMU, MAX_COMBINED_PIXELS, MAX_DEFINITION_BYTES,
    MAX_SOURCE_PIXELS, MAX_SURFACE_PIXELS, MAX_XML, SCALES, _native_picture_inputs,
    _render_picture)
from .native_svg_preview import NS, _integer, _number, _one, _paint, _shape
from .package import XmlDocument, relationship_map, resolve_target

POLICY = 'delivered-native-filled-path-prefix-grid-v1'


def validate_request(request):
    if (type(request) is not dict or set(request) != {'schema_version', 'object_ids', 'frame_emu', 'pixel_grid'} or
            type(request['schema_version']) is not int or request['schema_version'] != 1):
        raise ValueError('Invalid native path-prefix grid request')
    ids, frame, grid = (request[k] for k in ('object_ids', 'frame_emu', 'pixel_grid'))
    if (type(ids) is not list or not 0 < len(ids) <= 10000 or
            any(type(v) is not str or not v for v in ids) or len(set(ids)) != len(ids)):
        raise ValueError('Native path-prefix identities must be unique and bounded')
    if (type(frame) is not list or len(frame) != 4 or any(type(v) is not int or abs(v) > 2147483647 for v in frame) or
            min(frame[:2]) < 0 or min(frame[2:]) <= 0):
        raise ValueError('Native path-prefix frame must use bounded integer EMU')
    if type(grid) is not list or len(grid) != 2 or any(type(v) is not int or not 0 < v <= 32768 for v in grid):
        raise ValueError('Invalid native path-prefix pixel grid')
    if grid[0]*grid[1] > MAX_SOURCE_PIXELS:
        raise ValueError('Native path-prefix source grid pixel budget')
    x, y, w, h = frame
    total = grid[0]*grid[1]
    for scale in SCALES:
        pw = -(-(x+w)*scale//EMU)-x*scale//EMU
        ph = -(-(y+h)*scale//EMU)-y*scale//EMU
        if max(pw, ph) > 32768 or pw*ph > MAX_SURFACE_PIXELS:
            raise ValueError('Native path-prefix target surface pixel budget')
        total += pw*ph
    if total > MAX_COMBINED_PIXELS:
        raise ValueError('Combined native path-prefix grid pixel budget')
    return total


def prepare_path_prefix_preview(pptx, manifest, request):
    cost = validate_request(request)
    if type(manifest) is not dict:
        raise ValueError('Native path-prefix manifest must be a record')
    if 'source_canvas_clip' in manifest:
        raise ValueError('Native path-prefix preview does not support source clipping')
    pptx, payload, xml, _, _, _, _, order = _native_picture_inputs(pptx, manifest)
    declared = sorted(enumerate(manifest['objects']), key=lambda v: (v[1].get('z_index', v[0]), v[0]))
    if order != [{'id': o['id'], 'type': 'image' if o['kind'] == 'image' else 'shape'} for _, o in declared]:
        raise ValueError('Actual native path-prefix paint order disagrees with declared scene')
    ids = request['object_ids']
    if ids != [v['id'] for v in order[:len(ids)]] or any(o['kind'] != 'path' for _, o in declared[:len(ids)]):
        raise ValueError('Native path-prefix must start at the first paint without text or pictures')
    with ZipFile(BytesIO(payload)) as z:
        parts = ('ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels')
        if any(z.getinfo(p).file_size > MAX_XML for p in parts):
            raise ValueError('Native path-prefix presentation XML budget')
        presentation = XmlDocument.parse(z.read(parts[0]), parts[0]).root
        relationships = relationship_map(XmlDocument.parse(z.read(parts[1]), parts[1]))
    slides = _one(presentation, 'p:sldIdLst')
    if len(slides) != 1:
        raise ValueError('Native path-prefix requires one active standalone slide')
    rid = slides[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
    rel = relationships.get(rid)
    if rel is None or not rel.get('Type', '').endswith('/slide') or resolve_target(parts[0], rel) != 'ppt/slides/slide1.xml':
        raise ValueError('Native path-prefix active slide relationship changed')
    size = _one(presentation, 'p:sldSz')
    dimensions = [_integer(size.get(k), k, True) for k in ('cx', 'cy')]
    canvas = manifest.get('canvas', {})
    if any(type(canvas.get(k)) not in (int, float) or not math.isfinite(canvas[k]) or
           canvas[k] <= 0 or canvas[k] != int(canvas[k]) for k in ('width', 'height')):
        raise ValueError('Native path-prefix requires integral positive canvas dimensions')
    if dimensions != [int(canvas[k])*EMU for k in ('width', 'height')]:
        raise ValueError('Native path-prefix actual canvas differs from declared canvas')
    cw, ch = (int(canvas[k]) for k in ('width', 'height'))
    if max(cw, ch)*4 > 32768 or cw*ch*16 > MAX_SURFACE_PIXELS or cw*ch*21 > MAX_COMBINED_PIXELS:
        raise ValueError('Native path-prefix complete canvas surface budget')
    x, y, w, h = request['frame_emu']
    if x+w > dimensions[0] or y+h > dimensions[1]:
        raise ValueError('Native path-prefix frame escapes actual canvas')
    root = XmlDocument.parse(xml, 'ppt/slides/slide1.xml').root
    bg = _one(root, 'p:cSld/p:bg/p:bgPr')
    background, opacity = _paint(bg)
    if len(bg) != 1 or opacity != 1 or background == 'none' or background.upper() != canvas.get('background', '#FFFFFF').upper():
        raise ValueError('Native path-prefix requires the actual opaque RGB canvas background')
    nodes = [n for n in _one(root, 'p:cSld/p:spTree') if n.tag.rsplit('}', 1)[-1] in ('sp', 'pic')]
    pieces, records, commands = [], [], 0
    for shape in nodes[:len(ids)]:
        svg, record = _shape(shape)
        sp = _one(shape, 'p:spPr')
        fill, _ = _paint(sp)
        if fill == 'none' or _paint(_one(sp, 'a:ln'))[0] != 'none':
            raise ValueError('Native path-prefix supports filled paths without painted strokes')
        path = _one(sp, 'a:custGeom/a:pathLst/a:path')
        if path.get('fill', 'norm') != 'norm':
            raise ValueError('Native path-prefix fill must paint the shape')
        off, ext = (_one(sp, 'a:xfrm/'+n) for n in ('a:off', 'a:ext'))
        ox, oy = [_integer(off.get(k), k) for k in ('x', 'y')]
        ew, eh = [_integer(ext.get(k), k, True) for k in ('cx', 'cy')]
        pw, ph = [_integer(path.get(k), k, True) for k in ('w', 'h')]
        for pt in path.iter('{'+NS['a']+'}pt'):
            px = ox+Q(_integer(pt.get('x'), 'x')*ew, pw)
            py = oy+Q(_integer(pt.get('y'), 'y')*eh, ph)
            if not x <= px <= x+w or not y <= py <= y+h:
                raise ValueError('Native path-prefix control hull escapes sampling frame')
        pieces.append(svg); records.append(record); commands += record['command_count']
        if commands > 200000:
            raise ValueError('Native path-prefix combined command budget')
    gw, gh = request['pixel_grid']
    view = ' '.join(_number(v/EMU) for v in (x, y, w, h))
    rectangle = f'<rect x="{_number(x/EMU)}" y="{_number(y/EMU)}" width="{_number(w/EMU)}" height="{_number(h/EMU)}" fill="{background}"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{gw}" height="{gh}" viewBox="{view}">'+rectangle+''.join(pieces)+'</svg>'
    if len(svg.encode()) > MAX_XML:
        raise ValueError('Native path-prefix SVG byte budget')
    import pymupdf as fitz
    with fitz.open(stream=svg.encode(), filetype='svg') as doc:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(gw/page.rect.width, gh/page.rect.height), alpha=False, colorspace=fitz.csRGB)
        if (pix.width, pix.height) != (gw, gh):
            raise ValueError('Native path-prefix source grid dimensions changed')
        encoded = pix.tobytes('png')
    previews, _ = _render_picture(tuple(request['frame_emu']), encoded, fitz,
                                   MAX_COMBINED_PIXELS-gw*gh, dict(l=0, t=0, r=0, b=0))
    result = dict(schema_version=1, policy=POLICY, preview_only=True, native_delivery_modified=False,
        reference_pixels_used=False, source_pixel_equivalence=False, application_playback_verified=False,
        input_pptx=dict(path=str(pptx), sha256=hashlib.sha256(payload).hexdigest()),
        native_slide_xml_sha256=hashlib.sha256(xml).hexdigest(), request=request, paint_order=order,
        objects=records, native_canvas_background=background, sampling_svg=svg,
        sampling_svg_sha256=hashlib.sha256(svg.encode()).hexdigest(),
        opaque_grid_png_base64=base64.b64encode(encoded).decode('ascii'),
        opaque_grid_png_sha256=hashlib.sha256(encoded).hexdigest(), previews=previews,
        source_and_target_pixels=cost, renderer='PyMuPDF', renderer_version=fitz.VersionBind, mupdf_version=fitz.VersionFitz)
    if len(json.dumps(result).encode()) > MAX_DEFINITION_BYTES:
        raise ValueError('Native path-prefix definition byte budget')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pptx', 'manifest', 'request', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    from .source_inventory import _load_json
    manifest = _load_json(Path(args.manifest), MAX_DEFINITION_BYTES)
    request = _load_json(Path(args.request), MAX_DEFINITION_BYTES)
    result = prepare_path_prefix_preview(args.pptx, manifest, request)
    with Path(args.output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False); stream.write('\n')


if __name__ == '__main__':
    main()
