"""Authenticate restored native cubics before a bounded fill-rule rewrite.

The only mutation is a single path replacement after independent final XML
verification. Unsupported input keeps the entry (post-restoration) tree intact.
Scene/map inputs are authoritative build inputs, not a PDF replay certificate.
"""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
import math
import re
from xml.etree import ElementTree as ET

from .pdf_cubic_winding import (
    normalize_isolated_cubic_fill, UnsupportedPdfCubicWindingError, _Arithmetic, _limits,
)
from .pdf_cubic_contact_winding import _normalize_with_budget as _normalize_contact_with_budget
from .native_cubic_geometry import (
    decode_native_cubic_path, verify_native_cubic_path_encoding,
    _decode, _proof_mode, _verify_native_cubic_path_encoding_with_budget,
)

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
K = 9525
MAX_INT = 2**31 - 1
POLICY = 'isolated_cubic_authenticated_existing_native_grid_v1'
CONTACT_POLICY = 'endpoint_contact_cubic_authenticated_existing_native_grid_v1'
TRANSVERSE_POLICY = 'transverse_line_cubic_authenticated_existing_native_grid_v1'
CLASSIFIED_POLICY = 'classified_contact_or_transverse_cubic_v1'


def _fail(code, message):
    raise UnsupportedPdfCubicWindingError(code, message)


def _number(value):
    if type(value) not in (int, float):
        _fail('source_binding', 'Source numbers must be finite int/float, not bool')
    try:
        if not math.isfinite(value):
            _fail('source_binding', 'Nonfinite source number')
        return F(value)
    except (OverflowError, ValueError):
        _fail('source_binding', 'Source number exceeds the bounded numeric domain')


def _integer(value, positive=False, uint32=False):
    if type(value) is not str or len(value) > 11 or not re.fullmatch(r'-?(0|[1-9][0-9]*)', value):
        _fail('native_context', 'Native integer must have bounded canonical syntax')
    n = int(value)
    limit = 2**32 - 1 if uint32 else MAX_INT
    if str(n) != value or abs(n) > limit or (positive and n <= 0):
        _fail('native_context', 'Native integer exceeds the admitted range')
    return n


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        _fail('source_binding', 'Source/receipt cannot be serialized in the bounded JSON profile')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _one(node, tag):
    found = [c for c in node if c.tag == tag]
    if len(found) != 1:
        _fail('native_context', 'Native requires exactly one ' + tag)
    return found[0]


def _structure(node, attrs, children=()):
    if set(node.attrib) != set(attrs) or any(c.tag not in children for c in node):
        _fail('native_context', 'Unsupported native attributes or children: ' + node.tag)
    if node.text and node.text.strip():
        _fail('native_context', 'Unexpected native element text')


def _leaf(node, attrs):
    _structure(node, attrs)
    if len(node):
        _fail('native_context', 'Expected native leaf')


def _metadata(node, slide=False):
    prefix = P if slide else A
    uri = '{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}' if slide else '{FF2B5EF4-FFF2-40B4-BE49-F238E27FC236}'
    name = '{http://schemas.microsoft.com/office/powerpoint/2010/main}creationId' if slide else '{http://schemas.microsoft.com/office/drawing/2014/main}creationId'
    _structure(node, (), (prefix+'ext',))
    if node.tag != prefix+'extLst' or len(node) != 1:
        _fail('native_context', 'Unsupported native metadata')
    ext = node[0]
    _structure(ext, ('uri',), (name,))
    if ext.get('uri') != uri or len(ext) != 1:
        _fail('native_context', 'Unknown native metadata extension')
    _leaf(ext[0], ('val' if slide else 'id',))


