"""Restore eligible opaque photo matrices from the actual delivered PPTX.

This separate derived-PDF policy changes only isolated image cm operands.
Pixels, alpha, fonts, vectors and the original PDF/PPTX remain unchanged.
Only exact opaque PNG-to-PDF RGB matches on a fontless standalone slide qualify.
"""
from io import BytesIO
from pathlib import Path
import math
import re
from xml.etree import ElementTree as E
from zipfile import ZipFile
import zlib

from PIL import Image
from .artifact_image_preview import NS, UnsupportedPicture, _one, _plain_picture
from .package import XmlDocument, checked_part_name
from .placement import require_identity_shape_tree
from .pdf_binary_alpha import (_binding, _decode, _dimensions, _load, _plain_image,
                               _same_data, _sha, _snapshot, _stream)

POLICY = 'native-opaque-photo-matrix-v1'
LIMITS = {'max_input_bytes': 64_000_000, 'max_package_expansion_bytes': 64_000_000,
          'max_xml_bytes': 8_000_000, 'max_content_bytes': 16_000_000,
          'max_objects': 10_000, 'max_images': 2048, 'max_image_pixels': 8_000_000,
          'max_image_axis': 32768, 'max_decoded_work_bytes': 256_000_000}
FRAME_GUARD_PX = .001
CORRESPONDENCE_GUARD_PX = .125
_NUMBER = rb'[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
_IMAGE = re.compile(rb'(?<!\S)q\s+(?P<matrix>' + _NUMBER + rb'(?:\s+' + _NUMBER +
                    rb'){5})\s+cm\s*/(?P<name>Im[0-9]+)\s+Do\s+Q')


def _limits(value):
    if value is None:
        return dict(LIMITS)
    if type(value) is not dict or set(value) - set(LIMITS):
        raise ValueError('Invalid native-photo PDF limits')
    result = dict(LIMITS)
    for key, number in value.items():
        if type(number) is not int or not 0 < number <= LIMITS[key]:
            raise ValueError('Native-photo limits must be positive bounded integers')
        result[key] = number
    return result


