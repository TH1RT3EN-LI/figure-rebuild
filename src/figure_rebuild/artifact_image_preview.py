"""Preview-only image sampling from delivered PPTX media and native coordinates.

This explicit adapter uses MuPDF for plain top-level PNG/JPEG pictures, then
Artifact for the complete mixed scene. References never participate in pixel
generation. Version 2 also samples nonnegative rectangular source windows.
Effects, rotation, groups and other unsupported pictures keep
ordinary Artifact rendering and an explicit limitation. Delivery is unchanged.
"""
import argparse
import base64
import hashlib
import json
from io import BytesIO
from fractions import Fraction
from pathlib import Path
import posixpath
from xml.etree import ElementTree as E
from zipfile import ZipFile

from PIL import Image
from .package import XmlDocument, checked_part_name
from .placement import require_identity_shape_tree

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
POLICY = 'delivered-native-picture-mupdf-device-grid-v1'
POLICY_V2 = 'delivered-native-picture-mupdf-device-grid-v2'
EMU = 9525
MAX_BYTES = 128 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_DEFINITION_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PIXELS = 16_000_000
MAX_SURFACE_PIXELS = 16_000_000
MAX_PICTURE_PIXELS = 24_000_000
MAX_COMBINED_PIXELS = 64 * 1024 * 1024
SCALES = (1, 2, 4)


class UnsupportedPicture(ValueError):
    pass


def _one(node, path):
    rows = node.findall(path, NS)
    if len(rows) != 1:
        raise UnsupportedPicture('missing or ambiguous native field: ' + path)
    return rows[0]


def _integer(value, name, positive=False):
    if not isinstance(value, str) or not value.lstrip('-').isdigit():
        raise UnsupportedPicture('invalid native integer: ' + name)
    result = int(value)
    if abs(result) > 2147483647 or (positive and result <= 0):
        raise UnsupportedPicture('native integer budget: ' + name)
    return result


def _plain_picture(pic, package, relationships, version):
    if pic.tag != '{' + NS['p'] + '}pic' or pic.attrib or any(c.tag not in {
            '{' + NS['p'] + '}' + k for k in ('nvPicPr', 'blipFill', 'spPr')} for c in pic):
        raise UnsupportedPicture('unsupported native picture state')
    props = _one(pic, 'p:spPr')
    if set(props.attrib) or any(c.tag not in {'{' + NS['a'] + '}' + k for k in ('xfrm', 'prstGeom', 'ln')} for c in props):
        raise UnsupportedPicture('picture effect, fill or custom geometry')
    name = _one(pic, 'p:nvPicPr/p:cNvPr')
    if name.get('hidden', '0') not in ('0', 'false'):
        raise UnsupportedPicture('hidden picture')
    xf = _one(props, 'a:xfrm')
    if set(xf.attrib) - {'rot', 'flipH', 'flipV'} or _integer(xf.get('rot', '0'), 'rotation') % 21600000 or any(
            xf.get(k, '0') not in ('0', 'false') for k in ('flipH', 'flipV')):
        raise UnsupportedPicture('rotated or reflected picture')
    off, ext = _one(xf, 'a:off'), _one(xf, 'a:ext')
    if len(xf) != 2 or set(off.attrib) != {'x', 'y'} or set(ext.attrib) != {'cx', 'cy'} or len(off) or len(ext):
        raise UnsupportedPicture('unknown native picture transform state')
    native = [_integer(off.get(k), k) for k in ('x', 'y')] + [_integer(ext.get(k), k, True) for k in ('cx', 'cy')]
    geom = _one(props, 'a:prstGeom')
    if geom.attrib != {'prst': 'rect'} or any(c.tag != '{' + NS['a'] + '}avLst' or len(c) or c.attrib for c in geom):
        raise UnsupportedPicture('nonrectangular picture geometry')
    for line in props.findall('a:ln', NS):
        if len(line) != 1 or line[0].tag != '{' + NS['a'] + '}noFill' or line[0].attrib or len(line[0]):
            raise UnsupportedPicture('painted picture border')
    if pic.find('p:style', NS) is not None:
        raise UnsupportedPicture('theme picture style')
    fill = _one(pic, 'p:blipFill')
    if fill.attrib or any(c.tag not in {'{' + NS['a'] + '}' + k for k in ('blip', 'srcRect', 'stretch')} for c in fill):
        raise UnsupportedPicture('tile or unsupported picture fill')
    blip = _one(fill, 'a:blip')
    if set(blip.attrib) - {'{' + NS['r'] + '}embed', 'cstate'} or len(blip):
        raise UnsupportedPicture('linked picture or native image effect')
    crop_nodes = fill.findall('a:srcRect', NS)
    if len(crop_nodes) > 1 or (crop_nodes and (set(crop_nodes[0].attrib) - set('ltrb') or len(crop_nodes[0]))):
        raise UnsupportedPicture('invalid native source crop')
    crop = {k: _integer(crop_nodes[0].get(k, '0'), 'crop') if crop_nodes else 0 for k in 'ltrb'}
    if version == 1 and any(crop.values()):
        raise UnsupportedPicture('cropped picture requires separate pixel-window support')
    if any(v < 0 or v > 100000 for v in crop.values()) or crop['l'] + crop['r'] >= 100000 or crop['t'] + crop['b'] >= 100000:
        raise UnsupportedPicture('source crop must leave a positive internal rectangular window')
    stretch = _one(fill, 'a:stretch')
    if stretch.attrib or len(stretch) > 1 or any(c.tag != '{' + NS['a'] + '}fillRect' or len(c) or set(c.attrib) - set('ltrb') or any(
            _integer(v, 'fillRect') != 0 for v in c.attrib.values()) for c in stretch):
        raise UnsupportedPicture('picture destination inset')
    rid = blip.get('{' + NS['r'] + '}embed')
    rel = relationships.get(rid)
    if rel is None or rel.get('TargetMode', 'Internal') != 'Internal' or not rel.get('Type', '').endswith('/image'):
        raise UnsupportedPicture('missing or external picture relationship')
    target = rel.get('Target', '')
    media = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('ppt/slides', target))
    checked_part_name(media)
    if not media.startswith('ppt/media/') or media not in package:
        raise UnsupportedPicture('picture media escapes the native media directory')
    encoded = package[media]
    with Image.open(BytesIO(encoded)) as image:
        if image.format not in ('PNG', 'JPEG') or image.mode not in ('RGB', 'RGBA', 'L', 'LA') or image.info.get('icc_profile'):
            raise UnsupportedPicture('unsupported image encoding or color profile')
        if max(image.size) > 32768 or image.width * image.height > MAX_SOURCE_PIXELS:
            raise UnsupportedPicture('decoded source pixel budget')
        dimensions = list(image.size)
        image.verify()
    return native, encoded, media, dimensions, crop


