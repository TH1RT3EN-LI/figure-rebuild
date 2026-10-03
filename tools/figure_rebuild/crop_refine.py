"""Propose a tighter crop inside an explicitly selected raster ROI.

This is geometric assistance, not semantic image recognition. The source and
manifest are never changed. Foreground is measured against a caller-specified
background after alpha compositing; every connected component is retained by
default, including disconnected one-pixel dots and labels.
"""
import hashlib
import io
import math
import os
import re
import warnings
from pathlib import Path


def _vision():
    try:
        import cv2
        import numpy as np
    except (ImportError, OSError) as exc:
        raise ValueError('Crop refinement needs optional OpenCV and NumPy; install '
                         'requirements-vision.txt with python -m pip install -r requirements-vision.txt') from exc
    return cv2, np


def _finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _background(value):
    if value is None:
        return (255, 255, 255)
    if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(channel) is int and 0 <= channel <= 255 for channel in value)):
        return tuple(value)
    raise ValueError('Background must be #RRGGBB or three integer RGB channels in [0, 255]')


def _load(input_path):
    from PIL import Image, UnidentifiedImageError
    path = Path(input_path).resolve()
    try:
        data = path.read_bytes()
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('Crop refinement requires one static raster frame')
                if any(dimension > 20000 for dimension in image.size):
                    raise ValueError('Source dimensions exceed manifest v1 limit of 20000 pixels')
                rgba = image.convert('RGBA')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError('Cannot safely read crop-refinement source: ' + str(exc)) from exc
    return path, hashlib.sha256(data).hexdigest(), rgba


def _composite(rgba, background):
    from PIL import Image
    image = Image.new('RGBA', rgba.size, background + (255,))
    image.alpha_composite(rgba)
    return image.convert('RGB')


def _region(region, size):
    if (not isinstance(region, (list, tuple)) or len(region) != 4
            or not all(_finite(value) for value in region)):
        raise ValueError('Region must contain four finite source-pixel numbers: x y width height')
    x, y, width, height = region
    image_width, image_height = size
    if width <= 0 or height <= 0:
        raise ValueError('Region width and height must be positive')
    if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
        raise ValueError('Region must be fully inside the source image')
    left, top = math.floor(x), math.floor(y)
    right, bottom = math.ceil(x + width), math.ceil(y + height)
    if right <= left or bottom <= top:
        raise ValueError('Region must resolve to at least one source pixel in each dimension')
    return [left, top, right - left, bottom - top]


