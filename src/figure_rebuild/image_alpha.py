"""Explicit crops of fully transparent image storage borders.

The binary rectangle policy creates RGB; the RGBA policy keeps every retained
channel. Neither flattens a background, changes visible pixels or infers a clip.
Actual application filtering and exported geometry require separate review.
"""
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path


POLICY = 'exact-opaque-rectangle-crop-v1'
RGBA_POLICY = 'exact-rgba-transparent-border-trim-v1'
MAX_PIXELS = 64_000_000
MAX_DIMENSION = 32768
MAX_INPUT_BYTES = 128_000_000
PLACEMENT_BUDGET = Fraction(1, 1_000_000_000)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def derive_opaque_rect_image(obj, asset_root, output_relative_path):
    """Return a new declared image and receipt; write only a new derived asset.

    Accept an unrotated, uncropped RGBA PNG in a stretch frame, with opaque
    support equal to one solid rectangle and zero alpha everywhere outside.
    Pixel density metadata is preserved. Other metadata/color profiles,
    partial alpha, disconnected support and holes are explicitly rejected.
    The caller saves the receipt and reviews/builds the revised manifest.
    """
    return _derive_image(obj, asset_root, output_relative_path, opaque_rectangle=True)


def derive_rgba_border_image(obj, asset_root, output_relative_path):
    """Trim only zero-alpha outer rows/columns and keep all remaining RGBA.

    Accept the same unrotated, uncropped PNG stretch placement. Partial alpha,
    holes and disconnected support are preserved, including hidden RGB within
    the retained rectangle. A new RGBA asset retains dpi and every nonzero-alpha
    sample. This policy does not establish a solid opaque rectangle or flatten
    alpha. It needs a removable storage border and a nonempty visible support.
    """
    return _derive_image(obj, asset_root, output_relative_path, opaque_rectangle=False)