def _bounded_tree(root, max_nodes, max_bytes, budget=None):
    if type(root) is not ET.Element:
        _fail('native_context', 'Actual native slide root required')
    stack = [root]; seen = set(); size = 0
    while stack:
        if budget is not None:budget.spend()
        node = stack.pop()
        if type(node) is not ET.Element:
            _fail('native_context', 'Only actual ElementTree nodes are admitted')
        if type(node.attrib) is not dict:
            _fail('native_context', 'Native XML attributes require a plain dictionary')
        if id(node) in seen:
            _fail('native_context', 'Repeated or cyclic native element reference')
        seen.add(id(node))
        if len(seen) > max_nodes or type(node.tag) is not str or len(node.attrib) > 32:
            _fail('budget', 'Native tree node/attribute budget exceeded')
        for value in (node.tag, node.text, node.tail, *node.attrib.keys(), *node.attrib.values()):
            if value is not None:
                if type(value) is not str:
                    _fail('native_context', 'Native XML contains nonstring content')
                size += len(value)
                if size > max_bytes:
                    _fail('budget', 'Native XML text budget exceeded')
        if node.tail and node.tail.strip():
            _fail('native_context', 'Unexpected native tail text')
        if len(node) > max_nodes - len(seen) + 1:
            _fail('budget', 'Native child budget exceeded')
        stack.extend(node)
    return len(seen)


def _select(root, oid):
    if root.tag != P+'sld':
        _fail('native_context', 'Expected actual p:sld root')
    _structure(root, (), (P+'cSld',))
    content = _one(root, P+'cSld')
    _structure(content, (), (P+'bg', P+'spTree', P+'extLst'))
    for tag in (P+'bg', P+'extLst'):
        if sum(c.tag == tag for c in content) > 1:
            _fail('native_context', 'Duplicate slide context')
    for item in content:
        if item.tag == P+'extLst':
            _metadata(item, slide=True)
    tree = _one(content, P+'spTree')
    _structure(tree, (), (P+'nvGrpSpPr', P+'grpSpPr', P+'sp', P+'pic', P+'grpSp', P+'cxnSp', P+'graphicFrame'))
    group = _one(tree, P+'grpSpPr')
    _structure(group, (), (A+'xfrm',))
    if len(group) > 1:
        _fail('native_context', 'Duplicate parent transform')
    for transform in group:
        _leaf(transform, ())  # Proven exporter empty transform profile only.
    nvgroup = _one(tree, P+'nvGrpSpPr')
    _structure(nvgroup, (), (P+'cNvPr', P+'cNvGrpSpPr', P+'nvPr'))
    identity = _one(nvgroup, P+'cNvPr')
    _leaf(identity, ('id', 'name'))
    _leaf(_one(nvgroup, P+'cNvGrpSpPr'), ())
    _leaf(_one(nvgroup, P+'nvPr'), ())
    ids, names = set(), set()
    for identity in tree.iter(P+'cNvPr'):
        n = _integer(identity.get('id'), True, True)
        if n in ids:
            _fail('native_identity', 'Duplicate numeric native identity')
        ids.add(n)
        name = identity.get('name')
        if name and name in names:
            _fail('native_identity', 'Duplicate native name')
        names.add(name)
    matches = [s for s in tree if s.tag == P+'sp' and any(n.get('name') == oid for n in s.iter(P+'cNvPr'))]
    if len(matches) != 1:
        _fail('native_identity', 'Source shape missing, duplicated, or nested')
    shape = matches[0]
    _structure(shape, (), (P+'nvSpPr', P+'spPr'))
    nv = _one(shape, P+'nvSpPr')
    _structure(nv, (), (P+'cNvPr', P+'cNvSpPr', P+'nvPr'))
    identity = _one(nv, P+'cNvPr')
    if not {'id', 'name'} <= set(identity.attrib) or set(identity.attrib) - {'id', 'name', 'descr', 'hidden'}:
        _fail('native_identity', 'Unknown native identity attributes')
    if identity.get('name') != oid or identity.get('hidden', '0') not in ('0', 'false'):
        _fail('native_identity', 'Selected native identity changed or hidden')
    if identity.text and identity.text.strip():
        _fail('native_identity', 'Unexpected native identity text')
    if len(identity) > 1:
        _fail('native_identity', 'Duplicate native identity metadata')
    for ext in identity:
        if ext.tag != A+'extLst':
            _fail('native_identity', 'Unknown native identity behavior')
        _metadata(ext)
    locks = _one(nv, P+'cNvSpPr')
    _structure(locks, (), (A+'spLocks',))
    if len(locks) > 1:
        _fail('native_context', 'Duplicate shape locks')
    for lock in locks:
        _leaf(lock, ('noGrp',))
        if lock.get('noGrp') != '1':
            _fail('native_context', 'Unknown shape locking context')
    _leaf(_one(nv, P+'nvPr'), ())
    return shape, identity, group