def refine_crop(input_path, region, *, background=None, tolerance=18, padding=1, min_area=1):
    """Return a JSON-safe crop proposal without modifying any file.

    ``region`` is [x, y, width, height] in source pixels. Finite fractional
    coordinates are accepted only inside the image and rounded outward to pixel
    boundaries (floor left/top, ceil right/bottom). Padding is constrained to
    that selected ROI. Background may be #RRGGBB or an RGB integer triplet.

    A pixel is foreground when any composited RGB channel differs from the
    background by more than ``tolerance``. There is no erosion or morphology.
    ``min_area=1`` preserves all non-background components; larger values are
    explicit caller-requested filtering and must be reviewed. Empty detection
    never proposes a crop or deletion. Crop fractions remove margins from the
    full source and can be used directly as a manifest v1 image object's crop.
    """
    if not _finite(tolerance) or not 0 <= tolerance <= 255:
        raise ValueError('Tolerance must be a finite number in [0, 255]')
    if type(padding) is not int or padding < 0:
        raise ValueError('Padding must be a nonnegative integer in source pixels')
    if type(min_area) is not int or min_area < 1:
        raise ValueError('Minimum component area must be a positive integer')
    color = _background(background)
    path, checksum, rgba = _load(input_path)
    effective = _region(region, rgba.size)
    cv2, np = _vision()
    x, y, width, height = effective
    # Only composite the selected pixels; a small ROI in a large reference
    # should not allocate another full-size RGB background and output.
    rgb = np.array(_composite(rgba.crop((x, y, x + width, y + height)), color))
    # int16 prevents uint8 subtraction from wrapping around near black/white.
    delta = np.abs(rgb.astype(np.int16) - np.array(color, dtype=np.int16))
    foreground = (delta.max(axis=2) > tolerance).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    components = stats[1:]
    retained = components[components[:, cv2.CC_STAT_AREA] >= min_area]
    raw_pixels = int(foreground.sum())
    retained_pixels = int(retained[:, cv2.CC_STAT_AREA].sum())
    notes = ['Proposal requires visual review; background thresholding does not identify semantic content.',
             'Source and manifest are unchanged; coordinates refer to decoded source pixels without EXIF rotation.',
             'Fractional ROI bounds are rounded outward; padding never expands outside the selected ROI.']
    if min_area > 1:
        notes.append('Caller-selected minimum area can exclude tiny dots or labels; inspect excluded content.')
    report = {
        'schema_version': 1, 'operation': 'crop_refine', 'status': 'empty', 'review_required': True,
        'source': {'path': str(path), 'sha256': checksum, 'width': rgba.width, 'height': rgba.height},
        'requested_region': list(region), 'effective_region': effective,
        'detected_content_bbox': None, 'proposed_region': None, 'proposed_crop': None,
        'parameters': {'background': list(color), 'tolerance': tolerance, 'padding': padding, 'min_area': min_area},
        'diagnostics': {'foreground_pixels': raw_pixels, 'retained_foreground_pixels': retained_pixels,
                        'component_count': len(components), 'retained_component_count': len(retained),
                        'excluded_foreground_pixels': raw_pixels - retained_pixels},
        'notes': notes,
    }
    if not len(retained):
        report['notes'].append('No foreground survived the selected threshold/filter; keep the original ROI and review. This is not a deletion recommendation.')
        return report
    left = x + int(retained[:, cv2.CC_STAT_LEFT].min())
    top = y + int(retained[:, cv2.CC_STAT_TOP].min())
    right = x + int((retained[:, cv2.CC_STAT_LEFT] + retained[:, cv2.CC_STAT_WIDTH]).max())
    bottom = y + int((retained[:, cv2.CC_STAT_TOP] + retained[:, cv2.CC_STAT_HEIGHT]).max())
    report['detected_content_bbox'] = [left, top, right - left, bottom - top]
    left, top = max(x, left - padding), max(y, top - padding)
    right, bottom = min(x + width, right + padding), min(y + height, bottom + padding)
    report.update(status='proposal', proposed_region=[left, top, right - left, bottom - top],
                  proposed_crop={'left': left / rgba.width, 'top': top / rgba.height,
                                 'right': (rgba.width - right) / rgba.width,
                                 'bottom': (rgba.height - bottom) / rgba.height})
    return report


def save_crop_preview(input_path, report, output_path):
    """Save a separate PNG with selected (blue) and proposed (green) ROI boxes.

    The report is bound to source SHA256 and dimensions. Existing output files
    are not overwritten, and a preview can never replace the original source.
    Returns the resolved preview path. The preview is review assistance only.
    """
    from PIL import ImageDraw
    path, checksum, rgba = _load(input_path)
    if not isinstance(report, dict) or not isinstance(report.get('source'), dict):
        raise ValueError('Preview requires a crop-refinement report')
    source = report['source']
    if checksum != source.get('sha256') or [rgba.width, rgba.height] != [source.get('width'), source.get('height')]:
        raise ValueError('Preview source no longer matches the crop-refinement report')
    if report.get('status') not in ('proposal', 'empty'):
        raise ValueError('Preview requires a proposal or empty crop-refinement report')
    selected = _region(report.get('effective_region'), rgba.size)
    proposed = report.get('proposed_region')
    if report['status'] == 'proposal':
        proposed = _region(proposed, rgba.size)
    elif proposed is not None:
        raise ValueError('Empty crop-refinement report cannot contain a proposed region')
    output = Path(output_path).resolve()
    if output == path or (output.exists() and os.path.samefile(path, output)):
        raise ValueError('Preview must not replace the source image')
    color = _background(report.get('parameters', {}).get('background'))
    preview = _composite(rgba, color)
    draw = ImageDraw.Draw(preview)
    for bounds, outline in ((selected, '#147dc0'), (proposed, '#2aa846')):
        if bounds is not None:
            x, y, width, height = bounds
            draw.rectangle((x, y, x + width - 1, y + height - 1), outline=outline, width=1)
    try:
        stream = output.open('xb')
    except OSError as exc:
        raise ValueError('Cannot create separate crop preview: ' + str(exc)) from exc
    try:
        with stream:
            preview.save(stream, format='PNG')
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return str(output)
