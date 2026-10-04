"""Bounded source/context invisibility certificates.
No general effect reduction, geometry replacement, or nearest-identity matching.
"""
from fractions import Fraction as F
import hashlib, json, math, re, struct, xml.etree.ElementTree as ET
ID = tuple(map(F, (1, 0, 0, 1, 0, 0)))
NUM = '[-+]?(?:\\d*\\.\\d+|\\d+\\.?\\d*)(?:[eE][-+]?\\d+)?'

def frac(x):
    if type(x) not in (int, float, str, F):
        raise ValueError('unsupported coordinate type')
    if type(x) is str:
        if len(x) > 4096:
            raise ValueError('rational text budget')
        if 'e' in x.lower():
            exponent = x.lower().rsplit('e', 1)[-1]
            if len(exponent) > 5 or abs(int(exponent)) > 4096:
                raise ValueError('exponent budget')
    r = F(x)
    if max(abs(r.numerator).bit_length(), r.denominator.bit_length()) > 4096:
        raise ValueError('rational budget')
    return r

def exact_matrix(value):
    if value is None or value == '':
        return ID
    if type(value) is not str or len(value) > 4096:
        raise ValueError('exact transform text budget/type')
    match = re.fullmatch('\\s*matrix\\s*\\(([^)]*)\\)\\s*', value)
    if not match:
        raise ValueError('exact source transform requires explicit matrix')
    values = re.findall(NUM, match[1])
    if len(values) != 6 or re.sub(NUM, '', match[1]).strip(' ,\t\r\n'):
        raise ValueError('invalid exact matrix')
    return tuple(map(frac, values))

def mul(a, b):
    return tuple(map(frac, (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])))

def exact_chain(parent, value, translation=None):
    """Optional source evidence: failure never changes the existing float geometry."""
    if parent is None:
        return None
    try:
        result = mul(parent, exact_matrix(value))
        if translation is not None:
            result = mul(result, (F(1), F(0), F(0), F(1), frac(translation[0]), frac(translation[1])))
        return result
    except (ValueError, TypeError, OverflowError):
        return None

def map_point(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])

def bounds(points):
    return (min((p[0] for p in points)), min((p[1] for p in points)), max((p[0] for p in points)), max((p[1] for p in points)))

def _f32_next(x, up):
    if x == 0:
        return struct.unpack('>f', struct.pack('>I', 1 if up else 2147483649))[0]
    bits = struct.unpack('>I', struct.pack('>f', x))[0]
    bits += 1 if (x > 0) == up else -1
    return struct.unpack('>f', struct.pack('>I', bits))[0]

def _enclose(x):
    y = struct.unpack('>f', struct.pack('>f', float(x)))[0]
    if not math.isfinite(y):
        raise ValueError('float32 enclosure overflow')
    f = F(y)
    return (F(_f32_next(y, False)) if f > x else f, F(_f32_next(y, True)) if f < x else f)

def _op(a, b, op):
    vals = [op(x, y) for x in a for y in b]
    lo, hi = (min(vals), max(vals))
    return (min(lo, _enclose(lo)[0]), max(hi, _enclose(hi)[1]))

