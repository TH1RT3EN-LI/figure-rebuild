"""Narrow, occurrence-bound lowering of constant axial PDF shading to a path.

This is not gradient approximation or image extraction. Unsupported functions,
composition, color quantization and clip intersections fail closed.
"""
from __future__ import annotations

import hashlib
import math
import zlib
from fractions import Fraction
from pathlib import Path


class UnsupportedPdfShadingError(ValueError):
    """The selected shading cannot be represented by one exact solid path."""


_MAX_PDF_BYTES = 256 * 1024 * 1024
_MAX_SAMPLE_BYTES = 4 * 1024 * 1024
_MAX_COMMANDS = 100_000
_MAX_PAINTS = 100_000
_MAX_CONTEXT_EVENTS = 200_000
_MAX_DEPTH = 128


def _finite(values):
    try:
        return all(isinstance(v, (float, int, Fraction)) and not isinstance(v, bool)
                   and (isinstance(v, Fraction) or math.isfinite(v)) for v in values)
    except (OverflowError, ValueError):
        return False


def _exact(value):
    if not _finite((value,)):
        raise UnsupportedPdfShadingError('Non-finite proof coordinate')
    return Fraction(value)


def _point_exact(matrix, point):
    # Convert ORIGINAL native values before any multiply or translation.
    # Converting rounded page-space floats to Fraction would lose tiny signs.
    a, b, c, d, e, f = map(_exact, matrix)
    x, y = map(_exact, point)
    return a*x+c*y+e, b*x+d*y+f


def _matrix(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            a, c, e = value[0]
            b, d, f = value[1]
            value = (a, b, c, d, e, f)
        except (TypeError, ValueError) as error:
            raise UnsupportedPdfShadingError('Expected a 2x3 source transform') from error
    if (not isinstance(value, (list, tuple)) or len(value) != 6 or not _finite(value)
            or any(not isinstance(v, (float, int)) for v in value)):
        raise UnsupportedPdfShadingError('Expected a finite affine source transform')
    a, b, c, d, _, _ = map(_exact, value)
    if a*d-b*c == 0:
        raise UnsupportedPdfShadingError('Singular source transform')
    return tuple(value)


def _transform_commands(commands, matrix):
    """Exact proof geometry; keep rationals until final output conversion."""
    result = []
    for command in commands:
        op, *values = command
        if op not in ('M', 'L', 'C', 'Z') or len(values) != {'M': 2, 'L': 2, 'C': 6, 'Z': 0}[op] or not _finite(values):
            raise UnsupportedPdfShadingError('Unsupported or non-finite native clip command')
        points = [_point_exact(matrix, values[i:i+2]) for i in range(0, len(values), 2)]
        result.append([op, *(v for point in points for v in point)])
    return result


def _controls(commands):
    return [(cmd[i], cmd[i+1]) for cmd in commands for i in range(1, len(cmd), 2)]


def _rectangle(commands):
    if any(command[0] == 'Z' for command in commands[:-1]):
        return None
    points = []
    for command in commands:
        if (command[0] == 'M' and not points) or command[0] == 'L':
            points.append(tuple(command[1:]))
        elif command[0] != 'Z':
            return None
    if len(points) == 5 and points[-1] == points[0]:
        points.pop()
    if len(points) != 4:
        return None
    xs, ys = {p[0] for p in points}, {p[1] for p in points}
    if len(xs) != 2 or len(ys) != 2 or len(set(points)) != 4:
        return None
    if any(a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:]+points[:1])):
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _closed_manifest_commands(commands, matrix):
    result, opened, maximum_roundoff = [], False, Fraction(0)
    for command in _transform_commands(commands, matrix):
        op, *exact_points = command
        p = [float(value) for value in exact_points]
        if not _finite(p):
            raise UnsupportedPdfShadingError('Output coordinates exceed finite float range')
        maximum_roundoff = max([maximum_roundoff, *[abs(Fraction(value)-exact)
                                                   for value, exact in zip(p, exact_points)]])
        if op == 'M':
            if opened:
                result.append({'close': {}})
            result.append({'moveTo': {'x': p[0], 'y': p[1]}})
            opened = True
        elif op == 'L':
            if not opened:
                raise UnsupportedPdfShadingError('Clip line without a subpath')
            result.append({'lineTo': {'x': p[0], 'y': p[1]}})
        elif op == 'C':
            if not opened:
                raise UnsupportedPdfShadingError('Clip curve without a subpath')
            result.append({'cubicTo': dict(zip(('x1', 'y1', 'x2', 'y2', 'x', 'y'), p))})
        else:
            if opened:
                result.append({'close': {}})
                opened = False
    if opened:
        result.append({'close': {}})
    return result, {'proof_to_output_conversion': 'exact target rational to binary64 float, once per coordinate',
                    'maximum_coordinate_roundoff_target_units_exact': str(maximum_roundoff),
                    'roundoff_scope': 'does not bound native PDF extraction or DrawingML/render quantization'}


