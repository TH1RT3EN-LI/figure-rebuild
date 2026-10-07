"""Embed exact registered static SFNT bytes as uncompressed EOT font parts.

Only explicit native text faces are admitted. Permissions are preserved; this
operation proves packaging and used cmap coverage, not application rendering.
"""
import io
import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .package import (Package, xml, sha256, slide_catalog, relationship_part,
                      relationship_map, resolve_target, P, A, R, REL, CT, NS)

ROLES = ('regular', 'bold', 'italic', 'boldItalic')
MAX_PACKAGE = 256 * 1024 * 1024
MAX_FONT = 32 * 1024 * 1024


def _refuse_inherited_live_text(package, catalog):
    """Allow empty figure scaffolding; inherited live text needs its own policy."""
    kinds = {R + '/slideLayout': 'sldLayout', R + '/slideMaster': 'sldMaster'}
    pending = [slide['part'] for slide in catalog]
    visited = set()
    while pending:
        part = pending.pop()
        if part in visited: continue
        visited.add(part)
        rel_part = relationship_part(part)
        if rel_part not in package.payloads: continue
        for relation in relationship_map(xml(package, rel_part)).values():
            if relation.get('Type') not in kinds: continue
            target = resolve_target(part, relation)
            root = xml(package, target).root
            if root.tag != '{' + P + '}' + kinds[relation.get('Type')]:
                raise ValueError('Unsupported master/layout XML root: ' + target)
            if root.findall('.//a:fld', NS) or any(t.text for t in root.findall('.//a:t', NS)):
                raise ValueError('Inherited master/layout live text requires a separate embedding policy: ' + target)
            pending.append(target)


def eot_bytes(data):
    """EOT v1 header with the complete unchanged OpenType payload."""
    from fontTools.ttLib import TTFont
    if not 0 < len(data) <= MAX_FONT or data[:4] not in (b'OTTO', b'\x00\x01\x00\x00', b'true'):
        raise ValueError('Embedding requires a bounded static SFNT face')
    with TTFont(io.BytesIO(data), lazy=False) as font:
        if 'fvar' in font or 'OS/2' not in font:
            raise ValueError('Embedding requires a static face with OS/2 permissions')
        if ('CFF ' in font) != (data[:4] == b'OTTO') or ('glyf' not in font and 'CFF ' not in font):
            raise ValueError('SFNT signature and outline tables do not agree')
        os2 = font['OS/2']
        rights = os2.fsType
        # Reserved/contradictory rights are not silently reinterpreted.
        if rights & ~0x30E or rights & 2 or rights & 0x200 or (rights & 0xE) not in (0, 4, 8):
            raise ValueError('Font embedding permissions are restricted or unsupported')
        names = []
        for nid in (1, 2, 5, 4):
            value = font['name'].getDebugName(nid)
            if value is None:
                if nid == 5: value = 'Version 1.0'
                else: raise ValueError('Font lacks required EOT name metadata')
            encoded = value.encode('utf-16le')
            if len(encoded) > 65535: raise ValueError('EOT font name exceeds budget')
            names.append(struct.pack('<HH', 0, len(encoded)) + encoded)
        panose = bytes(getattr(os2.panose, k) for k in ('bFamilyType', 'bSerifStyle', 'bWeight', 'bProportion', 'bContrast', 'bStrokeVariation', 'bArmStyle', 'bLetterForm', 'bMidline', 'bXHeight'))
        header = struct.pack('<4I10sBBIHH11I', 0, len(data), 0x10000, 0,
            panose, 1, int(bool(os2.fsSelection & 1)), os2.usWeightClass,
            rights, 0x504C, *(getattr(os2, 'ulUnicodeRange' + str(i), 0) for i in range(1, 5)),
            getattr(os2, 'ulCodePageRange1', 0), getattr(os2, 'ulCodePageRange2', 0),
            font['head'].checkSumAdjustment, 0, 0, 0, 0)
        result = header + b''.join(names) + data
        result = struct.pack('<I', len(result)) + result[4:]
        return result, {'fsType': rights, 'editing_permission': (rights & 0xE) in (0, 8),
            'font_format': 'OpenType-CFF' if data[:4] == b'OTTO' else 'TrueType',
            'complete_registered_face_embedded': True, 'additional_subsetting': False,
            'available_codepoints': sorted((font.getBestCmap() or {}).keys())}


