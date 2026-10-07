#!/usr/bin/env python3
"""Independent, bounded supported black-outline resolved-scene -> actual PPTX audit.

No rendering imports and no mutation of inputs. Explicit source_id is a
claimed identity, accepted only with full native geometry/style/media checks.
This proves delivery of a declared scene; it does NOT recognize source meaning
or prove that the resolved scene was derived faithfully from the source PDF.
"""
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import posixpath
import re
import time
import stat
from dataclasses import asdict
import zipfile
from xml.etree import ElementTree as ET

EMU = 9525
NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
DRAW = {'sp', 'pic', 'grpSp', 'graphicFrame', 'cxnSp', 'contentPart'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def snap(value):
    return math.floor(value + .5)


def points(command):
    op, value = next(iter(command.items()))
    if op == 'close':
        return []
    if op in ('moveTo', 'lineTo'):
        return [(value['x'], value['y'])]
    if op == 'cubicTo':
        return [(value[k], value[l]) for k, l in [('x1', 'y1'), ('x2', 'y2'), ('x', 'y')]]
    raise ValueError('unsupported scene command: ' + op)


def hull(commands):
    p = [p for cmd in commands for p in points(cmd)]
    xs, ys = zip(*p)
    return [min(xs), min(ys), max(.01, max(xs) - min(xs)), max(.01, max(ys) - min(ys))]


def expected_commands(commands, box):
    names = {'moveTo': 'moveTo', 'lineTo': 'lnTo', 'cubicTo': 'cubicBezTo', 'close': 'close'}
    return [[names[next(iter(cmd))], [[snap((x-box[0])*EMU), snap((y-box[1])*EMU)]
                                    for x, y in points(cmd)]] for cmd in commands]


def actual_commands(path):
    out = []
    for cmd in path:
        op = tag(cmd)
        if op not in {'moveTo', 'lnTo', 'cubicBezTo', 'close'} or cmd.tag != '{'+NS['a']+'}'+op:
            raise ValueError('unsupported native command: ' + op)
        if cmd.attrib or len(cmd) != {'moveTo':1,'lnTo':1,'cubicBezTo':3,'close':0}[op] or (cmd.text or '').strip():
            raise ValueError('unexpected native command attributes')
        pts = []
        for p in cmd:
            if p.tag != '{'+NS['a']+'}pt' or set(p.attrib) != {'x', 'y'} or len(p) or (p.text or '').strip():
                raise ValueError('unsupported native path point')
            pts.append([int(p.get('x')), int(p.get('y'))])
        out.append([op, pts])
    return out


def xfrm(node):
    xf = node.find('p:spPr/a:xfrm', NS)
    if xf is None:
        raise ValueError('missing native shape transform')
    if set(xf.attrib) - {'rot', 'flipH', 'flipV'}:
        raise ValueError('unsupported transform attributes')
    off, ext = xf.find('a:off', NS), xf.find('a:ext', NS)
    if off is None or ext is None or len(xf) != 2 or set(off.attrib)!={'x','y'} or set(ext.attrib)!={'cx','cy'} or len(off) or len(ext):
        raise ValueError('unsupported native transform children')
    return [int(off.get('x')), int(off.get('y')), int(ext.get('cx')), int(ext.get('cy'))], {
        'rotation': int(xf.get('rot', '0')),
        'flipH': xf.get('flipH', '0'), 'flipV': xf.get('flipV', '0')}


def _audit(raw, scene, asset_root, limits, deadline):
    result = {'schema_version': 1, 'scope': 'resolved_scene_to_actual_pptx_delivery_only',
              'source_pdf_fidelity': 'NOT_EVALUATED', 'semantic_recognition': 'NOT_EVALUATED',
              'inputs': {'scene_canonical_sha256':sha(canonical(scene)), 'pptx_sha256':sha(raw)},
              'method': {'emu_per_source_pixel': EMU, 'identity': 'explicit cNvPr descr source_id, then complete geometry/style/media verification; no name guessing',
                         'quantization': 'floor(value + 0.5), exact equality of every command and integer control coordinate',
                         'frame': 'independent min/max control hull from scene commands; zero default placement',
                         'implementation': 'bounded standard-library ZIP/XML/media parser; no rendering imports',
                         'root_transform_profile': 'this supported producer emits root spTree grpSpPr with empty a:xfrm; only empty root wrapper is accepted as identity; nonempty transforms/effects and nested groups are not accepted'},
              'failures': [], 'unresolved': [], 'objects': []}
    failures, unresolved = result['failures'], result['unresolved']
    if scene.get('base') or scene.get('placement'):
        unresolved.append('base-slide placement is outside this bounded supported black-outline prototype')
    if scene.get('canvas', {}).get('background') != '#FFFFFF':
        unresolved.append('nonwhite scene background outside prototype scope')
    ordered = [o for _, o in sorted(enumerate(scene['objects']), key=lambda row:(row[1].get('z_index', row[0]), row[0]))]
    ids = [o['id'] for o in ordered]
    if len(ids) != len(set(ids)):
        failures.append('duplicate resolved scene object IDs')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            failures.append('duplicate package member names')
        pr = _xml(z.read('ppt/presentation.xml'), limits, deadline)
        slides = pr.findall('p:sldIdLst/p:sldId', NS)
        if len(slides) != 1:
            unresolved.append('expected exactly one slide')
        presentation_rels = _xml(z.read('ppt/_rels/presentation.xml.rels'), limits, deadline)
        records = list(presentation_rels)
        if len({r.get('Id') for r in records}) != len(records):
            unresolved.append('duplicate presentation relationship identities')
        slide_rid = slides[0].get('{'+NS['r']+'}id') if len(slides)==1 else None
        rel = next((r for r in records if r.get('Id')==slide_rid),None)
        target = rel.get('Target','') if rel is not None else ''
        member = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt',target))
        if rel is None or rel.get('TargetMode')=='External' or rel.get('Type')!=NS['r']+'/slide' or member!='ppt/slides/slide1.xml':
            unresolved.append('actual presentation slide relationship outside single-slide profile')
        unresolved.extend(_presentation_profile(pr))
        size = pr.find('p:sldSz', NS)
        actual_size = [int(size.get('cx')), int(size.get('cy'))]
        expected_size = [snap(scene['canvas'][k]*EMU) for k in ('width','height')]
        result['slide_size'] = {'actual_emu': actual_size, 'expected_emu': expected_size}
        if actual_size != expected_size:
            failures.append('slide canvas mismatch')
        payload = z.read('ppt/slides/slide1.xml')
        root = _xml(payload, limits, deadline)
        result['slide_xml_sha256'] = sha(payload)
        tree = root.find('p:cSld/p:spTree', NS)
        native = [n for n in tree if tag(n) in DRAW]
        other = [tag(n) for n in tree if tag(n) not in DRAW | {'nvGrpSpPr','grpSpPr'}]
        if other:
            unresolved.append('unhandled slide tree elements: ' + repr(other))
        root_group = tree.find('p:grpSpPr', NS)
        root_xf = tree.find('p:grpSpPr/a:xfrm', NS)
        result['slide_group_properties_xml'] = ET.tostring(root_group, encoding='unicode') if root_group is not None else None
        result['slide_group_transform_xml'] = ET.tostring(root_xf, encoding='unicode') if root_xf is not None else None
        root_group_supported = (root_group is None or (not root_group.attrib and
            all(child.tag == '{'+NS['a']+'}xfrm' and not child.attrib and len(child) == 0 for child in root_group) and
            len(root_group) <= 1))
        result['slide_group_transform_identity_in_supported_producer_profile'] = root_group_supported
        if not root_group_supported:
            unresolved.append('root group has nonempty transform or effective properties outside supported producer profile')
        if any(tag(n) == 'grpSp' for n in native):
            unresolved.append('nested native groups are outside supported producer profile')
        bg = root.find('p:cSld/p:bg/p:bgPr/a:solidFill/a:srgbClr', NS)
        result['slide_background_rgb'] = bg.get('val') if bg is not None else None
        if result['slide_background_rgb'] != 'FFFFFF':
            unresolved.append('white native slide background not independently established')
        rels = _xml(z.read('ppt/slides/_rels/slide1.xml.rels'), limits, deadline)
        rel_by_id = {}
        for rel in rels:
            if rel.get('Id') in rel_by_id:
                failures.append('duplicate slide relationship ID')
            rel_by_id[rel.get('Id')] = rel
        source_native = {}
        native_ids = []
        native_order = []
        for ordinal, n in enumerate(native):
            idn = n.find('.//p:cNvPr', NS)
            descr = idn.get('descr', '') if idn is not None else ''
            match = re.fullmatch(r'source_id=([^;]+)(?:; semantic_group=([^;]+))?', descr)
            sid = match.group(1) if match else None
            native_order.append(sid)
            if sid is None:
                unresolved.append(f'object {ordinal} has no explicit parseable source_id')
                continue
            if sid in source_native:
                failures.append('duplicate native source_id: ' + sid)
            source_native[sid] = (ordinal, n, idn)
            native_ids.append(idn.get('id'))
        if len(native_ids) != len(set(native_ids)):
            failures.append('duplicate native cNvPr ID')
        result['paint_order'] = {'scene_ids': ids, 'native_source_ids': native_order, 'exact_match': native_order == ids}
        if native_order != ids:
            failures.append('native painter order or object inventory differs from resolved scene')
        for o in ordered:
            _tick(deadline)
            rec = {'id': o['id'], 'kind': o['kind'], 'failures': [], 'unresolved': []}
            result['objects'].append(rec)
            if o['id'] not in source_native:
                rec['failures'].append('missing actual PPT source_id')
                rec['status'] = 'FAIL'
                continue
            ordinal, n, idn = source_native[o['id']]
            rec.update(native_ordinal=ordinal, native_id=idn.get('id'), native_name=idn.get('name'),
                       explicit_source_id_description=idn.get('descr'), native_xml_sha256=sha(ET.tostring(n)))
            try:
                actual_frame, orientation = xfrm(n)
                rec.update(actual_frame_emu=actual_frame, orientation=orientation)
                if orientation['rotation'] % 21600000 or any(orientation[k] not in {'0','false'} for k in ('flipH','flipV')):
                    rec['failures'].append('changed orientation')
                if idn.get('hidden','0') not in {'0','false'}:
                    rec['failures'].append('native object hidden')
                if n.find('p:style', NS) is not None:
                    rec['unresolved'].append('theme style references require separate effective-style evaluation')
                sp = n.find('p:spPr', NS)
                if o['kind'] == 'path':
                    if tag(n) != 'sp':
                        raise ValueError('path is not native p:sp')
                    box = hull(o['commands'])
                    want_frame = [snap(v*EMU) for v in box]
                    rec['scene_control_hull_px'] = box
                    rec['expected_frame_emu'] = want_frame
                    if actual_frame != want_frame:
                        rec['failures'].append('native path transform differs from independently recomputed frame')
                    paths = sp.findall('a:custGeom/a:pathLst/a:path', NS)
                    if len(paths) != 1:
                        raise ValueError('path is not one custom path')
                    p = paths[0]
                    pw, ph = int(p.get('w')), int(p.get('h'))
                    expected_extent = [max(1,snap(v*EMU)) for v in box[2:]]
                    rec['custom_path_extent_emu'] = [pw,ph]
                    if [pw,ph] != expected_extent:
                        rec['failures'].append('custom path coordinate dimensions mismatch')
                    if set(p.attrib)-{'w','h'}:
                        rec['unresolved'].append('custom path attributes outside default paint scope')
                    actual, expected = actual_commands(p), expected_commands(o['commands'], box)
                    rec.update(command_count=len(expected), point_count=sum(len(v[1]) for v in expected),
                               actual_commands_sha256=sha(canonical(actual)), expected_commands_sha256=sha(canonical(expected)),
                               commands_exact_match=actual == expected)
                    if actual != expected:
                        rec['failures'].append('full native path commands differ')
                    if actual == expected and [pw,ph] == expected_extent and pw and ph:
                        original = [xy for cmd in o['commands'] for xy in points(cmd)]
                        restored = [(actual_frame[0]/EMU+x*actual_frame[2]/pw/EMU,
                                     actual_frame[1]/EMU+y*actual_frame[3]/ph/EMU)
                                    for _, pts in actual for x,y in pts]
                        rec['max_abs_control_coordinate_quantization_error_px'] = max(abs(a-b) for xy,uv in zip(original,restored) for a,b in zip(xy,uv))
                    expected_style = {'fill':'#000000','opacity':1.0,'stroke':'none','stroke_width':0}
                    fill = sp.find('a:solidFill/a:srgbClr', NS)
                    alpha = fill.find('a:alpha', NS) if fill is not None else None
                    line = sp.find('a:ln', NS)
                    observed = {'fill_rgb': fill.get('val') if fill is not None else None,
                                'alpha':int(alpha.get('val')) if alpha is not None else None,
                                'line_width_emu':int(line.get('w','0')) if line is not None else None,
                                'line_no_fill':line is not None and line.find('a:noFill',NS) is not None}
                    rec['paint'] = observed
                    if o.get('style') != expected_style:
                        rec['unresolved'].append('scene paint outside supported black-outline solid-black no-stroke scope')
                    if observed != {'fill_rgb':'000000','alpha':100000,'line_width_emu':0,'line_no_fill':True}:
                        rec['failures'].append('native paint differs from supported black-outline scene paint')
                    if set(tag(v) for v in sp)-{'xfrm','custGeom','solidFill','ln'}:
                        rec['unresolved'].append('unsupported native path paint/effect element')
                elif o['kind'] == 'image':
                    if tag(n) != 'pic':
                        raise ValueError('image is not native p:pic')
                    if o.get('fit','contain') != 'stretch':
                        rec['unresolved'].append('only explicit stretch image placement evaluated')
                    box = [o['box'][k] for k in ('x','y','width','height')]
                    expected_frame = [snap(v*EMU) for v in box]
                    rec['expected_frame_emu'] = expected_frame
                    if actual_frame != expected_frame:
                        rec['failures'].append('native image transform mismatch')
                    rect = n.find('p:blipFill/a:srcRect', NS)
                    actual_crop = {k:int(rect.get(v,'0')) if rect is not None else 0 for k,v in [('left','l'),('top','t'),('right','r'),('bottom','b')]}
                    expected_crop = {k:snap(o.get('crop',{}).get(k,0)*100000) for k in actual_crop}
                    rec.update(actual_crop_100000=actual_crop, expected_crop_100000=expected_crop)
                    if actual_crop != expected_crop:
                        rec['failures'].append('native image crop mismatch')
                    blip = n.find('p:blipFill/a:blip', NS)
                    if blip is None or len(blip) or set(blip.attrib) != {'{'+NS['r']+'}embed'}:
                        raise ValueError('unsupported embedded-image transform/effect')
                    rel = rel_by_id.get(blip.get('{'+NS['r']+'}embed'))
                    if rel is None or rel.get('TargetMode') == 'External' or rel.get('Type')!=NS['r']+'/image':
                        raise ValueError('image relationship is not internal image')
                    target = rel.get('Target','')
                    member = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt/slides',target))
                    embedded = z.read(member)
                    asset, asset_bytes = _asset(asset_root, o['path'], limits['max_asset_bytes'])
                    rec['image'] = {'embedded_member':member, 'embedded_sha256':sha(embedded),
                                    'declared_sha256':o['sha256'], 'asset':{'path':str(asset),'sha256':sha(asset_bytes)},
                                    'exact_embedded_asset_bytes_match':embedded == asset_bytes}
                    if sha(embedded) != o['sha256'] or not rec['image']['exact_embedded_asset_bytes_match']:
                        rec['failures'].append('embedded image bytes differ from resolved scene asset')
                    if embedded[:8] != b'\x89PNG\r\n\x1a\n':
                        rec['unresolved'].append('only source PNG assets are supported')
                    geom = sp.find('a:prstGeom',NS)
                    if geom is None or geom.get('prst') != 'rect' or set(tag(v) for v in sp)-{'xfrm','prstGeom'}:
                        rec['unresolved'].append('unsupported image geometry/paint effects')
                    if n.find('p:blipFill/a:stretch',NS) is None:
                        rec['unresolved'].append('native image stretch not explicit')
                else:
                    rec['unresolved'].append('unsupported resolved scene kind')
            except (ValueError,KeyError,TypeError,AttributeError) as exc:
                rec['unresolved'].append(str(exc))
            rec['status'] = 'FAIL' if rec['failures'] else 'UNRESOLVED' if rec['unresolved'] else 'VERIFIED_IN_DECLARED_SCOPE'
        unresolved.extend(_strict_profile(root))
        result['inherited_slide_drawings'] = []
        for member in z.namelist():
            if re.fullmatch(r'ppt/(?:slideMasters/slideMaster|slideLayouts/slideLayout)\d+\.xml',member):
                r = _xml(z.read(member), limits, deadline)
                unresolved.extend(_inherited_profile(r, member))
                counts = Counter(tag(v) for v in r.findall('p:cSld/p:spTree/*',NS) if tag(v) in DRAW)
                result['inherited_slide_drawings'].append({'member':member,'drawing_counts':dict(counts),'sha256':sha(z.read(member))})
                if counts:
                    unresolved.append('layout/master contains drawings; visibility inheritance not evaluated: '+member)
    counts = Counter(o['status'] for o in result['objects'])
    result['summary'] = {'scene_objects':len(ordered), 'actual_slide_objects':len(native),
                         'object_status_counts':dict(counts), 'verified_paths':sum(o['kind']=='path' and o['status']=='VERIFIED_IN_DECLARED_SCOPE' for o in result['objects']),
                         'verified_images':sum(o['kind']=='image' and o['status']=='VERIFIED_IN_DECLARED_SCOPE' for o in result['objects']),
                         'verified_control_points':sum(o.get('point_count',0) for o in result['objects'] if o['status']=='VERIFIED_IN_DECLARED_SCOPE'),
                         'max_abs_control_coordinate_quantization_error_px':max([o.get('max_abs_control_coordinate_quantization_error_px',0) for o in result['objects']] or [0])}
    result['status'] = 'FAIL' if failures or counts['FAIL'] else 'UNRESOLVED' if unresolved or counts['UNRESOLVED'] else 'VERIFIED_IN_DECLARED_SCOPE'
    result['limitations'] = ['source_id metadata is an explicit author claim; full geometry/style/media checks establish only correspondence to this resolved scene',
                             'no source PDF extraction, glyph meaning, formula transcription, arrow semantic interpretation, or user visual acceptance is established',
                             'path coordinates are quantized to DrawingML integer units; exact source floating-point bytes are not preserved',
                             'empty root spTree xfrm is accepted only under the explicitly supported producer profile; any attrs, children, sibling effects or nested groups remain unresolved',
                             'bounded to direct one-slide supported black-outline objects, default placement, black filled cubic paths, unchanged stretch raster; unsupported features stay unresolved']
    return result


def _tick(deadline):
    if time.monotonic() > deadline:
        raise ValueError('PPTX audit time budget exceeded')


def _read(path, limit):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('PPTX/asset byte budget exceeded')
    return raw


def _asset(root, relative, limit):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Image asset path must be relative')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Image asset escapes its bound asset directory')
    return path, _read(path, limit)


