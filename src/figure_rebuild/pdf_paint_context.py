"""Bind PDF paints to original native mask ownership, without converting them.

``get_drawings()`` includes paints used to *define* a soft mask.  They are not
page objects.  ``end_mask`` starts use of that mask; only its later ``pop_clip``
ends that use.  This module deliberately does not simplify any soft mask.
PyMuPDF is optional and imported only on an explicit inspection call.
"""
import hashlib
import json
import math
from pathlib import Path


class PdfPaintContextError(ValueError):
    """The complete original native paint/context identity was not verified."""


_PAINTS = ('fill_path', 'stroke_path', 'fill_text', 'stroke_text', 'ignore_text',
           'fill_shade', 'fill_image', 'fill_image_mask')
_CONTEXTS = ('begin_mask', 'end_mask', 'begin_group', 'end_group', 'clip_path',
             'clip_stroke_path', 'clip_text', 'clip_stroke_text', 'clip_image_mask',
             'pop_clip', 'begin_tile', 'end_tile')


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PdfPaintContextError(f'{name} must be a positive integer')


def _bboxlog(rows):
    result = []
    for row in rows:
        if (not isinstance(row, (list, tuple)) or len(row) != 2 or
                not isinstance(row[0], str) or
                not isinstance(row[1], (list, tuple)) or len(row[1]) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                    not math.isfinite(v) for v in row[1])):
            raise PdfPaintContextError('Invalid or non-finite paint bboxlog')
        result.append((row[0], tuple(row[1])))
    return result