def _paint(shape, obj):
    style = obj.get('style')
    if type(style) is not dict or set(style) - {'fill', 'stroke', 'stroke_width', 'opacity', 'fill_rule', 'fill-rule'}:
        _fail('source_paint', 'Only a declared solid fill/no-stroke scene style is supported')
    if any(type(c.get(k, 'nonzero')) is not str or c.get(k, 'nonzero') != 'nonzero' for c in (obj, style) for k in ('fill_rule', 'fill-rule')):
        _fail('source_paint', 'Scene must declare nonzero fill')
    fill = style.get('fill')
    if type(fill) is not str or not re.fullmatch(r'#[a-fA-F0-9]{6}', fill) or type(style.get('stroke')) is not str or style.get('stroke') != 'none':
        _fail('source_paint', 'Only solid RGB fill and explicit no stroke are supported')
    if _number(style.get('stroke_width', 0)) != 0:
        _fail('source_paint', 'Nonzero declared stroke width is outside this profile')
    opacity = _number(style.get('opacity', 1))
    if not 0 < opacity <= 1:
        _fail('source_paint', 'Visible finite fill opacity required')
    props = _one(shape, P+'spPr')
    _structure(props, (), (A+'xfrm', A+'custGeom', A+'solidFill', A+'ln'))
    solid = _one(props, A+'solidFill'); _structure(solid, (), (A+'srgbClr',))
    color = _one(solid, A+'srgbClr'); _structure(color, ('val',), (A+'alpha',))
    if color.get('val', '').lower() != fill[1:].lower() or len(color) > 1:
        _fail('native_paint', 'Native color or alpha structure differs from scene')
    alpha = 100000
    for child in color:
        _leaf(child, ('val',)); alpha = _integer(child.get('val'))
    if alpha != math.floor(float(opacity)*100000+.5) or not 0 < alpha <= 100000:
        _fail('native_paint', 'Native opacity differs from visible scene serialization')
    line = _one(props, A+'ln')
    if set(line.attrib) - {'w'} or line.get('w', '0') != '0':
        _fail('native_paint', 'Unexpected native line width or attributes')
    _structure(line, line.attrib, (A+'noFill', A+'prstDash'))
    _leaf(_one(line, A+'noFill'), ())
    if sum(c.tag == A+'prstDash' for c in line) > 1:
        _fail('native_paint', 'Duplicate native line style')
    for child in line:
        if child.tag == A+'prstDash':
            _leaf(child, ('val',))
            if child.get('val') != 'solid':
                _fail('native_paint', 'Unsupported native line dash')
    return props, {'color': fill.lower(), 'native_alpha_units': alpha,
                   'scene_opacity_exact': str(opacity),
                   'existing_alpha_error_exact': str(abs(opacity-F(alpha, 100000)))}