def _clip_geometry(clips, region):
    rectangles, complex_paths = [tuple(map(_exact, region))], []
    for clip in clips:
        if clip['kind'] != 'clip_path':
            raise UnsupportedPdfShadingError('Non-path clipping or an active soft mask is unsupported')
        commands = _transform_commands(clip['commands'], clip['matrix'])
        rectangle = _rectangle(commands)
        if rectangle:
            rectangles.append(rectangle)
        elif clip['evenodd']:
            raise UnsupportedPdfShadingError('Complex evenodd clips require native fill-rule support')
        else:
            complex_paths.append(commands)
    if len(complex_paths) > 1:
        raise UnsupportedPdfShadingError('Intersecting complex clips are unsupported')
    bounds = (max(r[0] for r in rectangles), max(r[1] for r in rectangles),
              min(r[2] for r in rectangles), min(r[3] for r in rectangles))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise UnsupportedPdfShadingError('Empty clip intersection requires separate source accounting')
    if complex_paths:
        commands = complex_paths[0]
        points = _controls(commands)
        if not points or any(not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]) for x, y in points):
            raise UnsupportedPdfShadingError('Complex clip control hull is not wholly inside all rectangular clips and ROI')
    else:
        x0, y0, x1, y1 = bounds
        commands = [['M', x0, y0], ['L', x1, y0], ['L', x1, y1], ['L', x0, y1], ['Z']]
    return commands, {'method': 'exact_nonzero_clip_with_control_hull_containment',
                      'predicate_arithmetic': 'exact_rationals_of_original_native_controls_and_clip_matrices',
                      'rectangular_intersection_pdf_pt_exact': list(map(str, bounds)),
                      'rectangular_intersection_pdf_pt': list(map(float, bounds)),
                      'complex_clip_count': len(complex_paths), 'curves_flattened': False}


