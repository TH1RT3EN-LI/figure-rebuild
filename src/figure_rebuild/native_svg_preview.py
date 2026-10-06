"""MuPDF previews of bounded flat, solid paths read from the delivered PPTX.

This opt-in renderer reads actual native coordinates, colors and stroke fields.
It admits no live text, pictures, groups, effects, gradients or source clipping.
The editable delivery is unchanged; source PDFs and reference pixels are never
inputs to this renderer. Native application appearance remains unverified.
"""
import argparse
import hashlib
import json
import math
import re
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile

from .artifact_image_preview import _native_picture_inputs, MAX_XML, MAX_DEFINITION_BYTES
from .artifact_stroke_preview import _one, _integer, _number, NS, EMU, MAX_COMMANDS
from .package import XmlDocument, relationship_map, resolve_target

POLICY = 'delivered-native-flat-solid-paths-mupdf-svg-v1'
SCALES = (1, 2, 4)


def _paint(node):
    nofill, solids = node.findall('a:noFill', NS), node.findall('a:solidFill', NS)
    if len(nofill) == 1 and not solids:
        return 'none', 1
    if nofill or len(solids) != 1 or len(solids[0]) != 1:
        raise ValueError('Native SVG requires an explicit solid RGB paint or noFill')
    color = solids[0][0]
    rgb = color.get('val', '')
    if (color.tag != '{'+NS['a']+'}srgbClr' or set(color.attrib) != {'val'} or
            len(rgb) != 6 or any(c not in '0123456789abcdefABCDEF' for c in rgb)):
        raise ValueError('Unsupported native SVG color')
    alpha = color.findall('a:alpha', NS)
    if len(alpha) != len(color) or len(alpha) > 1:
        raise ValueError('Unsupported native SVG color transform')
    opacity = _integer(alpha[0].get('val'), 'alpha') if alpha else 100000
    if not 0 <= opacity <= 100000:
        raise ValueError('Native SVG alpha range')
    return '#'+rgb, opacity/100000