def _render_picture(native, encoded, fitz, remaining_pixel_budget, crop):
    x, y, w, h = native
    plans, total = [], 0
    for scale in SCALES:
        left, top = x * scale // EMU, y * scale // EMU
        right, bottom = -(-(x+w)*scale // EMU), -(-(y+h)*scale // EMU)
        pw, ph = right-left, bottom-top
        pixels = pw*ph
        if max(pw, ph) > 32768 or pixels > MAX_SURFACE_PIXELS:
            raise UnsupportedPicture('device-grid render surface budget')
        if any(crop.values()):
            # Bound the complete transformed media as well as the visible
            # device surface before image decoding or rendering allocations.
            full_w = Fraction(w * scale * 100000, EMU * (100000 - crop['l'] - crop['r']))
            full_h = Fraction(h * scale * 100000, EMU * (100000 - crop['t'] - crop['b']))
            if max(full_w, full_h) > 32768 or full_w * full_h > MAX_SURFACE_PIXELS:
                raise UnsupportedPicture('uncropped media transform budget')
        plans.append((scale, left, top, pw, ph)); total += pixels
    if total > MAX_PICTURE_PIXELS:
        raise UnsupportedPicture('picture render pixel budget')
    if total > remaining_pixel_budget:
        raise ValueError('Combined native picture render pixel budget')
    outputs = []
    for scale, left, top, pw, ph in plans:
        with fitz.open() as doc:
            page = doc.new_page(width=pw, height=ph)
            if any(crop.values()):
                fx, fy = Fraction(x * scale, EMU) - left, Fraction(y * scale, EMU) - top
                fw, fh = Fraction(w * scale, EMU), Fraction(h * scale, EMU)
                full_w = fw * 100000 / (100000 - crop['l'] - crop['r'])
                full_h = fh * 100000 / (100000 - crop['t'] - crop['b'])
                full_x = fx - full_w * Fraction(crop['l'], 100000)
                full_y = fy - full_h * Fraction(crop['t'], 100000)
                rect = fitz.Rect(float(full_x), float(full_y), float(full_x + full_w), float(full_y + full_h))
            else:
                # Keep version 1's zero-crop byte procedure unchanged.
                rect = fitz.Rect(x*scale/EMU-left, y*scale/EMU-top,
                                 (x+w)*scale/EMU-left, (y+h)*scale/EMU-top)
            xref = page.insert_image(rect, stream=encoded, keep_proportion=False)
            doc.xref_set_key(xref, 'Interpolate', 'false')
            if any(crop.values()):
                contents = page.get_contents()
                if len(contents) != 1:
                    raise ValueError('Ambiguous generated cropped-picture content')
                # PDF clip is in bottom-up page coordinates. Clip the exact
                # native visible frame, including its fractional device edges;
                # never round the source crop to an integer media pixel box.
                clip = ' '.join(format(float(v), '.12g') for v in (fx, ph - fy - fh, fw, fh))
                data = b'q\n' + clip.encode('ascii') + b' re W n\n' + page.read_contents() + b'\nQ\n'
                doc.update_stream(contents[0], data)
            pix = page.get_pixmap(alpha=True)
            if (pix.width, pix.height) != (pw, ph):
                raise ValueError('MuPDF picture preview changed device-grid dimensions')
            data = pix.tobytes('png')
        outputs.append({'scale': scale, 'position': {'left': left/scale, 'top': top/scale,
                        'width': pw/scale, 'height': ph/scale}, 'width': pw, 'height': ph,
                        'png_base64': base64.b64encode(data).decode('ascii'), 'png_sha256': hashlib.sha256(data).hexdigest()})
    return outputs, total


def _native_picture_inputs(pptx, manifest):
    pptx = Path(pptx).resolve()
    if not isinstance(manifest, dict) or not isinstance(manifest.get('objects'), list) or len(manifest['objects']) > 10000:
        raise ValueError('Invalid native picture source object budget')
    ids = [o.get('id') for o in manifest['objects'] if isinstance(o, dict)]
    if len(ids) != len(manifest['objects']) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid native picture source identities')
    if pptx.stat().st_size > MAX_BYTES:
        raise ValueError('Native picture package byte budget')
    payload = pptx.read_bytes()
    with ZipFile(BytesIO(payload)) as z:
        infos = z.infolist()
        if len(infos) > 10000 or len({i.filename for i in infos}) != len(infos) or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError('Native picture package expansion budget or duplicate member')
        for info in infos: checked_part_name(info.filename)
        names = ('ppt/slides/slide1.xml', 'ppt/slides/_rels/slide1.xml.rels')
        if any(z.getinfo(n).file_size > MAX_XML for n in names):
            raise ValueError('Native picture XML byte budget')
        xml, relxml = (z.read(n) for n in names)
        package = {i.filename: z.read(i) for i in infos if i.filename.startswith('ppt/media/')}
    root = XmlDocument.parse(xml, names[0]).root
    rels = XmlDocument.parse(relxml, names[1]).root
    relationships = {}
    for rel in rels:
        rid = rel.get('Id')
        if not rid or rid in relationships:
            raise ValueError('Duplicate or missing native relationship identity')
        relationships[rid] = rel
    tree = _one(root, 'p:cSld/p:spTree'); require_identity_shape_tree(tree, 'native image preview slide')
    pictures, order = {}, []
    for node in tree:
        kind = node.tag.rsplit('}', 1)[-1]
        if kind in ('nvGrpSpPr', 'grpSpPr', 'extLst'): continue
        if kind not in ('sp', 'pic'):
            raise ValueError('Native picture preview requires an ungrouped standalone slide')
        if node.tag != '{' + NS['p'] + '}' + kind:
            raise ValueError('Unknown namespace in native picture paint tree')
        prop = _one(node, 'p:nvPicPr/p:cNvPr' if kind == 'pic' else 'p:nvSpPr/p:cNvPr')
        name = prop.get('name')
        if not name or any(o['id'] == name for o in order):
            raise ValueError('Duplicate or missing native paint identity')
        order.append({'id': name, 'type': 'image' if kind == 'pic' else 'shape'})
        if kind == 'pic': pictures[name] = node
    expected_images = {o['id'] for o in manifest['objects'] if o.get('kind') == 'image'}
    if set(pictures) != expected_images:
        raise ValueError('Declared pictures disagree with actual native picture identities')
    return pptx, payload, xml, names, package, relationships, pictures, order


def prepare_image_preview(pptx, manifest, *, version=1, shared_grid=None, source_sampling=None,
                          source_rgb_group_sampling=None, asset_root=None):
    """Sample delivered media or an explicit byte-replayed source recipe."""
    if source_rgb_group_sampling is not None:
        if type(version) is not int or version != 5 or shared_grid is not None or source_sampling is not None:
            raise ValueError('Source RGB group sampling requires explicit version 5 without another sampling policy')
        from .artifact_source_image_preview import prepare_source_image_preview
        return prepare_source_image_preview(pptx, manifest, source_rgb_group_sampling, asset_root, rgb_groups=True)
    if source_sampling is not None:
        if type(version) is not int or version != 4 or shared_grid is not None:
            raise ValueError('Source image sampling requires explicit version 4 without a shared media grid')
        from .artifact_source_image_preview import prepare_source_image_preview
        return prepare_source_image_preview(pptx, manifest, source_sampling, asset_root)
    if type(version) is int and version == 3 and shared_grid is not None:
        from .artifact_shared_image_preview import prepare_shared_image_preview
        return prepare_shared_image_preview(pptx, manifest, shared_grid)
    if shared_grid is not None:
        raise ValueError('Shared native image grid requires explicit version 3')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported native picture preview version')
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise ValueError('Native picture preview requires the optional PyMuPDF source dependency') from exc
    pptx, payload, xml, names, package, relationships, pictures, order = _native_picture_inputs(pptx, manifest)
    objects, skipped, pixels = [], [], 0
    for name, pic in pictures.items():
        try:
            native, encoded, media, dimensions, crop = _plain_picture(pic, package, relationships, version)
            rendered, cost = _render_picture(native, encoded, fitz, MAX_COMBINED_PIXELS-pixels, crop)
        except UnsupportedPicture as exc:
            skipped.append({'id': name, 'reason': str(exc)}); continue
        pixels += cost
        if pixels > MAX_COMBINED_PIXELS:
            raise ValueError('Combined native picture render pixel budget')
        object_record = {'id': name, 'native_position': dict(zip(('left', 'top', 'width', 'height'), (v/EMU for v in native))),
                        'native_picture_xml_sha256': hashlib.sha256(E.canonicalize(E.tostring(pic), rewrite_prefixes=True).encode()).hexdigest(),
                        'native_media_part': media, 'native_media_sha256': hashlib.sha256(encoded).hexdigest(),
                        'native_media_dimensions': dimensions, 'previews': rendered}
        if version == 2:
            object_record['native_source_crop_units'] = crop
            object_record['native_source_window_exact'] = [str(Fraction(crop['l'], 100000)), str(Fraction(crop['t'], 100000)),
                                                          str(Fraction(100000-crop['r'], 100000)), str(Fraction(100000-crop['b'], 100000))]
        objects.append(object_record)
    result = {'schema_version': version, 'policy': POLICY if version == 1 else POLICY_V2, 'input_pptx': {'path': str(pptx), 'sha256': hashlib.sha256(payload).hexdigest()},
              'slide_part': names[0], 'native_slide_xml_sha256': hashlib.sha256(xml).hexdigest(),
              'paint_order': order, 'objects': objects, 'unsupported': skipped, 'scales': list(SCALES),
              'renderer': 'PyMuPDF', 'renderer_version': fitz.VersionBind, 'mupdf_version': fitz.VersionFitz,
              'sampling': 'actual_native_media_independent_color_and_mask_device_grid', 'PDF_image_Interpolate': False,
              'render_pixels': pixels, 'preview_only': True, 'native_delivery_modified': False,
              'reference_pixels_used': False, 'source_pixel_equivalence': False, 'application_playback_verified': False}
    if len(json.dumps(result, separators=(',', ':')).encode()) > MAX_DEFINITION_BYTES:
        raise ValueError('Native picture definition byte budget')
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pptx'); p.add_argument('--manifest'); p.add_argument('--output'); p.add_argument('--preflight', action='store_true')
    p.add_argument('--version', type=int, choices=(1, 2, 3, 4, 5), default=1)
    p.add_argument('--shared-grid', help='Explicit integer source windows grouped on one common native grid')
    p.add_argument('--source-sampling', help='Explicit source image recipes which replay actual native media bytes')
    p.add_argument('--source-rgb-group-sampling', help='Explicit single-image RGB group recipes; group decomposition remains unverified')
    p.add_argument('--asset-root', help='Immutable root containing the declared source PDF')
    a = p.parse_args(argv)
    if a.preflight:
        import pymupdf
        print(json.dumps({'PyMuPDF': pymupdf.VersionBind, 'MuPDF': pymupdf.VersionFitz})); return
    if not all((a.pptx, a.manifest, a.output)): p.error('--pptx, --manifest and --output are required')
    from .source_inventory import _load_json
    shared_grid = _load_json(Path(a.shared_grid), MAX_DEFINITION_BYTES) if a.shared_grid else None
    source_sampling = _load_json(Path(a.source_sampling), MAX_DEFINITION_BYTES) if a.source_sampling else None
    source_rgb = _load_json(Path(a.source_rgb_group_sampling), MAX_DEFINITION_BYTES) if a.source_rgb_group_sampling else None
    result = prepare_image_preview(a.pptx, _load_json(Path(a.manifest), MAX_DEFINITION_BYTES), version=a.version,
                                   shared_grid=shared_grid, source_sampling=source_sampling,
                                   source_rgb_group_sampling=source_rgb, asset_root=a.asset_root)
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__': main()