def _xml(raw, limits, deadline):
    _tick(deadline)
    if len(raw) > limits['max_xml_bytes'] or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('XML bytes/entities outside bounded profile')
    root = ET.fromstring(raw)
    for index, _ in enumerate(root.iter()):
        if index >= limits['max_xml_nodes']:
            raise ValueError('XML node budget exceeded')
    _tick(deadline)
    return root


def _tree(node):
    return None if node is None else (node.tag, tuple(sorted(node.attrib.items())),
        (node.text or '').strip(), tuple(_tree(c) for c in node))


def _strict_profile(root):
    # Closed subtree profiles: unknown color transforms, extra alpha, guides,
    # picture fillRect shifts and hidden text are never silently ignored.
    a, p = NS['a'], NS['p']
    fill = ET.fromstring(f'<solidFill xmlns="{a}"><srgbClr val="000000"><alpha val="100000"/></srgbClr></solidFill>')
    line = ET.fromstring(f'<ln xmlns="{a}" w="0"><noFill/><prstDash val="solid"/></ln>')
    rect = ET.fromstring(f'<prstGeom xmlns="{a}" prst="rect"><avLst/></prstGeom>')
    errors = []
    expected_bg = ET.fromstring(f'<bg xmlns="{p}"><bgPr><solidFill xmlns="{a}"><srgbClr val="FFFFFF"/></solidFill></bgPr></bg>')
    if root.tag != f'{{{p}}}sld' or root.attrib or [c.tag for c in root] not in ([f'{{{p}}}cSld'],[f'{{{p}}}cSld',f'{{{p}}}clrMapOvr']):
        errors.append('unknown/duplicate slide properties, namespace or effects')
    cs = root.find('p:cSld',NS)
    content_tags=[c.tag for c in cs] if cs is not None else []
    base_tags=[f'{{{p}}}bg',f'{{{p}}}spTree']
    if cs is None or set(cs.attrib)-{'name'} or content_tags not in (base_tags,base_tags+[f'{{{p}}}extLst']):
        errors.append('common slide background/tree must be unique and have no other effective children')
    elif len(cs)==3 and not _slide_creation_metadata(cs[2]):
        errors.append('unknown common-slide extension; only inert creationId is supported')
    if _tree(root.find('p:cSld/p:bg',NS)) != _tree(expected_bg):
        errors.append('background is not the complete opaque-white profile')
    override=root.find('p:clrMapOvr',NS)
    if override is not None:
        expected_override=ET.fromstring(f'<clrMapOvr xmlns="{p}"><masterClrMapping xmlns="{a}"/></clrMapOvr>')
        if _tree(override)!=_tree(expected_override): errors.append('unknown color-map override')
    st=root.find('p:cSld/p:spTree',NS)
    if st is None or st.attrib or len(st.findall('p:nvGrpSpPr',NS))!=1 or len(st.findall('p:grpSpPr',NS))!=1 or any(c.tag not in {f'{{{p}}}'+v for v in ('nvGrpSpPr','grpSpPr','sp','pic')} for c in st):
        errors.append('unknown/duplicate direct slide-tree properties or namespace')
    if st is not None:
        errors.extend(_nonvisual_profile(st.find('p:nvGrpSpPr',NS),'group'))
    for node in root.findall('p:cSld/p:spTree/*', NS):
        kind = tag(node)
        if kind not in ('sp', 'pic'):
            continue
        if node.tag != f'{{{p}}}'+kind:
            errors.append('foreign drawing element namespace')
        ident = node.find('.//p:cNvPr', NS)
        sid = ident.get('descr', '<no identity>') if ident is not None else '<no identity>'
        local = _nonvisual_profile(node.find('p:nvSpPr' if kind=='sp' else 'p:nvPicPr',NS),kind)
        children = ['nvSpPr', 'spPr'] if kind == 'sp' else ['nvPicPr', 'blipFill', 'spPr']
        if node.attrib or [c.tag for c in node] != [f'{{{p}}}'+v for v in children]:
            local.append('extra/duplicate shape children, text body or attributes')
        props = node.find('p:spPr', NS)
        if props is None or props.attrib:
            local.append('unknown shape properties')
        elif kind == 'sp':
            if [c.tag for c in props] != [f'{{{a}}}'+v for v in ('xfrm','custGeom','solidFill','ln')]:
                local.append('extra/duplicate path properties')
            if _tree(props.find('a:solidFill', NS)) != _tree(fill):
                local.append('unknown color or alpha transform')
            if _tree(props.find('a:ln', NS)) != _tree(line):
                local.append('unknown line subtree')
            geom = props.find('a:custGeom', NS)
            if geom is None or geom.attrib or [c.tag for c in geom] != [f'{{{a}}}pathLst'] or geom[0].attrib or len(geom[0]) != 1:
                local.append('custom geometry guides/adjustments outside profile')
        else:
            if [c.tag for c in props] != [f'{{{a}}}xfrm',f'{{{a}}}prstGeom'] or _tree(props.find('a:prstGeom', NS)) != _tree(rect):
                local.append('picture geometry/effect outside profile')
            bf = node.find('p:blipFill', NS)
            if bf is None or bf.attrib or [c.tag for c in bf] != [f'{{{a}}}'+v for v in ('blip','srcRect','stretch')]:
                local.append('picture fill children outside profile')
            else:
                src, stretch = bf.find('a:srcRect', NS), bf.find('a:stretch', NS)
                if src is None or set(src.attrib) != {'l','t','r','b'} or len(src):
                    local.append('picture source crop outside profile')
                if stretch is None or stretch.attrib or len(stretch):
                    local.append('picture stretch fillRect outside profile')
        errors.extend(sid + ': ' + item for item in local)
    return errors