def _source_commands(obj, maximum, budget=None):
    if set(obj) - {'id','kind','commands','style','z_index','group_id','fill_rule','fill-rule'}:
        _fail('source_binding', 'Unknown source object context is not admitted')
    if 'group_id' in obj and (type(obj['group_id']) is not str or len(obj['group_id']) > 512):
        _fail('source_binding', 'Invalid source semantic group identity')
    if 'z_index' in obj:
        _number(obj['z_index'])
    commands = obj.get('commands')
    if type(commands) is not list or not 1 <= len(commands) <= maximum:
        _fail('budget', 'Source command budget exceeded')
    result, points = [], []
    for cmd in commands:
        if budget is not None:budget.spend()
        if type(cmd) is not dict or len(cmd) != 1:
            _fail('source_binding', 'Invalid source command')
        op, values = next(iter(cmd.items()))
        keys = {'moveTo': ('x','y'), 'lineTo': ('x','y'), 'cubicTo': ('x1','y1','x2','y2','x','y'), 'close': ()}.get(op)
        if keys is None or type(values) is not dict or set(values) != set(keys):
            _fail('source_binding', 'Unsupported source operation or control record')
        xy = []
        for i in range(0, len(keys), 2):
            if budget is not None:budget.spend()
            x,y = values[keys[i]], values[keys[i+1]]; _number(x); _number(y)
            xy.append((x,y)); points.append((x,y))
        result.append(({'moveTo':'M','lineTo':'L','cubicTo':'C','close':'Z'}[op], *xy))
    if not points:
        _fail('source_binding', 'Source path has no points')
    x0,y0=min(x for x,y in points),min(y for x,y in points)
    w,h=max(.01,max(x for x,y in points)-x0),max(.01,max(y for x,y in points)-y0)
    box = {'x': x0, 'y': y0, 'width': max(.01,(x0+w)-x0), 'height': max(.01,(y0+h)-y0)}
    return result, box


