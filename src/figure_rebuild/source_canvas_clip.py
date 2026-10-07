"""Explicit, replay-verified source glyph clipping by a standalone slide canvas.

This is deliberately not a general out-of-bounds authoring option. V1 accepts
only filled, nonzero-winding source glyph outlines without source clips, strokes,
masks or uncertain native contexts. Caller receipts never authorize validation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
import re
import struct
import zipfile
from xml.etree import ElementTree as ET


class SourceCanvasClipError(ValueError):
    """The source viewport/glyph or final native boundary was not verified."""


MAX_SOURCE_PIXELS = 16_000_000
MAX_SOURCE_PNG_BYTES = 64 * 1024 * 1024


def _fail(message):
    raise SourceCanvasClipError('source_canvas_clip: ' + message)


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _record(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(label + ' requires exactly ' + ', '.join(keys))
    return value


def _asset(root, value, keys, label):
    from .validate import confined, digest
    _record(value, keys, label)
    if not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        _fail(label + ' requires a lowercase SHA256')
    path = confined(root, value['path'])
    budget = 134217728 if label == 'source_pdf' else 16777216
    if path.stat().st_size > budget:
        _fail(label + ' bytes exceed bounded input budget')
    if digest(path) != value['sha256']:
        _fail(label + ' hash changed')
    return path


def _possibly_intersects(box, roi):
    # MuPDF's infinite sentinel is *not* reliably handled by Rect.intersects.
    if (not isinstance(box, (list, tuple)) or len(box) != 4 or
            not all(_number(v) for v in box) or
            any(abs(v) >= 2147483520 for v in box) or box[2] <= box[0] or box[3] <= box[1]):
        return True
    return not (box[2] < roi[0] or box[3] < roi[1] or box[0] > roi[2] or box[1] > roi[3])


def _native_context(pdf, page, roi):
    from .pdf_paint_context import inspect_pdf_paint_context
    report = inspect_pdf_paint_context(pdf, page=page)
    if report.get('identity_complete') is not True:
        _fail('native context identity is incomplete')
    admitted, outside, definitions = [], [], []
    groups = {g['group_id']: g for g in report['groups']}
    for paint in report['paints']:
        if paint['role'] == 'mask_definition':
            definitions.append(paint['source_seqno'])
            continue
        if not _possibly_intersects(paint['bbox_pdf_pt'], roi):
            outside.append(paint['source_seqno'])
            continue
        if (paint['role'] != 'normal' or not paint['page_object_allowed'] or
                paint['unresolved'] or paint['clip_ids'] or paint['pattern_depth'] or
                paint['active_mask_ids'] or paint['active_image_mask_clip_ids']):
            _fail('unknown or clipped native context potentially intersects ROI at paint ' + str(paint['source_seqno']))
        # V1 is conservative about native groups. Isolation can affect blend
        # semantics even where an outlined SVG has lost that context.
        if paint['group_ids']:
            for gid in paint['group_ids']:
                g = groups.get(gid)
                if g is None or g['isolated'] or g['knockout'] or g['blendmode'] != 0 or g['alpha'] != 1 or g['colorspace'] not in (None, 'DeviceRGB'):
                    _fail('unsupported native group potentially intersects ROI')
        admitted.append(paint['source_seqno'])
    return {'source_pdf_sha256': report['source_pdf_sha256'], 'bboxlog_sha256': report['bboxlog_sha256'],
            'paint_count': report['paint_count'], 'identity_complete': True,
            'admitted_roi_paint_seqnos': admitted, 'outside_roi_seqnos': outside,
            'mask_definition_seqnos_not_page_objects': definitions,
            'scope': 'conservative whole ROI native context guard; not per-glyph native identity or semantic approval'}


def verify_source_canvas_clip(manifest, root):
    """Replay original source evidence and return a fresh proof, or None.

    The PNG source is compared to a pristine-document RGB raster, the SVG to a
    pristine-document text_as_path extraction, and each selected glyph to the
    strict maintained source lowerer. Pixel-aligned ROI is mandatory in v1.
    Callers must independently refuse base/merge/nonidentity placement.
    """
    if not isinstance(manifest, dict):
        _fail('manifest must be a record')
    if 'source_canvas_clip' not in manifest:
        return None
    cfg = _record(manifest['source_canvas_clip'], ['version', 'mode', 'source_pdf', 'source_svg', 'raster', 'objects'], 'declaration')
    if type(cfg['version']) is not int or cfg['version'] != 1 or cfg['mode'] != 'standalone_slide_canvas':
        _fail('unsupported version or mode')
    pdf = _asset(root, cfg['source_pdf'], ['path', 'sha256', 'page'], 'source_pdf')
    svg = _asset(root, cfg['source_svg'], ['path', 'sha256'], 'source_svg')
    page = cfg['source_pdf']['page']
    if type(page) is not int or page < 1:
        _fail('page must be a one-based integer')
    if pdf.stat().st_size > 134217728 or svg.stat().st_size > 16777216:
        _fail('source bytes exceed bounded input budget')
    raster = _record(cfg['raster'], ['pymupdf_version', 'scale', 'roi_pdf_points', 'irect', 'alpha', 'colorspace'], 'raster')
    if raster['alpha'] is not False or raster['colorspace'] != 'DeviceRGB':
        _fail('v1 requires an opaque DeviceRGB source raster')
    scale, roi, irect = raster['scale'], raster['roi_pdf_points'], raster['irect']
    if not _number(scale) or not 0 < scale <= 64:
        _fail('invalid raster scale')
    if (not isinstance(roi, list) or len(roi) != 4 or not all(_number(v) for v in roi) or
            roi[2] <= roi[0] or roi[3] <= roi[1]):
        _fail('invalid positive ROI')
    # Certify the actual operands, not a rounded floating product (0.1*10
    # happens to be 1.0 although the exact binary operands do not multiply to
    # an integer). MuPDF's native crop/matrix operands are float32 as well.
    scaled_exact = [Fraction(v) * Fraction(scale) for v in roi]
    try:
        native_exact = all(struct.unpack('f', struct.pack('f', v))[0] == v for v in [scale, *roi])
    except (OverflowError, struct.error):
        native_exact = False
    if (not native_exact or not all(v.denominator == 1 for v in scaled_exact) or
            not isinstance(irect, list) or len(irect) != 4 or not all(type(v) is int for v in irect) or
            irect != [int(v) for v in scaled_exact]):
        _fail('v1 ROI must map exactly to the declared integer raster pixel boundaries')
    canvas = manifest.get('canvas', {})
    if not isinstance(canvas, dict):
        _fail('canvas must be a record')
    if not isinstance(canvas.get('background', '#FFFFFF'), str) or canvas.get('background', '#FFFFFF').lower() != '#ffffff':
        _fail('v1 requires the opaque white native PDF raster backdrop')
    width, height = irect[2] - irect[0], irect[3] - irect[1]
    if (not 0 < width <= 20000 or not 0 < height <= 20000 or
            type(canvas.get('width')) is not int or type(canvas.get('height')) is not int or
            canvas.get('width') != width or canvas.get('height') != height):
        _fail('canvas differs from the exact source pixel viewport')
    if width * height > MAX_SOURCE_PIXELS:
        _fail('source viewport exceeds the 16000000 pixel replay budget')
    from .validate import confined, digest
    source = manifest.get('source', {})
    if not isinstance(source, dict):
        _fail('source must be a record')
    original = confined(root, source.get('path'))
    if original.stat().st_size > MAX_SOURCE_PNG_BYTES:
        _fail('original PNG exceeds the 64 MiB byte budget')
    if original.suffix.lower() != '.png' or digest(original) != source.get('sha256'):
        _fail('original PNG source bytes changed or are not PNG')
    if (type(source.get('width')) is not int or type(source.get('height')) is not int or
            source.get('width') != width or source.get('height') != height):
        _fail('original PNG dimensions differ from viewport')
    try:
        import pymupdf as fitz
        from PIL import Image
    except ImportError as exc:
        raise SourceCanvasClipError('source_canvas_clip requires PyMuPDF and Pillow source replay') from exc
    if raster['pymupdf_version'] != fitz.VersionBind:
        _fail('source extraction PyMuPDF version changed')
    pdf_bytes = pdf.read_bytes()
    if hashlib.sha256(pdf_bytes).hexdigest() != cfg['source_pdf']['sha256']:
        _fail('PDF changed while being opened')
    with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
        if page > len(document):
            _fail('page is outside source PDF')
        sheet = document[page - 1]
        if sheet.rotation or not sheet.rect.contains(fitz.Rect(roi)):
            _fail('v1 requires an unrotated page containing the complete ROI')
        fresh_svg = sheet.get_svg_image(text_as_path=True).encode()
    if hashlib.sha256(fresh_svg).hexdigest() != cfg['source_svg']['sha256']:
        _fail('SVG is not the fresh outlined source page')
    # Separate documents prevent SVG/image info cache warming from changing a
    # native image sampling path. No extraction method touches this document.
    with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
        pix = document[page - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(roi), colorspace=fitz.csRGB, alpha=False)
        if list(pix.irect) != irect or (pix.width, pix.height) != (width, height):
            _fail('actual source pixmap irect differs from exact pixel viewport')
        try:
            with Image.open(original) as image:
                if image.size != (width, height) or image.mode != 'RGB' or image.tobytes() != pix.samples:
                    _fail('source PNG pixels differ from pristine original PDF ROI')
        except Image.DecompressionBombError as exc:
            raise SourceCanvasClipError('source_canvas_clip: source PNG exceeds safe image dimensions') from exc
        pixel_sha = hashlib.sha256(pix.samples).hexdigest()
    native = _native_context(pdf, page, roi)
    if native['source_pdf_sha256'] != cfg['source_pdf']['sha256']:
        _fail('PDF changed during native context replay')
    from .pdf_source import extract_outlined_svg, outline_paths
    document = extract_outlined_svg(fresh_svg, max_total_commands=250000)
    paints = {p.source_id: p for p in document.paints}
    selections = cfg['objects']
    objects = manifest.get('objects')
    if (not isinstance(selections, list) or not 1 <= len(selections) <= 10000 or
            not isinstance(objects, list) or not 1 <= len(objects) <= 10000):
        _fail('invalid selected object list')
    object_map = {}
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str) or obj['id'] in object_map:
            _fail('objects require unique stable identities')
        object_map[obj['id']] = obj
    ordered = sorted(enumerate(objects), key=lambda row: (row[1].get('z_index', row[0]), row[0]))
    order = {o['id']: i for i, (_, o) in enumerate(ordered)}
    used_objects, used_paints, verified = set(), set(), []
    transform = (scale, 0., 0., scale, -irect[0], -irect[1])
    for item in selections:
        _record(item, ['object_id', 'source_paint_id'], 'selected object')
        oid, pid = item['object_id'], item['source_paint_id']
        if not isinstance(oid, str) or not isinstance(pid, str) or oid not in object_map or pid not in paints or oid in used_objects or pid in used_paints:
            _fail('missing or duplicated source/object identity')
        used_objects.add(oid); used_paints.add(pid)
        paint, obj = paints[pid], object_map[oid]
        if paint.kind != 'glyph' or paint.clips or paint.unsupported:
            _fail('v1 permits only supported glyphs without internal source clips')
        if paint.style['fill-rule'] != 'nonzero' or paint.style['stroke'] != 'none' or paint.style['stroke-dasharray'] != 'none':
            _fail('v1 glyph requires nonzero fill and no stroke/dash')
        result = outline_paths(document, glyph_mode='outline', paint_ids=[pid], region=None, transform=transform, max_clip_overhang=0)
        if result.skipped or len(result.objects) != 1:
            _fail('selected glyph must lower to exactly one visible source path')
        expected = result.objects[0]
        if expected['style']['fill'] == 'none' or expected['style']['opacity'] <= 0:
            _fail('selected glyph has no visible fill')
        if (obj.get('kind') != 'path' or _canonical(obj.get('commands')) != _canonical(expected['commands']) or
                _canonical(obj.get('style')) != _canonical(expected['style'])):
            _fail('selected glyph commands or style differ from the full original source')
        coords = [v for command in expected['commands'] for point in command.values() for v in point.values()]
        if not coords or any(not _number(v) or abs(v) > 100000 for v in coords):
            _fail('selected glyph exceeds bounded native coordinate range')
        verified.append({'object_id': oid, 'source_paint_id': pid, 'source_paint_index': paint.paint_index,
                         'object_order': order[oid], 'commands_sha256': _canonical(expected['commands']),
                         'style_sha256': _canonical(expected['style']), 'command_count': len(expected['commands']),
                         'source_xml_sha256': hashlib.sha256(paint.source_xml.encode()).hexdigest(),
                         'native_kind': 'source_glyph_outline_not_live_text'})
    sorted_records = sorted(verified, key=lambda r: r['object_order'])
    if [r['source_paint_index'] for r in sorted_records] != sorted(r['source_paint_index'] for r in sorted_records):
        _fail('selected source glyph painter order changed')
    return {'schema_version': 1, 'mode': cfg['mode'], 'status': 'VERIFIED_SOURCE_VIEWPORT_GLYPHS',
            'canonical_manifest_sha256': _canonical(manifest), 'declaration_sha256': _canonical(cfg),
            'source_pdf_sha256': cfg['source_pdf']['sha256'], 'source_svg_sha256': cfg['source_svg']['sha256'],
            'source_png_sha256': source['sha256'], 'source_rgb_samples_sha256': pixel_sha,
            'page': page, 'pymupdf_version': fitz.VersionBind, 'roi_pdf_points': roi, 'irect': irect,
            'target_transform': list(transform), 'canvas': [width, height],
            'object_ids': [r['object_id'] for r in sorted_records], 'objects': sorted_records,
            'native_context': native, 'scope': 'standalone canvas clipping of verified selected glyph outlines only',
            'semantic_fidelity': 'NOT_PROVIDED', 'visual_review_required': True,
            'base_deck_merge_or_nonidentity_placement_allowed': False}


def verify_native_canvas_clip(pptx, manifest, receipt):
    """Verify final slide viewport and full selected native glyph commands.

    This checks a fresh trusted source receipt against the current manifest;
    caller must obtain it from verify_source_canvas_clip during this build.
    """
    if 'source_canvas_clip' not in manifest:
        if receipt is not None:
            _fail('unexpected native source viewport receipt')
        return None
    if not isinstance(receipt, dict) or receipt.get('canonical_manifest_sha256') != _canonical(manifest) or receipt.get('declaration_sha256') != _canonical(manifest['source_canvas_clip']) or receipt.get('status') != 'VERIFIED_SOURCE_VIEWPORT_GLYPHS':
        _fail('native verification requires the current fresh source proof')
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main', 'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
          'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
    def tag(prefix, name):
        return '{' + ns[prefix] + '}' + name
    def only(parent, query, label):
        found = parent.findall(query, ns)
        if len(found) != 1:
            _fail('native requires exactly one ' + label)
        return found[0]
    def leaf(node, attrs, label):
        if set(node.attrib) != set(attrs) or len(node) or (node.text and node.text.strip()):
            _fail('unverified native leaf structure: ' + label)
    def creation_metadata(node, location):
        # The author emits only these non-rendering creation identities.
        # Unknown extensions can carry visible/animated behavior and reject.
        if location == 'slide':
            prefix, uri, child_tag, attr = 'p', '{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}', '{http://schemas.microsoft.com/office/powerpoint/2010/main}creationId', 'val'
        else:
            prefix, uri, child_tag, attr = 'a', '{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}', '{http://schemas.microsoft.com/office/drawing/2014/main}creationId', 'id'
        if node.tag != tag(prefix, 'extLst') or node.attrib or len(node) != 1:
            _fail('unsupported native creation metadata')
        ext = node[0]
        if ext.tag != tag(prefix, 'ext') or ext.attrib != {'uri': uri} or len(ext) != 1 or ext[0].tag != child_tag:
            _fail('unsupported native creation metadata extension')
        leaf(ext[0], {attr}, 'creation identity')
    with zipfile.ZipFile(pptx) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            _fail('native package has duplicate entries')
        pres = ET.fromstring(archive.read('ppt/presentation.xml'))
        if (pres.tag != tag('p', 'presentation') or pres.attrib or
                any(node.tag not in [tag('p', n) for n in ('sldMasterIdLst', 'notesMasterIdLst', 'sldIdLst', 'sldSz', 'notesSz')] for node in pres)):
            _fail('native presentation root namespace or static context changed')
        slides = only(pres, 'p:sldIdLst', 'slide list')
        if len(slides) != 1 or slides[0].tag != tag('p', 'sldId'):
            _fail('native canvas mode requires exactly one standalone slide')
        slide_id = slides[0]
        identity = slide_id.get('id', '')
        if (set(slide_id.attrib) != {'id', tag('r', 'id')} or len(slide_id) or
                not re.fullmatch(r'[0-9]+', identity) or not 256 <= int(identity) <= 2147483647):
            _fail('native displayed slide identity or context is invalid')
        size = only(pres, 'p:sldSz', 'slide size')
        expected_size = [manifest['canvas'][key] * 9525 for key in ('width', 'height')]
        if [int(size.get(k)) for k in ('cx', 'cy')] != expected_size:
            _fail('native slide dimensions differ from source viewport')
        rid = slides[0].get(tag('r', 'id'))
        rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        if rels.tag != tag('rel', 'Relationships') or not rid:
            _fail('native presentation relationships are invalid')
        ids = [r.get('Id') for r in rels]
        if len(ids) != len(set(ids)):
            _fail('duplicate native presentation relationship identity')
        matches = [r for r in rels if r.get('Id') == rid]
        if len(matches) != 1:
            _fail('displayed native slide relationship is missing')
        rel = matches[0]
        if (rel.tag != tag('rel', 'Relationship') or rel.get('Type') != ns['r']+'/slide' or
                rel.get('TargetMode', 'Internal') != 'Internal'):
            _fail('displayed native slide relationship is unsupported')
        from .package import resolve_target
        part = resolve_target('ppt/presentation.xml', rel)
        if not part.startswith('ppt/slides/') or not part.endswith('.xml'):
            _fail('displayed native slide target is outside the bounded slide namespace')
        slide = ET.fromstring(archive.read(part))
    if slide.tag != tag('p', 'sld') or slide.attrib or any(node.tag != tag('p', 'cSld') for node in slide):
        _fail('native slide has unsupported static/animation context')
    content = only(slide, 'p:cSld', 'slide content')
    if content.attrib or any(node.tag not in [tag('p', n) for n in ('bg', 'spTree', 'extLst')] for node in content) or len(content.findall('p:extLst', ns)) > 1:
        _fail('native slide content has unsupported context')
    for ext in content.findall('p:extLst', ns):
        creation_metadata(ext, 'slide')
    bg = only(content, 'p:bg', 'explicit slide background')
    background = only(bg, 'p:bgPr', 'background properties')
    if bg.attrib or len(bg) != 1 or background.attrib:
        _fail('native slide background has unverified context')
    solid_bg = only(background, 'a:solidFill', 'opaque white background')
    bg_color = only(solid_bg, 'a:srgbClr', 'background color')
    if (solid_bg.attrib or set(bg_color.attrib) != {'val'} or bg_color.get('val', '').lower() != 'ffffff' or len(solid_bg) != 1 or
            len(background) != 1 or len(bg_color) > 1 or any(
                node.tag != tag('a', 'alpha') or node.get('val') != '100000' for node in bg_color)):
        _fail('native slide background differs from the opaque white source viewport')
    for alpha in bg_color:
        leaf(alpha, {'val'}, 'background alpha')
    tree = only(content, 'p:spTree', 'shape tree')
    if tree.attrib:
        _fail('native shape tree has unverified attributes')
    native_ids = set()
    for identity in tree.findall('.//p:cNvPr', ns):
        value = identity.get('id', '')
        if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 4294967295 or int(value) in native_ids:
            _fail('native shape identity is absent, invalid or numerically duplicated')
        native_ids.add(int(value))
    group_properties = only(tree, 'p:grpSpPr', 'root group properties')
    # Current standalone authoring emits an empty xfrm. A later identity
    # representation needs explicit proof, not implicit default transforms.
    if group_properties.attrib or len(group_properties) > 1 or any(
            node.tag != tag('a', 'xfrm') or node.attrib or len(node) for node in group_properties):
        _fail('native root shape tree has an unverified transform or effects')
    shapes = {}
    for node in tree.findall('p:sp', ns):
        identity = node.find('p:nvSpPr/p:cNvPr', ns)
        if identity is not None:
            name = identity.get('name')
            if name in shapes:
                _fail('duplicate native shape name')
            shapes[name] = node
    records = []; objects = {o['id']: o for o in manifest['objects']}
    selected = [x['object_id'] for x in manifest['source_canvas_clip']['objects']]
    if set(selected) != set(receipt['object_ids']):
        _fail('native source proof object selection changed')
    if [name for name in shapes if name in selected] != receipt['object_ids']:
        _fail('selected glyph native painter order changed')
    for oid in receipt['object_ids']:
        obj, shape = objects[oid], shapes.get(oid)
        if shape is None:
            _fail('selected glyph missing or nested in a transformed native group: ' + oid)
        if shape.attrib or any(node.tag not in (tag('p', 'nvSpPr'), tag('p', 'spPr')) for node in shape):
            _fail('selected glyph has unverified native text or theme properties')
        nv = only(shape, 'p:nvSpPr', 'shape identity container')
        if nv.attrib or any(node.tag not in [tag('p', n) for n in ('cNvPr', 'cNvSpPr', 'nvPr')] for node in nv):
            _fail('selected glyph has unverified native identity context')
        identity = only(nv, 'p:cNvPr', 'shape identity')
        if set(identity.attrib).difference({'id', 'name', 'descr', 'hidden'}) or len(identity) > 1:
            _fail('selected glyph has unverified native identity properties')
        for ext in identity:
            creation_metadata(ext, 'shape')
        for name in ('cNvSpPr', 'nvPr'):
            containers = nv.findall('p:' + name, ns)
            if len(containers) > 1:
                _fail('selected glyph duplicates native identity properties')
            for container in containers:
                if container.attrib or len(container) > (1 if name == 'cNvSpPr' else 0):
                    _fail('selected glyph has unverified native application properties')
                for lock in container:
                    if lock.tag != tag('a', 'spLocks') or lock.attrib != {'noGrp': '1'}:
                        _fail('selected glyph has unverified native editing properties')
                    leaf(lock, {'noGrp'}, 'shape locks')
        markers = [part.strip() for part in identity.get('descr', '').split(';')]
        if 'source_canvas_clip_required=true' not in markers:
            _fail('selected glyph is missing its intrinsic no-merge canvas clipping marker')
        if identity.get('hidden', '0') not in ('0', 'false'):
            _fail('selected glyph was hidden')
        properties = only(shape, 'p:spPr', 'shape properties')
        if properties.attrib or any(node.tag not in [tag('a', n) for n in ('xfrm', 'custGeom', 'solidFill', 'ln')] for node in properties):
            _fail('selected glyph has unverified native paint effects')
        solid = only(properties, 'a:solidFill', 'glyph fill')
        fill = only(solid, 'a:srgbClr', 'glyph fill color')
        alpha = fill.find('a:alpha', ns) if fill is not None else None
        opacity = math.floor(obj['style']['opacity'] * 100000 + .5)
        if (solid.attrib or set(fill.attrib) != {'val'} or len(solid) != 1 or fill.get('val', '').lower() != obj['style']['fill'][1:].lower() or
                any(node.tag != tag('a', 'alpha') for node in fill) or
                len(fill) > 1 or (100000 if alpha is None else int(alpha.get('val'))) != opacity):
            _fail('selected glyph native fill or opacity changed')
        if alpha is not None:
            leaf(alpha, {'val'}, 'glyph alpha')
        line = only(properties, 'a:ln', 'glyph line')
        if (len(line.findall('a:noFill', ns)) != 1 or len(line.findall('a:prstDash', ns)) > 1 or
                set(line.attrib).difference({'w'}) or line.get('w', '0') != '0' or
                any(node.tag not in [tag('a', n) for n in ('noFill', 'prstDash')] for node in line)):
            _fail('selected glyph acquired an unverified native stroke')
        for item in line:
            leaf(item, set() if item.tag == tag('a', 'noFill') else {'val'}, 'glyph line style')
            if item.tag == tag('a', 'prstDash') and item.get('val') != 'solid':
                _fail('selected glyph has an unverified native dash')
        points = [(v['x'], v['y']) for c in obj['commands'] for op, v in c.items() if op in ('moveTo', 'lineTo')]
        points += [(v[kx], v[ky]) for c in obj['commands'] for op, v in c.items() if op == 'cubicTo' for kx, ky in [('x1','y1'),('x2','y2'),('x','y')]]
        x0, y0 = min(x for x,y in points), min(y for x,y in points)
        w, h = max(.01, max(x for x,y in points)-x0), max(.01, max(y for x,y in points)-y0)
        xf = only(properties, 'a:xfrm', 'glyph transform')
        if xf.get('rot','0') != '0' or xf.get('flipH','0') not in ('0','false') or xf.get('flipV','0') not in ('0','false'):
            _fail('selected glyph native orientation changed')
        off, ext = only(xf, 'a:off', 'glyph offset'), only(xf, 'a:ext', 'glyph extent')
        if len(xf) != 2 or set(xf.attrib).difference({'rot', 'flipH', 'flipV'}):
            _fail('selected glyph has an unverified native transform')
        leaf(off, {'x', 'y'}, 'glyph offset'); leaf(ext, {'cx', 'cy'}, 'glyph extent')
        actual_frame = [int(off.get('x')),int(off.get('y')),int(ext.get('cx')),int(ext.get('cy'))]
        if any(abs(actual-expected)>2 for actual,expected in zip(actual_frame,[v*9525 for v in [x0,y0,w,h]])):
            _fail('selected glyph native source frame or placement changed')
        geometry = only(properties, 'a:custGeom', 'glyph custom geometry')
        path_list = only(geometry, 'a:pathLst', 'glyph path list')
        if geometry.attrib or len(geometry) != 1 or path_list.attrib:
            _fail('selected glyph custom geometry has unverified context')
        paths = list(path_list)
        if len(paths)!=1 or paths[0].tag != tag('a', 'path') or len(paths[0])!=len(obj['commands']):
            _fail('selected glyph native command/subpath count changed')
        path=paths[0]
        if set(path.attrib).difference({'w', 'h', 'fill'}) or path.get('fill', 'norm') != 'norm':
            _fail('selected glyph native path fill mode changed')
        if [int(path.get(k)) for k in ('w','h')] != [max(1,math.floor(v*9525+.5)) for v in (w,h)]:
            _fail('selected glyph native coordinate units changed')
        for command, node in zip(obj['commands'],path):
            op,v=next(iter(command.items()))
            if node.attrib or node.tag != tag('a', {'moveTo':'moveTo','lineTo':'lnTo','cubicTo':'cubicBezTo','close':'close'}[op]):
                _fail('selected glyph native path operation changed')
            xy=[] if op=='close' else [(v['x'],v['y'])] if op!='cubicTo' else [(v[kx],v[ky]) for kx,ky in [('x1','y1'),('x2','y2'),('x','y')]]
            if len(node)!=len(xy):
                _fail('selected glyph native control count changed')
            for pt,(x,y) in zip(node,xy):
                if pt.tag != tag('a', 'pt') or set(pt.attrib) != {'x', 'y'} or len(pt):
                    _fail('selected glyph native control tag or attributes changed')
                if [int(pt.get(k)) for k in ('x','y')] != [math.floor((x-x0)*9525+.5),math.floor((y-y0)*9525+.5)]:
                    _fail('selected glyph original native control changed')
        records.append({'object_id':oid,'full_native_commands_verified':True,'command_count':len(path),'frame_emu':actual_frame})
    return {'schema_version':1,'status':'VERIFIED_NATIVE_SOURCE_VIEWPORT','pptx_sha256':hashlib.sha256(Path(pptx).read_bytes()).hexdigest(),
            'source_proof_sha256':_canonical(receipt),'slide_emu':expected_size,'displayed_slide_part':part,'objects':records,
            'semantic_fidelity':'NOT_PROVIDED','visual_review_required':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--base-present',action='store_true')
    parser.add_argument('--pptx');parser.add_argument('--source-receipt')
    args=parser.parse_args(argv)
    manifest=json.loads(Path(args.manifest).read_text())
    if args.base_present and 'source_canvas_clip' in manifest:
        _fail('base deck and merge placement are forbidden')
    result=verify_source_canvas_clip(manifest,args.root)
    if bool(args.pptx) != bool(args.source_receipt):
        _fail('--pptx and --source-receipt must be supplied together')
    if args.pptx:
        saved=json.loads(Path(args.source_receipt).read_text())
        if saved != result:
            _fail('saved source receipt differs from fresh source replay')
        result=verify_native_canvas_clip(args.pptx,manifest,result)
    with Path(args.output).open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')


if __name__=='__main__':
    main()