def _shape(shape):
    prop = _one(shape, 'p:nvSpPr/p:cNvPr')
    if prop.get('hidden', '0') not in ('0', 'false') or shape.find('p:txBody', NS) is not None:
        raise ValueError('Native SVG requires visible paths without text')
    if any(c.tag not in ('{'+NS['p']+'}nvSpPr', '{'+NS['p']+'}spPr') for c in shape):
        raise ValueError('Unsupported native SVG shape style or extension')
    sp = _one(shape, 'p:spPr')
    allowed = {'xfrm', 'custGeom', 'solidFill', 'noFill', 'ln'}
    if any(c.tag not in {'{'+NS['a']+'}'+n for n in allowed} for c in sp):
        raise ValueError('Native SVG requires solid custom paths without effects')
    xf = _one(sp, 'a:xfrm')
    if (set(xf.attrib)-{'rot', 'flipH', 'flipV'} or _integer(xf.get('rot', '0'), 'rotation') % 21600000 or
            any(xf.get(k, '0') not in ('0', 'false') for k in ('flipH', 'flipV'))):
        raise ValueError('Rotated or reflected native SVG path')
    off, ext = _one(xf, 'a:off'), _one(xf, 'a:ext')
    ox, oy = [_integer(off.get(k), k)/EMU for k in ('x', 'y')]
    ew, eh = [_integer(ext.get(k), k, True)/EMU for k in ('cx', 'cy')]
    geom = _one(sp, 'a:custGeom')
    if any(c.tag != '{'+NS['a']+'}pathLst' for c in geom):
        raise ValueError('Native SVG geometry formulas and handles are unsupported')
    paths = _one(geom, 'a:pathLst')
    if len(paths) != 1 or paths[0].tag != '{'+NS['a']+'}path':
        raise ValueError('Native SVG requires one explicit custom path')
    path = paths[0]
    if (set(path.attrib)-{'w', 'h', 'fill', 'stroke', 'extrusionOk'} or
            path.get('fill', 'norm') not in ('norm', 'none') or
            path.get('stroke', '1') not in ('0', '1', 'false', 'true')):
        raise ValueError('Unsupported native SVG path properties')
    pw, ph = [_integer(path.get(k), k, True) for k in ('w', 'h')]
    commands = []
    if not 0 < len(path) <= 4096:
        raise ValueError('Native SVG per-path command budget')
    for node in path:
        op = node.tag.rsplit('}', 1)[-1]
        count = {'moveTo': 1, 'lnTo': 1, 'cubicBezTo': 3, 'close': 0}.get(op)
        if node.tag != '{'+NS['a']+'}'+op or count is None or len(node) != count or node.attrib:
            raise ValueError('Unsupported native SVG path command')
        coords = []
        for pt in node:
            if pt.tag != '{'+NS['a']+'}pt' or set(pt.attrib) != {'x', 'y'} or len(pt):
                raise ValueError('Unsupported native SVG point')
            coords.extend((ox+_integer(pt.get('x'), 'x')*ew/pw, oy+_integer(pt.get('y'), 'y')*eh/ph))
        if any(not math.isfinite(v) or abs(v) > 32768 for v in coords):
            raise ValueError('Native SVG coordinate budget')
        commands.append({'moveTo': 'M', 'lnTo': 'L', 'cubicBezTo': 'C', 'close': 'Z'}[op]+' '.join(map(_number, coords)))
    if path[0].tag != '{'+NS['a']+'}moveTo':
        raise ValueError('Native SVG path must start with moveTo')
    fill, fa = _paint(sp)
    ln = _one(sp, 'a:ln')
    if (set(ln.attrib)-{'w', 'cap', 'cmpd', 'algn'} or ln.get('cmpd', 'sng') != 'sng' or
            ln.get('algn', 'ctr') != 'ctr' or any(c.tag not in {'{'+NS['a']+'}'+n for n in
            ('solidFill', 'noFill', 'prstDash', 'round', 'bevel', 'miter', 'headEnd', 'tailEnd')} for c in ln)):
        raise ValueError('Unsupported native SVG line paint/effect')
    stroke, sa = _paint(ln)
    props = ''
    if path.get('fill', 'norm') == 'none':
        fill = 'none'
    if path.get('stroke', '1') in ('0', 'false'):
        stroke = 'none'
    if stroke != 'none':
        width = _integer(ln.get('w'), 'stroke width', True)/EMU
        cap = {'flat': 'butt', 'rnd': 'round', 'sq': 'square'}.get(ln.get('cap', 'flat'))
        joins = [q for q in ln if q.tag.rsplit('}', 1)[-1] in ('round', 'bevel', 'miter')]
        if cap is None or len(joins) != 1 or _one(ln, 'a:prstDash').get('val') != 'solid':
            raise ValueError('Native SVG requires solid stroke and explicit join')
        join = joins[0].tag.rsplit('}', 1)[-1]
        miter = _integer(joins[0].get('lim', '800000'), 'miter limit', True)/100000 if join == 'miter' else 1
        if any(q.get('type', 'none') != 'none' for n in ('headEnd', 'tailEnd') for q in ln.findall('a:'+n, NS)):
            raise ValueError('Native SVG line ends must be expanded source geometry')
        props = f' stroke-width="{_number(width)}" stroke-linecap="{cap}" stroke-linejoin="{join}" stroke-miterlimit="{_number(miter)}"'
    svg = f'<path d="{" ".join(commands)}" fill="{fill}" fill-rule="evenodd" fill-opacity="{_number(fa)}" stroke="{stroke}" stroke-opacity="{_number(sa)}"{props}/>'
    return svg, {'id': prop.get('name'), 'command_count': len(path), 'fill_rule': 'evenodd',
                 'native_shape_xml_sha256': hashlib.sha256(E.canonicalize(E.tostring(shape), rewrite_prefixes=True).encode()).hexdigest()}