def _native(pptx, limits):
    pptx, payload = _load(pptx, limits)
    with ZipFile(BytesIO(payload)) as z:
        infos = z.infolist()
        if (len(infos) > limits['max_objects'] or len({i.filename for i in infos}) != len(infos) or
                sum(i.file_size for i in infos) > limits['max_package_expansion_bytes'] or
                any(i.flag_bits & 1 for i in infos)):
            raise ValueError('Native-photo package expansion, identity or encryption budget')
        for info in infos:
            checked_part_name(info.filename)
        slides = [i.filename for i in infos if re.fullmatch(r'ppt/slides/[^/]+\.xml', i.filename)]
        if slides != ['ppt/slides/slide1.xml']:
            raise ValueError('Native-photo placement requires one standalone slide1')
        names = ['ppt/presentation.xml', slides[0], 'ppt/slides/_rels/slide1.xml.rels']
        if any(z.getinfo(n).file_size > limits['max_xml_bytes'] for n in names):
            raise ValueError('Native-photo XML byte budget')
        presentation, slide, relationships = [XmlDocument.parse(z.read(n), n).root for n in names]
        if len(_one(presentation, 'p:sldIdLst')) != 1:
            raise ValueError('Native-photo placement requires exactly one slide')
        size = _one(presentation, 'p:sldSz')
        dimensions = {}
        for key in ('cx', 'cy'):
            value = size.get(key, '')
            if not re.fullmatch(r'[0-9]{1,10}', value) or not 0 < int(value) <= 2147483647:
                raise ValueError('Invalid native-photo slide dimensions')
            dimensions[key] = int(value)
        rels = {}
        for rel in relationships:
            rid = rel.get('Id')
            if not rid or rid in rels:
                raise ValueError('Duplicate/missing native-photo relationship')
            rels[rid] = rel
        package = {i.filename: z.read(i) for i in infos if i.filename.startswith('ppt/media/')}
    tree = _one(slide, 'p:cSld/p:spTree')
    require_identity_shape_tree(tree, 'native-photo placement slide')
    pictures, identities, work = [], set(), 0
    for node in tree:
        kind = node.tag.rsplit('}', 1)[-1]
        if kind in ('nvGrpSpPr', 'grpSpPr', 'extLst'):
            continue
        if kind not in ('sp', 'pic') or node.tag != '{' + NS['p'] + '}' + kind:
            raise ValueError('Native-photo placement requires an ungrouped slide')
        props = _one(node, 'p:nvPicPr/p:cNvPr' if kind == 'pic' else 'p:nvSpPr/p:cNvPr')
        name = props.get('name')
        if not name or name in identities:
            raise ValueError('Duplicate/missing native-photo paint identity')
        identities.add(name)
        if kind != 'pic':
            continue
        record = {'id': name, 'native_picture_xml_sha256': _sha(E.canonicalize(E.tostring(node), rewrite_prefixes=True).encode())}
        try:
            native, encoded, media, extent, crop = _plain_picture(node, package, rels, 1)
            if max(extent) > limits['max_image_axis'] or extent[0] * extent[1] > limits['max_image_pixels']:
                raise ValueError('Native-photo source image pixel/axis budget')
            cost = extent[0] * extent[1] * 12
            if work + cost > limits['max_decoded_work_bytes']:
                raise ValueError('Native-photo decoded work budget')
            work += cost
            with Image.open(BytesIO(encoded)) as im:
                if im.format != 'PNG' or im.mode not in ('RGB', 'RGBA') or im.n_frames != 1 or set(im.info) - {'dpi'}:
                    raise UnsupportedPicture('opaque plain single-frame PNG required')
                rgba = im.convert('RGBA')
                if rgba.getchannel('A').getextrema() != (255, 255):
                    raise UnsupportedPicture('partial_alpha_picture_retained')
                rgb = rgba.convert('RGB').tobytes()
            record.update(native_emu=native, native_media_part=media, native_media_sha256=_sha(encoded),
                          dimensions=extent, native_RGB_sha256=_sha(rgb))
        except UnsupportedPicture as exc:
            record['retained_reason'] = str(exc)
        pictures.append(record)
    if len(pictures) > limits['max_images']:
        raise ValueError('Native-photo picture count budget')
    return pptx, payload, dimensions, pictures, work


def _content(pdf, xref, limits):
    if pdf.xref_get_key(xref, 'DecodeParms')[0] != 'null':
        raise ValueError('Predicted native-photo content stream is unsupported')
    raw = pdf.xref_stream_raw(xref)
    kind = pdf.xref_get_key(xref, 'Filter')
    if kind == ('name', '/FlateDecode'):
        decoder = zlib.decompressobj()
        try:
            data = decoder.decompress(raw, limits['max_content_bytes'] + 1)
        except zlib.error as exc:
            raise ValueError('Invalid native-photo Flate content') from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Native-photo content expansion or trailing bytes')
    elif kind[0] == 'null':
        data = raw
    else:
        raise ValueError('Unsupported native-photo content encoding')
    if not isinstance(data, bytes) or len(data) > limits['max_content_bytes']:
        raise ValueError('Native-photo content byte budget')
    # A deliberately narrow fontless path/image domain. Comments, strings,
    # escaped names and binary inline images cannot conceal operator matches.
    if not re.fullmatch(rb'[A-Za-z0-9_.+/\-\[\]\s*]*', data) or re.search(rb'(?<!\S)(?:BT|BI)(?!\S)', data):
        raise ValueError('Native-photo content is outside plain fontless path/image policy')
    return data


