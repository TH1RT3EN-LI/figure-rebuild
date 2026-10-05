"""Decode PDF image paints from their actual MuPDF device image handles.

This preserves PDF colorspace, Decode and attached mask interpretation without
looking up an xref by an image-content digest. Geometry clipping is separate.
"""
import hashlib
import io
import math

from PIL import Image


class PdfImageNativeError(ValueError):
    """An actual PDF image paint cannot be captured without losing meaning."""


MAX_NATIVE_IMAGE_PIXELS = 64_000_000


def capture_native_pdf_images(sheet, bboxlog, selected_seqnos, *,
                              allow_device_rgb_page_wrapper=False,
                              preserve_straight_mask_colors=False):
    """Return selected ``fill-image`` paints keyed by their bboxlog sequence.

    Every paint's type and bbox is checked in order, including unselected
    paints. ``pixels`` is a straight-alpha PIL RGBA image. ``native_digest``
    is MuPDF's decoded image digest before RGB conversion or attached SMask
    composition, allowing an independent image-info identity cross-check.

    ``selected_seqnos=None`` selects every ordinary image paint. Stencil
    image paints and external transparency/group compositing are unsupported;
    an image's own co-registered native mask is supported. Explicitly allowing
    a DeviceRGB page wrapper only admits the actual canonical DeviceRGB handle,
    identical default RGB, a full-page isolated Normal alpha-one root, and no
    children. It does not admit general RGB/ICC groups. Straight-mask mode keeps
    converted RGB samples and the co-registered mask separately, avoiding a
    premultiply/unpremultiply byte round trip; it does not prove filter equality.
    """
    for value, name in ((allow_device_rgb_page_wrapper, 'allow_device_rgb_page_wrapper'),
                        (preserve_straight_mask_colors, 'preserve_straight_mask_colors')):
        if type(value) is not bool:
            raise PdfImageNativeError(name + ' must be a boolean')
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise PdfImageNativeError('Native PDF image capture requires PyMuPDF') from error
    m = getattr(fitz, 'mupdf', None)
    required_fitz = ('JM_new_bbox_device_Device', 'jm_bbox_fill_image', 'Pixmap')
    required_native = ('FzDevice2', 'FzDefaultColorspaces', 'FzPixmap', 'FzColorspace',
                       'FzColorParams', 'FzMatrix', 'FzCookie', 'fz_image',
                       'll_fz_keep_default_colorspaces', 'll_fz_get_unscaled_pixmap_from_image',
                       'fz_convert_pixmap', 'fz_device_rgb', 'fz_new_pixmap_from_color_and_mask',
                       'fz_run_page', 'fz_close_device')
    if allow_device_rgb_page_wrapper:
        required_native += ('fz_default_rgb',)
    callbacks = ('set_default_colorspaces', 'begin_mask', 'end_mask', 'begin_group',
                 'end_group', 'clip_image_mask', 'clip_path', 'clip_stroke_path',
                 'clip_text', 'clip_stroke_text', 'pop_clip', 'begin_tile', 'end_tile')
    if (any(not hasattr(fitz, key) for key in required_fitz)
            or any(not hasattr(m, key) for key in required_native)
            or any(not hasattr(m.FzDevice2, 'use_virtual_' + key) for key in callbacks)
            or any(not hasattr(m.fz_image, key) for key in ('has_intent', 'intent', 'mask',
                                                            'use_colorkey', 'use_decode'))):
        raise PdfImageNativeError('Installed PyMuPDF lacks required native image-device capabilities')
    expected = []
    for record in bboxlog:
        if len(record) < 2 or len(record[1]) != 4:
            raise PdfImageNativeError('Malformed bboxlog record')
        box = tuple(record[1])
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            raise PdfImageNativeError('Non-finite bboxlog geometry')
        expected.append((record[0], box))
    if selected_seqnos is None:
        selected = {i for i, row in enumerate(expected) if row[0] == 'fill-image'}
    else:
        try:
            selected = set(selected_seqnos)
        except TypeError as error:
            raise PdfImageNativeError('Selected paint sequence numbers must be integers') from error
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(expected)
               for i in selected):
            raise PdfImageNativeError('Selected paint sequence is out of range')
        if any(expected[i][0] != 'fill-image' for i in selected):
            raise PdfImageNativeError('Selected paint is not an ordinary fill-image')
    if sheet.rotation:
        raise PdfImageNativeError('Native capture requires an unrotated PDF page')

    class NativeImages(fitz.JM_new_bbox_device_Device):
        # This installed PyMuPDF device is itself a FzDevice2 subclass. Reusing
        # its path/text/shade callbacks exactly reproduces get_bboxlog's paint
        # semantics, including ignore-text and fill-imgmask records.
        def __init__(self):
            super().__init__([], False)
            self.captured = {}
            self.errors = []
            self.default_colorspaces = m.FzDefaultColorspaces(None)
            self.default_colorspace_events = 0
            self.groups = []
            self.mask_definitions = 0
            self.clips = []
            self.tiles = 0
            self.root_group_seen = False
            for name in callbacks:
                getattr(self, 'use_virtual_' + name)()

        def set_default_colorspaces(self, ctx, defaults):
            # Callback pointers are borrowed. Keep the defaults before letting
            # the owning wrapper retain them beyond this callback.
            self.default_colorspaces = m.FzDefaultColorspaces(m.ll_fz_keep_default_colorspaces(defaults))
            self.default_colorspace_events += 1

        def begin_mask(self, ctx, area, luminosity, cs, background, params):
            self.mask_definitions += 1

        def end_mask(self, ctx, function):
            self.mask_definitions -= 1
            self.clips.append('external-soft-mask')

        def begin_group(self, ctx, area, cs, isolated, knockout, blendmode, alpha):
            bounds = tuple(float(getattr(area, key)) for key in ('x0', 'y0', 'x1', 'y1'))
            # pdf-run.c wraps transparent pages in this exact neutral group.
            # The explicit exception is the canonical DeviceRGB full-page
            # wrapper with identical default RGB. Child groups stay unsupported.
            actual_rgb_root = bool(allow_device_rgb_page_wrapper and cs and
                                   int(cs.this) == int(m.fz_device_rgb().m_internal.this) and
                                   int(m.fz_default_rgb(self.default_colorspaces).m_internal.this)
                                   == int(m.fz_device_rgb().m_internal.this))
            neutral_root = (not self.root_group_seen and not self.groups and not self.result
                            and not self.clips and not self.mask_definitions and not self.tiles
                            and (not cs or actual_rgb_root) and isolated == 1 and knockout == 0
                            and blendmode == 0 and alpha == 1
                            and bounds == tuple(sheet.rect))
            self.root_group_seen = True
            self.groups.append({'isolated': isolated, 'knockout': knockout,
                                'blendmode': blendmode, 'alpha': alpha,
                                'neutral_page_wrapper': neutral_root,
                                'explicit_device_rgb_page_wrapper': bool(neutral_root and actual_rgb_root)})

        def end_group(self, ctx):
            if self.groups:
                self.groups.pop()
            else:
                self.errors.append('Unbalanced native group callbacks')

        def clip_image_mask(self, ctx, image, ctm, scissor):
            # Store pointer identity only, never a borrowed image handle.
            self.clips.append({'image_pointer': int(image.this),
                               'transform': [float(getattr(ctm, key)) for key in 'abcdef'],
                               'paint_count': len(self.result)})

        def clip_path(self, *args):
            self.clips.append(None)

        def clip_stroke_path(self, *args):
            self.clips.append(None)

        def clip_text(self, *args):
            self.clips.append(None)

        def clip_stroke_text(self, *args):
            self.clips.append(None)

        def pop_clip(self, ctx):
            if self.clips:
                self.clips.pop()
            else:
                self.errors.append('Unbalanced native clip callbacks')

        def begin_tile(self, *args):
            self.tiles += 1
            return 0

        def end_tile(self, ctx):
            self.tiles -= 1

        def fill_image(self, ctx, image, ctm, alpha, color_params):
            seqno = len(self.result)
            fitz.jm_bbox_fill_image(self, ctx, image, ctm, alpha, color_params)
            if seqno not in selected:
                return
            try:
                self.captured[seqno] = self.capture(image, ctm, alpha, color_params, seqno)
            except Exception as error:
                # Do not let SWIG director exception handling hide a failed
                # callback or truncate the remaining paint-sequence audit.
                self.errors.append(f'Paint {seqno}: {type(error).__name__}: {error}')

        def capture(self, image, ctm, alpha, color_params, seqno):
            if any(not group['neutral_page_wrapper'] for group in self.groups):
                raise PdfImageNativeError('PDF transparency group requires explicit compositing')
            if any(group['explicit_device_rgb_page_wrapper'] for group in self.groups) and (
                    int(m.fz_default_rgb(self.default_colorspaces).m_internal.this)
                    != int(m.fz_device_rgb().m_internal.this)):
                raise PdfImageNativeError('DeviceRGB page wrapper has a changed default RGB')
            if self.tiles:
                raise PdfImageNativeError('PDF pattern tile requires explicit compositing')
            if not math.isfinite(alpha) or alpha != 1:
                raise PdfImageNativeError('PDF image draw alpha requires explicit compositing')
            if color_params.op:
                raise PdfImageNativeError('PDF image overprint requires explicit compositing')
            # pdf_show_image_imp applies /Intent before the device callback.
            # Fail closed if another runtime's callback contract differs.
            if image.has_intent and color_params.ri != image.intent:
                raise PdfImageNativeError('Native callback did not preserve PDF image rendering intent')
            if image.w <= 0 or image.h <= 0 or image.w * image.h > MAX_NATIVE_IMAGE_PIXELS:
                raise PdfImageNativeError('Native image dimensions exceed capture budget')
            transform = [float(getattr(ctm, field)) for field in 'abcdef']
            if not all(math.isfinite(v) for v in transform):
                raise PdfImageNativeError('Non-finite native image placement')
            mask_clips = [clip for clip in self.clips if clip is not None]
            attached_wrapper = (len(mask_clips) == 1 and image.mask
                                and isinstance(mask_clips[0], dict)
                                and mask_clips[0]['image_pointer'] == int(image.mask.this)
                                and mask_clips[0]['transform'] == transform
                                and mask_clips[0]['paint_count'] == seqno)
            if self.mask_definitions or (mask_clips and not attached_wrapper):
                raise PdfImageNativeError('External PDF soft/image mask requires explicit compositing')
            if image.mask and image.use_colorkey:
                # MuPDF encodes /Matte in this combination and unblends during
                # decode, leaving intrinsic alpha. Its separate mask must not
                # be merged again without a dedicated compositing contract.
                raise PdfImageNativeError('PDF image Matte composition is unsupported')

            # This function returns a *kept* pixmap. The wrapper owns that
            # reference; the callback's borrowed image is never retained.
            pix = m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image))
            native = fitz.Pixmap('raw', pix)
            if (native.width, native.height) != (image.w, image.h):
                raise PdfImageNativeError('Native image decoder unexpectedly changed dimensions')
            native_digest = native.digest.hex()
            rgb = m.fz_convert_pixmap(pix, m.fz_device_rgb(), m.FzColorspace(),
                                     self.default_colorspaces, m.FzColorParams(color_params), 1)
            mask_receipt = None
            straight_pixels = None
            if image.mask:
                if image.mask.mask:
                    raise PdfImageNativeError('Nested attached image masks are unsupported')
                if (image.mask.w, image.mask.h) != (image.w, image.h):
                    raise PdfImageNativeError('Attached image mask requires resampling')
                if native.alpha:
                    raise PdfImageNativeError('Combining intrinsic alpha and an attached mask is unsupported')
                maskpix = m.FzPixmap(m.ll_fz_get_unscaled_pixmap_from_image(image.mask))
                mask = fitz.Pixmap('raw', maskpix)
                if (mask.width, mask.height, mask.n) != (native.width, native.height, 1):
                    raise PdfImageNativeError('Attached image mask is not co-registered single-channel data')
                mask_receipt = {'identity': 'actual_fill_image_handle.mask',
                                'width': mask.width, 'height': mask.height,
                                'native_digest': mask.digest.hex(),
                                'native_alpha_channel': bool(mask.alpha),
                                'use_decode': bool(image.mask.use_decode),
                                'xref_used': False}
                if preserve_straight_mask_colors:
                    color = fitz.Pixmap('raw', rgb)
                    if color.alpha or color.n != 3:
                        raise PdfImageNativeError('Straight mask composition requires unassociated RGB samples')
                    straight_pixels = Image.frombytes('RGB', (color.width, color.height), color.samples).convert('RGBA')
                    straight_pixels.putalpha(Image.frombytes('L', (mask.width, mask.height), mask.samples))
                else:
                    rgb = m.fz_new_pixmap_from_color_and_mask(rgb, maskpix)

            output = fitz.Pixmap('raw', rgb)
            # Native RGBA samples are premultiplied. MuPDF's PNG encoder
            # unpremultiplies them; direct Image.frombytes would darken edges.
            encoded = output.tobytes('png')
            with Image.open(io.BytesIO(encoded)) as decoded:
                pixels = straight_pixels if straight_pixels is not None else decoded.convert('RGBA')
            receipt = {
                'identity': 'actual_mupdf_fill_image_callback',
                'paint_seqno': seqno,
                'xref_lookup_used': False,
                'resource_xref_identity_verified': False,
                'native_image_handle_bound_to_paint': True,
                'native_colorspace': native.colorspace.name if native.colorspace else None,
                'native_channels': native.n,
                'native_alpha_channel': bool(native.alpha),
                'native_digest_before_rgb_and_attached_mask': native_digest,
                'image_use_decode': bool(image.use_decode),
                'image_use_colorkey': bool(image.use_colorkey),
                'image_has_intent': bool(image.has_intent),
                'image_intent': int(image.intent) if image.has_intent else None,
                'image_intent_applied_before_callback': bool(image.has_intent),
                'matte_supported': False,
                'color_key_alpha_handled_by_native_decoder': bool(image.use_colorkey),
                'pdf_colorspace_and_decode_handled_by_native_decoder': True,
                'color_params': {key: getattr(color_params, key) for key in ('ri', 'bp', 'op', 'opm')},
                'actual_color_params_used_for_rgb_conversion': True,
                'default_colorspace_events_before_paint': self.default_colorspace_events,
                'actual_default_colorspaces_used_for_rgb_conversion': True,
                'attached_mask': mask_receipt,
                'attached_mask_clip_wrapper_verified': bool(attached_wrapper),
                'neutral_page_transparency_wrapper': bool(self.groups),
                'draw_alpha': alpha,
                'external_group_or_mask_compositing': False,
                'explicit_device_rgb_page_wrapper': any(group['explicit_device_rgb_page_wrapper'] for group in self.groups),
                'straight_attached_mask_rgb_preserved': straight_pixels is not None,
                'mask_composition': 'straight_RGB_plus_co_registered_mask' if straight_pixels is not None else 'native_premultiplied_pixmap_PNG_unpremultiply',
                'straight_rgba_via_native_png_roundtrip': straight_pixels is None,
                'native_rgb_png_sha256': hashlib.sha256(encoded).hexdigest(),
                'pymupdf_version': fitz.VersionBind,
            }
            return {'pixels': pixels, 'transform': transform,
                    'width': native.width, 'height': native.height,
                    'native_digest': native_digest, 'receipt': receipt}

    try:
        device = NativeImages()
        m.fz_run_page(sheet.this, device, m.FzMatrix(), m.FzCookie())
        m.fz_close_device(device)
    except Exception as error:
        raise PdfImageNativeError('Native image device traversal failed: ' + str(error)) from error
    observed = [(record[0], tuple(record[1])) for record in device.result]
    if len(observed) != len(expected):
        raise PdfImageNativeError('Native paint count differs from bboxlog')
    for seqno, (actual, wanted) in enumerate(zip(observed, expected)):
        if actual != wanted:
            raise PdfImageNativeError(f'Native paint type/bbox differs from bboxlog at sequence {seqno}')
    if device.errors:
        raise PdfImageNativeError('; '.join(device.errors[:5]))
    if set(device.captured) != selected:
        raise PdfImageNativeError('Selected image paints were not all captured')
    for record in device.captured.values():
        record['receipt']['full_bboxlog_type_and_bbox_verified_in_order'] = True
        record['receipt']['verified_total_paint_count'] = len(expected)
    return device.captured