def prepare_native_svg(pptx, manifest):
    if (type(manifest) is not dict or type(manifest.get('objects')) is not list or
            'source_canvas_clip' in manifest or any(type(o) is not dict or o.get('kind') != 'path' for o in manifest['objects'])):
        raise ValueError('Native SVG supports flat path-only slides without source clipping')
    pptx, payload, xml, _, _, _, pictures, order = _native_picture_inputs(pptx, manifest)
    if pictures:
        raise ValueError('Native SVG does not support pictures')
    ordered = sorted(enumerate(manifest['objects']), key=lambda v: (v[1].get('z_index', v[0]), v[0]))
    if order != [{'id': o['id'], 'type': 'shape'} for _, o in ordered]:
        raise ValueError('Native SVG actual path identity/order differs from declared source')
    root = XmlDocument.parse(xml, 'ppt/slides/slide1.xml').root
    with ZipFile(BytesIO(payload)) as z:
        part = 'ppt/presentation.xml'
        if z.getinfo(part).file_size > MAX_XML:
            raise ValueError('Native SVG canvas XML budget')
        presentation = XmlDocument.parse(z.read(part), part).root
        relpart = 'ppt/_rels/presentation.xml.rels'
        if z.getinfo(relpart).file_size > MAX_XML:
            raise ValueError('Native SVG presentation relationship XML budget')
        relationships = relationship_map(XmlDocument.parse(z.read(relpart), relpart))
    slides = _one(presentation, 'p:sldIdLst')
    if len(slides) != 1:
        raise ValueError('Native SVG requires a single-slide delivery')
    rid = slides[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
    rel = relationships.get(rid)
    if rel is None or not rel.get('Type', '').endswith('/slide') or resolve_target(part, rel) != 'ppt/slides/slide1.xml':
        raise ValueError('Native SVG first-slide relationship identity differs from sampled native slide')
    size = _one(presentation, 'p:sldSz')
    dimensions = [_integer(size.get(k), k, True) for k in ('cx', 'cy')]
    canvas = manifest['canvas']
    if any(type(canvas.get(k)) not in (int, float) or not math.isfinite(canvas[k]) or
           canvas[k] <= 0 or canvas[k] != int(canvas[k]) for k in ('width', 'height')):
        raise ValueError('Native SVG requires integral positive canvas dimensions')
    w, h = int(canvas['width']), int(canvas['height'])
    if dimensions != [w*EMU, h*EMU] or max(w, h)*4 > 32768 or w*h*16 > 16000000 or w*h*21 > 64*1024*1024:
        raise ValueError('Native SVG canvas identity or render surface budget')
    bg = _one(root, 'p:cSld/p:bg/p:bgPr')
    if len(bg) != 1:
        raise ValueError('Unsupported native SVG background effect')
    background, opacity = _paint(bg)
    if background.upper() != canvas.get('background', '#FFFFFF').upper() or opacity != 1:
        raise ValueError('Native SVG actual background differs from source declaration')
    pieces, records, commands = [], [], 0
    for shape in _one(root, 'p:cSld/p:spTree').findall('p:sp', NS):
        svg, record = _shape(shape); pieces.append(svg); records.append(record); commands += record['command_count']
        if commands > MAX_COMMANDS:
            raise ValueError('Native SVG combined command budget')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="{background}"/>'+''.join(pieces)+'</svg>'
    if len(svg.encode()) > MAX_XML:
        raise ValueError('Native SVG source byte budget')
    return {'schema_version': 1, 'policy': POLICY, 'input_pptx': {'path': str(pptx), 'sha256': hashlib.sha256(payload).hexdigest()},
            'native_slide_xml_sha256': hashlib.sha256(xml).hexdigest(), 'canvas': [w, h], 'objects': records,
            'svg': svg, 'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(), 'scales': list(SCALES),
            'preview_only': True, 'native_delivery_modified': False, 'reference_pixels_used': False,
            'source_pixel_equivalence': False, 'application_playback_verified': False}


def sample_native_svg(definition):
    import pymupdf as fitz
    if (type(definition) is not dict or definition.get('policy') != POLICY or
            type(definition.get('canvas')) is not list or len(definition['canvas']) != 2 or
            any(type(v) is not int or v <= 0 for v in definition['canvas']) or type(definition.get('svg')) is not str):
        raise ValueError('Invalid native SVG sample definition')
    w, h = definition['canvas']; svg = definition['svg'].encode()
    if len(svg) > MAX_XML or max(w, h)*4 > 32768 or w*h*16 > 16000000 or w*h*21 > 64*1024*1024:
        raise ValueError('Native SVG sample resource budget')
    if hashlib.sha256(svg).hexdigest() != definition['svg_sha256']:
        raise ValueError('Native SVG bytes differ from definition')
    root = XmlDocument.parse(svg, 'native.svg').root
    ns = '{http://www.w3.org/2000/svg}'
    if (root.tag != ns+'svg' or root.attrib != {'width': str(w), 'height': str(h), 'viewBox': f'0 0 {w} {h}'} or
            len(root) > 10001 or any(len(n) or n.tag not in (ns+'rect', ns+'path') for n in root) or
            any(any(k not in {'width', 'height', 'fill', 'd', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-opacity',
                             'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit'} for k in n.attrib) for n in root)):
        raise ValueError('Native SVG samples cannot contain external resources or other elements')
    for node in root:
        if any(value != 'none' and re.fullmatch('#[0-9a-fA-F]{6}', value) is None
               for k, value in node.attrib.items() if k in ('fill', 'stroke')):
            raise ValueError('Native SVG samples require literal solid RGB paint')
        for k in ('fill-opacity', 'stroke-opacity', 'stroke-width', 'stroke-miterlimit'):
            if k in node.attrib:
                value = float(node.get(k))
                if not math.isfinite(value) or not 0 <= value <= (1 if k.endswith('opacity') else 32768):
                    raise ValueError('Native SVG sample paint budget')
        if node.tag == ns+'path' and re.fullmatch('[MLCZ0-9eE .+\\-]+', node.get('d', '')) is None:
            raise ValueError('Unsupported native SVG sample command')
    outputs, samples = {}, []
    with fitz.open(stream=svg, filetype='svg') as doc:
        if len(doc) != 1 or doc[0].rect.width <= 0 or doc[0].rect.height <= 0:
            raise ValueError('Native SVG single-page decoding failed')
        for scale in SCALES:
            page = doc[0]; matrix = fitz.Matrix(w*scale/page.rect.width, h*scale/page.rect.height)
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            if [pix.width, pix.height] != [w*scale, h*scale]:
                raise ValueError('Native SVG raster size differs from native canvas')
            data = pix.tobytes('png'); outputs[scale] = data
            samples.append({'scale': scale, 'width': pix.width, 'height': pix.height, 'png_sha256': hashlib.sha256(data).hexdigest()})
    return outputs, {'schema_version': 1, 'policy': POLICY, 'input_pptx': definition['input_pptx'],
                     'svg_sha256': definition['svg_sha256'], 'samples': samples, 'renderer': 'PyMuPDF',
                     'renderer_version': fitz.VersionBind, 'mupdf_version': fitz.VersionFitz,
                     'native_delivery_modified': False, 'reference_pixels_used': False, 'application_playback_verified': False}


def render(config):
    from .native_preview import binding, _save
    from .source_inventory import _load_json
    if (config.get('preview_backend') != 'native-svg' or type(config.get('native_svg_preview_version')) is not int or
            config['native_svg_preview_version'] != 1 or config.get('base')):
        raise ValueError('Native SVG renderer requires explicit standalone version-1 provenance')
    run = Path(config['run']).resolve(); pptx = run/'validated-output/reconstruction.pptx'
    scene = _load_json(run/'resolved-scene.json', MAX_DEFINITION_BYTES)
    definition = prepare_native_svg(pptx, scene); outputs, receipt = sample_native_svg(definition)
    directory = run/'native-svg-preview'; directory.mkdir()
    definition_path, receipt_path, svg_path = directory/'definition.json', directory/'receipt.json', directory/'actual-native.svg'
    _save(definition_path, definition); _save(receipt_path, receipt); svg_path.write_text(definition['svg'], encoding='utf-8')
    for scale, data in outputs.items():
        (run/f'preview-{scale}x.png').write_bytes(data)
    if binding(pptx) != definition['input_pptx']:
        raise ValueError('Native PPT changed while sampling SVG previews')
    return {'renderer': 'MuPDF native PPT SVG', 'renderer_backend': 'delivered_native_flat_solid_paths',
            'rasterizer': 'PyMuPDF', 'rasterizer_version': receipt['renderer_version'], 'mupdf_version': receipt['mupdf_version'],
            'preview_scales': list(SCALES), 'application_playback_verified': False, 'reference_pixels_used': False,
            'native_delivery_modified': False, 'evidence': {'font_audit': binding(run/'font-audit.json'),
                'native_svg_definition': binding(definition_path), 'native_svg_receipt': binding(receipt_path), 'native_svg_source': binding(svg_path)},
            'preview_limitations': [{'code': 'native_svg_finite_path_only_preview', 'status': 'needs_review',
                'detail': 'Actual flat solid PPT paths use MuPDF SVG rasterization and native even-odd fill; source pixel equivalence and PowerPoint/WPS appearance remain unverified.'}]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config'); parser.add_argument('--preflight', action='store_true'); args = parser.parse_args(argv)
    if args.preflight:
        import pymupdf as fitz
        print(json.dumps({'PyMuPDF': fitz.VersionBind, 'MuPDF': fitz.VersionFitz})); return
    if not args.config:
        parser.error('--config is required')
    from .source_inventory import _load_json
    print(json.dumps(render(_load_json(Path(args.config), MAX_DEFINITION_BYTES)), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