def _binding(props, obj, source, box, entry, manifest, mapping, placement, max_commands, budget=None):
    if budget is not None:
        budget.spend(1 + len(source) + sum(len(command) - 1 for command in source))
    if set(entry) - {'id','kind','box','group_id','editable'}:
        _fail('source_binding', 'Only the raw known path object-map profile is admitted')
    if entry.get('group_id') is not None and (type(entry['group_id']) is not str or len(entry['group_id']) > 512):
        _fail('source_binding', 'Mapped semantic group requires a bounded string identity')
    if ('editable' in entry and entry['editable'] is not True) or entry.get('group_id') != obj.get('group_id'):
        _fail('source_binding', 'Mapped editable/group identity differs from the source path')
    if type(placement) not in (list, tuple) or len(placement) != 4:
        _fail('source_binding', 'Explicit occupied placement requires four numbers')
    for v in placement: _number(v)
    mapped_placement = mapping.get('placement')
    if type(mapped_placement) is not list or len(mapped_placement) != 4 or any(_number(a) != _number(b) for a,b in zip(mapped_placement,placement)):
        _fail('source_binding', 'Object-map placement differs from explicit occupied placement')
    canvas=manifest.get('canvas', {})
    cw,ch = _number(canvas.get('width')), _number(canvas.get('height'))
    tx,ty,pw,ph=map(_number,placement)
    if min(cw,ch,pw,ph) <= 0 or pw/cw != ph/ch:
        _fail('source_binding', 'Placement must have exact positive uniform scale')
    mapped_box = entry.get('box')
    if type(entry.get('kind')) is not str or entry.get('kind') != 'path' or type(mapped_box) is not dict or set(mapped_box) != set(box) or any(_number(mapped_box[k]) != _number(box[k]) for k in box):
        _fail('source_binding', 'Object map does not match the recomputed source control hull')
    # A supplied precomputed context is never authority. If present it must agree.
    if 'source_to_slide' in entry or 'source_box' in entry or 'frame' in entry:
        _fail('source_binding', 'Pass raw object map entries, not caller-authorized mapped frames')
    scale=pw/cw
    xf=_one(props,A+'xfrm')
    if set(xf.attrib)-{'rot','flipH','flipV'} or xf.get('rot','0') != '0' or any(xf.get(k,'0') not in ('0','false') for k in ('flipH','flipV')):
        _fail('native_frame', 'Only unrotated positive native mapping is supported')
    _structure(xf,xf.attrib,(A+'off',A+'ext'))
    off,ext=_one(xf,A+'off'),_one(xf,A+'ext');_leaf(off,('x','y'));_leaf(ext,('cx','cy'))
    ox,oy=(_integer(off.get(k)) for k in ('x','y'));ex,ey=(_integer(ext.get(k),True) for k in ('cx','cy'))
    bx,by,bw,bh=(F(box[k]) for k in ('x','y','width','height'))
    ideal=[(tx+bx*scale)*K,(ty+by*scale)*K,bw*scale*K,bh*scale*K]
    errors=[abs(F(a)-b) for a,b in zip((ox,oy,ex,ey),ideal)]
    if max(errors)>2:
        _fail('native_frame', 'Actual frame differs from scene placement by more than 2 EMU')
    geom=_one(props,A+'custGeom');_structure(geom,(),(A+'pathLst',))
    paths=_one(geom,A+'pathLst');_structure(paths,(),(A+'path',))
    path=_one(paths,A+'path')
    if budget is None:
        decoded=decode_native_cubic_path(path,max_commands=max_commands)
    else:
        initial=budget.used
        decoded=_decode(path,budget,max_commands)
        decoded['decode_operations']=budget.used-initial
        decoded['path_dimensions_are_not_a_clip']=True
    dw,dh=[max(1,math.floor(box[k]*K+.5)) for k in ('width','height')]
    if decoded['dimensions'] != [dw,dh]:
        _fail('restore_binding', 'Initial native path dimensions differ from restoration serializer')
    expected=[];maximum=[F(0),F(0)]
    for cmd in source:
        row=[cmd[0]]
        for x,y in cmd[1:]:
            u,v=math.floor((x-box['x'])*K+.5),math.floor((y-box['y'])*K+.5)
            if abs(u)>MAX_INT or abs(v)>MAX_INT:
                _fail('budget', 'Restored source point exceeds native integer budget')
            row.append((u,v))
            actual=[F(ox)+F(u*ex,dw),F(oy)+F(v*ey,dh)]
            desired=[(tx+F(x)*scale)*K,(ty+F(y)*scale)*K]
            maximum=[max(a,abs(b-c)) for a,b,c in zip(maximum,actual,desired)]
        expected.append(tuple(row))
    if decoded['commands'] != expected:
        _fail('restore_binding', 'Initial actual native commands do not match source restoration')
    return paths,path,decoded,(ex,ey),{'actual_frame_emu':[ox,oy,ex,ey], 'path_dimensions':[dw,dh],
        'source_to_slide_exact':{'translate_x':str(tx),'translate_y':str(ty),'scale':str(scale)},
        'initial_frame_error_emu_exact':[str(x) for x in errors], 'initial_frame_guard_emu':2,
        'maximum_scene_to_initial_native_control_error_emu_exact':[str(x) for x in maximum],
        'original_pdf_to_scene_error':'NOT_RECOMPUTED_BY_THIS_WRAPPER',
        'restoration_serializer':'binary64_source_pixel_emu_floor_plus_half',
        'initial_full_command_and_integer_control_identity_verified':True}