def _validate_scene(scene, limits):
    if not isinstance(scene, dict) or not isinstance(scene.get('objects'), list) or not isinstance(scene.get('canvas'), dict):
        raise ValueError('Expected scene structure invalid')
    if len(scene['objects']) > limits['max_source_paints']:
        raise ValueError('Scene object budget exceeded')
    count = 0
    def finite(v):
        if isinstance(v, bool) or not isinstance(v, (int,float)) or abs(v) > 1e9 or not math.isfinite(v):
            raise ValueError('Non-finite/out-of-range coordinate')
    for k in ('width','height'):
        finite(scene['canvas'][k])
        if scene['canvas'][k] <= 0:
            raise ValueError('Invalid canvas extent')
    for obj in scene['objects']:
        if not isinstance(obj,dict) or not isinstance(obj.get('id'),str):
            raise ValueError('Invalid object identity')
        if type(obj.get('z_index')) is not int:
            raise ValueError('Explicit integral painter order required')
        if obj.get('kind') == 'path':
            for command in obj['commands']:
                if not isinstance(command,dict) or len(command) != 1:
                    raise ValueError('Invalid source path command')
                for xy in points(command):
                    count += 1
                    for v in xy: finite(v)
        elif obj.get('kind') == 'image':
            for k in ('x','y','width','height'): finite(obj['box'][k])
            for v in obj.get('crop',{}).values(): finite(v)
        if count > limits['max_control_points']:
            raise ValueError('Scene control point budget exceeded')