def _constant_function(doc, m, resource, components):
    def get(obj, key):
        return m.pdf_dict_gets(obj, key)

    def number(obj, key, default=None):
        value = get(obj, key)
        if m.pdf_is_null(value) and default is not None:
            return default
        if not m.pdf_is_number(value):
            raise UnsupportedPdfShadingError(f'{key} must be numeric')
        value = m.pdf_to_real(value)
        if not math.isfinite(value):
            raise UnsupportedPdfShadingError(f'{key} must be finite')
        return value

    def array(obj, key, length, default=None, boolean=False):
        value = get(obj, key)
        if m.pdf_is_null(value) and default is not None:
            return list(default)
        if not m.pdf_is_array(value) or m.pdf_array_len(value) != length:
            raise UnsupportedPdfShadingError(f'{key} requires {length} entries')
        result = []
        for i in range(length):
            item = m.pdf_array_get(value, i)
            if boolean:
                if not m.pdf_is_bool(item):
                    raise UnsupportedPdfShadingError(f'{key} requires Boolean entries')
                result.append(bool(m.pdf_to_bool(item)))
            else:
                if not m.pdf_is_number(item):
                    raise UnsupportedPdfShadingError(f'{key} requires numeric entries')
                result.append(m.pdf_to_real(item))
        if not boolean and not _finite(result):
            raise UnsupportedPdfShadingError(f'Non-finite {key}')
        return result

    if number(resource, 'ShadingType') != 2:
        raise UnsupportedPdfShadingError('Only axial ShadingType 2 is supported')
    if any(not m.pdf_is_null(get(resource, key)) for key in ('Background', 'BBox')):
        raise UnsupportedPdfShadingError('Shading background or explicit BBox is unsupported')
    antialias = get(resource, 'AntiAlias')
    if not m.pdf_is_null(antialias) and (not m.pdf_is_bool(antialias) or m.pdf_to_bool(antialias)):
        raise UnsupportedPdfShadingError('Shading AntiAlias must be absent or false')
    coords = array(resource, 'Coords', 4)
    if coords[:2] == coords[2:]:
        raise UnsupportedPdfShadingError('Degenerate axial shading coordinates')
    domain = array(resource, 'Domain', 2, [0, 1])
    if domain[0] >= domain[1]:
        raise UnsupportedPdfShadingError('Non-increasing shading Domain')
    extend = array(resource, 'Extend', 2, [False, False], boolean=True)
    function = get(resource, 'Function')
    if not m.pdf_is_indirect(function):
        raise UnsupportedPdfShadingError('A single indirect sampled Function is required')
    xref = m.pdf_to_num(function)
    if not doc.xref_is_stream(xref) or number(function, 'FunctionType') != 0:
        raise UnsupportedPdfShadingError('Only sampled FunctionType 0 is supported')
    if number(function, 'BitsPerSample') != 8 or number(function, 'Order', 1) != 1:
        raise UnsupportedPdfShadingError('Only 8-bit, Order 1 sampled functions are supported')
    size = array(function, 'Size', 1)[0]
    if size < 1 or size != int(size) or size*components > _MAX_SAMPLE_BYTES:
        raise UnsupportedPdfShadingError('Invalid sampled function size or sample budget')
    size = int(size)
    function_domain = array(function, 'Domain', 2)
    if function_domain != domain:
        raise UnsupportedPdfShadingError('Function and shading Domain must match exactly')
    encode = array(function, 'Encode', 2, [0, size-1])
    if min(encode) < 0 or max(encode) > size-1:
        raise UnsupportedPdfShadingError('Function Encode lies outside the sampled grid')
    ranges = array(function, 'Range', 2*components)
    decode = array(function, 'Decode', 2*components, ranges)
    if any(ranges[i] > ranges[i+1] for i in range(0, len(ranges), 2)):
        raise UnsupportedPdfShadingError('Invalid Function Range')
    filters = get(function, 'Filter')
    if m.pdf_is_array(filters) and m.pdf_array_len(filters) == 1:
        filters = m.pdf_array_get(filters, 0)
    filter_name = m.pdf_to_name(filters) if m.pdf_is_name(filters) else None
    if not m.pdf_is_null(get(function, 'DecodeParms')):
        raise UnsupportedPdfShadingError('Sample stream DecodeParms are unsupported')
    raw_samples = doc.xref_stream_raw(xref)
    if m.pdf_is_null(filters):
        samples = raw_samples
    elif filter_name == 'FlateDecode':
        decoder = zlib.decompressobj()
        samples = decoder.decompress(raw_samples, size*components+1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise UnsupportedPdfShadingError('Sample stream exceeds decoded budget or has trailing compressed data')
    else:
        raise UnsupportedPdfShadingError('Only unfiltered or FlateDecode sampled streams are supported')
    if len(samples) != size*components:
        raise UnsupportedPdfShadingError('Sample byte count does not equal declared grid')
    first = samples[:components]
    if samples != first*size:
        raise UnsupportedPdfShadingError('Sampled function is not exactly constant')
    color = [max(ranges[2*i], min(ranges[2*i+1], decode[2*i]+first[i]/255*(decode[2*i+1]-decode[2*i]))) for i in range(components)]
    if not _finite(color) or any(c < 0 or c > 1 for c in color):
        raise UnsupportedPdfShadingError('Decoded device color must lie in [0,1]')
    return color, {'function_xref': xref, 'function_type': 0, 'order': 1, 'bits_per_sample': 8,
                   'size': [size], 'domain': domain, 'function_domain': function_domain,
                   'encode': encode, 'decode': decode, 'range': ranges, 'constant_components': color,
                   'sample_sha256': hashlib.sha256(samples).hexdigest(), 'sample_bytes': len(samples),
                   'sample_stream_filter': filter_name,
                   'encoded_sample_stream_sha256': hashlib.sha256(raw_samples).hexdigest(),
                   'function_dictionary_sha256': hashlib.sha256(doc.xref_object(xref).encode()).hexdigest(),
                   'coords': coords, 'extend': extend}


def extract_constant_axial_shading(pdf_path, *, page, paint_seqno, shading_xref,
                                    region, source_transform):
    """Return one native solid path plus source proof, or fail closed.

    ``page`` is one-based, ``paint_seqno`` is the native bboxlog index, and
    ``region`` is a top-left PDF rectangle. The resource xref is a claim checked
    against the actual callback pointer, never a nearest-bbox lookup.
    """
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise UnsupportedPdfShadingError('PDF shading requires PyMuPDF') from error
    for name, value, minimum in [('page', page, 1), ('paint_seqno', paint_seqno, 0), ('shading_xref', shading_xref, 1)]:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise UnsupportedPdfShadingError(f'Invalid {name}')
    if not isinstance(region, (list, tuple)) or len(region) != 4 or not _finite(region) or region[2] <= region[0] or region[3] <= region[1]:
        raise UnsupportedPdfShadingError('Expected a finite nonempty PDF region')
    source_transform = _matrix(source_transform)
    source = Path(pdf_path)
    if source.stat().st_size > _MAX_PDF_BYTES:
        raise UnsupportedPdfShadingError('PDF exceeds source byte budget')
    with source.open('rb') as stream:
        source_bytes = stream.read(_MAX_PDF_BYTES+1)
    if len(source_bytes) > _MAX_PDF_BYTES:
        raise UnsupportedPdfShadingError('PDF exceeds source byte budget')
    m = getattr(fitz, 'mupdf', None)
    required = ('FzPathWalker2', 'pdf_load_shading', 'pdf_new_indirect', 'pdf_specifics',
                'fz_walk_path', 'll_fz_keep_path', 'fz_convert_color', 'pdf_is_number',
                'pdf_is_bool', 'fz_default_rgb', 'fz_default_gray', 'fz_default_cmyk',
                'FzDevice2', 'FzCookie', 'fz_run_page', 'fz_close_device', 'pdf_load_colorspace',
                'll_fz_keep_colorspace', 'll_fz_keep_default_colorspaces')
    callbacks = ('clip_path', 'clip_stroke_path', 'clip_text', 'clip_stroke_text', 'clip_image_mask',
                 'pop_clip', 'begin_group', 'end_group', 'begin_mask', 'end_mask',
                 'begin_tile', 'end_tile', 'set_default_colorspaces')
    if (not hasattr(fitz, 'JM_new_bbox_device_Device') or any(not callable(getattr(m, key, None)) for key in required)
            or not callable(getattr(getattr(m, 'FzCookie', None), 'set_abort', None))
            or any(not hasattr(getattr(m, 'FzDevice2', None), 'use_virtual_'+key) for key in callbacks)):
        raise UnsupportedPdfShadingError('Installed PyMuPDF lacks native shading-device capabilities')
    try:
        with fitz.open(stream=source_bytes, filetype='pdf') as doc:
            if page > len(doc) or shading_xref >= doc.xref_length():
                raise UnsupportedPdfShadingError('Page or shading resource is outside the PDF')
            sheet = doc[page-1]
            if sheet.rotation:
                raise UnsupportedPdfShadingError('Rotated pages are unsupported')
            if not (sheet.rect.x0 <= region[0] < region[2] <= sheet.rect.x1 and sheet.rect.y0 <= region[1] < region[3] <= sheet.rect.y1):
                raise UnsupportedPdfShadingError('The source ROI must lie wholly within the PDF page')
            bboxlog = sheet.get_bboxlog()
            if len(bboxlog) > _MAX_PAINTS:
                raise UnsupportedPdfShadingError('Native paint budget exceeded')
            if any(not _finite(row[1]) for row in bboxlog):
                raise UnsupportedPdfShadingError('Non-finite native paint geometry')
            cookie = m.FzCookie()
            if paint_seqno >= len(bboxlog) or bboxlog[paint_seqno][0] != 'fill-shade':
                raise UnsupportedPdfShadingError('Selected native paint is not fill-shade')
            pdf = m.pdf_specifics(doc.this)
            resource = m.pdf_new_indirect(pdf, shading_xref, 0)
            csobj = m.pdf_dict_gets(resource, 'ColorSpace')
            csname = m.pdf_to_name(csobj) if m.pdf_is_name(csobj) else None
            components = {'DeviceGray': 1, 'DeviceRGB': 3, 'DeviceCMYK': 4}.get(csname)
            if components is None:
                raise UnsupportedPdfShadingError('Only explicit device shading colorspaces are supported')
            color, proof = _constant_function(doc, m, resource, components)
            loaded = m.pdf_load_shading(pdf, resource)
            pointer = int(loaded.m_internal.this)

            class Walker(m.FzPathWalker2):
                def __init__(self):
                    super().__init__(); self.commands = []; self.overflow = False
                    for key in ('moveto', 'lineto', 'curveto', 'closepath'):
                        getattr(self, 'use_virtual_'+key)()
                def append(self, command):
                    if len(self.commands) >= _MAX_COMMANDS:
                        self.overflow = True
                    else:
                        self.commands.append(command)
                def moveto(self, arg, x, y): self.append(['M', x, y])
                def lineto(self, arg, x, y): self.append(['L', x, y])
                def curveto(self, arg, *values): self.append(['C', *values])
                def closepath(self, arg): self.append(['Z'])

            class Capture(fitz.JM_new_bbox_device_Device):
                def __init__(self):
                    super().__init__([], False)
                    self.clips, self.groups, self.errors = [], [], []
                    self.mask_depth = self.tiles = self.command_count = self.context_events = 0
                    self.defaults_identity = True
                    self.selected = None
                    for key in callbacks:
                        getattr(self, 'use_virtual_'+key)()
                def error(self, error):
                    if len(self.errors) < 16: self.errors.append(str(error))
                    cookie.set_abort()
                def clip_path(self, ctx, path, evenodd, matrix, scissor):
                    try:
                        walker = Walker()
                        m.fz_walk_path(m.FzPath(m.ll_fz_keep_path(path)), walker, walker.m_internal)
                        self.command_count += len(walker.commands)
                        if walker.overflow or self.command_count > _MAX_COMMANDS:
                            raise UnsupportedPdfShadingError('Native clip command budget exceeded')
                        record = {'kind': 'clip_path', 'evenodd': bool(evenodd), 'commands': walker.commands,
                                  'matrix': [float(getattr(matrix, k)) for k in 'abcdef'],
                                  'scissor': [float(getattr(scissor, k)) for k in ('x0','y0','x1','y1')]}
                        _transform_commands(record['commands'], record['matrix'])
                        self.clips.append(record)
                    except Exception as error:
                        self.error(error); self.clips.append({'kind': 'failed'})
                def clip_stroke_path(self, *args): self.clips.append({'kind': 'clip_stroke_path'})
                def clip_text(self, *args): self.clips.append({'kind': 'clip_text'})
                def clip_stroke_text(self, *args): self.clips.append({'kind': 'clip_stroke_text'})
                def clip_image_mask(self, *args): self.clips.append({'kind': 'clip_image_mask'})
                def pop_clip(self, *args):
                    if self.clips: self.clips.pop()
                    else: self.error('Unbalanced clip callbacks')
                def begin_group(self, *args): self.groups.append(True)
                def end_group(self, *args):
                    if self.groups: self.groups.pop()
                    else: self.error('Unbalanced group callbacks')
                def begin_mask(self, *args): self.mask_depth += 1
                def end_mask(self, *args):
                    self.mask_depth -= 1; self.clips.append({'kind': 'soft-mask'})
                    if self.mask_depth < 0: self.error('Unbalanced mask callbacks')
                def begin_tile(self, *args): self.tiles += 1; return 0
                def end_tile(self, *args):
                    self.tiles -= 1
                    if self.tiles < 0: self.error('Unbalanced tile callbacks')
                def set_default_colorspaces(self, ctx, defaults):
                    try:
                        defaults = m.FzDefaultColorspaces(m.ll_fz_keep_default_colorspaces(defaults))
                        self.defaults_identity = all(int(getattr(m, 'fz_default_'+key)(defaults).m_internal.this)
                                                     == int(getattr(m, 'fz_device_'+key)().m_internal.this)
                                                     for key in ('gray','rgb','cmyk'))
                    except Exception as error: self.error(error)
                def fill_shade(self, ctx, shade, matrix, alpha, params):
                    seq = len(self.result)
                    try:
                        fitz.jm_bbox_fill_shade(self, ctx, shade, matrix, alpha, params)
                    except Exception as error:
                        self.error('Native fill_shade bbox callback failed: '+str(error))
                        return
                    if seq != paint_seqno: return
                    try:
                        if int(shade.this) != pointer:
                            raise UnsupportedPdfShadingError('Actual native shading pointer differs from claimed resource')
                        if self.groups or self.mask_depth or self.tiles or not self.defaults_identity:
                            raise UnsupportedPdfShadingError('Shading group, mask, tile or default-colorspace substitution is unsupported')
                        if alpha != 1 or params.op or shade.use_background or shade.type != 2:
                            raise UnsupportedPdfShadingError('Unsupported shading alpha, overprint, background or type')
                        shade_matrix = [float(getattr(shade.matrix, k)) for k in 'abcdef']
                        if shade_matrix != [1,0,0,1,0,0]:
                            raise UnsupportedPdfShadingError('Unexpected native shading resource matrix')
                        ctm = _matrix([float(getattr(matrix, k)) for k in 'abcdef'])
                        commands, geometry_proof = _clip_geometry(self.clips, region)
                        a,b,c,d,e,f = map(_exact, ctm); determinant = a*d-b*c
                        x0,y0,x1,y1 = map(_exact, proof['coords']); dx,dy = x1-x0,y1-y0
                        axis2 = dx*dx+dy*dy
                        if axis2 == 0:
                            raise UnsupportedPdfShadingError('Invalid axial shading length')
                        positions = []
                        for x,y in _controls(commands):
                            u=(d*(x-e)-c*(y-f))/determinant
                            v=(-b*(x-e)+a*(y-f))/determinant
                            positions.append(((u-x0)*dx+(v-y0)*dy)/axis2)
                        if not _finite(positions) or (not proof['extend'][0] and min(positions)<0) or (not proof['extend'][1] and max(positions)>1):
                            raise UnsupportedPdfShadingError('Clip control hull crosses an unextended axial domain')
                        source_cs = m.FzColorspace(m.ll_fz_keep_colorspace(shade.colorspace))
                        declared_cs = m.pdf_load_colorspace(csobj)
                        if int(source_cs.m_internal.this) != int(declared_cs.m_internal.this):
                            raise UnsupportedPdfShadingError('Actual native shading colorspace differs from declared device space')
                        rgb = m.fz_convert_color(source_cs, color, m.fz_device_rgb(), m.FzColorspace(), m.FzColorParams(params))[:3]
                        rounded = [round(v*255) for v in rgb]
                        if not _finite(rgb) or any(v < 0 or v > 1 for v in rgb) or any(abs(v*255-q)>1e-5 for v,q in zip(rgb,rounded)):
                            raise UnsupportedPdfShadingError('Constant native color is not exactly representable as 8-bit RGB')
                        output_commands, output_roundoff = _closed_manifest_commands(commands, source_transform)
                        self.selected = {'commands': output_commands,
                                         'output_coordinate_conversion': output_roundoff,
                                         'color': '#'+''.join(f'{v:02X}' for v in rounded),
                                         'native_ctm': list(ctm), 'native_shade_matrix': shade_matrix,
                                         'native_rgb': list(rgb), 'native_clips': list(self.clips),
                                         'geometry_proof': geometry_proof,
                                         'axis_parameter_control_hull': [float(min(positions)), float(max(positions))],
                                         'axis_parameter_control_hull_exact': [str(min(positions)), str(max(positions))],
                                         'axis_proof_arithmetic': 'exact_original_native_controls_and_clip_CTM_then_exact_inverse_shade_CTM',
                                         'color_params': {k: int(getattr(params,k)) for k in ('ri','bp','op','opm')}}
                    except Exception as error: self.error(error)

            # Director callbacks must never let an exception disappear into SWIG.
            for callback_name in callbacks:
                original_callback = getattr(Capture, callback_name)
                def guarded(self, *args, _callback=original_callback):
                    if self.errors:
                        return 0
                    try:
                        self.context_events += 1
                        if self.context_events > _MAX_CONTEXT_EVENTS:
                            raise UnsupportedPdfShadingError('Native context event budget exceeded')
                        result = _callback(self, *args)
                        if len(self.clips)+len(self.groups)+self.mask_depth+self.tiles > _MAX_DEPTH:
                            raise UnsupportedPdfShadingError('Native context depth budget exceeded')
                        return result
                    except Exception as error:
                        self.error(error)
                        return 0
                setattr(Capture, callback_name, guarded)
            capture = Capture()
            m.fz_run_page(sheet.this, capture, m.FzMatrix(), cookie)
            m.fz_close_device(capture)
            if capture.errors:
                raise UnsupportedPdfShadingError('; '.join(capture.errors))
            if capture.result != bboxlog:
                raise UnsupportedPdfShadingError('Native paint type/order/bbox differs from source bboxlog')
            if capture.clips or capture.groups or capture.mask_depth or capture.tiles or capture.selected is None:
                raise UnsupportedPdfShadingError('Unbalanced source context or missing selected shade')
            selected = capture.selected
            oid = f'pdf-shading-{paint_seqno:05d}-constant'
            return {'object': {'id': oid, 'kind': 'path', 'z_index': paint_seqno,
                               'commands': selected.pop('commands'),
                               'style': {'fill': selected.pop('color'), 'stroke': 'none', 'stroke_width': 0, 'opacity': 1}},
                    'provenance': {'source_kind': 'pdf_constant_axial_shading',
                                   'source_pdf_sha256': hashlib.sha256(source_bytes).hexdigest(),
                                   'page': page, 'page_xref': sheet.xref, 'paint_seqno': paint_seqno,
                                   'shading_xref': shading_xref, 'actual_native_resource_pointer_verified': True,
                                   'shading_dictionary_sha256': hashlib.sha256(doc.xref_object(shading_xref).encode()).hexdigest(),
                                   'source_colorspace': csname, 'function': proof,
                                   'region_pdf_pt': list(region), 'source_transform': list(source_transform),
                                   'verified_source_paint_count': len(bboxlog), 'pymupdf_version': fitz.VersionBind,
                                   'rendered_as': 'native_solid_path_not_image', 'alpha': 1,
                                   'default_colorspaces_verified_device_identity': True,
                                   **selected}}
    except UnsupportedPdfShadingError:
        raise
    except Exception as error:
        raise UnsupportedPdfShadingError('Native constant shading extraction failed: '+str(error)) from error