def _derive_image(obj, asset_root, output_relative_path, *, opaque_rectangle):
    from PIL import Image

    if (not isinstance(obj, dict) or obj.get('kind') != 'image'
            or obj.get('editable') is not False):
        raise ValueError('Expected a declared noneditable image object')
    zero_crop = {'left': 0, 'top': 0, 'right': 0, 'bottom': 0}
    if (obj.get('fit', 'contain') != 'stretch' or obj.get('rotation', 0) != 0
            or obj.get('crop') not in (None, zero_crop)):
        raise ValueError('Rectangular alpha trimming requires unrotated, uncropped stretch placement')
    style = obj.get('style', {})
    if (not isinstance(style, dict) or style.get('fill', 'none') != 'none'
            or style.get('stroke', 'none') != 'none' or style.get('stroke_width', 0) != 0
            or style.get('opacity', 1) != 1):
        raise ValueError('Unsupported image paint')
    root = Path(asset_root).resolve()

    def confined(relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError('Asset path must be relative')
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Asset path escapes its root')
        return path

    source, target = confined(obj.get('path')), confined(output_relative_path)
    if target.suffix.lower() != '.png':
        raise ValueError('Derived asset must have a PNG filename')
    if source == target or target.exists():
        raise ValueError('Derived asset must use a new path')
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('Source image exceeds the encoded-byte budget')
    data = source.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('Source image exceeds the encoded-byte budget')
    digest = sha256(data).hexdigest()
    if digest != obj.get('sha256'):
        raise ValueError('Source image hash changed')
    box = obj.get('box')
    if (not isinstance(box, dict) or set(box) != {'x', 'y', 'width', 'height'}
            or not all(_finite(value) for value in box.values())
            or box['width'] <= 0 or box['height'] <= 0):
        raise ValueError('Image needs a finite positive box')
    with Image.open(BytesIO(data)) as image:
        if (image.format != 'PNG' or image.mode != 'RGBA'
                or image.width * image.height > MAX_PIXELS
                or max(image.size) > MAX_DIMENSION):
            raise ValueError('Only bounded RGBA PNG assets are supported')
        if set(image.info) - {'dpi'}:
            raise ValueError('Image metadata or color profiles require separate preservation')
        metadata = dict(image.info)
        image.load()
        alpha = image.getchannel('A')
        histogram = alpha.histogram()
        bounds = alpha.getbbox()
        if opaque_rectangle:
            if sum(histogram[1:255]) or not histogram[0] or not histogram[255]:
                raise ValueError('Alpha must contain both 0 and 255, with no intermediate values')
            if alpha.crop(bounds).getextrema() != (255, 255):
                raise ValueError('Opaque support must be exactly one solid rectangle')
        elif bounds is None or bounds == (0, 0, image.width, image.height):
            raise ValueError('No removable zero-alpha border around a nonempty support')
        x0, y0, x1, y1 = bounds
        width, height = image.size
        cropped = image.crop(bounds)
        if opaque_rectangle:
            cropped = cropped.convert('RGB')
        buffer = BytesIO()
        cropped.save(buffer, format='PNG', **metadata)
        encoded = buffer.getvalue()
        with Image.open(BytesIO(encoded)) as decoded:
            if (decoded.info != metadata or decoded.mode != cropped.mode
                    or decoded.size != cropped.size or decoded.tobytes() != cropped.tobytes()):
                raise ValueError('Derived pixels or metadata changed')
        rgba_digest = sha256(image.tobytes()).hexdigest()
        cropped_digest = sha256(cropped.tobytes()).hexdigest()
    original = {key: Fraction(value) for key, value in box.items()}
    exact = {
        'x': original['x'] + original['width'] * x0 / width,
        'y': original['y'] + original['height'] * y0 / height,
        'width': original['width'] * (x1 - x0) / width,
        'height': original['height'] * (y1 - y0) / height,
    }
    mapped = {key: float(value) for key, value in exact.items()}
    if not all(math.isfinite(value) for value in mapped.values()):
        raise ValueError('Crop placement overflows finite coordinates')
    rounded = {key: Fraction(value) for key, value in mapped.items()}
    residual = max(abs(rounded[key] - exact[key]) for key in exact)
    # An affine coordinate error reaches its maximum on the two endpoints.
    pixel_error = max(abs(rounded[axis] + side * rounded[extent]
                          - exact[axis] - side * exact[extent])
                      for axis, extent in [('x', 'width'), ('y', 'height')]
                      for side in (0, 1))
    if max(residual, pixel_error) > PLACEMENT_BUDGET:
        raise ValueError('Crop placement exceeds the fixed 1e-9 canvas-coordinate encoding budget')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(encoded)
    result = deepcopy(obj)
    result.update(path=output_relative_path, sha256=sha256(encoded).hexdigest(), box=mapped)
    receipt = {
        'policy': POLICY if opaque_rectangle else RGBA_POLICY,
        'source': {'path': str(source), 'sha256': digest, 'decoded_RGBA_sha256': rgba_digest},
        'derived': {'path': str(target), 'sha256': result['sha256'],
                    ('decoded_RGB_sha256' if opaque_rectangle else 'decoded_RGBA_sha256'): cropped_digest},
        'before': deepcopy(obj), 'after': deepcopy(result),
        'source_pixel_dimensions': [width, height],
        ('opaque_pixel_cell_bounds' if opaque_rectangle else 'nonzero_alpha_pixel_cell_bounds'): list(bounds),
        'source_PNG_metadata_preserved': metadata,
        'source_alpha_values': [i for i, count in enumerate(histogram) if count],
        'opaque_rectangle_proved': opaque_rectangle,
        'only_fully_transparent_pixels_removed': True, 'source_image_changed': False,
        'exact_new_box_rational': {key: [value.numerator, value.denominator]
                                   for key, value in exact.items()},
        'box_encoding_max_abs_error_canvas_px': float(residual),
        'pixel_to_canvas_max_abs_error_canvas_px': float(pixel_error),
        'box_encoding_budget_canvas_px': float(PLACEMENT_BUDGET),
        'source_PDF_or_filtering_equivalence_claimed': False,
        'actual_application_verification_required': True,
    }
    receipt['all_retained_RGB_bytes_identical' if opaque_rectangle
            else 'all_retained_RGBA_bytes_identical'] = True
    return result, receipt