def native_hull_certificate(local, matrix):
    """Caller supplies fz_bound_path(path, NULL, identity) control-hull bounds from the version-checked native API, not a rounded page-space bbox.
 Input coordinates/matrix are native float32 values. Exact rational interval
 ops enclose each float32 multiply/add, also exact or fused intermediate ops.
 """
    if type(local) not in (tuple, list) or type(matrix) not in (tuple, list) or len(local) != 4 or (len(matrix) != 6):
        raise ValueError('arity')
    v = list(local) + list(matrix)
    if any((type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 2 ** 24 for x in v)):
        raise ValueError('native numeric domain')
    if any((float(struct.unpack('>f', struct.pack('>f', x))[0]) != x for x in v)):
        raise ValueError('native inputs are not exact float32')
    r = tuple(map(F, local))
    m = tuple(map(F, matrix))
    if r[2] <= r[0] or r[3] <= r[1] or m[0] * m[3] - m[1] * m[2] == 0:
        raise ValueError('empty/degenerate native rectangle')
    out = []
    for axis in (0, 1):
        ax = _op((m[axis], m[axis]), (r[0], r[2]), lambda a, b: a * b)
        by = _op((m[axis + 2], m[axis + 2]), (r[1], r[3]), lambda a, b: a * b)
        out.append(_op(_op(ax, by, lambda a, b: a + b), (m[axis + 4], m[axis + 4]), lambda a, b: a + b))
    b = (out[0][0], out[1][0], out[0][1], out[1][1])
    return {'method': 'native_control_hull_identity_bounds_then_exact_rational_float32_interval', 'local_control_bounds': list(local), 'matrix': list(matrix), 'bounds_exact': list(map(str, b)), 'native_path_rectangle_predicate_used': False, 'native_bounds_api': 'fz_bound_path(path,NULL,identity)', 'provider_version': '1.28.2', 'native_transform_rounding_enclosed': True, 'control_hull_coverage': 'all local path support inside cached control hull; no path shape simplification'}

def _native_visibility(context, record, region):
    if type(context) is not dict or type(record) is not dict:
        return None
    if context.get('identity_complete') is not True or len(context['paints']) != context['paint_count'] or len(context['paints']) > 100000:
        return None
    if type(region) not in (tuple, list) or len(region) != 4:
        return None
    r = tuple(map(frac, region))
    if r[2] <= r[0] or r[3] <= r[1]:
        return None
    raw = {k: v for k, v in context.items() if k != 'context_sha256'}
    if hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest() != context.get('context_sha256'):
        return None
    seq = record['source_seqno']
    if type(seq) is not int or not 0 <= seq < len(context['paints']):
        return None
    if context['paints'][seq] != record or record['mask_definition_ids'] or record['active_mask_ids'] or record['pattern_depth']:
        return None
    if len(record['group_ids']) > 1:
        return None
    page = context.get('page_rect_pdf_pt')
    for gid in record['group_ids']:
        if type(gid) is not int or not 1 <= gid <= len(context['groups']):
            return None
        g = context['groups'][gid - 1]
        if not (page and g['bbox_pdf_pt'] == page and (g['colorspace'] is None) and (g['isolated'] is True) and (g['knockout'] is False) and (g['blendmode'] == 0) and (g['alpha'] == 1)):
            return None
        if g['group_id'] != gid or not g['begin_paint_seqno'] <= seq < g['end_paint_seqno']:
            return None
    proofs = []
    for cid in record['clip_ids']:
        if type(cid) is not int or not 1 <= cid <= len(context['clips']):
            return None
        c = context['clips'][cid - 1]
        if c['clip_id'] != cid or not c['begin_paint_seqno'] <= seq < c['end_paint_seqno']:
            return None
        cert = c.get('conservative_extent')
        if not cert:
            continue
        try:
            expected = native_hull_certificate(cert['local_control_bounds'], c['matrix'])
            if c['kind'] != 'clip_path' or cert != expected:
                return None
            b = tuple(map(F, cert['bounds_exact']))
        except (ValueError, KeyError, TypeError):
            return None
        if b[2] < r[0] or b[0] > r[2] or b[3] < r[1] or (b[1] > r[3]):
            proofs.append({'native_clip_id': cid, 'clip_span': [c['begin_paint_seqno'], c['end_paint_seqno']], 'certificate': cert})
    if not proofs:
        return None
    return {'method': 'supported_neutral_context_active_conservative_clip_disjoint_from_roi', 'source_pdf_sha256': context['source_pdf_sha256'], 'page': context['page'], 'bboxlog_sha256': context['bboxlog_sha256'], 'native_seqno': seq, 'roi_exact': list(map(str, r)), 'proofs': proofs, 'original_native_record_retained': True, 'native_page_object_allowed_not_changed': True, 'arbitrary_group_mask_support_claimed': False}