def _plan(pdf, dimensions, pictures, work, limits):
    if (not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or len(pdf) != 1 or
            pdf.xref_length() > limits['max_objects'] or pdf[0].rotation or pdf[0].get_fonts() or
            pdf.xref_get_key(pdf.pdf_catalog(), 'AcroForm')[0] != 'null'):
        raise ValueError('Native-photo PDF requires a fontless, unrotated standalone page')
    page = pdf[0]
    if any(abs(actual - dimensions[key] / 12700) > .02
           for actual, key in zip((page.rect.width, page.rect.height), ('cx', 'cy'))):
        raise ValueError('Native-photo PDF page size disagrees with delivered PPTX')
    image_count = 0
    for x in range(1, pdf.xref_length()):
        if pdf.xref_get_key(x, 'Subtype') != ('name', '/Image'):
            continue
        image_count += 1
        extent = _dimensions(pdf, x, limits)
        if (extent is None or max(extent) > limits['max_image_axis'] or
                not (_plain_image(pdf, x, '/DeviceRGB') or _plain_image(pdf, x, '/DeviceGray'))):
            raise ValueError('Native-photo PDF image is outside bounded plain image policy')
        work += extent[0] * extent[1] * 4
        if work > limits['max_decoded_work_bytes']:
            raise ValueError('Native-photo PDF image decode work budget')
    if image_count > limits['max_images']:
        raise ValueError('Native-photo PDF image count budget')
    contents = page.get_contents()
    if len(contents) != 1:
        raise ValueError('Native-photo placement requires one direct page content stream')
    xref = contents[0]
    data = _content(pdf, xref, limits)
    matches = list(_IMAGE.finditer(data))
    infos = page.get_image_info(xrefs=True)
    if len(matches) != len(infos) or len(matches) != len(pictures):
        raise ValueError('Native-photo image invocation/native paint correspondence is incomplete')
    aliases = {}
    for row in page.get_images(full=True):
        if row[7] in aliases:
            raise ValueError('Ambiguous native-photo PDF resource alias')
        aliases[row[7]] = row[0]
    chunks, last, transformed, retained = [], 0, [], []
    sx, sy = dimensions['cx'] / 9525 / page.rect.width, dimensions['cy'] / 9525 / page.rect.height
    for index, (pic, match, info) in enumerate(zip(pictures, matches, infos)):
        parent = aliases.get(match['name'].decode())
        values = [float(v) for v in match['matrix'].split()]
        a, b, c, d, e, f = values
        if (not all(math.isfinite(v) for v in values) or parent != info['xref'] or b or c or a <= 0 or d <= 0 or
                max(abs(v - w) for v, w in zip([a, 0, 0, d, e, page.rect.height - d - f], info['transform'])) > .0001):
            raise ValueError('Native-photo image operator has an unsupported transform context')
        if 'retained_reason' in pic:
            retained.append({'picture_index': index, **pic})
            continue
        if pic['dimensions'] != [info['width'], info['height']] or not _plain_image(pdf, parent, '/DeviceRGB'):
            raise ValueError('Native-photo PDF image dimensions/color context disagree with native media')
        if _decode(pdf.xref_get_key(parent, 'Decode'), 3) != [0, 1] * 3:
            raise ValueError('Native-photo nonidentity RGB Decode')
        pixels = info['width'] * info['height']
        rgb = _stream(pdf, parent, pixels * 3)
        if _sha(rgb) != pic['native_RGB_sha256']:
            raise ValueError('Native-photo actual PDF RGB does not exactly match delivered media')
        mask_kind, mask_value = pdf.xref_get_key(parent, 'SMask')
        if mask_kind != 'null':
            if mask_kind != 'xref':
                raise ValueError('Unsupported native-photo soft mask')
            mask = int(mask_value.split()[0])
            if (not _plain_image(pdf, mask, '/DeviceGray') or
                    _dimensions(pdf, mask, limits) != tuple(pic['dimensions']) or
                    pdf.xref_get_key(mask, 'Matte')[0] != 'null' or pdf.xref_get_key(mask, 'SMask')[0] != 'null'):
                raise ValueError('Unsupported native-photo soft mask context')
            decode = _decode(pdf.xref_get_key(mask, 'Decode'), 1)
            if decode not in ([0, 1], [1, 0]) or _stream(pdf, mask, pixels) != bytes([255 if decode[0] == 0 else 0]) * pixels:
                raise ValueError('Native-photo PDF opacity does not exactly match opaque native media')
        n = pic['native_emu']
        before = [info['bbox'][0] * sx, info['bbox'][1] * sy,
                  (info['bbox'][2] - info['bbox'][0]) * sx, (info['bbox'][3] - info['bbox'][1]) * sy]
        delta = [v - w / 9525 for v, w in zip(before, n)]
        if max(abs(v) for v in delta) > CORRESPONDENCE_GUARD_PX:
            raise ValueError('Native-photo source/native image placement correspondence is outside fixed guard')
        precise = [n[2] * page.rect.width / dimensions['cx'], 0, 0,
                   n[3] * page.rect.height / dimensions['cy'], n[0] * page.rect.width / dimensions['cx'],
                   page.rect.height - (n[1] + n[3]) * page.rect.height / dimensions['cy']]
        replacement = ' '.join(format(v, '.12f').rstrip('0').rstrip('.') or '0' for v in precise).encode()
        start, end = match.span('matrix')
        chunks.extend([data[last:start], replacement]); last = end
        transformed.append({'picture_index': index, **pic, 'PDF_image_xref': parent,
                            'PDF_resource_name': match['name'].decode(), 'original_cm': match['matrix'].decode(),
                            'derived_cm': replacement.decode(), 'original_minus_native_frame_canvas_px': delta})
    chunks.append(data[last:])
    return xref, data, b''.join(chunks), transformed, retained, work