def embed_fonts(input_path, output_path, faces):
    """Embed only used explicit faces from a build's hash-bound font audit.

    Inherited/theme fonts, existing embeddings and unknown faces are refused.
    Slide/media/layout bytes stay unchanged. Output is always a new file.
    """
    source, output = Path(input_path).resolve(), Path(output_path).resolve()
    if source == output or output.exists(): raise ValueError('Font embedding requires a new output path')
    if source.stat().st_size > MAX_PACKAGE: raise ValueError('PPTX exceeds embedding budget')
    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_PACKAGE:
            raise ValueError('Expanded PPTX exceeds embedding budget')
    package = Package.read(source)
    presentation = xml(package, 'ppt/presentation.xml')
    if presentation.root.find('p:embeddedFontLst', NS) is not None:
        raise ValueError('Existing font embeddings are unsupported; preserve the original')
    used = {}
    _, catalog = slide_catalog(package)
    _refuse_inherited_live_text(package, catalog)
    for slide in catalog:
        part = slide['part']
        root = xml(package, part).root
        for run in root.findall('.//a:r', NS):
            text = run.find('a:t', NS)
            if text is None or not text.text: continue
            props = run.find('a:rPr', NS)
            latin = props.find('a:latin', NS) if props is not None else None
            if latin is None or not latin.get('typeface') or latin.get('typeface').startswith('+'):
                raise ValueError('Text needs an explicit native font for embedding')
            for key in ('b', 'i'):
                if props.get(key) not in ('0', '1', 'false', 'true'):
                    raise ValueError('Text needs explicit native bold/italic flags')
            bold, italic = (props.get(k) in ('1', 'true') for k in ('b', 'i'))
            role = 'boldItalic' if bold and italic else 'bold' if bold else 'italic' if italic else 'regular'
            family = latin.get('typeface')
            for kind in ('ea', 'cs', 'sym'):
                other = props.find('a:' + kind, NS)
                if other is not None and other.get('typeface') != family:
                    raise ValueError('Mixed-script face selection requires a separate embedding policy')
            used.setdefault((family, role), set()).update(ord(c) for c in text.text if ord(c) >= 32)
        if root.findall('.//a:fld', NS): raise ValueError('Field fonts are unsupported')
    if not used: raise ValueError('No explicit live text faces to embed')
    registry = {}
    if not isinstance(faces, list) or not faces:
        raise ValueError('Embedding requires a nonempty registered font audit')
    for face in faces:
        if (not isinstance(face, dict) or not isinstance(face.get('family'), str)
                or not face['family'] or face.get('role') not in ROLES
                or not isinstance(face.get('renderer'), str)
                or not isinstance(face.get('renderer_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', face['renderer_sha256'])):
            raise ValueError('Invalid registered font audit face')
        key = (face['family'], face['role'])
        if key in registry: raise ValueError('Duplicate registered font face')
        registry[key] = face
    rels = xml(package, 'ppt/_rels/presentation.xml.rels')
    types = xml(package, '[Content_Types].xml')
    ids = {e.get('Id') for e in rels.root}
    fontlist = ET.Element('{' + P + '}embeddedFontLst')
    entries, records, font_parts, total = {}, [], {}, sum(len(v) for v in package.payloads.values())
    for (family, role), codepoints in sorted(used.items(), key=lambda row: (row[0][0], ROLES.index(row[0][1]))):
        if (family, role) not in registry: raise ValueError('Unregistered used font: ' + family + ' ' + role)
        face = registry[(family, role)]
        path = Path(face['renderer'])
        if path.stat().st_size > MAX_FONT: raise ValueError('Font exceeds embedding budget')
        data = path.read_bytes()
        if sha256(data) != face['renderer_sha256']: raise ValueError('Registered font SHA256 mismatch')
        eot, record = eot_bytes(data)
        from fontTools.ttLib import TTFont
        with TTFont(io.BytesIO(data)) as font:
            families = {n.toUnicode() for n in font['name'].names if n.nameID in (1, 16)}
            if family not in families: raise ValueError('Registered font family mismatch')
            expected_bold, expected_italic = role in ('bold', 'boldItalic'), role in ('italic', 'boldItalic')
            actual_italic = bool(font['OS/2'].fsSelection & (1 | 512) or font['head'].macStyle & 2 or font['post'].italicAngle)
            if expected_bold != (font['OS/2'].usWeightClass >= 600) or expected_italic != actual_italic:
                raise ValueError('Registered font role mismatch')
        missing = codepoints - set(record['available_codepoints'])
        if missing: raise ValueError('Embedded face lacks used Unicode: ' + family + ' ' + str(sorted(missing)))
        member = 'ppt/fonts/fr-' + sha256(eot) + '.fntdata'
        if member not in font_parts:
            if member in package.payloads or any(t.get('PartName') == '/' + member for t in types.root):
                raise ValueError('Font part or content type collides with the original package')
            total += len(eot)
            if total > MAX_PACKAGE: raise ValueError('Embedded package exceeds budget')
            package.payloads[member] = eot
            font_parts[member] = eot
            ET.SubElement(types.root, '{' + CT + '}Override', PartName='/' + member, ContentType='application/x-fontdata')
        elif font_parts[member] != eot:
            raise ValueError('Embedded font hash collision')
        rid = 'rIdFRFont' + str(len(records) + 1)
        if rid in ids: raise ValueError('Font relationship ID collision')
        ids.add(rid)
        ET.SubElement(rels.root, '{' + REL + '}Relationship', Id=rid, Type=R + '/font', Target=member[4:])
        if family not in entries:
            entry = ET.SubElement(fontlist, '{' + P + '}embeddedFont')
            ET.SubElement(entry, '{' + P + '}font', typeface=family)
            entries[family] = entry
        ET.SubElement(entries[family], '{' + P + '}' + role, {'{' + R + '}id': rid})
        records.append(dict(record, family=family, role=role, registered_sha256=sha256(data),
            eot_sha256=sha256(eot), part=member, used_codepoints=sorted(codepoints)))
    # CT_Presentation sequence places embeddedFontLst after notesSz/smartTags.
    successors = {'custShowLst', 'photoAlbum', 'custDataLst', 'kinsoku', 'defaultTextStyle', 'modifyVerifier', 'extLst'}
    index = next((i for i,e in enumerate(presentation.root) if e.tag.rsplit('}',1)[-1] in successors), len(presentation.root))
    presentation.root.insert(index, fontlist)
    presentation.root.set('embedTrueTypeFonts', '1')
    presentation.root.set('saveSubsetFonts', '0')
    changed = {'ppt/presentation.xml': presentation.bytes(), 'ppt/_rels/presentation.xml.rels': rels.bytes(), '[Content_Types].xml': types.bytes()}
    package.payloads.update(changed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        z.comment = package.comment
        for name, data in package.payloads.items(): z.writestr(package.infos.get(name, name), data)
    return {'schema_version': 1, 'input_sha256': package.source_hash, 'output_sha256': sha256(output.read_bytes()),
        'embedded_faces': records, 'all_slide_and_media_bytes_unchanged': True,
        'permissions_unchanged': True, 'unseen_repertoire_or_application_acceptance_verified': False}