def audit_pptx_fidelity(pptx_path, expected_scene, *, asset_root, limits=None):
    """Verify one supported direct slide against independently replayed geometry.

    This checks integer geometry, original embedded PNG bytes, placement and
    paint order; it does not render the slide or recognize source semantics.
    Unknown OOXML/effects and exhausted budgets return UNRESOLVED. The caller
    must separately ensure expected_scene was freshly replayed from its source.
    """
    from .pdf_source_replay import ReplayLimits
    limits = ReplayLimits() if limits is None else limits
    if isinstance(limits, ReplayLimits):
        limits = asdict(limits)
    if not isinstance(limits, dict):
        raise ValueError('limits must be ReplayLimits or its explicit mapping')
    defaults = asdict(ReplayLimits())
    if set(limits)-set(defaults):
        raise ValueError('Unknown PPTX budget key')
    limits = {**defaults, **limits}
    for key,value in limits.items():
        if key == 'timeout_seconds':
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not .05 <= value <= 600 or not math.isfinite(value):
                raise ValueError('Invalid time budget')
        elif type(value) is not int or not 1 <= value <= 1073741824:
            raise ValueError('Invalid integral budget: '+key)
    start = time.monotonic()
    raw = None
    try:
        _validate_scene(expected_scene, limits)
        raw = _read(pptx_path, limits['max_pptx_bytes'])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > limits['max_zip_entries'] or len({z.filename for z in entries}) != len(entries):
                raise ValueError('ZIP entry budget or duplicate member')
            if sum(z.file_size for z in entries) > limits['max_zip_uncompressed_bytes']:
                raise ValueError('ZIP expanded byte budget exceeded')
            for entry in entries:
                name = entry.filename
                if name.startswith(('/', '\\')) or '\\' in name or '..' in name.split('/') or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Unsafe ZIP member identity')
                if entry.flag_bits & 1 or entry.file_size > limits['max_zip_entry_bytes']:
                    raise ValueError('Encrypted or oversized ZIP member')
                if entry.file_size > max(1,entry.compress_size)*limits['max_zip_expansion_ratio']:
                    raise ValueError('ZIP expansion ratio budget exceeded')
        result = _audit(raw, expected_scene, asset_root, limits, start+limits['timeout_seconds'])
        result['inputs']['pptx_path'] = str(Path(pptx_path).resolve())
    except (OSError,ValueError,KeyError,TypeError,AttributeError,OverflowError,RuntimeError,NotImplementedError,RecursionError,zipfile.BadZipFile,ET.ParseError) as error:
        result = {'schema_version':1,'status':'UNRESOLVED','scope':'actual_pptx_geometry_and_media',
                  'failures':[],'unresolved':[str(error)],'semantic_recognition':'NOT_PROVIDED',
                  'visual_acceptance':'NOT_EVALUATED'}
    if raw is not None:
        result.setdefault('inputs',{}).update(pptx_path=str(Path(pptx_path).resolve()),pptx_sha256=sha(raw))
    result['limits'] = limits
    result['elapsed_seconds'] = time.monotonic()-start
    result['semantic_recognition'] = 'NOT_PROVIDED'
    result['visual_acceptance'] = 'NOT_EVALUATED'
    return result