def inspect_pdf_paint_context(source_pdf, *, page=1, expected_bboxlog=None,
                              max_paints=100000, max_context_events=200000,
                              max_context_depth=128, max_pdf_bytes=134217728):
    """Return every paint's original sequence, role, and context references.

    Page numbers are one-based. The entire native type/bbox sequence must equal
    ``get_bboxlog`` exactly, including mask-definition paints. Optional caller
    evidence must also match that full sequence, never a nearest-bbox subset.

    Roles are ``mask_definition``, ``active_mask_unsupported`` and ``normal``.
    Normal means no active mask, **not** that arbitrary clipping, transparency,
    patterns, geometry or colors can be converted faithfully. The caller must
    still implement those effects. ``page_object_allowed`` additionally rejects
    pattern contexts. Masked paints always remain explicitly unresolved; even
    an apparently trivial alpha-one rectangle is not silently reduced.
    """
    for value, name in ((page, 'page'), (max_paints, 'max_paints'),
                        (max_context_events, 'max_context_events'),
                        (max_context_depth, 'max_context_depth'),
                        (max_pdf_bytes, 'max_pdf_bytes')):
        _positive_int(value, name)
    path = Path(source_pdf).resolve()
    if path.stat().st_size > max_pdf_bytes:
        raise PdfPaintContextError('PDF source exceeds byte budget')
    # Open the exact bytes whose digest is returned, avoiding a path-read race.
    with path.open('rb') as stream:
        data = stream.read(max_pdf_bytes + 1)
    if len(data) > max_pdf_bytes:
        raise PdfPaintContextError('PDF source exceeds byte budget')
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise PdfPaintContextError('PDF paint context requires optional PyMuPDF') from error
    m = getattr(fitz, 'mupdf', None)
    if (m is None or not callable(getattr(fitz, 'JM_new_bbox_device_Device', None)) or
            any(not callable(getattr(fitz, 'jm_bbox_' + k, None)) for k in _PAINTS) or
            any(not callable(getattr(m, k, None)) for k in ('FzMatrix', 'FzCookie', 'fz_run_page',
                                           'fz_close_device', 'll_fz_colorspace_name')) or
            not callable(getattr(getattr(m, 'FzCookie', None), 'set_abort', None))):
        raise PdfPaintContextError('Installed PyMuPDF lacks native context APIs')
    with fitz.open(stream=data, filetype='pdf') as document:
        if page > len(document):
            raise PdfPaintContextError('Page is outside the PDF')
        sheet = document[page - 1]
        if sheet.rotation:
            raise PdfPaintContextError('Native context inspection requires an unrotated page')
        expected = _bboxlog(sheet.get_bboxlog())
        if len(expected) > max_paints:
            raise PdfPaintContextError('PDF paint count exceeds budget')
        if expected_bboxlog is not None and _bboxlog(expected_bboxlog) != expected:
            raise PdfPaintContextError('Caller bboxlog does not match the complete source page')
        cookie = m.FzCookie()

        class ContextDevice(fitz.JM_new_bbox_device_Device):
            def __init__(self):
                super().__init__([], False)
                self.errors, self.events, self.paints = [], [], []
                self.definitions, self.clips, self.groups, self.tiles = [], [], [], []
                self.mask_records, self.clip_records, self.group_records = [], [], []
                for kind in _CONTEXTS:
                    method = getattr(self, 'use_virtual_' + kind, None)
                    if method is None:
                        raise PdfPaintContextError('Missing native callback: ' + kind)
                    method()

            def fail(self, message):
                if len(self.errors) < 16:
                    self.errors.append(message)
                cookie.set_abort()

            def event(self, kind, **details):
                if self.errors:
                    return False
                if len(self.events) >= max_context_events:
                    self.fail('PDF context event count exceeds budget')
                    return False
                self.events.append({'event': kind, 'paint_seqno': len(self.result), **details})
                return True

            def depth(self):
                if sum(map(len, (self.definitions, self.clips, self.groups, self.tiles))) > max_context_depth:
                    self.fail('PDF context depth exceeds budget')

            def rect(self, area):
                values = [float(getattr(area, k)) for k in ('x0', 'y0', 'x1', 'y1')]
                if not all(math.isfinite(v) for v in values):
                    self.fail('Non-finite native context bounds')
                return values

            def begin_mask(self, ctx, area, luminosity, cs, background, params):
                if not self.event('begin_mask', mask_id=len(self.mask_records) + 1):
                    return
                record = {'mask_id': len(self.mask_records) + 1,
                          'begin_paint_seqno': len(self.result),
                          'luminosity': bool(luminosity), 'bbox_pdf_pt': self.rect(area),
                          'colorspace': m.ll_fz_colorspace_name(cs) if cs else None,
                          'parent_definition_ids': [d['mask_id'] for d in self.definitions],
                          'clip_depth_before_definition': len(self.clips),
                          'reduction': 'not_attempted; masked content is unsupported'}
                self.mask_records.append(record)
                self.definitions.append(record)
                self.depth()

            def end_mask(self, *args):
                if not self.event('end_mask'):
                    return
                if not self.definitions:
                    self.fail('Unbalanced end_mask')
                    return
                mask = self.definitions.pop()
                if len(self.clips) != mask['clip_depth_before_definition']:
                    self.fail('Mask definition did not restore its clip stack')
                    return
                mask['end_definition_paint_seqno'] = len(self.result)
                self.push_clip('soft_mask', mask_id=mask['mask_id'])

            def begin_group(self, ctx, area, cs, isolated, knockout, blendmode, alpha):
                if not self.event('begin_group', group_id=len(self.group_records) + 1):
                    return
                if not math.isfinite(alpha):
                    self.fail('Non-finite group opacity')
                    return
                group = {'group_id': len(self.group_records) + 1,
                         'begin_paint_seqno': len(self.result), 'bbox_pdf_pt': self.rect(area),
                         'isolated': bool(isolated), 'knockout': bool(knockout),
                         'blendmode': blendmode, 'alpha': alpha,
                         'colorspace': m.ll_fz_colorspace_name(cs) if cs else None}
                self.group_records.append(group)
                self.groups.append(group)
                self.depth()

            def end_group(self, *args):
                if not self.event('end_group'):
                    return
                if not self.groups:
                    self.fail('Unbalanced end_group')
                    return
                self.groups.pop()['end_paint_seqno'] = len(self.result)

            def push_clip(self, kind, **details):
                record = {'clip_id': len(self.clip_records) + 1, 'kind': kind,
                          'begin_paint_seqno': len(self.result), **details}
                self.clip_records.append(record)
                self.clips.append(record)
                self.depth()

            def clip(self, kind, matrix):
                if not self.event(kind):
                    return
                values = [float(getattr(matrix, k)) for k in 'abcdef']
                if not all(math.isfinite(v) for v in values):
                    self.fail('Non-finite native clip transform')
                    return
                self.push_clip(kind, matrix=values)

            def clip_path(self, ctx, path, evenodd, ctm, scissor):
                self.clip('clip_path', ctm)

            def clip_stroke_path(self, ctx, path, stroke, ctm, scissor):
                self.clip('clip_stroke_path', ctm)

            def clip_text(self, ctx, text, ctm, scissor):
                self.clip('clip_text', ctm)

            def clip_stroke_text(self, ctx, text, stroke, ctm, scissor):
                self.clip('clip_stroke_text', ctm)

            def clip_image_mask(self, ctx, image, ctm, scissor):
                self.clip('clip_image_mask', ctm)

            def pop_clip(self, *args):
                if not self.event('pop_clip'):
                    return
                if not self.clips:
                    self.fail('Unbalanced pop_clip')
                    return
                clip = self.clips.pop()
                clip['end_paint_seqno'] = len(self.result)
                self.events[-1]['clip_id'] = clip['clip_id']
                if clip['kind'] == 'soft_mask':
                    self.mask_records[clip['mask_id'] - 1]['end_use_paint_seqno'] = len(self.result)

            def begin_tile(self, *args):
                if self.event('begin_tile'):
                    self.tiles.append(len(self.events))
                    self.depth()
                return 0

            def end_tile(self, *args):
                if not self.event('end_tile'):
                    return
                if not self.tiles:
                    self.fail('Unbalanced end_tile')
                else:
                    self.tiles.pop()

            def paint(self, kind, ctx, args):
                if self.errors:
                    return
                seq = len(self.result)
                if seq >= max_paints:
                    self.fail('Native paint count exceeds budget')
                    return
                getattr(fitz, 'jm_bbox_' + kind)(self, ctx, *args)
                if len(self.result) != seq + 1:
                    self.fail('Native paint did not produce exactly one bboxlog entry')
                    return
                if seq >= len(expected) or _bboxlog([self.result[-1]])[0] != expected[seq]:
                    self.fail('Native paint type/bbox sequence differs from source bboxlog')
                    return
                definitions = [d['mask_id'] for d in self.definitions]
                masks = [c['mask_id'] for c in self.clips if c['kind'] == 'soft_mask']
                image_masks = [c['clip_id'] for c in self.clips if c['kind'] == 'clip_image_mask']
                role = ('mask_definition' if definitions else
                        'active_mask_unsupported' if masks or image_masks else 'normal')
                unresolved = []
                if role == 'active_mask_unsupported':
                    unresolved.append('Native mask use requires exact supported reconstruction; not reduced')
                if self.tiles:
                    unresolved.append('Pattern context requires separate reconstruction')
                self.paints.append({'source_seqno': seq, 'kind': expected[seq][0],
                                    'bbox_pdf_pt': list(expected[seq][1]), 'role': role,
                                    'mask_definition_ids': definitions, 'active_mask_ids': masks,
                                    'active_image_mask_clip_ids': image_masks,
                                    'clip_ids': [c['clip_id'] for c in self.clips],
                                    'group_ids': [g['group_id'] for g in self.groups],
                                    'pattern_depth': len(self.tiles), 'unresolved': unresolved,
                                    'page_object_allowed': role == 'normal' and not unresolved})

        # Bind each original paint callback; do not derive identities from
        # get_drawings rectangles or resource-xref deduplication.
        for name in _PAINTS:
            def callback(self, ctx, *args, _kind=name):
                try:
                    self.paint(_kind, ctx, args)
                except Exception as error:
                    self.fail(f'Native {_kind} callback failed: {error}')
            setattr(ContextDevice, name, callback)
        # SWIG callback exceptions must abort explicitly rather than relying on
        # a later stack imbalance to expose a failed group/mask/clip callback.
        for name in _CONTEXTS:
            original_callback = getattr(ContextDevice, name)

            def guarded(self, *args, _callback=original_callback, _kind=name):
                try:
                    return _callback(self, *args)
                except Exception as error:
                    self.fail(f'Native {_kind} callback failed: {error}')
                    return 0 if _kind == 'begin_tile' else None

            setattr(ContextDevice, name, guarded)
        device = ContextDevice()
        try:
            m.fz_run_page(sheet.this, device, m.FzMatrix(), cookie)
            m.fz_close_device(device)
        except Exception as error:
            raise PdfPaintContextError('Native context replay failed: ' + str(error)) from error
        if device.errors:
            raise PdfPaintContextError('; '.join(device.errors))
        if _bboxlog(device.result) != expected or len(device.paints) != len(expected):
            raise PdfPaintContextError('Complete native paint sequence was not verified')
        if device.definitions or device.clips or device.groups or device.tiles:
            raise PdfPaintContextError('Native contexts did not return to their original state')
        identity = hashlib.sha256(json.dumps(expected, separators=(',', ':')).encode()).hexdigest()
        return {'schema_version': 1, 'source_pdf': str(path),
                'source_pdf_sha256': hashlib.sha256(data).hexdigest(), 'page': page,
                'pymupdf_version': fitz.VersionBind, 'identity_complete': True,
                'identity_method': 'complete original native paint type/bbox sequence exact equality',
                'bboxlog_sha256': identity, 'paint_count': len(expected),
                'paints': device.paints, 'masks': device.mask_records,
                'clips': device.clip_records, 'groups': device.group_records,
                'events': device.events, 'mask_reductions': [],
                'claim_boundary': 'Paint mask ownership only; normal is not general fidelity approval.'}


def paint_context_record(report, source_seqno, *, expected_kind):
    """Look up one original source sequence, refusing absent/wrong-kind identity.

    Callers must inspect ``page_object_allowed`` before creating any object;
    excluded definitions and unsupported mask uses require distinct receipts.
    """
    if (report.get('identity_complete') is not True or isinstance(source_seqno, bool) or
            not isinstance(source_seqno, int) or not 0 <= source_seqno < len(report['paints'])):
        raise PdfPaintContextError('Unverified or absent original paint sequence')
    record = report['paints'][source_seqno]
    if record['source_seqno'] != source_seqno or record['kind'] != expected_kind:
        raise PdfPaintContextError('Original paint sequence kind differs from caller evidence')
    return record
