"""Create academic figures from an explicitly authored graph, without a reference.

The calling host supplies research facts and stage/lane assignments. This module
measures real fonts, lays out a grid and routes directed edges around modules.
It does not infer a scientific model from prose or certify aesthetic quality.
"""
import hashlib
import heapq
import json
import math
import re
import shutil
import tempfile
from pathlib import Path

from PIL import ImageFont

VERSION = 'academic-grid-v1'
NODE_KINDS = {'input', 'module', 'tensor', 'operator', 'output', 'loss'}
EDGE_KINDS = {'flow', 'skip', 'feedback', 'training'}
PALETTE = {
    'input': ('#F2F4F7', '#667085'), 'module': ('#EAF1FA', '#3F6695'),
    'tensor': ('#EAF5EF', '#427A60'), 'operator': ('#FFFFFF', '#475467'),
    'output': ('#F2EFF9', '#79649B'), 'loss': ('#FCF1E7', '#A37242'),
}


def checksum(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _record(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(label + ' must be a record with supported fields: ' + ', '.join(sorted(allowed)))


def _text(value, label, limit=500):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 and c != '\n' for c in value)
            or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError(label + ' needs nonempty printable text within the character limit')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', value):
        raise ValueError('Creation IDs must start with a letter and use at most 48 letters, digits, _ or -')


def validate_spec(spec):
    _record(spec, {'schema_version', 'id', 'brief', 'title', 'caption', 'direction',
                   'nodes', 'edges', 'groups'}, 'Creation spec')
    if type(spec.get('schema_version')) is not int or spec['schema_version'] != 1:
        raise ValueError('Creation schema_version must be integer 1')
    _id(spec.get('id')); _text(spec.get('brief'), 'brief', 20000)
    for key in ('title', 'caption'):
        if key in spec: _text(spec[key], key, 1000)
    if spec.get('direction', 'LR') not in ('LR', 'TB'):
        raise ValueError('direction must be LR or TB')
    nodes = spec.get('nodes')
    edges = spec.get('edges')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 48:
        raise ValueError('Creation needs 1..48 nodes')
    if not isinstance(edges, list) or len(edges) > 96:
        raise ValueError('Creation supports at most 96 explicit edges')
    ids, slots = set(), set()
    for n in nodes:
        _record(n, {'id', 'label', 'detail', 'kind', 'stage', 'lane', 'emphasis'}, 'Node')
        _id(n.get('id')); _text(n.get('label'), 'Node label', 160)
        if n['id'] in ids: raise ValueError('Duplicate node id: ' + n['id'])
        ids.add(n['id'])
        if not isinstance(n.get('kind', 'module'), str) or n.get('kind', 'module') not in NODE_KINDS:
            raise ValueError('Unknown node kind')
        if 'detail' in n: _text(n['detail'], 'Node detail', 220)
        if 'emphasis' in n and type(n['emphasis']) is not bool:
            raise ValueError('Node emphasis must be boolean')
        if type(n.get('stage')) is not int or not 0 <= n['stage'] <= 12:
            raise ValueError('Node stage must be an integer in 0..12')
        lane = n.get('lane', 0)
        if type(lane) not in (int, float) or not math.isfinite(lane) or not 0 <= lane <= 8 or lane * 2 != int(lane * 2):
            raise ValueError('Node lane must be a half-integer in 0..8')
        slot = n['stage'], lane
        if slot in slots: raise ValueError('Two nodes occupy the same stage/lane')
        slots.add(slot)
    edge_ids = set()
    for e in edges:
        _record(e, {'id', 'from', 'to', 'kind', 'label'}, 'Edge')
        _id(e.get('id'))
        if e['id'] in edge_ids: raise ValueError('Duplicate edge id: ' + e['id'])
        edge_ids.add(e['id'])
        if not isinstance(e.get('from'), str) or not isinstance(e.get('to'), str) or e['from'] not in ids or e['to'] not in ids:
            raise ValueError('Edge needs declared from/to nodes')
        if e['from'] == e['to']: raise ValueError('Self loops require an explicitly authored scene')
        if not isinstance(e.get('kind', 'flow'), str) or e.get('kind', 'flow') not in EDGE_KINDS:
            raise ValueError('Unknown edge kind')
        if 'label' in e:
            _text(e['label'], 'Edge label', 100)
            if '\n' in e['label']: raise ValueError('Edge label must be a single line')
    groups = spec.get('groups', [])
    if not isinstance(groups, list) or len(groups) > 12: raise ValueError('At most 12 flat groups are supported')
    group_ids, members = set(), set()
    for g in groups:
        _record(g, {'id', 'label', 'members'}, 'Group')
        _id(g.get('id')); _text(g.get('label'), 'Group label', 100)
        if g['id'] in group_ids: raise ValueError('Duplicate group id')
        group_ids.add(g['id'])
        selected = g.get('members')
        if not isinstance(selected, list) or not selected or any(not isinstance(v, str) or v not in ids for v in selected):
            raise ValueError('Group members must identify declared nodes')
        if len(set(selected)) != len(selected) or members.intersection(selected):
            raise ValueError('Groups must have unique, disjoint members; nesting requires a custom scene')
        members.update(selected)
    return spec


def _box(x, y, w, h):
    return dict(x=x, y=y, width=w, height=h)


def _rect(b, radius=10):
    x, y, w, h = (b[k] for k in ('x', 'y', 'width', 'height'))
    r = min(radius, w / 2, h / 2); k = .5522847498
    return [
        {'moveTo': {'x': x+r, 'y': y}}, {'lineTo': {'x': x+w-r, 'y': y}},
        {'cubicTo': dict(x1=x+w-r+k*r, y1=y, x2=x+w, y2=y+r-k*r, x=x+w, y=y+r)},
        {'lineTo': {'x': x+w, 'y': y+h-r}},
        {'cubicTo': dict(x1=x+w, y1=y+h-r+k*r, x2=x+w-r+k*r, y2=y+h, x=x+w-r, y=y+h)},
        {'lineTo': {'x': x+r, 'y': y+h}},
        {'cubicTo': dict(x1=x+r-k*r, y1=y+h, x2=x, y2=y+h-r+k*r, x=x, y=y+h-r)},
        {'lineTo': {'x': x, 'y': y+r}},
        {'cubicTo': dict(x1=x, y1=y+r-k*r, x2=x+r-k*r, y2=y, x=x+r, y=y)}, {'close': {}},
    ]


def _path(oid, commands, fill, stroke, width=1.5):
    return dict(id=oid, kind='path', commands=commands,
                style=dict(fill=fill, stroke=stroke, stroke_width=width,
                           stroke_linecap='round', stroke_linejoin='round'))


def _label(oid, text, box, size, *, bold=False, color='#253041', alignment='center'):
    return dict(id=oid, kind='text', text=text, box=box, font_size=size,
                bold=bold, alignment=alignment, vertical_alignment='middle',
                wrap='square', line_height=size * 1.3,
                insets=dict(left=0, right=0, top=0, bottom=0), style=dict(fill=color))


def _intersects(a, b):
    return (a['x'] < b['x']+b['width'] and b['x'] < a['x']+a['width']
            and a['y'] < b['y']+b['height'] and b['y'] < a['y']+a['height'])


def segment_hits(a, b, box):
    """Strict interior intersection for an axis-aligned segment and rectangle."""
    x, y, w, h = (box[k] for k in ('x', 'y', 'width', 'height'))
    if a[0] == b[0]: return x < a[0] < x+w and max(a[1], b[1]) > y and min(a[1], b[1]) < y+h
    if a[1] == b[1]: return y < a[1] < y+h and max(a[0], b[0]) > x and min(a[0], b[0]) < x+w
    raise ValueError('Route segment must be orthogonal')


def _port(box, site):
    x, y, w, h = (box[k] for k in ('x', 'y', 'width', 'height'))
    return {'left': (x, y+h/2), 'right': (x+w, y+h/2),
            'top': (x+w/2, y), 'bottom': (x+w/2, y+h)}[site]


def _route(a, b, obstacles, bounds):
    """Bounded orthogonal visibility-grid search with a bend penalty."""
    xs = {a[0], b[0], 28, bounds[0]-28}; ys = {a[1], b[1], 92, bounds[1]-48}
    for box in obstacles:
        xs.update((box['x']-16, box['x']+box['width']+16))
        ys.update((box['y']-16, box['y']+box['height']+16))
    xs = sorted(x for x in xs if 20 <= x <= bounds[0]-20)
    ys = sorted(y for y in ys if 80 <= y <= bounds[1]-32)
    allowed = {(x, y) for x in xs for y in ys if not any(
        o['x'] < x < o['x']+o['width'] and o['y'] < y < o['y']+o['height'] for o in obstacles)}
    start = (a, ''); distances = {start: 0}; previous = {}; queue = [(0, a, '')]
    xi, yi = {x:i for i,x in enumerate(xs)}, {y:i for i,y in enumerate(ys)}
    while queue:
        cost, point, axis = heapq.heappop(queue); state = point, axis
        if cost != distances[state]: continue
        if point == b:
            path = [point]
            while state in previous:
                state = previous[state]; path.append(state[0])
            path.reverse(); compact = []
            for p in path:
                if len(compact) >= 2 and ((compact[-2][0] == compact[-1][0] == p[0]) or (compact[-2][1] == compact[-1][1] == p[1])):
                    compact[-1] = p
                else: compact.append(p)
            return compact
        x, y = point
        neighbors = []
        for i in (xi[x]-1, xi[x]+1):
            if 0 <= i < len(xs): neighbors.append(((xs[i], y), 'x'))
        for i in (yi[y]-1, yi[y]+1):
            if 0 <= i < len(ys): neighbors.append(((x, ys[i]), 'y'))
        for target, new_axis in neighbors:
            if target not in allowed or any(segment_hits(point, target, o) for o in obstacles): continue
            candidate = cost + abs(x-target[0]) + abs(y-target[1]) + (28 if axis and axis != new_axis else 0)
            new_state = target, new_axis
            if candidate < distances.get(new_state, math.inf):
                distances[new_state] = candidate; previous[new_state] = state
                heapq.heappush(queue, (candidate, target, new_axis))
    raise ValueError('No clear route; change stages/lanes or author a custom scene')


def compose(spec, fonts):
    """Produce ordinary editable scene objects plus inspectable design records."""
    validate_spec(spec)
    from .export_svg import text_lines
    regular = ImageFont.truetype(fonts['regular']['path'], 18, index=fonts['regular'].get('face_index', 0))
    bold = ImageFont.truetype(fonts['bold']['path'], 22, index=fonts['bold'].get('face_index', 0))
    node_w = 196
    heights = {}
    for n in spec['nodes']:
        lines = text_lines(n['label'], bold, node_w-32)
        details = text_lines(n.get('detail', ''), regular, node_w-32) if n.get('detail') else []
        if len(lines) > 3 or len(details) > 3:
            raise ValueError('Node text is too dense; shorten labels or split a module: ' + n['id'])
        heights[n['id']] = max(96, 32 + len(lines)*29 + (12+len(details)*24 if details else 0))
    node_h = max(heights.values()); col_step = node_w+100; row_step = node_h+110
    direction = spec.get('direction', 'LR')
    boxes = {}
    for n in spec['nodes']:
        stage, lane = n['stage'], n.get('lane', 0)
        x, y = (stage*col_step, lane*row_step) if direction == 'LR' else (lane*col_step, stage*row_step)
        boxes[n['id']] = _box(64+x, 132+y, node_w, node_h)
    width = max(b['x']+b['width'] for b in boxes.values())+64
    height = max(b['y']+b['height'] for b in boxes.values())+104
    # Adjacent half-lanes are valid only if their physical boxes remain disjoint.
    for i, n in enumerate(spec['nodes']):
        for other in spec['nodes'][i+1:]:
            if _intersects(boxes[n['id']], boxes[other['id']]):
                raise ValueError('Nodes overlap; increase lane separation')
    objects, group_records = [], []
    for g in spec.get('groups', []):
        bs = [boxes[k] for k in g['members']]
        x, y = min(b['x'] for b in bs)-20, min(b['y'] for b in bs)-44
        b = _box(x, y, max(b['x']+b['width'] for b in bs)-x+20,
                 max(b['y']+b['height'] for b in bs)-y+20)
        if any(_intersects(b, v) for k,v in boxes.items() if k not in g['members']):
            raise ValueError('Group encloses unrelated nodes: ' + g['id'])
        if any(_intersects(b, v['box']) for v in group_records):
            raise ValueError('Group frames overlap; use separated groups or a custom scene')
        if b['y'] < 80: raise ValueError('Group header intrudes into title band')
        group_records.append(dict(id=g['id'], box=b, members=g['members']))
        header_font=ImageFont.truetype(fonts['bold']['path'],18,index=fonts['bold'].get('face_index',0))
        if len(text_lines(g['label'],header_font,b['width']-28))>1:
            raise ValueError('Group label is too long; shorten it: '+g['id'])
        objects.append(_path('group-'+g['id'], _rect(b, 14), '#FAFBFD', '#C4CBD5', 1))
        objects.append(_label('group-label-'+g['id'], g['label'], _box(x+14, y+7, b['width']-28, 28), 18,
                              bold=True, color='#526074', alignment='left'))
    routes = []; edge_objects = []; label_boxes = []
    for e in spec['edges']:
        source, target = boxes[e['from']], boxes[e['to']]
        kind = e.get('kind', 'flow')
        if kind in ('feedback', 'skip'):
            sites = ('bottom', 'bottom') if direction == 'LR' else ('right', 'right')
        elif kind == 'training' and source['y'] != target['y']:
            sites = ('bottom', 'top') if source['y'] < target['y'] else ('top', 'bottom')
        elif direction == 'LR' and source['x'] != target['x']:
            sites = ('right', 'left') if source['x'] < target['x'] else ('left', 'right')
        elif direction == 'TB' and source['y'] != target['y']:
            sites = ('bottom', 'top') if source['y'] < target['y'] else ('top', 'bottom')
        else:
            sites = ('bottom', 'top') if source['y'] < target['y'] else ('top', 'bottom')
            if source['y'] == target['y']: sites = ('right', 'left') if source['x'] < target['x'] else ('left', 'right')
        a, b = _port(source, sites[0]), _port(target, sites[1])
        vectors = dict(left=(-1,0), right=(1,0), top=(0,-1), bottom=(0,1))
        def lead(p, site):
            dx,dy=vectors[site]; return (p[0]+dx*20, p[1]+dy*20)
        middle = _route(lead(a, sites[0]), lead(b, sites[1]), list(boxes.values()), (width,height))
        points = [a]+middle+[b]
        color = '#A37242' if kind == 'training' else '#64748B' if kind == 'feedback' else '#34465D'
        if kind in ('training', 'feedback'):
            # Explicit dash segments keep preview semantics across both backends.
            commands = []
            for p,q in zip(points, points[1:]):
                length = abs(q[0]-p[0])+abs(q[1]-p[1]); dx=(q[0]-p[0])/length; dy=(q[1]-p[1])/length
                for start in range(0, math.ceil(length), 12):
                    end=min(start+6,length)
                    commands += [{'moveTo': dict(x=p[0]+dx*start,y=p[1]+dy*start)},
                                 {'lineTo': dict(x=p[0]+dx*end,y=p[1]+dy*end)}]
        else:
            commands=[{('moveTo' if i==0 else 'lineTo'):dict(x=p[0],y=p[1])} for i,p in enumerate(points)]
        oid='edge-'+e['id']; edge_objects.append(_path(oid,commands,'none',color,1.8))
        p=points[-2]; dx=b[0]-p[0];dy=b[1]-p[1];length=math.hypot(dx,dy);dx/=length;dy/=length
        head=[b,(b[0]-dx*9-dy*4,b[1]-dy*9+dx*4),(b[0]-dx*9+dy*4,b[1]-dy*9-dx*4)]
        edge_objects.append(_path(oid+'-head',[{('moveTo' if i==0 else 'lineTo'):dict(x=v[0],y=v[1])} for i,v in enumerate(head)]+[{'close':{}}],color,'none',0))
        route=dict(id=e['id'], **{'from':e['from'],'to':e['to']}, kind=kind,
                   from_site=sites[0], to_site=sites[1], points=points, object_ids=[oid,oid+'-head'])
        if 'label' in e:
            label_width=regular.getlength(e['label'])+14
            candidates=[]
            for p,q in zip(points,points[1:]):
                if p[1]==q[1] and abs(p[0]-q[0])>=label_width:
                    candidates.append(_box((p[0]+q[0]-label_width)/2,p[1]-31,label_width,26))
                elif p[0]==q[0] and abs(p[1]-q[1])>=30:
                    candidates.append(_box(p[0]+9,(p[1]+q[1]-26)/2,label_width,26))
            usable=[v for v in candidates if 0<=v['x'] and v['x']+v['width']<=width and not any(
                _intersects(v,o) for o in list(boxes.values())+label_boxes)]
            if not usable: raise ValueError('No clear edge label placement; shorten it or change layout: '+e['id'])
            lb=usable[0]; label_boxes.append(lb)
            edge_objects.append(_path(oid+'-label-bg',_rect(lb,2),'#FFFFFF','none',0))
            edge_objects.append(_label(oid+'-label',e['label'],lb,18,color=color))
            route['object_ids'] += [oid+'-label-bg',oid+'-label']
        routes.append(route)
    objects += edge_objects
    node_records=[]
    for n in spec['nodes']:
        b=boxes[n['id']]; kind=n.get('kind','module');fill,stroke=PALETTE[kind]
        if n.get('emphasis'): fill,stroke='#DBE8FA','#285B99'
        oid='node-'+n['id']; ids=[]
        if kind=='tensor':
            for index in (2,1):
                bg=_box(b['x']+index*4,b['y']-index*4,b['width'],b['height'])
                obj=_path(oid+'-layer-'+str(index),_rect(bg,4),fill,stroke,1);objects.append(obj);ids.append(obj['id'])
        obj=_path(oid,_rect(b,4 if kind=='tensor' else 10),fill,stroke,2.2 if n.get('emphasis') else 1.5)
        objects.append(obj);ids.append(oid)
        title_lines=len(text_lines(n['label'],bold,node_w-32)); detail_lines=len(text_lines(n['detail'],regular,node_w-32)) if n.get('detail') else 0
        total=title_lines*29+(12+detail_lines*24 if detail_lines else 0); top=b['y']+(node_h-total)/2
        obj=_label(oid+'-label',n['label'],_box(b['x']+16,top,node_w-32,title_lines*29),22,bold=True)
        objects.append(obj);ids.append(obj['id'])
        if detail_lines:
            obj=_label(oid+'-detail',n['detail'],_box(b['x']+16,top+title_lines*29+12,node_w-32,detail_lines*24),18,color='#526074')
            objects.append(obj);ids.append(obj['id'])
        node_records.append(dict(id=n['id'],box=b,kind=kind,object_ids=ids))
    if spec.get('title'):
        title_font=ImageFont.truetype(fonts['bold']['path'],26,index=fonts['bold'].get('face_index',0))
        if len(text_lines(spec['title'],title_font,width-128)) > 1:
            raise ValueError('Title is too long for this canvas; shorten it or omit the figure title')
        objects.append(_label('figure-title',spec['title'],_box(64,22,width-128,40),26,bold=True,alignment='left'))
    if spec.get('caption'):
        caption_font=ImageFont.truetype(fonts['regular']['path'],16,index=fonts['regular'].get('face_index',0))
        count=len(text_lines(spec['caption'],caption_font,width-128))
        height+=max(0,count*22-32)
        objects.append(_label('figure-caption',spec['caption'],_box(64,height-54-max(0,count*22-32),width-128,count*22),16,color='#526074',alignment='left'))
    for i,o in enumerate(objects): o['z_index']=i
    if len({o['id'] for o in objects}) != len(objects):
        raise ValueError('Creation IDs collide with generated label/head suffixes; rename the conflicting ID')
    canvas=dict(width=width,height=height,background='#FFFFFF')
    warnings=[]
    for i,r in enumerate(routes):
        for other in routes[i+1:]:
            for p,q in zip(r['points'],r['points'][1:]):
                for a,b in zip(other['points'],other['points'][1:]):
                    if p[1]==q[1] and a[0]==b[0] and min(p[0],q[0])<a[0]<max(p[0],q[0]) and min(a[1],b[1])<p[1]<max(a[1],b[1]):
                        warnings.append(dict(code='edge_crossing',edges=[r['id'],other['id']],point=[a[0],p[1]]))
                    if p[0]==q[0] and a[1]==b[1] and min(a[0],b[0])<p[0]<max(a[0],b[0]) and min(p[1],q[1])<a[1]<max(p[1],q[1]):
                        warnings.append(dict(code='edge_crossing',edges=[r['id'],other['id']],point=[p[0],a[1]]))
    report=dict(schema_version=1,layout_version=VERSION,nodes=node_records,edges=routes,groups=group_records,
                scene_sha256=checksum(dict(canvas=canvas,objects=objects)),warnings=warnings,
                checks=dict(node_overlap='PASS',edge_node_intersection='PASS',literal_scope='declared_spec_only'),
                scientific_correctness='caller_review_required',visual_review='pending',user_acceptance='pending',
                limitations=['Routes are editable paths; dragging a node does not reroute them.',
                             'Font measurement and geometric checks do not certify final PPT appearance.',
                             'Stage/lane choices and research facts are supplied by the calling host.'])
    return canvas,objects,report


def verify_creation_inputs(manifest, root):
    """Bind an authored scene to its immutable brief/spec and layout record."""
    from .validate import confined, digest
    declaration=manifest['authoring']
    if not isinstance(declaration,dict) or set(declaration)!={'layout_version','spec','audit'} or declaration['layout_version']!=VERSION:
        raise ValueError('Invalid creation declaration')
    records={}
    for role in ('spec','audit'):
        entry=declaration[role]
        if not isinstance(entry,dict) or set(entry)!={'path','sha256'}:
            raise ValueError('Creation requires bound '+role)
        path=confined(root,entry['path'])
        if digest(path)!=entry['sha256']: raise ValueError('Creation '+role+' changed; create a new job')
        records[role]=json.loads(path.read_text(encoding='utf-8'))
    validate_spec(records['spec'])
    audit=records['audit']
    if not isinstance(audit,dict) or audit.get('scene_sha256')!=checksum(dict(canvas=manifest['canvas'],objects=manifest['objects'])):
        raise ValueError('Authored scene changed; revise the creation spec and create a new job')
    if manifest['source']['kind']!='generated_diagram': raise ValueError('Creation source must be generated_diagram')
    return dict(status='BOUND_TO_DECLARED_SPEC',node_count=len(records['spec']['nodes']),edge_count=len(records['spec']['edges']),
                research_correctness='caller_review_required',user_acceptance='pending')


def create_job(spec_path, job, fonts):
    """Create a fresh source/spec/scene job; retain normal review/build gates."""
    from .export_svg import export
    from .font_prepare import prepare_fonts
    from .validate import digest, validate
    spec_path=Path(spec_path).resolve(); job=Path(job).resolve()
    if job.exists(): raise ValueError('A new creation job directory is required')
    raw=spec_path.read_bytes()
    if len(raw)>256*1024: raise ValueError('Creation spec exceeds 256 KiB')
    def unique_pairs(pairs):
        result={}
        for k,v in pairs:
            if k in result: raise ValueError('Duplicate JSON field: '+k)
            result[k]=v
        return result
    spec=json.loads(raw,object_pairs_hook=unique_pairs)
    canvas,objects,audit=compose(spec,fonts)
    job.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.'+job.name+'-',dir=job.parent) as temporary:
        root=Path(temporary); (root/'sources').mkdir()
        (root/'sources/creation-spec.json').write_bytes(raw)
        (root/'authoring-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        manifest=dict(schema_version=1,id=spec['id'],revision=1,canvas=canvas,objects=objects,
                      recognition=dict(provider='calling_host',status='needs_review',notes='Original figure authored from the saved brief and explicit graph; review scientific meaning and actual preview.',unresolved=[]))
        manifest['source']=dict(path='sources/authored.svg',sha256='0'*64,**{k:canvas[k] for k in ('width','height')},kind='generated_diagram',uri='')
        manifest['authoring']=dict(layout_version=VERSION,spec=dict(path='sources/creation-spec.json',sha256=hashlib.sha256(raw).hexdigest()),
                                   audit=dict(path='authoring-audit.json',sha256=digest(root/'authoring-audit.json')))
        file=root/'manifest.json';file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix='figure-authoring-fonts-') as font_directory:
            font_audit=prepare_fonts(fonts,Path(font_directory)/'faces',objects)
            export(file,root/'sources/authored.svg',font_audit[0]['renderer'],bold_font_path=next(x['renderer'] for x in font_audit if x['role']=='bold' and x['family']==fonts['family']),family=fonts['family'],font_audit=font_audit)
        manifest['source']['sha256']=digest(root/'sources/authored.svg')
        file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        validation=validate(manifest,root,require_review=False)
        if validation['errors']: raise ValueError('\n'.join(validation['errors']))
        # Exclusive reservation; no existing job or source is replaced.
        job.mkdir()
        try:
            for entry in root.iterdir(): shutil.move(str(entry),job/entry.name)
        except BaseException:
            shutil.rmtree(job); raise
    return dict(job=str(job),manifest=str(job/'manifest.json'),source=str(job/'sources/authored.svg'),
                audit=str(job/'authoring-audit.json'),node_count=len(spec['nodes']),edge_count=len(spec['edges']),
                warnings=audit['warnings'],next='Review the saved brief, graph and SVG; run review, build and inspect the actual PPT preview.')