def _verify_documents(original, derived, plan, dimensions, limits):
    xref, data, changed, transformed, retained, work = plan
    if (not derived.is_pdf or derived.is_encrypted or derived.is_repaired or
            original.xref_length() != derived.xref_length()):
        raise ValueError('Native-photo derived PDF changed identities')
    before, after = _snapshot(original), _snapshot(derived)
    for x, record in before.items():
        if x == xref:
            a = {k: v for k, v in record['keys'].items() if k not in {'Filter', 'Length'}}
            b = {k: v for k, v in after[x]['keys'].items() if k not in {'Filter', 'Length'}}
            if a != b or _content(derived, x, limits) != changed:
                raise ValueError('Native-photo content stream failed exact operand replay')
        elif record != after[x]:
            raise ValueError('Native-photo derivation changed an unrelated object, image or mask stream')
    for document in (original, derived):
        if document[0].get_texttrace():
            raise ValueError('Native-photo placement is outside fontless policy')
    if original[0].get_drawings() != derived[0].get_drawings():
        raise ValueError('Native-photo placement changed a nonimage paint')
    skip = {'ID', 'DocChecksum'}
    if ({k: original.xref_get_key(-1, k) for k in original.xref_get_keys(-1) if k not in skip} !=
            {k: derived.xref_get_key(-1, k) for k in derived.xref_get_keys(-1) if k not in skip}):
        raise ValueError('Native-photo derivation changed unrelated trailer fields')
    old, new = original[0].get_image_info(xrefs=True), derived[0].get_image_info(xrefs=True)
    if len(old) != len(new):
        raise ValueError('Native-photo derivation changed image paints')
    moved = {p['picture_index']: p for p in transformed}
    sx, sy = dimensions['cx'] / 9525 / derived[0].rect.width, dimensions['cy'] / 9525 / derived[0].rect.height
    for index, (a, b) in enumerate(zip(old, new)):
        if index not in moved:
            if a != b:
                raise ValueError('Native-photo derivation changed a retained picture')
            continue
        if {k: v for k, v in a.items() if k not in {'bbox', 'transform'}} != {k: v for k, v in b.items() if k not in {'bbox', 'transform'}}:
            raise ValueError('Native-photo derivation changed image samples or identity')
        n = moved[index]['native_emu']
        actual = [b['bbox'][0] * sx, b['bbox'][1] * sy,
                  (b['bbox'][2] - b['bbox'][0]) * sx, (b['bbox'][3] - b['bbox'][1]) * sy]
        if max(abs(v - w / 9525) for v, w in zip(actual, n)) > FRAME_GUARD_PX:
            raise ValueError('Actual derived photo frame disagrees with exact native PPT coordinates')