def normalize_native_cubic_fill(slide_root, *, object_id, manifest, object_map,
                                placement, max_input_segments=128, max_commands=512,
                                max_atomic_edges=2048, max_operations=1_000_000,
                                max_probe_halvings=80, max_tree_nodes=200_000,
                                max_xml_text_bytes=16_000_000,
                                proof_mode='isolated', proof_split_depth=None):
    """Return a receipt, replacing only a proven mismatching compound path.

    ``placement`` is the build's explicit resolved occupied placement; it must
    agree with the raw object map. This is not a caller-restored/identity flag.
    The original PDF extraction is not re-proved here. All source-canvas marked
    paths are excluded. Failures leave the entry post-restoration XML intact.
    Explicit isolated/contact modes keep their existing contracts. The build's
    classified policy examines authenticated integer geometry before selecting
    exactly one contact or transverse proof; failed classification/normalization
    never retries another domain. Its shared budget counts instrumented bounded
    work units, not every interpreter instruction or Fraction primitive.
    """
    label = object_id if type(object_id) is str and len(object_id) <= 512 else None
    base={'id':label,'policy':POLICY,'geometry_preserved':True,'manifest_modified':False,'visual_review_required':True}
    try:
        classified = type(proof_mode) is str and proof_mode == 'classified_contact_or_transverse'
        if classified and proof_split_depth is not None:
            _fail('input','Classified policy does not accept a caller-selected proof split depth')
        mode,depth=_proof_mode('endpoint_contact' if classified else proof_mode,proof_split_depth)
        extended = classified or mode == 'transverse_line'
        if mode=='endpoint_contact':
            base.update(policy=CONTACT_POLICY,proof_mode=mode,proof_split_depth=depth)
        if classified:base.update(policy=CLASSIFIED_POLICY,requested_proof_policy=proof_mode)
        elif mode=='transverse_line':base.update(policy=TRANSVERSE_POLICY,proof_mode=mode)
        for limit in (max_input_segments,max_commands,max_atomic_edges,max_operations,max_probe_halvings,max_tree_nodes,max_xml_text_bytes):
            if type(limit) is not int or limit <= 0:
                _fail('budget','Wrapper budgets must be positive integers')
        budget=None
        if extended:
            from . import pdf_cubic_transverse_winding as transverse
            limits=_limits(max_input_segments,max_commands,max_atomic_edges,max_operations,max_probe_halvings)
            transverse._domain(limits,80,F(1,2))
            if max_tree_nodes>200_000 or max_xml_text_bytes>16_000_000:
                _fail('budget','Transverse context limits exceed the bounded native profile')
            budget=transverse.Arithmetic(max_operations)
        if type(object_id) is not str or not object_id or len(object_id)>512 or type(manifest) is not dict or type(object_map) is not dict:
            _fail('source_binding','Bounded source identity and full manifest/map required')
        objects,entries=manifest.get('objects'),object_map.get('objects')
        if type(objects) is not list or type(entries) is not list or max(len(objects),len(entries))>100_000:
            _fail('budget','Source identity index budget exceeded')
        initial_operations = len(objects)+len(entries)
        if initial_operations >= max_operations:
            _fail('budget','Source identity index exhausted operation budget')
        def index(rows):
            found={}
            for row in rows:
                if extended:budget.spend()
                if type(row) is not dict or type(row.get('id')) is not str or len(row['id'])>512 or row['id'] in found:
                    _fail('source_binding','Invalid or duplicate source/map identity')
                found[row['id']]=row
            return found
        obj=index(objects).get(object_id);entry=index(entries).get(object_id)
        if obj is None or entry is None:
            _fail('source_binding','Source or map identity missing')
        if type(obj.get('kind')) is not str:
            _fail('source_binding','Source kind must be a string')
        if obj.get('kind') != 'path':
            return {**base,'status':'not_applicable','reason_code':'not_path','visual_review_required':False}
        source,box=_source_commands(obj,max_commands,budget if extended else None)
        if not any(c[0]=='C' for c in source):
            return {**base,'status':'not_applicable','reason_code':'no_cubic_commands','visual_review_required':False}
        nodes=_bounded_tree(slide_root,max_tree_nodes,max_xml_text_bytes,budget if extended else None)
        used=budget.used if extended else initial_operations+nodes
        if used>=max_operations:
            _fail('budget','Native context exhausted operation budget')
        shape,identity,parent=_select(slide_root,object_id)
        declaration=manifest.get('source_canvas_clip')
        if declaration is not None:
            if type(declaration) is not dict or type(declaration.get('objects')) is not list:
                _fail('source_binding','Invalid source canvas declaration')
            selected=[]; selected_ids=set()
            if len(declaration['objects']) > len(objects):
                _fail('source_binding','Source canvas selection exceeds declared objects')
            for row in declaration['objects']:
                if extended:budget.spend()
                if (type(row) is not dict or set(row)-{'object_id','source_paint_id'} or
                        type(row.get('object_id')) is not str or not row['object_id'] or len(row['object_id'])>512 or
                        row['object_id'] in selected_ids or
                        ('source_paint_id' in row and (type(row['source_paint_id']) is not str or not row['source_paint_id'] or len(row['source_paint_id'])>512))):
                    _fail('source_binding','Invalid or duplicate source canvas selection identity')
                selected.append(row['object_id']);selected_ids.add(row['object_id'])
            used = budget.used if extended else used+len(selected)
        else:selected=[]
        if object_id in selected or 'source_canvas_clip_required=true' in [p.strip() for p in identity.get('descr','').split(';')]:
            return {**base,'status':'not_applicable','reason_code':'keep_original_canvas_clip_geometry'}
        # Select one proof strategy before geometric work. Contact mode shares
        # this counter through decoding, normalization and final reproof; there
        # is no free retry after an isolated failure.
        options=dict(max_input_segments=max_input_segments,max_commands=max_commands,max_atomic_edges=max_atomic_edges,max_probe_halvings=max_probe_halvings)
        limits=_limits(max_input_segments,max_commands,max_atomic_edges,max_operations,max_probe_halvings)
        if not extended:
            budget=_Arithmetic(max_operations) if mode=='endpoint_contact' else None
            if budget is not None:budget.spend(used)
        else:budget.spend()  # Selected native paint/context validation work unit.
        props,paint=_paint(shape,obj)
        paths,original,decoded,extent,frame=_binding(props,obj,source,box,entry,manifest,object_map,placement,max_commands,budget)
        selection=None
        if classified:
            selection=transverse._classify_with_budget(decoded['commands'],limits,budget)
            mode=selection['selected_proof_mode'];depth=1 if mode=='endpoint_contact' else None
            base.update(policy=CONTACT_POLICY if mode=='endpoint_contact' else TRANSVERSE_POLICY,proof_mode=mode)
            if depth is None:base.pop('proof_split_depth',None)
            else:base['proof_split_depth']=depth
        used=budget.used if budget is not None else used+decoded['decode_operations']
        if used>=max_operations:_fail('budget','Source/native binding exhausted operation budget')
        before=ET.tostring(shape); original_bytes=ET.tostring(original)
        if extended:budget.spend(1+(len(before)+len(original_bytes))//64)
        if budget is None:
            normalized=normalize_isolated_cubic_fill(decoded['commands'],max_operations=max_operations-used,**options)
            used+=normalized['proof']['exact_predicate_operations']
        elif mode=='endpoint_contact':
            normalized=_normalize_contact_with_budget(decoded['commands'],limits,budget,proof_split_depth=depth)
            used=budget.used
        else:
            bounds=[min(F(1,2),budget.div(F(d),2*F(e))) for d,e in zip(decoded['dimensions'],extent)]
            normalized=transverse._normalize_with_budget(decoded['commands'],limits,budget,max_coordinate_error=bounds)
            used=budget.used
        witness=[r for r in normalized['proof']['boundary_classification'] if any(type(r[k]) is int and r[k]!=0 and r[k]%2==0 for k in ('left_winding','right_winding'))]
        common={**base,'source_commands_sha256':_hash(_json(obj['commands']).encode()),'initial_geometry_sha256':_hash(original_bytes),
                'initial_shape_sha256':_hash(before),'paint_verification':paint,'source_native_binding':frame,
                'source_object_sha256':_hash(_json(obj).encode()),'raw_object_map_entry_sha256':_hash(_json(entry).encode()),
                'parent_context_sha256':_hash(ET.tostring(parent)),'normalization':normalized['proof'],
                'rgb_alpha_error_bound':None}
        if selection is not None:common['source_domain_selection']=selection
        if extended:
            common['work_budget_counting_unit']='instrumented bounded work units; not every Python/Fraction primitive'
            budget.spend(1+(len(_json(obj))+len(_json(entry)))//64)
        if not witness:
            record={**common,'status':'not_applicable','reason_code':'no_proven_fill_rule_mismatch',
                    'reason':'No exact nonzero even-winding face witness; original XML retained'}
            if extended:
                record=budget.serialize(record);budget.spend(1+len(_json(record))//64)
                record['exact_predicate_operations_including_context']=budget.used
            return record
        if not normalized['commands']:
            _fail('empty_candidate','Empty candidate is not admitted by the bounded wrapper')
        replacement=ET.Element(original.tag,dict(original.attrib))
        for command in normalized['commands']:
            if budget is not None:budget.spend()
            node=ET.SubElement(replacement,A+{'M':'moveTo','L':'lnTo','C':'cubicBezTo','Z':'close'}[command[0]])
            for point in command[1:]:
                nums=[]
                for value in point:
                    q=value+F(1,2) if budget is None else budget.add(value,F(1,2))
                    n=q.numerator//q.denominator
                    if abs(n)>MAX_INT:_fail('budget','Candidate point exceeds native integer budget')
                    nums.append(n)
                encoded=[str(n) if budget is None else budget.text(F(n)) for n in nums]
                ET.SubElement(node,A+'pt',{'x':encoded[0],'y':encoded[1]})
        replacement=ET.fromstring(ET.tostring(replacement))
        if budget is not None:used=budget.used
        if used>=max_operations:_fail('budget','Normalization exhausted shared operation budget')
        if budget is None:
            final=verify_native_cubic_path_encoding(original,replacement,slide_extents=extent,
                                                   max_operations=max_operations-used,**options)
            used+=final['exact_predicate_operations_including_decode']
        else:
            final=_verify_native_cubic_path_encoding_with_budget(original,replacement,limits,budget,
                slide_extents=extent,proof_mode=mode,proof_split_depth=depth)
            used=budget.used
        candidate_shape=deepcopy(shape)
        cprops=_one(candidate_shape,P+'spPr'); cpaths=_one(_one(cprops,A+'custGeom'),A+'pathLst')
        cpaths.remove(cpaths[0]);cpaths.append(replacement)
        # Independent context recheck, then compare everything except the path.
        _paint(candidate_shape,obj)
        reverted=deepcopy(candidate_shape);rp=_one(_one(_one(reverted,P+'spPr'),A+'custGeom'),A+'pathLst')
        rp.remove(rp[0]);rp.append(deepcopy(original))
        if ET.tostring(reverted)!=before or ET.tostring(shape)!=before:
            _fail('native_context','Candidate changed non-path shape context')
        record={**common,'status':'applied','geometry_preserved':False,'reason_code':'proven_nonzero_evenodd_mismatch',
                'mismatch_witnesses':witness,'final_geometry_sha256':_hash(ET.tostring(replacement)),
                'final_geometry_verification':final,'native_frame_path_dimensions_and_paint_unchanged':True,
                'compound_paint_count':1,'exact_predicate_operations_including_context':used,
                'existing_source_error_in_additional_half_emu_bound':False}
        if budget is not None:
            record=budget.serialize(record)
            record['exact_predicate_operations_including_context']=budget.used
        encoded_receipt=_json(record)  # Finish all receipt work before the single mutation.
        if extended:
            budget.spend(1+len(encoded_receipt)//64)
            record['exact_predicate_operations_including_context']=budget.used
        paths.remove(original);paths.append(replacement)
        return record
    except UnsupportedPdfCubicWindingError as error:
        return {**base,'status':'rejected','reason_code':error.code,'reason':str(error),
                'details':error.details}
    except (ValueError,OverflowError,TypeError,KeyError,AttributeError) as error:
        return {**base,'status':'rejected','reason_code':'invalid_binding','reason':str(error)[:512]}
