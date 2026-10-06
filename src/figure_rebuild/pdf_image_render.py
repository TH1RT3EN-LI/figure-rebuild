"""Rasterize one selected native PDF image or stroke in its original context.

Independent paints are never forwarded. Image extraction and explicit stroke
sampling use separate public contracts and receipts.
This module imports the optional MuPDF runtime only when explicitly called.
"""
import hashlib
import math
from collections import Counter

from .pdf_image_native import PdfImageNativeError


def _render_native_pdf_paint(sheet, bboxlog, paint_seqno, *, source_transform,
                            source_bounds, user_clip_pdf, allow_native_rgb_group_sampling=False,
                            native_sampling_scale=8, allow_native_matte_sampling=False,
                            _paint_kind='fill-image', _image_seqnos=None,
                            _global_device_grid=False):
    """Return one sampled PNG and its explicit, grid-aligned source frame.

    ``source_bounds`` is the declared storage x0/y0/x1/y1 in source pixels.
    Image callers use their verified visible bounds; explicit stroke sampling
    uses the complete ROI rather than claiming a tight stroke-support proof.
    Its outward integer rounding introduces less than one pixel of transparent
    padding per edge. ``native_sampling_scale`` is explicitly 4 or 8 (default)
    samples per source pixel, a grid spacing rather than a color/filtering error
    bound. A different sampling scale can change filtering at all output sizes;
    it is not automatically more faithful. ``user_clip_pdf`` remains a native clip.

    Only Normal, unit-alpha, non-knockout RGB transparency groups are supported:
    an isolated full-page root and nonisolated children. Their actual callbacks
    are preserved, never eliminated. External masks and patterns fail closed.
    The separate ``allow_native_rgb_group_sampling`` opt-in admits one actual
    RGB child (including ICC and isolated groups) below that neutral page root.
    Splitting a shared group into per-image assets is unverified compositing,
    even though every forwarded callback retains its original colorspace.

    The caller must provide a fresh PDF document for this render. In particular,
    SVG, text-dictionary and hashed-image extraction can alter MuPDF's cached
    image sampling state. The public extraction helper opens a fresh document
    per occurrence; this low-level function cannot undo prior use of ``sheet``.
    Image/mask receipt decoding is deferred until the PNG has been encoded.

    ``allow_native_matte_sampling`` separately admits a native 8-bit DeviceRGB
    image with a same-size attached Matte mask. The actual image and mask are
    forwarded unchanged; decoded alpha is never manually merged a second time.
    This remains sampled output, without an RGB/alpha error bound.
    """
    if _paint_kind not in ('fill-image', 'stroke-path'):
        raise PdfImageNativeError('Unsupported selected paint kind')
    if not isinstance(allow_native_rgb_group_sampling, bool):
        raise ValueError('allow_native_rgb_group_sampling must be a boolean')
    if not isinstance(allow_native_matte_sampling, bool):
        raise ValueError('allow_native_matte_sampling must be a boolean')
    if allow_native_matte_sampling and _paint_kind != 'fill-image':
        raise ValueError('allow_native_matte_sampling requires an image paint')
    interval = _image_seqnos is not None
    if interval:
        if (_paint_kind != 'fill-image' or allow_native_rgb_group_sampling or allow_native_matte_sampling or
                type(_image_seqnos) is not list or not 1 <= len(_image_seqnos) <= 64 or
                any(type(i) is not int or not 0 <= i < len(bboxlog) or bboxlog[i][0] != 'fill-image'
                    for i in _image_seqnos) or
                _image_seqnos != list(range(_image_seqnos[0], _image_seqnos[-1] + 1))):
            raise PdfImageNativeError('Image interval requires 1 to 64 consecutive actual image paints')
        paint_seqno = _image_seqnos[0]
    if type(_global_device_grid) is not bool or (_global_device_grid and not interval):
        raise ValueError('Global device grid requires an explicit image interval')
    if (isinstance(native_sampling_scale, bool) or not isinstance(native_sampling_scale, int)
            or native_sampling_scale not in ((1, 2, 4, 8) if interval else (4, 8))):
        raise ValueError('native_sampling_scale must be an integer '+('1, 2, 4 or 8' if interval else '4 or 8'))
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise PdfImageNativeError('Native occurrence rendering requires PyMuPDF') from error
    m = getattr(fitz, 'mupdf', None)
    names = ('FzDevice2', 'FzMatrix', 'FzIrect', 'FzRect', 'fz_infinite_rect', 'FzCookie', 'FzSeparations', 'FzPixmap',
             'FzDefaultColorspaces', 'fz_new_pixmap_with_bbox', 'fz_clear_pixmap',
             'fz_new_draw_device', 'fz_new_path', 'fz_rectto', 'fz_clip_path',
             'fz_pop_clip', 'fz_run_page', 'fz_close_device', 'fz_device_rgb',
             'fz_default_rgb', 'll_fz_keep_default_colorspaces',
             'FzImage', 'll_fz_keep_image',
             'll_fz_get_unscaled_pixmap_from_image', 'll_fz_fill_image',
             'll_fz_clip_path', 'll_fz_clip_stroke_path', 'll_fz_clip_text',
             'll_fz_clip_stroke_text', 'll_fz_clip_image_mask', 'll_fz_pop_clip',
             'll_fz_begin_group', 'll_fz_end_group', 'll_fz_set_default_colorspaces')
    if _paint_kind == 'stroke-path':
        names += ('ll_fz_stroke_path', 'll_fz_colorspace_name')
    if allow_native_rgb_group_sampling:
        names += ('ll_fz_colorspace_is_rgb', 'll_fz_colorspace_n', 'll_fz_colorspace_name',
                  'll_fz_colorspace_digest', 'python_mutable_buffer_data')
    if (m is None or not hasattr(fitz, 'JM_new_bbox_device_Device') or
            not hasattr(fitz, 'jm_bbox_fill_image') or any(not hasattr(m, name) for name in names)):
        raise PdfImageNativeError('Installed PyMuPDF lacks required native image forwarding APIs')
    if _paint_kind == 'stroke-path' and not hasattr(fitz, 'jm_bbox_stroke_path'):
        raise PdfImageNativeError('Installed PyMuPDF lacks required native stroke forwarding APIs')
    if (isinstance(paint_seqno, bool) or not isinstance(paint_seqno, int) or
            not 0 <= paint_seqno < len(bboxlog) or bboxlog[paint_seqno][0] != _paint_kind):
        raise PdfImageNativeError('Selected sequence must equal the requested actual paint kind')
    selected_seqnos = set(_image_seqnos) if interval else {paint_seqno}
    last_selected = max(selected_seqnos)

    def finite(values, length):
        return (isinstance(values, (tuple, list)) and len(values) == length and
                all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
                    for v in values))

    if (not finite(source_transform, 6) or source_transform[1] != 0 or source_transform[2] != 0
            or source_transform[0] == 0 or source_transform[3] == 0):
        raise PdfImageNativeError('Source mapping must be finite, nonzero diagonal scale/translation')
    if any(not finite(rect, 4) or rect[2] <= rect[0] or rect[3] <= rect[1]
           for rect in (source_bounds, user_clip_pdf)):
        raise PdfImageNativeError('Native rendering needs positive finite bounds and region')
    if sheet.rotation:
        raise PdfImageNativeError('Native image rendering requires an unrotated PDF page')
    frame = [math.floor(source_bounds[0]), math.floor(source_bounds[1]),
             math.ceil(source_bounds[2]), math.ceil(source_bounds[3])]
    sampling = native_sampling_scale
    width, height = (frame[2]-frame[0])*sampling, (frame[3]-frame[1])*sampling
    if width*height > 64_000_000 or max(width, height) > 32768:
        raise PdfImageNativeError(f'Native image occurrence exceeds the explicit {sampling}x sampling budget')
    mapping = [source_transform[0]*sampling, 0, 0, source_transform[3]*sampling,
               (source_transform[4]-frame[0])*sampling,
               (source_transform[5]-frame[1])*sampling]
    device_bbox = [0, 0, width, height]
    if _global_device_grid:
        mapping = [source_transform[0]*sampling, 0, 0, source_transform[3]*sampling,
                   source_transform[4]*sampling, source_transform[5]*sampling]
        device_bbox = [v*sampling for v in frame]
    if not all(math.isfinite(v) for v in mapping):
        raise PdfImageNativeError('Source-to-sample matrix overflows finite coordinates')
    expected = [(row[0], tuple(row[1])) for row in bboxlog]
    if any(len(box) != 4 or not all(math.isfinite(v) for v in box) for _, box in expected):
        raise PdfImageNativeError('Non-finite native bboxlog geometry')

    def colorspace_record(cs):
        # Native type, not its display name or component count alone, excludes
        # Lab / DeviceN with three components. Digest is the native profile MD5;
        # it does not assert equivalence to a different ICC / DeviceRGB profile.
        if not cs:
            return None
        digest = bytearray(16)
        m.ll_fz_colorspace_digest(cs, m.python_mutable_buffer_data(digest))
        return {'name': m.ll_fz_colorspace_name(cs),
                'components': int(m.ll_fz_colorspace_n(cs)),
                'actual_rgb_type': bool(m.ll_fz_colorspace_is_rgb(cs)),
                'native_profile_md5': digest.hex(),
                'actual_device_rgb_identity': int(cs.this) == int(m.fz_device_rgb().m_internal.this)}

    class ForwardPaint(fitz.JM_new_bbox_device_Device):
        def __init__(self, target):
            super().__init__([], False)
            self.target = target
            self.errors = []
            self.groups = []
            self.clips = []
            self.mask_depth = 0
            self.tile_depth = 0
            self.group_count = self.clip_count = self.default_count = 0
            self.default_colorspaces = m.FzDefaultColorspaces(None)
            self.default_rgb_identity = (int(m.fz_default_rgb(self.default_colorspaces).m_internal.this)
                                         == int(m.fz_device_rgb().m_internal.this))
            self.selected = None
            self.selected_image = None
            self.interval_receipts = []
            self.interval_images = []
            self.interval_seqnos = []
            self.interval_resource_bytes = 0
            if _paint_kind == 'stroke-path':
                self.use_virtual_stroke_path()
            for name in ('clip_path', 'clip_stroke_path', 'clip_text', 'clip_stroke_text',
                         'clip_image_mask', 'pop_clip', 'begin_group', 'end_group',
                         'set_default_colorspaces', 'begin_mask', 'end_mask',
                         'begin_tile', 'end_tile'):
                getattr(self, 'use_virtual_'+name)()

        def error(self, message):
            if len(self.errors) < 16:
                self.errors.append(message)

        def forward(self, name, *args):
            try:
                getattr(m, 'll_fz_'+name)(self.target.m_internal, *args)
            except Exception as error:
                self.error(f'{name}: {type(error).__name__}: {error}')

        def set_default_colorspaces(self, ctx, defaults):
            try:
                self.default_colorspaces = m.FzDefaultColorspaces(m.ll_fz_keep_default_colorspaces(defaults))
                self.default_rgb_identity = (int(m.fz_default_rgb(self.default_colorspaces).m_internal.this)
                                             == int(m.fz_device_rgb().m_internal.this))
                self.default_count += 1
                self.forward('set_default_colorspaces', defaults)
            except Exception as error:
                self.error('Default colorspace callback failed: '+str(error))

        def begin_group(self, ctx, area, cs, isolated, knockout, blendmode, alpha):
            bounds = [float(getattr(area, key)) for key in ('x0', 'y0', 'x1', 'y1')]
            rgb = bool(cs and int(cs.this) == int(m.fz_device_rgb().m_internal.this))
            root = (not self.groups and not self.result and self.group_count == 0
                    and not self.clips and not self.mask_depth and not self.tile_depth
                    and bounds == list(sheet.rect) and isolated == 1)
            valid = (alpha == 1 and knockout == 0 and blendmode == 0
                     and (not cs or rgb) and self.default_rgb_identity
                     and (root or isolated == 0) and all(math.isfinite(v) for v in bounds))
            profile = default_profile = None
            sampled = False
            if allow_native_rgb_group_sampling:
                try:
                    profile = colorspace_record(cs)
                    default_profile = colorspace_record(m.fz_default_rgb(self.default_colorspaces).m_internal)
                    neutral_root = root and valid
                    child = (len(self.groups) == 1 and self.groups[0]['full_page_root']
                             and self.groups[0]['supported'] and profile is not None
                             and profile['actual_rgb_type'] and profile['components'] == 3
                             and alpha == 1 and knockout == 0 and blendmode == 0
                             and isolated in (0, 1) and self.default_rgb_identity
                             and all(math.isfinite(v) for v in bounds))
                    sampled = child and not valid
                    # Deliberately bounded to the neutral root + one child.
                    # Existing deeper strict-mode support is unchanged when
                    # this opt-in is absent.
                    valid = neutral_root or child
                except Exception as error:
                    self.error('Native group colorspace inspection failed: '+str(error))
                    valid = False
            self.group_count += 1
            self.groups.append({'group_id': self.group_count,
                                'parent_group_id': self.groups[-1]['group_id'] if self.groups else None,
                                'begin_paint_seqno': len(self.result), 'bbox_pdf_pt': bounds,
                                'isolated': bool(isolated), 'knockout': bool(knockout),
                                'blendmode': blendmode, 'alpha': alpha,
                                'colorspace': 'DeviceRGB' if rgb else ('unsupported' if cs else None),
                                'actual_device_rgb_identity': rgb,
                                'default_rgb_is_device_rgb': self.default_rgb_identity,
                                'full_page_root': root, 'supported': valid,
                                'forwarded': self.selected is None,
                                'handling': 'original_native_callback_forwarded_unchanged'})
            if allow_native_rgb_group_sampling:
                self.groups[-1].update({'colorspace': profile['name'] if profile else None,
                                        'native_colorspace': profile,
                                        'native_default_rgb': default_profile,
                                        'sampled_group_extension_used': sampled,
                                        'colorspace_aliased_or_replaced': False})
            if self.groups[-1]['forwarded']:
                self.forward('begin_group', area, cs, isolated, knockout, blendmode, alpha)

        def end_group(self, ctx):
            if not self.groups:
                self.error('Unbalanced source group callbacks')
            else:
                group = self.groups.pop()
                group['end_paint_seqno_exclusive'] = len(self.result)
                if group['forwarded']:
                    self.forward('end_group')

        def push_clip(self, kind, args, matrix_index, extra=None):
            self.clip_count += 1
            matrix = args[matrix_index]
            # A later, unrelated alpha clip can recomposite the existing
            # backdrop even if its image paint is suppressed. Forward only
            # contexts which can contain the selected paint, then unwind them.
            pending = len(self.result) <= last_selected if interval else self.selected is None
            forward = pending and (kind != 'clip_image_mask' or len(self.result) in selected_seqnos)
            self.clips.append({'clip_event_id': self.clip_count, 'kind': kind,
                               'matrix': [float(getattr(matrix, key)) for key in 'abcdef'],
                               'paint_count': len(self.result), 'forwarded': forward, **(extra or {})})
            if forward:
                self.forward(kind, *args)

        def clip_path(self, ctx, *args):
            self.push_clip('clip_path', args, 2, {'evenodd': bool(args[1])})

        def clip_stroke_path(self, ctx, *args):
            self.push_clip('clip_stroke_path', args, 2)

        def clip_text(self, ctx, *args):
            self.push_clip('clip_text', args, 1)

        def clip_stroke_text(self, ctx, *args):
            self.push_clip('clip_stroke_text', args, 2)

        def clip_image_mask(self, ctx, *args):
            self.push_clip('clip_image_mask', args, 1, {'image_pointer': int(args[0].this)})

        def pop_clip(self, ctx):
            if not self.clips:
                self.error('Unbalanced source clip callbacks')
            elif self.clips.pop()['forwarded']:
                self.forward('pop_clip')

        def begin_mask(self, *args):
            self.mask_depth += 1

        def end_mask(self, *args):
            self.mask_depth -= 1
            self.clips.append({'kind': 'external_mask', 'forwarded': False})

        def begin_tile(self, *args):
            self.tile_depth += 1
            return 0

        def end_tile(self, *args):
            self.tile_depth -= 1

        def stroke_path(self, ctx, path, stroke, ctm, cs, color, alpha, params):
            seqno = len(self.result)
            fitz.jm_bbox_stroke_path(self, ctx, path, stroke, ctm, cs, color, alpha, params)
            if seqno != paint_seqno or _paint_kind != 'stroke-path':
                return
            try:
                if any(not g['supported'] for g in self.groups) or not self.default_rgb_identity:
                    raise PdfImageNativeError('Unsupported stroke group/default RGB context')
                if self.mask_depth or self.tile_depth or any(c['kind'] in ('external_mask','clip_image_mask') for c in self.clips):
                    raise PdfImageNativeError('Stroke masks/patterns are not supported')
                if alpha != 1 or params.op:
                    raise PdfImageNativeError('Stroke alpha/overprint compositing is not supported')
                matrix = [float(getattr(ctm,k)) for k in 'abcdef']
                style = {k:float(getattr(stroke,k)) for k in ('linewidth','miterlimit','start_cap','dash_cap','end_cap','linejoin','dash_len','dash_phase')}
                if not all(math.isfinite(v) for v in [*matrix,*style.values()]) or style['linewidth'] <= 0:
                    raise PdfImageNativeError('Stroke native geometry/style is not finite and positive')
                self.selected = {'transform':matrix,'groups':list(self.groups),
                    'clip_chain':[dict(c) for c in self.clips], 'native_stroke':style,
                    'native_colorspace':m.ll_fz_colorspace_name(cs) if cs else None,
                    'color_params':{k:getattr(params,k) for k in ('ri','bp','op','opm')},
                    'default_rgb_is_device_rgb_at_paint':self.default_rgb_identity,
                    'default_colorspace_events_before_paint':self.default_count}
                if allow_native_rgb_group_sampling:
                    self.selected['native_default_rgb_at_paint'] = colorspace_record(
                        m.fz_default_rgb(self.default_colorspaces).m_internal)
                self.forward('stroke_path',path,stroke,ctm,cs,color,alpha,params)
            except Exception as error:
                self.error(f'Paint {seqno}: {type(error).__name__}: {error}')

        def fill_image(self, ctx, image, ctm, alpha, color_params):
            seqno = len(self.result)
            fitz.jm_bbox_fill_image(self, ctx, image, ctm, alpha, color_params)
            if seqno not in selected_seqnos or _paint_kind != 'fill-image':
                return
            try:
                self.capture_and_forward(image, ctm, alpha, color_params, seqno)
            except Exception as error:
                self.error(f'Paint {seqno}: {type(error).__name__}: {error}')

        def capture_and_forward(self, image, ctm, alpha, params, seqno):
            if interval and self.groups:
                raise PdfImageNativeError('Image intervals require group-free source paints')
            if any(not group['supported'] for group in self.groups):
                raise PdfImageNativeError('Unsupported PDF group: requires Normal alpha1 RGB with no knockout')
            # A nested Form need not start a group to change default spaces.
            # Group-entry checks alone do not bind the selected image context.
            if not self.default_rgb_identity:
                raise PdfImageNativeError('Selected image has a non-DeviceRGB default RGB colorspace')
            if self.mask_depth or self.tile_depth or any(c['kind'] == 'external_mask' for c in self.clips):
                raise PdfImageNativeError('External masks and pattern compositing are unsupported')
            if alpha != 1 or params.op:
                raise PdfImageNativeError('Image draw alpha or overprint requires unsupported compositing')
            matte = bool(image.mask and image.use_colorkey)
            if matte:
                if not allow_native_matte_sampling:
                    raise PdfImageNativeError('Image /Matte compositing is unsupported')
                if (not image.colorspace or
                        int(image.colorspace.this) != int(m.fz_device_rgb().m_internal.this) or
                        image.n != 3 or image.bpc != 8 or image.use_decode or
                        image.mask.bpc != 8 or image.mask.use_decode):
                    raise PdfImageNativeError('Native Matte sampling requires 8-bit DeviceRGB and an 8-bit mask without Decode changes')
            if image.w <= 0 or image.h <= 0 or image.w*image.h > 64_000_000:
                raise PdfImageNativeError('Native image dimensions exceed rendering budget')
            if interval:
                self.interval_resource_bytes += image.w*image.h*(image.n + (1 if image.mask else 0))
                if self.interval_resource_bytes > 64_000_000:
                    raise PdfImageNativeError('Image interval decoded resource byte budget')
            transform = [float(getattr(ctm, key)) for key in 'abcdef']
            if not all(math.isfinite(v) for v in transform):
                raise PdfImageNativeError('Non-finite native image transform')
            mask_clips = [c for c in self.clips if c['kind'] == 'clip_image_mask']
            matched = (len(mask_clips) == 1 and image.mask
                       and mask_clips[0]['image_pointer'] == int(image.mask.this)
                       and mask_clips[0]['matrix'] == transform
                       and mask_clips[0]['paint_count'] == seqno)
            if mask_clips and not matched:
                raise PdfImageNativeError('Image-mask clip is not the bound occurrence mask')
            if image.mask and not matched:
                raise PdfImageNativeError('Attached image mask lacks its verified native clip callback')
            if image.mask:
                if image.mask.mask or (image.mask.w, image.mask.h) != (image.w, image.h):
                    raise PdfImageNativeError('Nested or differently sampled attached masks are unsupported')
            # Decoding merely for a digest changes MuPDF's cached image state
            # and can change subsequent sampling. Keep the actual handle, but
            # do not request its unscaled pixels until every draw scope closes.
            self.selected_image = m.FzImage(m.ll_fz_keep_image(image))
            self.selected = {'width': image.w, 'height': image.h,
                             'transform': transform, 'groups': list(self.groups),
                             'clip_chain': [{k: v for k, v in c.items() if k != 'image_pointer'}
                                            for c in self.clips],
                             'native_interpolate': bool(image.interpolate),
                             'native_use_decode': bool(image.use_decode),
                             'native_use_colorkey': bool(image.use_colorkey),
                             'color_params': {key: getattr(params, key) for key in ('ri', 'bp', 'op', 'opm')},
                             'default_rgb_is_device_rgb_at_paint': self.default_rgb_identity,
                             'default_colorspace_events_before_paint': self.default_count}
            if allow_native_rgb_group_sampling:
                self.selected['native_default_rgb_at_paint'] = colorspace_record(
                    m.fz_default_rgb(self.default_colorspaces).m_internal)
            if allow_native_matte_sampling:
                self.selected.update(allow_native_matte_sampling=True,
                    native_matte_combination_present=matte,
                    matte_color_reconstructed=False,
                    decoded_alpha_manually_recombined=False,
                    matte_handling='actual_image_and_bound_mask_forwarded_unchanged' if matte else 'not_present')
            self.forward('fill_image', image, ctm, alpha, params)
            if interval:
                self.interval_receipts.append(self.selected)
                self.interval_images.append(self.selected_image)
                self.interval_seqnos.append(seqno)

    try:
        render_matrix = m.FzMatrix(*mapping)
        if not all(math.isfinite(getattr(render_matrix, key)) for key in 'abcdef'):
            raise PdfImageNativeError('Source-to-sample matrix exceeds native float range')
        pix = m.fz_new_pixmap_with_bbox(m.fz_device_rgb(), m.FzIrect(*device_bbox),
                                       m.FzSeparations(), 1)
        m.fz_clear_pixmap(pix)
        target = m.fz_new_draw_device(render_matrix, pix)
        # Padding changes the storage frame, never the caller's visible region.
        region_path = m.fz_new_path()
        m.fz_rectto(region_path, *user_clip_pdf)
        m.fz_clip_path(target, region_path, 0, m.FzMatrix(), m.FzRect(m.fz_infinite_rect))
        device = ForwardPaint(target)
        cookie = m.FzCookie()
        m.fz_run_page(sheet.this, device, m.FzMatrix(), cookie)
        m.fz_close_device(device)
        m.fz_pop_clip(target)
        m.fz_close_device(target)
    except Exception as error:
        raise PdfImageNativeError('Native occurrence forwarding failed: '+str(error)) from error
    if [(row[0], tuple(row[1])) for row in device.result] != expected:
        raise PdfImageNativeError('Native forwarded paint type/bbox sequence differs from bboxlog')
    if cookie.abort() or cookie.errors() or cookie.incomplete():
        raise PdfImageNativeError('Native occurrence replay was aborted, incomplete or reported errors')
    if device.errors:
        raise PdfImageNativeError('; '.join(device.errors))
    if device.groups or device.clips or device.mask_depth or device.tile_depth:
        raise PdfImageNativeError('Native context did not return to its original state')
    if device.selected is None:
        raise PdfImageNativeError('Selected native image was not forwarded')
    encoded = fitz.Pixmap('raw', pix).tobytes('png')
    selected = device.selected
    if _paint_kind == 'fill-image':
        try:
            captures = list(zip(device.interval_receipts, device.interval_images)) if interval else [(selected, device.selected_image)]
            if interval and device.interval_seqnos != _image_seqnos:
                raise PdfImageNativeError('Actual image interval differs from declared sequence')
            for captured, handle in captures:
                image = handle.m_internal
                native = fitz.Pixmap('raw', m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image)))
                if (native.width, native.height) != (captured['width'], captured['height']):
                    raise PdfImageNativeError('Native receipt decoder unexpectedly changed dimensions')
                attached = None
                if image.mask:
                    mask = fitz.Pixmap('raw', m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image.mask)))
                    if (mask.width, mask.height, mask.n) != (image.w, image.h, 1):
                        raise PdfImageNativeError('Native receipt mask dimensions or channels disagree')
                    attached = {'native_digest': mask.digest.hex(), 'width': mask.width, 'height': mask.height,
                                'actual_handle_and_matrix_and_sequence_bound': True}
                captured.update({'native_digest': native.digest.hex(),
                                 'native_colorspace': native.colorspace.name if native.colorspace else None,
                                 'attached_mask': attached})
        except Exception as error:
            raise PdfImageNativeError('Native post-render receipt capture failed: '+str(error)) from error
        finally:
            device.selected_image = None
            device.interval_images.clear()
    if allow_native_rgb_group_sampling:
        for group in selected['groups']:
            start, end = group['begin_paint_seqno'], group['end_paint_seqno_exclusive']
            counts = Counter(kind for kind, _ in expected[start:end])
            independent = counts.copy()
            independent[_paint_kind] -= 1
            group.update({'source_paint_count': end-start, 'source_paint_counts': dict(counts),
                          'independent_paint_count': end-start-1,
                          'independent_paint_counts': {k: v for k, v in independent.items() if v},
                          'paint_count_scope': 'complete native group interval, including descendant paints',
                          'shared_group_split_unverified': end-start > 1})
    receipt = {**selected, 'method': 'native_fill_image_and_original_context_forwarding',
               'native_replay': {'errors': 0, 'incomplete': 0, 'aborted': False},
               'paint_seqno': paint_seqno, 'pymupdf_version': fitz.VersionBind,
               'verified_total_source_paints': len(expected), 'image_paints_forwarded': 1,
               'independent_text_path_shading_other_image_paints_forwarded': 0,
               'native_image_handle_forwarded_without_decode_reencode': True,
               'native_color_mask_filtering_and_interpolation_retained': True,
               'source_groups_applied': 'original_callbacks_preserved_not_removed',
               'source_clip_callbacks_forwarded_without_geometry_approximation': True,
               'source_transform': list(source_transform), 'source_to_sample_matrix': mapping,
               'tight_original_visible_bbox_source_px': list(source_bounds),
               'derived_aligned_frame_source_px': frame, 'user_clip_pdf_pt': list(user_clip_pdf),
               'transparent_padding_source_px': [source_bounds[0]-frame[0], source_bounds[1]-frame[1],
                                                 frame[2]-source_bounds[2], frame[3]-source_bounds[3]],
               'native_sampling_scale_requested': native_sampling_scale,
               'native_sampling_scale_effective': sampling,
               'sampling_scale': sampling, 'sampling_pitch_source_px': 1/sampling,
               'sampling_bound_scope': 'grid spacing only; not a bound on RGB, alpha, filtering or numerical error',
               'raster_size': [width, height], 'native_png_sha256': hashlib.sha256(encoded).hexdigest(),
               'pixel_metadata_capture_phase': 'after_all_native_draw_devices_closed_and_png_encoded'}
    if interval:
        receipt.update(method='native_contiguous_images_and_original_context_forwarding',
            image_paints_forwarded=len(_image_seqnos), actual_complete_contiguous_image_interval=_image_seqnos,
            all_selected_image_receipts=[dict(row, paint_seqno=seqno) for row, seqno in
                                         zip(device.interval_receipts, device.interval_seqnos)],
            source_groups='none', global_device_grid=_global_device_grid, device_bbox=device_bbox,
            exact_group_decomposition_claimed=False, rgb_alpha_error_bound=None)
    if allow_native_rgb_group_sampling:
        receipt.update({'allow_native_rgb_group_sampling': True,
                        'sampled_group_extension_used': any(g['sampled_group_extension_used'] for g in selected['groups']),
                        'required_full_figure_visual_review': True,
                        'shared_group_split_unverified': any(g['shared_group_split_unverified'] for g in selected['groups']),
                        'rgb_alpha_error_bound': None,
                        'exact_group_decomposition_claimed': False,
                        'group_sampling_scope': 'original native callbacks; neutral page root plus at most one RGB child',
                        'mupdf_version': fitz.VersionFitz})
    if allow_native_matte_sampling:
        receipt.update(required_full_figure_visual_review=True,
            matte_sampling_rgb_alpha_error_bound=None,
            exact_matte_decomposition_claimed=False,
            matte_sampling_scope='native 8-bit DeviceRGB image and same-size bound 8-bit mask; original clips and callbacks')
    if _paint_kind == 'stroke-path':
        for key in ('image_paints_forwarded','independent_text_path_shading_other_image_paints_forwarded',
                    'native_image_handle_forwarded_without_decode_reencode','native_color_mask_filtering_and_interpolation_retained'):
            receipt.pop(key)
        receipt.update(method='native_stroke_path_and_original_context_forwarding',
            stroke_paints_forwarded=1, independent_other_paints_forwarded=0,
            original_native_stroke_handle_forwarded=True, source_geometry_rebuilt=False,
            source_dash_lowering_performed=False, output_representation='sampled_rgba_not_editable_path')
        receipt['declared_storage_bbox_source_px'] = receipt.pop('tight_original_visible_bbox_source_px')
        return {'asset_bytes':encoded,'frame':frame,'transform':selected['transform'],'receipt':receipt}
    return {'asset_bytes': encoded, 'frame': frame, 'width': selected['width'],
            'height': selected['height'], 'transform': selected['transform'],
            'native_digest': selected['native_digest'], 'receipt': receipt}