def _receipt(pptx, payload, source, data, derived, result, dimensions, plan, limits):
    xref, original, changed, transformed, retained, work = plan
    return {'schema_version': 1, 'policy': POLICY, 'input_pptx': _binding(pptx, payload),
            'source_pdf': _binding(source, data), 'derived_pdf': _binding(derived, result), 'limits': limits,
            'slide_size_emu': dimensions, 'content_xref': xref,
            'original_content_sha256': _sha(original), 'derived_content_sha256': _sha(changed),
            'transformed_photos': transformed, 'retained_pictures': retained, 'decoded_work_bytes': work,
            'native_photo_frame_guard_canvas_px': FRAME_GUARD_PX,
            'correspondence_guard_canvas_px': CORRESPONDENCE_GUARD_PX,
            'input_PPTX_and_PDF_bytes_unchanged': True, 'all_PDF_image_and_alpha_encoded_streams_unchanged': True,
            'all_other_PDF_object_semantics_and_encoded_streams_unchanged': True,
            'actual_nonimage_paints_unchanged': True, 'source_reference_pixels_used': False,
            'stream_length_references_may_be_inlined': True, 'volatile_trailer_fields': ['ID', 'DocChecksum'],
            'source_pixel_or_filter_equivalence_proved': False, 'visual_verification_required': True}


def derive_native_photo_pdf(pptx, source, output, *, limits=None):
    limits = _limits(limits)
    pptx, payload, dimensions, pictures, work = _native(pptx, limits)
    source, data = _load(source, limits); output = Path(output).resolve()
    if output in (pptx, source) or output.exists():
        raise ValueError('Native-photo PDF destination must be new')
    import pymupdf
    with pymupdf.open(stream=data, filetype='pdf') as pdf:
        plan = _plan(pdf, dimensions, pictures, work, limits)
        if len(plan[2]) > limits['max_content_bytes']:
            raise ValueError('Native-photo derived content byte budget')
        if plan[3] and plan[1] != plan[2]:
            explicit_null = 'DecodeParms' in pdf.xref_get_keys(plan[0])
            pdf.update_stream(plan[0], plan[2], compress=True)
            if explicit_null: pdf.xref_set_key(plan[0], 'DecodeParms', 'null')
            pdf.xref_set_key(-1, 'DocChecksum', 'null')
            candidate = pdf.tobytes(garbage=0, deflate=False)
        else:
            candidate = data
    if len(candidate) > limits['max_input_bytes']:
        raise ValueError('Native-photo derived PDF file budget')
    with output.open('xb') as f: f.write(candidate)
    if pptx.read_bytes() != payload or source.read_bytes() != data:
        raise ValueError('Native-photo inputs changed during derivation')
    receipt = _receipt(pptx, payload, source, data, output, candidate, dimensions, plan, limits)
    verify_native_photo_pdf(pptx, source, output, receipt)
    return receipt


def verify_native_photo_pdf(pptx, source, derived, receipt):
    if type(receipt) is not dict or type(receipt.get('schema_version')) is not int or receipt.get('schema_version') != 1 or receipt.get('policy') != POLICY:
        raise ValueError('Unsupported native-photo PDF receipt')
    limits = _limits(receipt.get('limits'))
    pptx, payload, dimensions, pictures, work = _native(pptx, limits)
    source, data = _load(source, limits); derived, candidate = _load(derived, limits)
    import pymupdf
    with pymupdf.open(stream=data, filetype='pdf') as original, pymupdf.open(stream=candidate, filetype='pdf') as result:
        plan = _plan(original, dimensions, pictures, work, limits)
        if not _same_data(receipt, _receipt(pptx, payload, source, data, derived, candidate, dimensions, plan, limits)):
            raise ValueError('Native-photo receipt disagrees with independently replayed native media/operands')
        _verify_documents(original, result, plan, dimensions, limits)
        if plan[1] == plan[2] and data != candidate:
            raise ValueError('Native-photo no-op must preserve every raw PDF byte')
    return {'status': 'PASS', 'policy': POLICY, 'transformed_photos': len(plan[3])}