def prove_native_clip_disjoint(context, record, region):
    """Prove only ROI exclusion from a freshly bound native inspection report.

 Mask/pattern ownership stays unchanged. Unsupported or malformed evidence
 yields no certificate. This does not authorize clipping or rendering effects.
 """
    try:
        return _native_visibility(context, record, region)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        return None

def simple_points(data):
    """Bounded exact absolute M/L/H/V/Z parser; other syntax is not certified."""
    tokens = re.findall('[MLHVZ]|' + NUM, data or '')
    if re.sub('[MLHVZ]|' + NUM, '', data or '').strip(' ,\t\n\r') or len(tokens) > 64:
        raise ValueError('not a bounded linear path')
    pts = []
    i = 0
    cmd = None
    closed = False
    while i < len(tokens):
        if tokens[i] in ('M', 'L', 'H', 'V', 'Z'):
            cmd = tokens[i]
            i += 1
        if cmd == 'Z':
            closed = True
            cmd = None
            continue
        n = 2 if cmd in ('M', 'L') else 1 if cmd in ('H', 'V') else 0
        if not n or i + n > len(tokens):
            raise ValueError('malformed linear path')
        vals = list(map(frac, tokens[i:i + n]))
        i += n
        if cmd == 'M' and pts:
            raise ValueError('multiple subpaths')
        if cmd in ('M', 'L'):
            pts.append(tuple(vals))
            cmd = 'L'
        elif not pts:
            raise ValueError('missing move')
        elif cmd == 'H':
            pts.append((vals[0], pts[-1][1]))
        else:
            pts.append((pts[-1][0], vals[0]))
    return (pts, closed)

def svg_clip_bound(context):
    if context.get('unsupported_resource_ancestors'):
        raise ValueError('unsupported clip ancestors')
    parent = context.get('exact_transform')
    if parent is None:
        raise ValueError('exact ancestral transform not recorded')
    node = ET.fromstring(context['element'])
    tag = lambda n: n.tag.rsplit('}', 1)[-1]
    if '}' in node.tag and (not node.tag.startswith('{http://www.w3.org/2000/svg}')):
        raise ValueError('foreign clip namespace')
    if tag(node) != 'clipPath' or node.get('clipPathUnits', 'userSpaceOnUse') != 'userSpaceOnUse' or len(node) != 1:
        raise ValueError('outside simple clip domain')
    allowed = {'id', 'clipPathUnits', 'transform'}
    if set(node.attrib) - allowed:
        raise ValueError('clip effects not certified')
    p = node[0]
    if list(p) or ('}' in p.tag and (not p.tag.startswith('{http://www.w3.org/2000/svg}'))):
        raise ValueError('clip child content/namespace')
    if tag(p) != 'path' or set(p.attrib) - {'d', 'transform', 'id', 'clip-rule', 'fill-rule'}:
        raise ValueError('clip shape/effects not certified')
    if p.get('clip-rule', 'nonzero') not in ('nonzero', 'evenodd') or p.get('fill-rule', 'nonzero') not in ('nonzero', 'evenodd'):
        raise ValueError('invalid fill rule')
    pts, closed = simple_points(p.get('d'))
    if not closed:
        raise ValueError('open clip not certified')
    if pts and pts[-1] == pts[0]:
        pts.pop()
    if len(pts) != 4 or len(set(pts)) != 4:
        raise ValueError('not a rectangle')
    for a, b in zip(pts, pts[1:] + pts[:1]):
        if (a[0] == b[0]) == (a[1] == b[1]):
            raise ValueError('not an axis rectangle')
    m = mul(mul(tuple(map(F, parent)), exact_matrix(node.get('transform'))), exact_matrix(p.get('transform')))
    b = bounds([map_point(m, p) for p in pts])
    return (b, {'id': context['id'], 'source_xml_sha256': hashlib.sha256(context['element'].encode()).hexdigest(), 'exact_ancestor_transform': parent, 'conservative_bounds_exact': list(map(str, b)), 'method': 'exact_lexical_svg_rectangle_affine_corner_hull'})

