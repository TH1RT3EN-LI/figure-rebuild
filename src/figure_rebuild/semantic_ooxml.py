"""Materialize audited formulas and explicit text layout in native DrawingML.

The PNG relationship stays the compatibility fallback. SVG is an embedded
font-outline part, not externally linked, and never replaces source PNG bytes.
Text baseline calibration remains measured/approximate and needs real previews.
"""
import hashlib
import math
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as ET

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
ASVG = 'http://schemas.microsoft.com/office/drawing/2016/SVG/main'
SVG_EXTENSION = '{96DAC541-7B7A-43D3-8B79-37D633B846F1}'
NS = {'a': A, 'p': P, 'r': R, 'asvg': ASVG}
EMU_PER_PX = 9525
HASH = re.compile(r'[a-fA-F0-9]{64}')
ET.register_namespace('asvg', ASVG)


def _finite(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(label + ' is outside its allowed range')
    return value


def _root(document, label):
    root = getattr(document, 'root', document)
    if not isinstance(root, ET.Element):
        raise ValueError(label + ' must be an XML document')
    return root


def _frozen_asset(asset_root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '\\' in relative:
        raise ValueError('Formula SVG path must be a job-relative file')
    root = Path(asset_root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Formula SVG path escapes the frozen job or is missing')
    return path


def _relationship_member(target):
    if not isinstance(target, str) or not target or ':' in target or '\\' in target or '\x00' in target:
        raise ValueError('Formula media relationship must be internal')
    member = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt/slides', target))
    if not member.startswith('ppt/media/') or any(part in ('', '.', '..') for part in member.split('/')):
        raise ValueError('Formula image relationship is outside embedded media')
    return member


def add_formula_svg(element, obj, payloads, rels_document, content_types_document, asset_root):
    """Attach Microsoft SVG DrawingML with unchanged high-resolution PNG fallback."""
    label = str(obj.get('id', '(unknown)'))
    record = obj.get('formula_asset')
    if obj.get('kind') != 'image' or obj.get('source_kind') != 'formula' or not isinstance(record, dict):
        raise ValueError('Formula SVG needs a compiled audited formula image: ' + label)
    representation = record.get('representation')
    if representation not in ('svg', 'png'):
        raise ValueError('Formula representation must be svg or png: ' + label)
    if element.tag != f'{{{P}}}pic':
        raise ValueError('Formula was flattened or is not a native picture: ' + label)
    blip = element.find('p:blipFill/a:blip', NS)
    if blip is None or not blip.get(f'{{{R}}}embed') or blip.get(f'{{{R}}}link'):
        raise ValueError('Formula must retain an embedded PNG fallback: ' + label)
    rels, types = _root(rels_document, 'Relationships'), _root(content_types_document, 'Content types')
    if rels.tag != f'{{{REL}}}Relationships' or types.tag != f'{{{CT}}}Types':
        raise ValueError('Formula embedding requires package relationship/content-type roots')
    fallback_id = blip.get(f'{{{R}}}embed')
    matches = [node for node in rels if node.get('Id') == fallback_id]
    if len(matches) != 1 or matches[0].get('TargetMode') == 'External' or matches[0].get('Type') != R + '/image':
        raise ValueError('Formula PNG fallback relationship is invalid: ' + label)
    fallback_member = _relationship_member(matches[0].get('Target'))
    if fallback_member not in payloads or hashlib.sha256(payloads[fallback_member]).hexdigest() != obj.get('sha256'):
        raise ValueError('Formula PNG fallback bytes changed: ' + label)
    result = {'id': label, 'representation': representation, 'png_member': fallback_member,
              'png_sha256': obj['sha256'], 'png_fallback_preserved': True, 'svg_embedded': False}
    if representation == 'png':
        return result
    transform = element.find('p:spPr/a:xfrm', NS)
    if obj.get('rotation', 0) != 0 or transform is None or int(transform.get('rot', '0')) % 21600000 or any(
            transform.get(flag, '0') in ('1', 'true') for flag in ('flipH', 'flipV')):
        raise ValueError('Formula SVG/PNG rotation must be baked into assets, without a native frame rotation: ' + label)
    crop = element.find('p:blipFill/a:srcRect', NS)
    if crop is not None and any(int(crop.get(key, '0')) for key in ('l', 't', 'r', 'b')):
        raise ValueError('Formula SVG and PNG require the same uncropped frame: ' + label)
    relative, hash_files = record.get('svg_path'), record.get('hash_files')
    if not isinstance(hash_files, list):
        raise ValueError('Formula SVG must have a frozen output hash list: ' + label)
    hashes = [entry.get('sha256') for entry in hash_files if isinstance(entry, dict) and entry.get('path') == relative]
    if len(hashes) != 1 or not isinstance(hashes[0], str) or not HASH.fullmatch(hashes[0]):
        raise ValueError('Formula SVG hash must be pinned exactly once: ' + label)
    data = _frozen_asset(asset_root, relative).read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != hashes[0].lower():
        raise ValueError('Formula SVG output hash changed: ' + label)
    from .formula_render import validate_outlined_svg
    geometry = validate_outlined_svg(data)
    audit = record.get('audit')
    if not isinstance(audit, dict) or audit.get('reference_crop_used') is not False:
        raise ValueError('Formula SVG needs a generated source-free audit: ' + label)
    vector = audit.get('vector_geometry')
    if not isinstance(vector, dict) or vector.get('embeddedfont_outlines') is not True or vector.get('external_references') is not False:
        raise ValueError('Formula SVG needs verified embedded font outlines: ' + label)
    rotation = audit.get('rotation_deg')
    if rotation not in (0, 90, 180, 270) or isinstance(rotation, bool) or vector.get('rotation_deg') != rotation:
        raise ValueError('Formula SVG/PNG audited rotation disagrees: ' + label)
    natural, viewbox = audit.get('natural_display_size_px'), geometry['viewbox']
    if not isinstance(natural, list) or len(natural) != 2 or viewbox[:2] != [0, 0] or any(
            abs(_finite(natural[i], 'Formula natural dimensions', positive=True) - viewbox[i + 2]) > 1e-7 for i in (0, 1)):
        raise ValueError('Formula SVG and PNG audited ink frames disagree: ' + label)
    if any(ext.get('uri') == SVG_EXTENSION for ext in blip.findall('a:extLst/a:ext', NS)):
        raise ValueError('Formula SVG extension already exists: ' + label)
    ids = [node.get('Id') for node in rels]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate package relationship IDs')
    member = next((name for name, value in payloads.items() if name.startswith('ppt/media/')
                   and name.endswith('.svg') and posixpath.normpath(name) == name
                   and all(char not in name for char in ('\\', ':', '\x00'))
                   and hashlib.sha256(value).hexdigest() == checksum),
                  'ppt/media/formula-' + checksum + '.svg')
    if member in payloads and payloads[member] != data:
        raise ValueError('Formula SVG media name collision')
    relationships = [node for node in rels if node.get('Type') == R + '/image'
                     and node.get('TargetMode') != 'External' and _relationship_member(node.get('Target')) == member]
    if relationships:
        svg_id = relationships[0].get('Id')
    else:
        next_id = 1
        while 'rId' + str(next_id) in ids:
            next_id += 1
        svg_id = 'rId' + str(next_id)
    declarations = [node for node in types if node.tag == f'{{{CT}}}Default' and node.get('Extension', '').lower() == 'svg']
    if declarations and (len(declarations) != 1 or declarations[0].get('ContentType') != 'image/svg+xml'):
        raise ValueError('Conflicting SVG content-type declaration')
    overrides = [node for node in types if node.tag == f'{{{CT}}}Override' and node.get('PartName') == '/' + member]
    if overrides and (len(overrides) != 1 or overrides[0].get('ContentType') != 'image/svg+xml'):
        raise ValueError('Conflicting SVG media-part content type')
    # Complete every byte/identity check before changing the package or picture.
    payloads[member] = data
    if not relationships:
        ET.SubElement(rels, f'{{{REL}}}Relationship', {'Id': svg_id, 'Type': R + '/image',
                      'Target': posixpath.relpath(member, 'ppt/slides')})
    if not declarations:
        ET.SubElement(types, f'{{{CT}}}Default', {'Extension': 'svg', 'ContentType': 'image/svg+xml'})
    extensions = blip.find('a:extLst', NS)
    if extensions is None:
        extensions = ET.SubElement(blip, f'{{{A}}}extLst')
    extension = ET.SubElement(extensions, f'{{{A}}}ext', {'uri': SVG_EXTENSION})
    ET.SubElement(extension, f'{{{ASVG}}}svgBlip', {f'{{{R}}}embed': svg_id})
    result.update(svg_embedded=True, svg_member=member, svg_sha256=checksum, svg_relationship=svg_id,
                  outlined_font_geometry=True, external_link=False, rotation_baked_deg=rotation)
    return result


def apply_text_layout(element, obj, mapped_entry, scale):
    """Set paragraph leading while preserving the source frame's first baseline.

    A renderer correction translates the native content frame by changing top
    and bottom insets oppositely. Its height and the shape's rotation center stay
    unchanged, so top, middle and bottom alignment retain their meaning. These
    derived DrawingML coordinates may be signed; source insets remain unchanged.
    """
    label = str(obj.get('id', '(unknown)'))
    if obj.get('kind') != 'text':
        raise ValueError('Text layout helper requires a text element: ' + label)
    scale = _finite(scale, 'Text layout scale', positive=True)
    body = element.find('p:txBody', NS)
    if body is None:
        raise ValueError('Text layout cannot apply to flattened text: ' + label)
    paragraphs = body.findall('a:p', NS)
    if not paragraphs:
        raise ValueError('Text layout requires native paragraphs: ' + label)
    from .text_spacing import prepare_native_character_spacing, apply_prepared_character_spacing
    character_spacing = prepare_native_character_spacing(element, obj, scale)
    result = {'id': label, 'line_height_applied': False, 'baseline_calibrated': False,
              'visual_verification_required': True}
    spacing = None
    if 'line_height' in obj:
        height = _finite(obj['line_height'], 'Text line_height', positive=True)
        spacing = math.floor(height * scale * .75 * 100 + .5)
    inset = bottom_inset = None
    layout = mapped_entry.get('text_layout') if isinstance(mapped_entry, dict) else None
    renderer = layout.get('renderer_baseline') if isinstance(layout, dict) else None
    spacing_percent = None
    if renderer is not None:
        if not isinstance(renderer, dict) or renderer.get('model') != 'artifact_presentation_v1':
            raise ValueError('Unsupported mapped renderer baseline metrics: ' + label)
        rendered_scale = _finite(renderer.get('scale'), 'Renderer text scale', positive=True)
        if abs(rendered_scale - scale) > 1e-8:
            raise ValueError('Renderer baseline metrics disagree with placement scale: ' + label)
        if renderer.get('spacing') == 'percent_of_natural_line':
            spacing_percent = _finite(renderer.get('spacing_thousandths_percent'),
                                      'Renderer percentage line spacing', positive=True)
            if spacing is None or int(spacing_percent) != spacing_percent:
                raise ValueError('Percentage line spacing requires an explicit height and integer native value: ' + label)
            natural_height = _finite(renderer.get('natural_line_height_px'),
                                     'Renderer natural line height', positive=True)
            represented_height = _finite(renderer.get('line_height_px'),
                                         'Renderer line height', positive=True)
            if abs(represented_height - natural_height * spacing_percent / 100000) > 1e-8:
                raise ValueError('Mapped percentage line height is inconsistent: ' + label)
            if spacing_percent != math.floor(height / natural_height * 100000 + .5):
                raise ValueError('Mapped percentage spacing disagrees with the requested line height: ' + label)
            spacing_percent = int(spacing_percent)
            line_count = layout.get('line_count', len(paragraphs))
            if not isinstance(line_count, int) or isinstance(line_count, bool) or line_count < 1:
                raise ValueError('Mapped native text line count must be a positive integer: ' + label)
        actual = _finite(renderer.get('first_baseline_px'), 'Renderer first baseline', nonnegative=True)
        desired = _finite(layout.get('native_baseline_ascent'), 'Source first baseline', nonnegative=True)
        adjustment = _finite(layout.get('baseline_adjustment_px'), 'Renderer baseline adjustment')
        if abs(adjustment - (desired - actual)) > 1e-8:
            raise ValueError('Mapped renderer baseline adjustment is inconsistent: ' + label)
        if 'baseline_offset' in obj and abs(desired - _finite(
                obj['baseline_offset'], 'Text baseline_offset', nonnegative=True)) > 1e-8:
            raise ValueError('Mapped baseline disagrees with the requested calibrated baseline: ' + label)
        body_pr = body.find('a:bodyPr', NS)
        if body_pr is None or body_pr.get('anchor', 't') not in ('t', 'ctr', 'b') or body_pr.get('vert', 'horz') != 'horz':
            raise ValueError('Baseline preservation needs a horizontal native text body: ' + label)
        try:
            original = int(body_pr.get('tIns', '0'))
            original_bottom = int(body_pr.get('bIns', '0'))
        except ValueError as error:
            raise ValueError('Invalid native text vertical insets: ' + label) from error
        delta = math.floor(adjustment * scale * EMU_PER_PX + .5)
        inset, bottom_inset = original + delta, original_bottom - delta
        if not all(-2147483648 <= value <= 2147483647 for value in (inset, bottom_inset)):
            raise ValueError('Derived native text inset exceeds DrawingML coordinate range: ' + label)
        result.update(baseline_calibrated=True, source_baseline_px=desired,
                      renderer_baseline_px=actual, baseline_adjustment_px=adjustment,
                      top_inset_before_emu=original, top_inset_after_emu=inset,
                      bottom_inset_before_emu=original_bottom, bottom_inset_after_emu=bottom_inset,
                      native_content_height_preserved=True,
                      baseline_basis='source baseline preserved using registered renderer metrics')
    elif 'baseline_offset' in obj:
        baseline = _finite(obj['baseline_offset'], 'Text baseline_offset', nonnegative=True)
        if not isinstance(layout, dict):
            raise ValueError('Baseline calibration requires mapped registered-font layout metrics: ' + label)
        default = _finite(layout.get('default_native_baseline_ascent'), 'Default native text baseline', nonnegative=True)
        resolved = _finite(layout.get('native_baseline_ascent'), 'Resolved native text baseline', nonnegative=True)
        if abs(resolved - baseline) > 1e-8:
            raise ValueError('Mapped baseline disagrees with the requested calibrated baseline: ' + label)
        body_pr = body.find('a:bodyPr', NS)
        if body_pr is None or body_pr.get('anchor', 't') != 't' or body_pr.get('vert', 'horz') != 'horz':
            raise ValueError('Baseline calibration needs a horizontal top-aligned native text body: ' + label)
        try:
            original = int(body_pr.get('tIns', '0'))
        except ValueError as error:
            raise ValueError('Invalid native text top inset: ' + label) from error
        inset = original + math.floor((baseline - default) * scale * EMU_PER_PX + .5)
        if not 0 <= inset <= 2147483647:
            raise ValueError('Requested baseline needs an unsupported negative or oversized native top inset: ' + label)
        result.update(baseline_calibrated=True, baseline_offset_px=baseline,
                      default_native_baseline_px=default, top_inset_before_emu=original,
                      top_inset_after_emu=inset, baseline_basis='registered-font ascent plus native leading; explicit calibration')
    # Validate all requested operations before any OOXML changes.
    if spacing is not None and spacing_percent is None and not 0 < spacing <= 20116800:
        raise ValueError('Text line_height is outside DrawingML point spacing range: ' + label)
    if spacing is not None:
        for paragraph in paragraphs:
            properties = paragraph.find('a:pPr', NS)
            if properties is None:
                properties = ET.Element(f'{{{A}}}pPr')
                paragraph.insert(0, properties)
            for existing in properties.findall('a:lnSpc', NS):
                properties.remove(existing)
            leading = ET.Element(f'{{{A}}}lnSpc')
            properties.insert(0, leading)
            ET.SubElement(leading, f'{{{A}}}' + ('spcPct' if spacing_percent is not None else 'spcPts'),
                          {'val': str(spacing_percent if spacing_percent is not None else spacing)})
        result.update(line_height_applied=True, line_height_px=obj['line_height'],
                      paragraph_count=len(paragraphs))
        if spacing_percent is None:
            result['spacing_hundredths_pt'] = spacing
        else:
            fractional = spacing_percent % 1000 != 0
            # LibreOffice 26.2 retains only integer percentages on import. Keep
            # the requested fractional value in OOXML; expose its measurable
            # cumulative consequence instead of silently rounding the design.
            whole_percent_pitch_loss = natural_height * (spacing_percent % 1000) / 100000
            result.update(spacing_thousandths_percent=spacing_percent,
                          rendered_line_height_px=renderer['line_height_px'],
                          line_count=line_count, fractional_native_percent=fractional,
                          whole_percent_fallback_pitch_loss_px=whole_percent_pitch_loss,
                          whole_percent_fallback_accumulated_loss_px=max(0, line_count - 1) * whole_percent_pitch_loss,
                          native_precision_review_required=fractional and line_count > 1)
    if inset is not None:
        body_pr.set('tIns', str(inset))
    if bottom_inset is not None:
        body_pr.set('bIns', str(bottom_inset))
    if character_spacing is not None:
        result['character_spacing'] = apply_prepared_character_spacing(character_spacing)
    return result