def _nonvisual_profile(node,kind):
    """Allow known inert IDs/locks/creation metadata; reject placeholders/media."""
    if node is None or node.attrib:
        return ['missing/unknown nonvisual properties']
    a,p=NS['a'],NS['p']; errors=[]
    expected={'sp':('cNvPr','cNvSpPr','nvPr'),'pic':('cNvPr','cNvPicPr','nvPr'),
              'group':('cNvPr','cNvGrpSpPr','nvPr')}[kind]
    tags=[c.tag for c in node]
    if len(tags)!=len(set(tags)) or any(t not in {f'{{{p}}}'+v for v in expected} for t in tags):
        errors.append('unknown/duplicate nonvisual subtree')
    for child in node:
        name=tag(child)
        if name=='cNvPr':
            if set(child.attrib)-{'id','name','descr','hidden'} or child.get('hidden','0') not in ('0','false'):
                errors.append('unknown or hidden nonvisual identity flags')
            for extension in child:
                ext=extension.find('a:ext',NS)
                creation=ext[0] if ext is not None and len(ext)==1 else None
                if (extension.tag!=f'{{{a}}}extLst' or extension.attrib or len(extension)!=1 or
                    ext is None or ext.attrib!={'uri':'{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}'} or
                    creation is None or creation.tag!='{http://schemas.microsoft.com/office/drawing/2014/main}creationId' or
                    set(creation.attrib)!={'id'} or len(creation) or not re.fullmatch(r'\{[0-9A-Fa-f-]{36}\}',creation.get('id',''))):
                    errors.append('unknown nonvisual extension/hyperlink/effect')
        elif name=='nvPr':
            if child.attrib or len(child):errors.append('placeholder/media/nonempty application nonvisual properties')
        else:
            lock='spLocks' if kind=='sp' else 'picLocks' if kind=='pic' else None
            attrs={'noGrp':'1'} if kind=='sp' else {'noChangeAspect':'1'}
            if child.attrib or len(child)>1 or any(c.tag!=f'{{{a}}}'+str(lock) or c.attrib!=attrs or len(c) for c in child):
                errors.append('unknown nonvisual lock/geometry flags')
    return errors