def _source_invisible_certificate(paint, region):
    """Called only after original source unsupported/group/effect checks."""
    s = paint.style
    if s.get('fill-rule', 'nonzero') not in ('nonzero', 'evenodd'):
        return None
    if paint.unsupported:
        return None
    for item in (*paint.groups, s):
        if any((item.get(k, default) != default for k, default in [('mask', 'none'), ('filter', 'none'), ('mix-blend-mode', 'normal'), ('isolation', 'auto'), ('display', 'inline'), ('visibility', 'visible'), ('vector-effect', 'none'), ('paint-order', 'normal')])):
            return None
        if frac(item.get('opacity', '1')) != 1 and item is not s:
            return None
    try:
        if s['stroke'] != 'none':
            if frac(s['stroke-width']) < 0 or frac(s['stroke-miterlimit']) < 1:
                return None
            if s['stroke-linecap'] not in ('butt', 'round', 'square') or s['stroke-linejoin'] not in ('miter', 'round', 'bevel'):
                return None
            frac(s.get('stroke-dashoffset', '0'))
            if s['stroke-dasharray'] != 'none':
                text = s['stroke-dasharray'].strip()
                if not re.fullmatch(NUM + '(?:(?:\\s*,\\s*|\\s+)' + NUM + ')*', text):
                    return None
                if any((frac(x) <= 0 for x in re.split('[\\s,]+', text))):
                    return None
    except (ValueError, KeyError, TypeError):
        return None
    try:
        clips = [svg_clip_bound(c) for c in paint.clips]
    except (ValueError, KeyError, TypeError):
        return None
    rects = [r[0] for r in clips]
    if region is not None:
        rects.append(tuple(map(frac, region)))
    if not rects:
        return None
    effective = (max((r[0] for r in rects)), max((r[1] for r in rects)), min((r[2] for r in rects)), min((r[3] for r in rects)))
    base = {'source_paint_id': paint.source_id, 'clip_certificates': [r[1] for r in clips], 'effective_conservative_bounds_exact': list(map(str, effective)), 'original_geometry_and_styles_unchanged': True}
    if effective[2] < effective[0] or effective[3] < effective[1]:
        return {**base, 'method': 'strictly_empty_conservative_active_clip_intersection'}
    s = paint.style
    if paint.kind != 'path' or paint.reference_chain or s['fill'] != 'none' or (s['stroke'] == 'none') or (s['stroke-linecap'] != 'butt') or (s['stroke-dasharray'] == 'none'):
        return None
    try:
        node = ET.fromstring(paint.source_xml)
        pts, closed = simple_points(node.get('d'))
        m = tuple(map(F, paint.exact_transform))
        w = frac(s['stroke-width'])
        pattern = [frac(v) for v in re.split('[\\s,]+', s['stroke-dasharray'].strip())]
        if len(pts) != 2 or pts[0] == pts[1] or closed or (len(m) != 6) or (w <= 0) or (not pattern) or any((v <= 0 for v in pattern)):
            return None
        if m[1] != 0 or m[2] != 0 or m[0] != m[3] or (m[0] == 0):
            return None
        b = bounds([map_point(m, p) for p in pts])
        radius = abs(m[0]) * w / 2
        b = (b[0] - radius, b[1] - radius, b[2] + radius, b[3] + radius)
        if not (b[2] < effective[0] or b[0] > effective[2] or b[3] < effective[1] or (b[1] > effective[3])):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return {**base, 'method': 'single_nonzero_straight_butt_dash_no_join_full_segment_support_disjoint', 'stroke_support_bounds_exact': list(map(str, b)), 'exact_matrix': list(paint.exact_transform), 'half_width_exact': str(radius), 'legacy_miter_envelope_modified': False, 'dash_rendering_modified': False}

def prove_source_paint_invisible(paint, region):
    """Bounded SVG source fact; preserve geometry and every skipped source ID."""
    try:
        return _source_invisible_certificate(paint, region)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        return None
