"""Write native endpoint references and safe module/label groups."""
import math
from xml.etree import ElementTree as ET

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NS = {'p': P, 'a': A}

# DrawingML preset equations, independently checked against Apache POI's
# presetShapeDefinitions.xml (Apache-2.0), straightConnector1 and
# bentConnector2/3/4.  All start at (0, 0) and end at (w, h); flips supply
# endpoint orientation.  bentConnector4 with adj1=0 expresses V-H-V.
_PRESET_SOURCE = ('https://raw.githubusercontent.com/apache/poi/trunk/poi/src/main/'
                  'resources/org/apache/poi/sl/draw/geom/presetShapeDefinitions.xml')


def _same(a, b):
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)


def _connector_preset(record):
    """Select a standard preset only if its initial route is exact."""
    points = record['points']
    first, last = points[0], points[-1]
    if len(points) == 2:
        return 'straightConnector1', {}
    # An elbow on an equal endpoint axis can retain its midpoint vertex after
    # adjacent duplicates collapse. Its painted monotone route is still one
    # straight segment; a padded native frame must not turn it into a slope.
    for axis, other in (('x', 'y'), ('y', 'x')):
        if all(_same(point[other], first[other]) for point in points) and not _same(first[axis], last[axis]):
            parameters = [(point[axis]-first[axis])/(last[axis]-first[axis]) for point in points]
            if all(a <= b for a,b in zip(parameters,parameters[1:])):
                return 'straightConnector1', {}
    if len(points) == 3:
        bend = points[1]
        if _same(bend['x'], last['x']) and _same(bend['y'], first['y']):
            return 'bentConnector2', {}
        if _same(bend['x'], first['x']) and _same(bend['y'], last['y']):
            return 'bentConnector3', {'adj1': 0}
    if len(points) == 4:
        p, q = points[1:3]
        if (_same(p['y'], first['y']) and _same(q['y'], last['y']) and
                _same(p['x'], q['x']) and _same(p['x'], (first['x']+last['x'])/2)):
            return 'bentConnector3', {'adj1': 50000}
        if (_same(p['x'], first['x']) and _same(q['x'], last['x']) and
                _same(p['y'], q['y']) and _same(p['y'], (first['y']+last['y'])/2)):
            return 'bentConnector4', {'adj1': 0, 'adj2': 50000}
    raise ValueError('Connector route has no exact supported native preset: ' + record['id'])


def _install_connector_preset(properties, record, mapped_frame):
    name, adjustments = _connector_preset(record)
    xf = properties.find('a:xfrm', NS)
    if xf is None:
        raise ValueError('Connector missing native transform: ' + record['id'])
    if int(xf.get('rot', '0')) % 21600000:
        raise ValueError('Connector source transform must have no rotation: ' + record['id'])
    off, extent = xf.find('a:off', NS), xf.find('a:ext', NS)
    box = mapped_frame['box']
    if box['width'] <= 0 or box['height'] <= 0 or off is None or extent is None:
        raise ValueError('Invalid connector mapped frame: ' + record['id'])
    # Use the precise placement map when available, rather than deriving scale
    # from an already rounded native editing frame.
    frame = mapped_frame.get('frame', (float(off.get('x')), float(off.get('y')),
                                      float(extent.get('cx')), float(extent.get('cy'))))
    first, last = record['points'][0], record['points'][-1]
    def mapped(point):
        return (frame[0] + (point['x']-box['x'])/box['width']*frame[2],
                frame[1] + (point['y']-box['y'])/box['height']*frame[3])
    start, end = mapped(first), mapped(last)
    off.set('x', str(round(min(start[0], end[0]))))
    off.set('y', str(round(min(start[1], end[1]))))
    # A one-EMU extent keeps horizontal/vertical connectors editable without
    # the source renderer's padded editing box introducing visible slope.
    extent.set('cx', str(max(1, round(abs(end[0]-start[0])))))
    extent.set('cy', str(max(1, round(abs(end[1]-start[1])))))
    for flag, axis in (('flipH', 0), ('flipV', 1)):
        xf.attrib.pop(flag, None)
        if end[axis] < start[axis]:
            xf.set(flag, '1')
    geom = properties.find('a:custGeom', NS)
    if geom is None:
        raise ValueError('Connector missing materialized custom geometry: ' + record['id'])
    at = list(properties).index(geom)
    properties.remove(geom)
    preset = ET.Element('{' + A + '}prstGeom', {'prst': name})
    guides = ET.SubElement(preset, '{' + A + '}avLst')
    for key, value in adjustments.items():
        ET.SubElement(guides, '{' + A + '}gd', {'name': key, 'fmla': 'val ' + str(value)})
    properties.insert(at, preset)
    return name, adjustments