def render_native_pdf_image(sheet, bboxlog, paint_seqno, *, source_transform,
                            source_bounds, user_clip_pdf, allow_native_rgb_group_sampling=False,
                            native_sampling_scale=8, allow_native_matte_sampling=False):
    """Sample one actual image; preserve the existing image-only contract."""
    return _render_native_pdf_paint(sheet, bboxlog, paint_seqno,
        source_transform=source_transform, source_bounds=source_bounds,
        user_clip_pdf=user_clip_pdf,
        allow_native_rgb_group_sampling=allow_native_rgb_group_sampling,
        native_sampling_scale=native_sampling_scale,
        allow_native_matte_sampling=allow_native_matte_sampling)


def render_native_pdf_image_interval(sheet, bboxlog, paint_seqnos, *, source_transform,
                                     source_bounds, user_clip_pdf, sampling_scale,
                                     global_device_grid=True):
    """Sample a declared group-free image-only interval on an integral grid.

    All original image handles, CTMs, clips, color programs and painter order
    are forwarded together. No text/path/shading paint enters the output. This
    contract admits no source transparency groups, external masks or Matte.
    It supplies finite sampled pixels, never a general color/alpha error bound.
    Use a fresh source document for every call.
    """
    return _render_native_pdf_paint(sheet, bboxlog, 0, source_transform=source_transform,
        source_bounds=source_bounds, user_clip_pdf=user_clip_pdf,
        native_sampling_scale=sampling_scale, _image_seqnos=paint_seqnos,
        _global_device_grid=global_device_grid)
