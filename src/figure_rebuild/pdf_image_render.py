"""Rasterize one actual PDF image paint while preserving its native context.

Independent text, path, shading and other image paints are never forwarded.
This module imports the optional MuPDF runtime only when explicitly called.
"""
import hashlib
import math

from .pdf_image_native import PdfImageNativeError


def render_native_pdf_image(sheet, bboxlog, paint_seqno, *, source_transform,
                            source_bounds, user_clip_pdf):
    """Return one sampled PNG and its explicit, grid-aligned source frame.

    ``source_bounds`` is the tight visible x0/y0/x1/y1 in source pixels.
    Its outward integer rounding introduces less than one pixel of transparent
    padding per edge. Eight samples per source pixel is a grid spacing, not a
    bound on color/filtering error. ``user_clip_pdf`` remains a native clip.

    Only Normal, unit-alpha, non-knockout RGB transparency groups are supported:
    an isolated full-page root and nonisolated children. Their actual callbacks
    are preserved, never eliminated. External masks and patterns fail closed.
    """
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
             'll_fz_get_unscaled_pixmap_from_image', 'll_fz_fill_image',
             'll_fz_clip_path', 'll_fz_clip_stroke_path', 'll_fz_clip_text',
             'll_fz_clip_stroke_text', 'll_fz_clip_image_mask', 'll_fz_pop_clip',
             'll_fz_begin_group', 'll_fz_end_group', 'll_fz_set_default_colorspaces')
    if (m is None or not hasattr(fitz, 'JM_new_bbox_device_Device') or
            not hasattr(fitz, 'jm_bbox_fill_image') or any(not hasattr(m, name) for name in names)):
        raise PdfImageNativeError('Installed PyMuPDF lacks required native image forwarding APIs')
    if (isinstance(paint_seqno, bool) or not isinstance(paint_seqno, int) or
            not 0 <= paint_seqno < len(bboxlog) or bboxlog[paint_seqno][0] != 'fill-image'):
        raise PdfImageNativeError('Selected sequence must be an actual fill-image paint')

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
    sampling = 8
    width, height = (frame[2]-frame[0])*sampling, (frame[3]-frame[1])*sampling
    if width*height > 64_000_000 or max(width, height) > 32768:
        raise PdfImageNativeError('Native image occurrence exceeds the explicit 8x sampling budget')
    mapping = [source_transform[0]*sampling, 0, 0, source_transform[3]*sampling,
               (source_transform[4]-frame[0])*sampling,
               (source_transform[5]-frame[1])*sampling]
    if not all(math.isfinite(v) for v in mapping):
        raise PdfImageNativeError('Source-to-sample matrix overflows finite coordinates')
    expected = [(row[0], tuple(row[1])) for row in bboxlog]
    if any(len(box) != 4 or not all(math.isfinite(v) for v in box) for _, box in expected):
        raise PdfImageNativeError('Non-finite native bboxlog geometry')

    class ForwardImage(fitz.JM_new_bbox_device_Device):
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
            forward = self.selected is None and (kind != 'clip_image_mask' or len(self.result) == paint_seqno)
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

        def fill_image(self, ctx, image, ctm, alpha, color_params):
            seqno = len(self.result)
            fitz.jm_bbox_fill_image(self, ctx, image, ctm, alpha, color_params)
            if seqno != paint_seqno:
                return
            try:
                self.capture_and_forward(image, ctm, alpha, color_params, seqno)
            except Exception as error:
                self.error(f'Paint {seqno}: {type(error).__name__}: {error}')

        def capture_and_forward(self, image, ctm, alpha, params, seqno):
            if any(not group['supported'] for group in self.groups):
                raise PdfImageNativeError('Unsupported PDF group: requires Normal alpha1 RGB with no knockout')
            if self.mask_depth or self.tile_depth or any(c['kind'] == 'external_mask' for c in self.clips):
                raise PdfImageNativeError('External masks and pattern compositing are unsupported')
            if alpha != 1 or params.op:
                raise PdfImageNativeError('Image draw alpha or overprint requires unsupported compositing')
            if image.mask and image.use_colorkey:
                raise PdfImageNativeError('Image /Matte compositing is unsupported')
            if image.w <= 0 or image.h <= 0 or image.w*image.h > 64_000_000:
                raise PdfImageNativeError('Native image dimensions exceed rendering budget')
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
            native = fitz.Pixmap('raw', m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image)))
            attached = None
            if image.mask:
                if image.mask.mask or (image.mask.w, image.mask.h) != (image.w, image.h):
                    raise PdfImageNativeError('Nested or differently sampled attached masks are unsupported')
                mask = fitz.Pixmap('raw', m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image.mask)))
                attached = {'native_digest': mask.digest.hex(), 'width': mask.width, 'height': mask.height,
                            'actual_handle_and_matrix_and_sequence_bound': True}
            self.selected = {'native_digest': native.digest.hex(), 'width': image.w, 'height': image.h,
                             'transform': transform, 'groups': list(self.groups),
                             'clip_chain': [{k: v for k, v in c.items() if k != 'image_pointer'}
                                            for c in self.clips],
                             'attached_mask': attached, 'native_colorspace': native.colorspace.name,
                             'native_interpolate': bool(image.interpolate),
                             'native_use_decode': bool(image.use_decode),
                             'native_use_colorkey': bool(image.use_colorkey),
                             'color_params': {key: getattr(params, key) for key in ('ri', 'bp', 'op', 'opm')},
                             'default_colorspace_events_before_paint': self.default_count}
            self.forward('fill_image', image, ctm, alpha, params)

    try:
        render_matrix = m.FzMatrix(*mapping)
        if not all(math.isfinite(getattr(render_matrix, key)) for key in 'abcdef'):
            raise PdfImageNativeError('Source-to-sample matrix exceeds native float range')
        pix = m.fz_new_pixmap_with_bbox(m.fz_device_rgb(), m.FzIrect(0, 0, width, height),
                                       m.FzSeparations(), 1)
        m.fz_clear_pixmap(pix)
        target = m.fz_new_draw_device(render_matrix, pix)
        # Padding changes the storage frame, never the caller's visible region.
        region_path = m.fz_new_path()
        m.fz_rectto(region_path, *user_clip_pdf)
        m.fz_clip_path(target, region_path, 0, m.FzMatrix(), m.FzRect(m.fz_infinite_rect))
        device = ForwardImage(target)
        m.fz_run_page(sheet.this, device, m.FzMatrix(), m.FzCookie())
        m.fz_close_device(device)
        m.fz_pop_clip(target)
        m.fz_close_device(target)
    except Exception as error:
        raise PdfImageNativeError('Native occurrence forwarding failed: '+str(error)) from error
    if [(row[0], tuple(row[1])) for row in device.result] != expected:
        raise PdfImageNativeError('Native forwarded paint type/bbox sequence differs from bboxlog')
    if device.errors:
        raise PdfImageNativeError('; '.join(device.errors))
    if device.groups or device.clips or device.mask_depth or device.tile_depth:
        raise PdfImageNativeError('Native context did not return to its original state')
    if device.selected is None:
        raise PdfImageNativeError('Selected native image was not forwarded')
    encoded = fitz.Pixmap('raw', pix).tobytes('png')
    selected = device.selected
    receipt = {**selected, 'method': 'native_fill_image_and_original_context_forwarding',
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
               'sampling_scale': sampling, 'sampling_pitch_source_px': 1/sampling,
               'sampling_bound_scope': 'grid spacing only; not a bound on RGB, alpha, filtering or numerical error',
               'raster_size': [width, height], 'native_png_sha256': hashlib.sha256(encoded).hexdigest()}
    return {'asset_bytes': encoded, 'frame': frame, 'width': selected['width'],
            'height': selected['height'], 'transform': selected['transform'],
            'native_digest': selected['native_digest'], 'receipt': receipt}