def connect_objects(tree, elements, objects, mapped_frames):
    try:
        from .connections import object_bounds
    except ImportError:
        from connections import object_bounds
    by_id = {obj['id']: (obj, element) for obj, element in zip(objects, elements)}
    records = []
    for index, (obj, element) in enumerate(zip(objects, elements)):
        if obj.get('source_kind') != 'connector':
            continue
        record = obj['connection_record']
        native = element.find('p:nvSpPr/p:cNvPr', NS)
        if native is None:
            raise ValueError('Connector missing native identity: ' + obj['id'])
        connection = ET.Element('{' + P + '}cxnSp')
        nonvisual = ET.SubElement(connection, '{' + P + '}nvCxnSpPr')
        nonvisual.append(native)
        endpoints = ET.SubElement(nonvisual, '{' + P + '}cNvCxnSpPr')
        ET.SubElement(nonvisual, '{' + P + '}nvPr')
        resolved_ends = []
        for tag, end in [('stCxn', record['from']), ('endCxn', record['to'])]:
            target_obj, target = by_id[end['id']]
            identity = target.find('.//p:cNvPr', NS)
            if identity is None or not identity.get('id'):
                raise ValueError('Connector target missing native identity: ' + end['id'])
            target_frame = mapped_frames[end['id']]
            target_box = object_bounds(target_obj)
            frame_box = target_frame['box']
            geometry = target.find('p:spPr/a:custGeom', NS)
            if geometry is not None:
                sites = geometry.find('a:cxnLst', NS)
                if sites is None:
                    sites = ET.Element('{' + A + '}cxnLst')
                    before = geometry.find('a:rect', NS)
                    if before is None: before = geometry.find('a:pathLst', NS)
                    geometry.insert(list(geometry).index(before) if before is not None else len(geometry), sites)
                sites.clear()
                # Custom paths may use a conservative hull frame. Sites must
                # describe the actual visible box in that same local frame.
                path = geometry.find('a:pathLst/a:path', NS)
                if path is None or frame_box['width'] <= 0 or frame_box['height'] <= 0:
                    raise ValueError('Connector target missing positive custom path frame: ' + end['id'])
                w, h = float(path.get('w')), float(path.get('h'))
                x, y, bw, bh = target_box['x'], target_box['y'], target_box['width'], target_box['height']
                points = [(x+bw/2,y,16200000),(x,y+bh/2,10800000),
                          (x+bw/2,y+bh,5400000),(x+bw,y+bh/2,0)]
                for sx, sy, angle in points:
                    site = ET.SubElement(sites, '{' + A + '}cxn', {'ang': str(angle)})
                    ET.SubElement(site, '{' + A + '}pos', {
                        'x': str(round((sx-frame_box['x']) / frame_box['width'] * w)),
                        'y': str(round((sy-frame_box['y']) / frame_box['height'] * h))})
            site_index = {'top':0,'left':1,'bottom':2,'right':3}[end['site']]
            ET.SubElement(endpoints, '{' + A + '}' + tag, {'id': identity.get('id'), 'idx': str(site_index)})
            resolved_ends.append({'id': end['id'], 'native_id': identity.get('id'), 'idx': site_index})
        properties = element.find('p:spPr', NS)
        connection.append(properties)
        preset, adjustments = _install_connector_preset(properties, record, mapped_frames[obj['id']])
        line = properties.find('a:ln', NS)
        if line is None: line = ET.SubElement(properties, '{' + A + '}ln')
        for name, side in [('headEnd','start'),('tailEnd','end')]:
            old = line.find('a:' + name, NS)
            if old is not None: line.remove(old)
            ET.SubElement(line, '{' + A + '}' + name, {'type': record.get('arrow', {}).get(side, 'none'), 'w':'med', 'len':'med'})
        for child in element:
            if child.tag == '{' + P + '}style': connection.append(child)
        at = list(tree).index(element); tree.remove(element); tree.insert(at, connection)
        elements[index] = connection
        by_id[obj['id']] = (obj, connection)
        records.append({'id': obj['id'], 'from': resolved_ends[0], 'to': resolved_ends[1],
                        'route': record['route'], 'native': True, 'preset': preset,
                        'adjustments': adjustments, 'initial_route_verified': True,
                        'preset_definition_source': _PRESET_SOURCE})
    return records


def _point_segment_distance(point, start, end):
    dx, dy = end[0]-start[0], end[1]-start[1]
    denominator = dx*dx + dy*dy
    t = max(0., min(1., ((point[0]-start[0])*dx + (point[1]-start[1])*dy)/denominator)) if denominator else 0.
    return math.hypot(point[0]-start[0]-t*dx, point[1]-start[1]-t*dy)


