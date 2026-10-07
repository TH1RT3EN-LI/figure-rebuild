"""Explicitly sample one original PDF stroke with its actual native clips.

This produces a transparent raster asset, never an editable path or an exact
stroke-to-cubic clipping claim. PyMuPDF is imported only on an explicit call.
"""
import hashlib
import math
from pathlib import Path
import re
from .pdf_image_native import PdfImageNativeError
from .pdf_image_render import _render_native_pdf_paint
from .pdf_paint_context import inspect_pdf_paint_context

class PdfStrokeSamplingError(ValueError):
    """The source stroke or supported sampling contract was not verified."""

def sample_pdf_stroke(source_pdf, *, source_pdf_sha256, page, native_sequence, region, scale=2, native_sampling_scale=8, allow_native_rgb_group_sampling=False):
    """Return a sampled original stroke on the complete transparent ROI frame.

    Selection is the actual one-based page and complete native paint sequence,
    not a resource, SVG index, closest bbox or inferred scientific relation.
    The caller must separately establish which source occurrence it needs.
    Original path/stroke/context callbacks are forwarded unchanged; unrelated
    paints are suppressed. No generic group decomposition equivalence is claimed.

    The source must match its declared SHA256. Uniform positive scale, an ROI
    aligned to that source pixel grid, and sampling 4 or 8 are supported. Full
    ROI storage avoids assuming a diagnostic paint bbox is a support proof.
    Native rendering retains the original clips. Sampling pitch is not an RGB,
    alpha or filtering error bound. Unsupported masks/patterns/groups fail.
    Explicit RGB-group sampling admits the existing bounded native group
    renderer, retaining its full-figure review and unverified group-split
    receipts. The default remains strict; no vector or group equivalence follows.
    """
    if type(allow_native_rgb_group_sampling) is not bool:
        raise PdfStrokeSamplingError('RGB group sampling flag must be boolean')
    if type(source_pdf_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', source_pdf_sha256):
        raise PdfStrokeSamplingError('Source PDF SHA256 must be declared')
    if type(page) is not int or page < 1 or type(native_sequence) is not int or (native_sequence < 0):
        raise PdfStrokeSamplingError('Page and native sequence must be nonnegative integers; page starts at 1')
    if type(scale) not in (int, float) or not 0 < scale <= 64 or not math.isfinite(scale):
        raise PdfStrokeSamplingError('Uniform source scale must be finite, positive and at most 64')
    if type(native_sampling_scale) is not int or native_sampling_scale not in (4, 8):
        raise PdfStrokeSamplingError('Native sampling scale must be integer 4 or 8')
    if type(region) not in (list, tuple) or len(region) != 4 or any((type(v) not in (int, float) or abs(v) > 2 ** 24 or not math.isfinite(v) for v in region)) or (region[2] <= region[0]) or (region[3] <= region[1]):
        raise PdfStrokeSamplingError('ROI must have four finite ordered PDF coordinates')
    if any((not float(v * scale).is_integer() for v in region)):
        raise PdfStrokeSamplingError('ROI must align to the declared source pixel grid')
    width, height = ((region[2] - region[0]) * scale, (region[3] - region[1]) * scale)
    if width * height * native_sampling_scale ** 2 > 64000000 or max(width, height) * native_sampling_scale > 32768:
        raise PdfStrokeSamplingError('Complete ROI exceeds the native sampling pixel budget')
    try:
        path = Path(source_pdf).resolve()
        if not path.is_file() or path.stat().st_size > 134217728:
            raise PdfStrokeSamplingError('Source must be a regular PDF within 128 MiB')
        with path.open('rb') as stream:
            data = stream.read(134217729)
    except OSError as error:
        raise PdfStrokeSamplingError('Source PDF could not be read: ' + str(error)) from error
    if len(data) > 134217728 or hashlib.sha256(data).hexdigest() != source_pdf_sha256:
        raise PdfStrokeSamplingError('Source PDF bytes differ from declared SHA256 or byte budget')
    try:
        import pymupdf as fitz
    except ImportError as error:
        raise PdfStrokeSamplingError('Stroke sampling requires optional PyMuPDF') from error
    if fitz.VersionFitz != '1.28.2':
        raise PdfStrokeSamplingError('Native stroke forwarding provider has not been verified')
    try:
        with fitz.open(stream=data, filetype='pdf') as document:
            if page > len(document):
                raise PdfStrokeSamplingError('Source page is outside the PDF')
            sheet = document[page - 1]
            page_rect = list(sheet.rect)
            if sheet.rotation or any((region[0] < page_rect[0], region[1] < page_rect[1], region[2] > page_rect[2], region[3] > page_rect[3])):
                raise PdfStrokeSamplingError('ROI must be inside an unrotated source page')
            bboxlog = sheet.get_bboxlog()
        context = inspect_pdf_paint_context(path, page=page, expected_bboxlog=bboxlog)
        if context['source_pdf_sha256'] != source_pdf_sha256:
            raise PdfStrokeSamplingError('Context was not inspected from the same frozen PDF bytes')
        if native_sequence >= context['paint_count']:
            raise PdfStrokeSamplingError('Native sequence is outside the complete source inventory')
        selected = context['paints'][native_sequence]
        if selected['kind'] != 'stroke-path' or selected['role'] != 'normal' or (not selected['page_object_allowed']):
            raise PdfStrokeSamplingError('Selected occurrence is not a supported ordinary native stroke')
        transform = [scale, 0, 0, scale, -scale * region[0], -scale * region[1]]
        with fitz.open(stream=data, filetype='pdf') as pristine:
            result = _render_native_pdf_paint(pristine[page - 1], bboxlog, native_sequence, source_transform=transform, source_bounds=[0, 0, width, height], user_clip_pdf=list(region), native_sampling_scale=native_sampling_scale, allow_native_rgb_group_sampling=allow_native_rgb_group_sampling, _paint_kind='stroke-path')
        receipt = result['receipt']
        if receipt['stroke_paints_forwarded'] != 1 or receipt['independent_other_paints_forwarded'] != 0:
            raise PdfStrokeSamplingError('Stroke forwarding count is not exactly the selected occurrence')
        original_clips = [context['clips'][cid - 1] for cid in selected['clip_ids']]
        forwarded = receipt['clip_chain']
        if len(original_clips) != len(forwarded) or any((a['kind'] != b['kind'] or a.get('matrix') != b.get('matrix') or a['begin_paint_seqno'] != b['paint_count'] or (not b['forwarded']) for a, b in zip(original_clips, forwarded))):
            raise PdfStrokeSamplingError('Original active clip identities were not preserved')
        if allow_native_rgb_group_sampling:
            if (receipt.get('allow_native_rgb_group_sampling') is not True or
                    receipt.get('required_full_figure_visual_review') is not True or
                    'rgb_alpha_error_bound' not in receipt or receipt['rgb_alpha_error_bound'] is not None or
                    receipt.get('exact_group_decomposition_claimed') is not False or
                    type(receipt.get('shared_group_split_unverified')) is not bool):
                raise PdfStrokeSamplingError('RGB group sampling uncertainty receipt is incomplete')
            original_groups = [context['groups'][gid - 1] for gid in selected['group_ids']]
            groups = receipt['groups']
            fields = ('group_id', 'begin_paint_seqno', 'bbox_pdf_pt', 'isolated', 'knockout', 'blendmode', 'alpha')
            if len(original_groups) != len(groups) or any(
                    any(a[key] != b[key] for key in fields) or
                    a['end_paint_seqno'] != b['end_paint_seqno_exclusive'] or not b['forwarded']
                    for a, b in zip(original_groups, groups)):
                raise PdfStrokeSamplingError('Original active group identities were not preserved')
            receipt.update(original_group_records=original_groups,
                           original_active_group_identity_verified=True)
    except PdfStrokeSamplingError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, PdfImageNativeError) as error:
        raise PdfStrokeSamplingError('Native source stroke sampling failed: ' + str(error)) from error
    receipt.update(source_pdf_sha256=source_pdf_sha256, source_pdf_page=page, original_native_context=selected, original_clip_records=original_clips, original_context_sha256=context['context_sha256'], full_native_identity_verified=True, source_occurrence_selection='caller_declared_native_sequence', declared_source_region_pdf_pt=list(region), uniform_source_scale=scale, complete_roi_storage=True, geometry_conversion_performed=False, semantic_recognition_performed=False, visual_acceptance='pending')
    return dict(asset_bytes=result['asset_bytes'], asset_sha256=hashlib.sha256(result['asset_bytes']).hexdigest(), box=dict(x=0, y=0, width=width, height=height), crop=dict(left=0, top=0, right=0, bottom=0), fit='stretch', editable=False, source_kind='sampled_original_native_stroke', provenance=receipt)
