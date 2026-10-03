"""Read-only translation and edge diagnostics for reconstructed figures.

Shifts are target-minus-source, in the original comparison canvas pixels.  An
estimated transform is a diagnostic only: no caller image or output geometry is
ever changed.  Edge errors below are measured BEFORE alignment.  NumPy/OpenCV
are optional and are imported only when this module is called.
"""
import math

from PIL import Image


MAX_ANALYSIS_SIDE = 1280
MAX_REGIONS = 256
MAX_ROI_PIXELS = 8_000_000


def _load_vision():
    import cv2
    import numpy as np
    return cv2, np


def _white(image):
    rgba = image.convert('RGBA')
    background = Image.new('RGBA', rgba.size, 'white')
    background.alpha_composite(rgba)
    return background.convert('RGB')


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _edge_metrics(source, target, cv2, np, pixel_scale, tolerance_px=2.0):
    """Symmetric distances use edge pixels, not the area of the white canvas."""
    first, second = source > 0, target > 0
    n_first, n_second = int(first.sum()), int(second.sum())
    common = {'source_edge_pixels': n_first, 'target_edge_pixels': n_second,
              'tolerance_px': tolerance_px, 'measurement': 'unaligned',
              'distance_units': 'comparison_canvas_px'}
    if not n_first or not n_second:
        both = not n_first and not n_second
        return dict(common, status='both_blank' if both else
                    ('missing_edges' if not n_second else 'extra_edges'),
                    precision=1.0 if both else 0.0, recall=1.0 if both else 0.0,
                    f1=1.0 if both else 0.0,
                    symmetric_mean_distance_px=0.0 if both else None,
                    symmetric_p95_distance_px=0.0 if both else None)
    source_dist = cv2.distanceTransform((~first).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    target_dist = cv2.distanceTransform((~second).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    source_to_target = target_dist[first] / pixel_scale
    target_to_source = source_dist[second] / pixel_scale
    recall = float(np.mean(source_to_target <= tolerance_px))
    precision = float(np.mean(target_to_source <= tolerance_px))
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(common, status='measured', precision=precision, recall=recall, f1=f1,
                symmetric_mean_distance_px=float((source_to_target.mean() + target_to_source.mean()) / 2),
                symmetric_p95_distance_px=float(max(np.percentile(source_to_target, 95),
                                                    np.percentile(target_to_source, 95))))


def _validate_regions(regions):
    result = []
    for region in regions or []:
        if not isinstance(region, dict) or not isinstance(region.get('id'), str) or not region['id']:
            raise ValueError('Diagnostic region needs a stable non-empty id')
        box = region.get('box')
        if not isinstance(box, dict) or any(key not in box for key in ('x', 'y', 'width', 'height')):
            raise ValueError('Diagnostic region needs an x/y/width/height box: ' + region['id'])
        if any(isinstance(box[key], bool) or not isinstance(box[key], (int, float)) or
               not math.isfinite(box[key]) for key in box):
            raise ValueError('Diagnostic region box must be finite: ' + region['id'])
        if box['width'] < 0 or box['height'] < 0:
            raise ValueError('Diagnostic region box cannot have negative size: ' + region['id'])
        result.append(region)
    return result


def diagnose_registration(source, target, *, regions=None, max_shift_px=32.0):
    """Return a JSON-safe report; optional dependencies may be unavailable.

    Global translation uses phase correlation, then translation-only ECC.  Both
    need enough edges and confidence; a non-convergent ECC is never reliable.
    A temporary aligned edge mask is used ONLY to gate a transform estimate.
    It is never used for the raw or local geometry errors.  For bounded CPU use,
    long sides above 1280 are downsampled. Local diagnostics cover at most 256
    ROIs and 8 million combined core/context pixels; omitted ROIs are counted.
    """
    regions = _validate_regions(regions)
    if isinstance(max_shift_px, bool) or not isinstance(max_shift_px, (int, float)) or not math.isfinite(max_shift_px) or max_shift_px <= 0:
        raise ValueError('max_shift_px must be finite and positive')
    base = {'status': 'unavailable', 'method': 'phase_correlation_then_translation_ecc',
            'shift_convention': 'target_minus_source', 'shift_units': 'comparison_canvas_px',
            'max_shift_px': float(max_shift_px), 'transform_applied': False,
            'raw_geometry': None, 'regions': [], 'diagnostic_only': True,
            'reliability_scope': 'translation_estimate_only', 'visual_acceptance': 'pending'}
    if source.size != target.size:
        return dict(base, status='failure', reason='different_canvas_sizes',
                    source_size=list(source.size), target_size=list(target.size))
    try:
        cv2, np = _load_vision()
    except (ImportError, OSError) as exc:
        return dict(base, reason='optional_vision_dependency_unavailable', missing_module=getattr(exc, 'name', None),
                    dependency_error=type(exc).__name__, hint='Install the compatible requirements/vision.txt extra for geometry diagnostics.')
    width, height = source.size
    if min(width, height) < 8:
        return dict(base, status='low_confidence', reason='canvas_too_small')
    scale = min(1.0, MAX_ANALYSIS_SIDE / max(width, height))
    size = (max(8, round(width * scale)), max(8, round(height * scale)))
    # Record the actual X/Y ratio; choosing the smallest keeps reported errors
    # conservative when integer resize rounding differs by a fraction of a px.
    pixel_scale = min(size[0] / width, size[1] / height)
    arrays = []
    for image in (source, target):
        rgb = _white(image)
        if rgb.size != size:
            rgb = rgb.resize(size, Image.Resampling.LANCZOS)
        arrays.append(cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY))
    gray_source, gray_target = arrays
    edge_source = cv2.Canny(gray_source, 60, 160)
    edge_target = cv2.Canny(gray_target, 60, 160)
    base.update(analysis_size=list(size), comparison_size=[width, height],
                analysis_scale=pixel_scale,
                raw_geometry=_edge_metrics(edge_source, edge_target, cv2, np, pixel_scale))
    region_pixels = 0
    for region in regions[:MAX_REGIONS]:
        box = region['box']
        # Neighbouring context is intentionally retained: a moved/missing part
        # must not escape a tight source bbox and disappear from the diagnostic.
        margin = max(8.0, min(32.0, float(max_shift_px)))
        sx, sy = size[0] / width, size[1] / height
        x0 = max(0, int(math.floor((box['x'] - margin) * sx)))
        y0 = max(0, int(math.floor((box['y'] - margin) * sy)))
        x1 = min(size[0], int(math.ceil((box['x'] + box['width'] + margin) * sx)))
        y1 = min(size[1], int(math.ceil((box['y'] + box['height'] + margin) * sy)))
        cx0, cy0 = max(0, int(math.floor(box['x']*sx))), max(0, int(math.floor(box['y']*sy)))
        cx1 = min(size[0], int(math.ceil((box['x']+box['width'])*sx)))
        cy1 = min(size[1], int(math.ceil((box['y']+box['height'])*sy)))
        cost = max(0, x1-x0)*max(0, y1-y0) + max(0, cx1-cx0)*max(0, cy1-cy0)
        if region_pixels + cost > MAX_ROI_PIXELS:
            break
        region_pixels += cost
        if x1 <= x0 or y1 <= y0:
            local = {'status': 'outside_canvas'}
        else:
            local = _edge_metrics(edge_source[y0:y1, x0:x1], edge_target[y0:y1, x0:x1], cv2, np, pixel_scale)
        # Report the original footprint separately so a correct neighbouring
        # object in the context window cannot hide a missing local object.
        core = (_edge_metrics(edge_source[cy0:cy1, cx0:cx1], edge_target[cy0:cy1, cx0:cx1], cv2, np, pixel_scale)
                if cx1 > cx0 and cy1 > cy0 else {'status': 'empty_or_outside_canvas'})
        base['regions'].append(dict(id=region['id'], kind=region.get('kind'), source_box=box,
                                    context_margin_px=margin, source_box_geometry=core, **local))
    base['region_count'] = len(regions)
    base['regions_diagnosed'] = len(base['regions'])
    base['regions_omitted_for_budget'] = len(regions) - len(base['regions'])
    base['roi_analysis_pixels'] = region_pixels
    base['roi_pixel_budget'] = MAX_ROI_PIXELS
    edges = base['raw_geometry']
    if min(edges['source_edge_pixels'], edges['target_edge_pixels']) < 24:
        return dict(base, status='low_confidence', reason='insufficient_edges',
                    translation={'dx': None, 'dy': None, 'response': None, 'ecc_correlation': None})
    if np.array_equal(gray_source, gray_target):
        return dict(base, status='reliable', reason='identical_nonblank_pixels',
                    translation={'dx': 0.0, 'dy': 0.0, 'response': 1.0, 'ecc_correlation': 1.0})
    smoothed = [cv2.GaussianBlur(gray.astype(np.float32) / 255, (5, 5), 0.8)
                for gray in arrays]
    window = cv2.createHanningWindow(size, cv2.CV_32F)
    # phaseCorrelate may modify buffers when given a window, so use copies.
    try:
        shift, response = cv2.phaseCorrelate(smoothed[0].copy(), smoothed[1].copy(), window)
    except cv2.error:
        return dict(base, status='failure', reason='phase_correlation_failed')
    dx, dy = _number(shift[0] / (size[0] / width)), _number(shift[1] / (size[1] / height))
    translation = {'dx': dx, 'dy': dy, 'response': _number(response), 'ecc_correlation': None,
                   'phase_dx': dx, 'phase_dy': dy}
    base['translation'] = translation
    if dx is None or dy is None or translation['response'] is None:
        return dict(base, status='failure', reason='nonfinite_phase_result')
    if abs(dx) > max_shift_px or abs(dy) > max_shift_px:
        return dict(base, status='low_confidence', reason='max_shift_exceeded')
    if response < 0.15:
        return dict(base, status='low_confidence', reason='weak_phase_response')
    warp = np.array([[1, 0, shift[0]], [0, 1, shift[1]]], dtype=np.float32)
    try:
        correlation, warp = cv2.findTransformECC(smoothed[0], smoothed[1], warp,
                cv2.MOTION_TRANSLATION, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5),
                None, 5)
    except cv2.error:
        return dict(base, status='low_confidence', reason='ecc_nonconvergence')
    translation.update(dx=_number(warp[0, 2] / (size[0] / width)),
                       dy=_number(warp[1, 2] / (size[1] / height)), ecc_correlation=_number(correlation))
    if any(translation[key] is None for key in ('dx', 'dy', 'ecc_correlation')):
        return dict(base, status='failure', reason='nonfinite_ecc_result')
    if abs(translation['dx']) > max_shift_px or abs(translation['dy']) > max_shift_px:
        return dict(base, status='low_confidence', reason='max_shift_exceeded')
    disagreement = math.hypot(translation['dx']-translation['phase_dx'],
                               translation['dy']-translation['phase_dy'])
    base['phase_ecc_disagreement_px'] = disagreement
    if disagreement > 4.0:
        return dict(base, status='low_confidence', reason='phase_ecc_disagreement')
    overlap = max(0, 1 - abs(translation['dx']) / width) * max(0, 1 - abs(translation['dy']) / height)
    base['estimated_canvas_overlap'] = overlap
    if overlap < 0.85:
        return dict(base, status='low_confidence', reason='low_canvas_overlap')
    # Confidence gate only. Unaligned raw/ROI errors above stay unchanged.
    aligned = cv2.warpAffine(edge_target, warp, size,
                            flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    gate = _edge_metrics(edge_source, aligned, cv2, np, pixel_scale)
    base['translation_confidence_gate'] = {'aligned_edge_f1': gate['f1'],
                                           'used_only_for_transform_confidence': True}
    if correlation < 0.65 or gate['f1'] < 0.4:
        return dict(base, status='low_confidence', reason='insufficient_matching_structure')
    return dict(base, status='reliable', reason='phase_and_ecc_agree')


def manifest_regions(manifest, comparison_size):
    """Project stable object bounds into a cropped/resized comparison canvas.

    This helper validates its required diagnostic fields. Malformed manifests
    are errors, not an unavailable-OpenCV fallback. Path control points form a
    conservative hull; rotation expands image/text boxes around their centre.
    Text needs the resolved renderer box, not an estimated box from its anchor.
    The builder supplies measured text/fitted image bounds in a derived scene.
    """
    if not isinstance(manifest, dict):
        raise ValueError('Diagnostic manifest must be an object')
    canvas = manifest.get('canvas')
    if not isinstance(canvas, dict) or any(not isinstance(canvas.get(key), (int, float)) or
           isinstance(canvas.get(key), bool) or not math.isfinite(canvas[key]) or canvas[key] <= 0
           for key in ('width', 'height')):
        raise ValueError('Diagnostic manifest requires a finite positive canvas')
    objects = manifest.get('objects')
    if not isinstance(objects, list):
        raise ValueError('Diagnostic manifest requires an objects list')
    sx, sy = comparison_size[0] / canvas['width'], comparison_size[1] / canvas['height']
    result, ids = [], set()
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get('id'), str) or not obj['id'] or obj['id'] in ids:
            raise ValueError('Diagnostic objects need unique stable ids')
        ids.add(obj['id'])
        box = obj.get('box')
        if box is None and obj.get('kind') == 'path':
            points = []
            def collect(value):
                if isinstance(value, dict):
                    if 'x' in value and 'y' in value:
                        if any(isinstance(value[k], bool) or not isinstance(value[k], (int, float)) or
                               not math.isfinite(value[k]) for k in ('x', 'y')):
                            raise ValueError('Diagnostic path points must be finite: ' + obj['id'])
                        points.append((value['x'], value['y']))
                    else:
                        for part in value.values(): collect(part)
                elif isinstance(value, list):
                    for part in value: collect(part)
            commands = obj.get('commands')
            if not isinstance(commands, list):
                raise ValueError('Diagnostic path requires commands: ' + obj['id'])
            collect(commands)
            if not points:
                raise ValueError('Diagnostic path has no points: ' + obj['id'])
            xs, ys = zip(*points)
            stroke_width = (obj.get('style') or {}).get('stroke_width', 0)
            if isinstance(stroke_width, bool) or not isinstance(stroke_width, (int, float)) or not math.isfinite(stroke_width) or stroke_width < 0:
                raise ValueError('Diagnostic path stroke width must be finite: ' + obj['id'])
            pad = stroke_width / 2
            box = dict(x=min(xs)-pad, y=min(ys)-pad, width=max(xs)-min(xs)+2*pad, height=max(ys)-min(ys)+2*pad)
        _validate_regions([{'id': obj['id'], 'box': box}])
        angle = obj.get('rotation', 0)
        if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
            raise ValueError('Diagnostic rotation must be finite: ' + obj['id'])
        if angle:
            radians = math.radians(angle)
            bw = abs(box['width'] * math.cos(radians)) + abs(box['height'] * math.sin(radians))
            bh = abs(box['width'] * math.sin(radians)) + abs(box['height'] * math.cos(radians))
            box = dict(x=box['x']+(box['width']-bw)/2, y=box['y']+(box['height']-bh)/2, width=bw, height=bh)
        result.append({'id': obj['id'], 'kind': obj.get('kind'), 'box':
                       dict(x=box['x']*sx, y=box['y']*sy, width=box['width']*sx, height=box['height']*sy)})
    return result