def _segment_rect_distance(start, end, rect):
    """Euclidean distance, including segments crossing the rectangle interior."""
    low, high = 0., 1.
    for axis in (0, 1):
        delta = end[axis]-start[axis]
        if not delta:
            if not rect[axis] <= start[axis] <= rect[axis+2]:
                break
        else:
            a, b = (rect[axis]-start[axis])/delta, (rect[axis+2]-start[axis])/delta
            low, high = max(low, min(a, b)), min(high, max(a, b))
            if low > high:
                break
    else:
        return 0.
    def point_rect(point):
        return math.hypot(max(rect[0]-point[0], 0, point[0]-rect[2]),
                          max(rect[1]-point[1], 0, point[1]-rect[3]))
    vertices = [(x, y) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    return min(point_rect(start), point_rect(end),
               *(_point_segment_distance(point, start, end) for point in vertices))


def _stroke_segments(element):
    """Conservative painted envelope for supported stroke-only native paths.

    Returns (segments, radius), or None when proof requires unsupported paint.
    Cubic subdivision bounds the curve by an EMU capsule around every chord;
    the returned radius includes that error and miter/cap bounds.  This is a
    read-only intersection proof; it does not flatten the authored PPT shape.
    """
    properties = element.find('p:spPr', NS)
    if properties is None:
        return None
    geometry = properties.find('a:custGeom', NS)
    if geometry is None:
        return None
    paths = geometry.findall('a:pathLst/a:path', NS)
    if not paths or (properties.find('a:noFill', NS) is None and
                     not all(path.get('fill') == 'none' for path in paths)):
        return None
    # Effects and native arrowheads need their own painted envelopes. Keep the
    # conservative editing-frame check for them, rather than guessing.
    if properties.find('a:effectLst', NS) is not None or properties.find('a:effectDag', NS) is not None:
        return None
    line = properties.find('a:ln', NS)
    if line is None:
        return None
    if line.find('a:noFill', NS) is not None:
        return [], 0.
    if any(node.get('type', 'none') != 'none' for tag in ('headEnd', 'tailEnd')
           for node in line.findall('a:' + tag, NS)):
        return None
    try:
        stroke = float(line.get('w', '9525'))
        if not math.isfinite(stroke) or stroke <= 0:
            return None
        radius = stroke/2
        if line.find('a:round', NS) is None and line.find('a:bevel', NS) is None:
            miter = line.find('a:miter', NS)
            limit = max(1., float(miter.get('lim', '400000'))/100000) if miter is not None else 4.
            radius *= limit
        if line.get('cap') == 'sq':
            radius = max(radius, stroke/2*math.sqrt(2))
        xf = properties.find('a:xfrm', NS)
        off, extent = xf.find('a:off', NS), xf.find('a:ext', NS)
        ox, oy = float(off.get('x')), float(off.get('y'))
        width, height = float(extent.get('cx')), float(extent.get('cy'))
        rotation = math.radians(int(xf.get('rot', '0'))/60000)
        if not all(math.isfinite(number) for number in (ox, oy, width, height, radius)) or min(width, height) <= 0:
            return None
        co, si = math.cos(rotation), math.sin(rotation)
        epsilon = max(1., min(64., stroke/64))
        segments = []
        def cubic(a, b, c, d, depth=0):
            if max(_point_segment_distance(b, a, d), _point_segment_distance(c, a, d)) <= epsilon:
                segments.append((a, d)); return
            if depth >= 24 or len(segments) > 20000:
                raise ValueError('Curve proof exceeds subdivision budget')
            ab, bc, cd = [tuple((u[i]+v[i])/2 for i in (0, 1)) for u, v in ((a,b),(b,c),(c,d))]
            abc, bcd = [tuple((u[i]+v[i])/2 for i in (0, 1)) for u, v in ((ab,bc),(bc,cd))]
            mid = tuple((abc[i]+bcd[i])/2 for i in (0, 1))
            cubic(a, ab, abc, mid, depth+1); cubic(mid, bcd, cd, d, depth+1)
        for path in paths:
            pw, ph = float(path.get('w')), float(path.get('h'))
            if min(pw, ph) <= 0:
                return None
            def point(node):
                x, y = float(node.get('x'))/pw*width, float(node.get('y'))/ph*height
                if xf.get('flipH') in ('1', 'true'): x = width-x
                if xf.get('flipV') in ('1', 'true'): y = height-y
                dx, dy = x-width/2, y-height/2
                value = (ox+width/2+co*dx-si*dy, oy+height/2+si*dx+co*dy)
                if not all(math.isfinite(number) for number in value):
                    raise ValueError('Nonfinite native path point')
                return value
            previous, beginning = None, None
            for command in path:
                operation = command.tag.rsplit('}', 1)[-1]
                points = [point(node) for node in command.findall('a:pt', NS)]
                if operation == 'moveTo' and len(points) == 1:
                    previous = beginning = points[0]
                elif operation == 'lnTo' and previous is not None and len(points) == 1:
                    segments.append((previous, points[0])); previous = points[0]
                elif operation == 'cubicBezTo' and previous is not None and len(points) == 3:
                    cubic(previous, *points); previous = points[-1]
                elif operation == 'quadBezTo' and previous is not None and len(points) == 2:
                    control, end = points
                    b = tuple(previous[i]+2/3*(control[i]-previous[i]) for i in (0,1))
                    c = tuple(end[i]+2/3*(control[i]-end[i]) for i in (0,1))
                    cubic(previous, b, c, end); previous = end
                elif operation == 'close' and previous is not None and beginning is not None:
                    segments.append((previous, beginning)); previous = beginning
                else:
                    return None
        return segments, radius+epsilon
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _paint_overlap(first, second, first_bounds, second_bounds):
    if (min(first_bounds[2], second_bounds[2]) < max(first_bounds[0], second_bounds[0]) or
            min(first_bounds[3], second_bounds[3]) < max(first_bounds[1], second_bounds[1])):
        return False
    for shape, other_bounds in ((first, second_bounds), (second, first_bounds)):
        stroke = _stroke_segments(shape)
        if stroke is not None:
            segments, radius = stroke
            if not any(_segment_rect_distance(start, end, other_bounds) <= radius for start, end in segments):
                return False
    # Filled paths, text, pictures, unsupported paint or an actual stroke
    # intersection retain the conservative rejection. False negatives cannot
    # be justified by transparent image pixels or presumed text glyph gaps.
    return True


def attachment_groups(tree, elements, objects, max_id, visual_bounds):
    """Group labels with their module only when paint order is provably safe."""
    by_id = {obj['id']: i for i, obj in enumerate(objects)}
    parent = {}
    for obj in objects:
        relation = obj.get('source_attachment')
        if relation: parent[obj['id']] = relation['id']
    roots = {}
    for child in parent:
        root = child
        while root in parent: root = parent[root]
        roots.setdefault(root, set()).update((root, child))
    grouped, taken = [], set()
    for root, members in roots.items():
        slots = sorted(by_id[identity] for identity in members)
        if any(i in taken for i in slots): raise ValueError('Overlapping attachment groups')
        start = slots[0]
        for slot in slots[1:]:
            moving = visual_bounds(elements[slot], objects[slot]['id'])
            for crossed in range(start + 1, slot):
                if crossed not in slots and _paint_overlap(elements[slot], elements[crossed], moving,
                                                           visual_bounds(elements[crossed], objects[crossed]['id'])):
                    raise ValueError('Attachment grouping would change overlapping paint order: ' +
                                     objects[slot]['id'] + ' crosses ' + objects[crossed]['id'])
        children = [elements[i] for i in slots]
        bounds = [visual_bounds(child, objects[i]['id']) for child,i in zip(children,slots)]
        left,top = min(b[0] for b in bounds),min(b[1] for b in bounds)
        right,bottom = max(b[2] for b in bounds),max(b[3] for b in bounds)
        group = ET.Element('{' + P + '}grpSp')
        nonvisual = ET.SubElement(group,'{' + P + '}nvGrpSpPr'); max_id += 1
        name = 'attached.' + root
        ET.SubElement(nonvisual,'{' + P + '}cNvPr',{'id':str(max_id),'name':name})
        ET.SubElement(nonvisual,'{' + P + '}cNvGrpSpPr'); ET.SubElement(nonvisual,'{' + P + '}nvPr')
        props=ET.SubElement(group,'{' + P + '}grpSpPr'); xf=ET.SubElement(props,'{' + A + '}xfrm')
        for tag,attrs in [('off',{'x':left,'y':top}),('ext',{'cx':max(1,right-left),'cy':max(1,bottom-top)}),
                          ('chOff',{'x':left,'y':top}),('chExt',{'cx':max(1,right-left),'cy':max(1,bottom-top)})]:
            ET.SubElement(xf,'{' + A + '}' + tag,{k:str(v) for k,v in attrs.items()})
        at=list(tree).index(children[0])
        for child in children: tree.remove(child); group.append(child)
        tree.insert(at,group); taken.update(slots)
        grouped.append({'id':name,'members':[objects[i]['id'] for i in slots],
                        'visual_bounds_emu':[left,top,right,bottom],'identity_transform':True,
                        'attachment_root':root,'paint_order_verified':True,
                        'paint_order_policy':'conservative_native_bounds_or_stroke_capsule_separation'})
    return grouped,taken,max_id