def _presentation_profile(root):
    p=NS['p']; errors=[]
    allowed={'sldMasterIdLst','notesMasterIdLst','sldIdLst','sldSz','notesSz'}
    tags=[c.tag for c in root]
    if root.tag!=f'{{{p}}}presentation' or root.attrib or len(tags)!=len(set(tags)) or any(c.tag not in {f'{{{p}}}'+v for v in allowed} for c in root):
        errors.append('presentation properties, namespace or duplicate lists outside profile')
    for c in root:
        name=tag(c)
        if name.endswith('IdLst'):
            if c.attrib or len(c)!=1:errors.append('presentation identity list outside single-slide profile')
            for identity in c:
                if identity.tag!=f'{{{p}}}'+name[:-3] or len(identity) or set(identity.attrib)-{'id','{'+NS['r']+'}id'}:
                    errors.append('unknown presentation identity node')
        elif len(c) or set(c.attrib)!={'cx','cy'}:
            errors.append('unknown presentation size attributes/children')
    return errors


def _inherited_profile(root,member):
    p,a=NS['p'],NS['a'];errors=[]
    is_layout=root.tag==f'{{{p}}}sldLayout'
    if not is_layout and root.tag!=f'{{{p}}}sldMaster':return ['foreign inherited root namespace: '+member]
    allowed={'cSld','clrMapOvr'} if is_layout else {'cSld','clrMap','sldLayoutIdLst','txStyles'}
    if set(root.attrib)-({'type'} if is_layout else set()) or len([c.tag for c in root])!=len({c.tag for c in root}) or any(c.tag not in {f'{{{p}}}'+v for v in allowed} for c in root):
        errors.append('unknown inherited effective root properties: '+member)
    cs=root.find('p:cSld',NS)
    if cs is None or set(cs.attrib)-{'name'} or len(cs.findall('p:spTree',NS))!=1 or len(cs.findall('p:bg',NS))>1 or any(c.tag not in (f'{{{p}}}bg',f'{{{p}}}spTree') for c in cs):
        errors.append('unknown/duplicate inherited common-slide content: '+member)
    st=root.find('p:cSld/p:spTree',NS)
    if st is None or st.attrib or [c.tag for c in st]!=[f'{{{p}}}nvGrpSpPr',f'{{{p}}}grpSpPr']:
        errors.append('inherited drawings or unknown tree content: '+member)
    else:
        errors.extend(_nonvisual_profile(st[0],'group'))
        if st[1].attrib or any(c.tag!=f'{{{a}}}xfrm' or c.attrib or len(c) for c in st[1]) or len(st[1])>1:
            errors.append('inherited group transformation/effect: '+member)
    # Background/theme text properties cannot affect a fully opaque explicit
    # white slide with no text or scheme-colored drawing. Other live content
    # and root effects have been rejected above.
    return errors


def _slide_creation_metadata(node):
    p=NS['p']
    if node.tag!=f'{{{p}}}extLst' or node.attrib or len(node)!=1:
        return False
    ext=node[0]
    if ext.tag!=f'{{{p}}}ext' or ext.attrib!={'uri':'{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}'} or len(ext)!=1:
        return False
    creation=ext[0]
    return (creation.tag=='{http://schemas.microsoft.com/office/powerpoint/2010/main}creationId' and
            set(creation.attrib)=={'val'} and not len(creation) and
            re.fullmatch(r'[0-9]{1,10}',creation.get('val','')) is not None and
            0<=int(creation.get('val'))<=4294967295)
