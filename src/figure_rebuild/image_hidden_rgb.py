"""Explicit white RGB at alpha zero; keep all alpha and visible RGBA samples.

Pointwise alpha composition is unchanged. Filtering can depend on hidden RGB,
so the original asset, an explicit receipt and actual application review remain
necessary. This is separate from the existing transparent-border crop policies.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path


POLICY = 'exact-zero-alpha-rgb-white-v1'
MAX_PIXELS = 8_000_000
MAX_DIMENSION = 32768
MAX_INPUT_BYTES = 64_000_000


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Asset path must be relative')
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Asset path escapes its root')
    return result


def _object(obj, root):
    if (not isinstance(obj, dict) or obj.get('kind') != 'image'
            or obj.get('editable') is not False):
        raise ValueError('Expected a declared noneditable image object')
    box = obj.get('box')
    if (not isinstance(box, dict) or set(box) != {'x', 'y', 'width', 'height'}
            or not all(_finite(v) for v in box.values())
            or box['width'] <= 0 or box['height'] <= 0):
        raise ValueError('Image needs a finite positive box')
    path = _path(root, obj.get('path'))
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('Image exceeds the encoded-byte budget')
    data = path.read_bytes()
    if len(data) > MAX_INPUT_BYTES or sha256(data).hexdigest() != obj.get('sha256'):
        raise ValueError('Image exceeds the byte budget or its hash changed')
    return path, data


def _decode(data):
    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        if (im.format != 'PNG' or im.mode != 'RGBA' or im.n_frames != 1
                or max(im.size) > MAX_DIMENSION or im.width * im.height > MAX_PIXELS):
            raise ValueError('Only bounded single-frame RGBA PNG is supported')
        if set(im.info) - {'dpi'}:
            raise ValueError('Image metadata or color profiles require separate preservation')
        metadata = dict(im.info)
        if 'dpi' in metadata:
            dpi = metadata['dpi']
            if (not isinstance(dpi, tuple) or len(dpi) != 2
                    or not all(_finite(v) and v > 0 for v in dpi)):
                raise ValueError('Unsupported pixel density metadata')
        im.load()
        return im.size, metadata, im.tobytes()


def _check(before, after, source, target, original, candidate):
    left = {k: v for k, v in before.items() if k not in {'path', 'sha256'}}
    right = {k: v for k, v in after.items() if k not in {'path', 'sha256'}}
    if not _same(left, right):
        raise ValueError('Derived image changed placement, crop, style or other object properties')
    size, metadata, rgba = _decode(original)
    new_size, new_metadata, new_rgba = _decode(candidate)
    if size != new_size or not _same(metadata, new_metadata):
        raise ValueError('Derived image changed pixel dimensions or metadata')
    histogram = [0] * 256
    changed = 0
    for i in range(0, len(rgba), 4):
        alpha = rgba[i + 3]
        histogram[alpha] += 1
        if new_rgba[i + 3] != alpha:
            raise ValueError('Derived image changed alpha')
        if alpha:
            if rgba[i:i + 4] != new_rgba[i:i + 4]:
                raise ValueError('Derived image changed a positive-alpha RGBA sample')
        else:
            if new_rgba[i:i + 3] != b'\xff\xff\xff':
                raise ValueError('Zero-alpha RGB is not white')
            changed += rgba[i:i + 3] != new_rgba[i:i + 3]
    if not changed or histogram[0] == size[0] * size[1]:
        raise ValueError('Requires changed zero-alpha RGB and nonempty visible support')
    return {
        'schema_version': 1, 'policy': POLICY,
        'source': {'path': str(source), 'sha256': sha256(original).hexdigest(),
                   'decoded_RGBA_sha256': sha256(rgba).hexdigest()},
        'derived': {'path': str(target), 'sha256': sha256(candidate).hexdigest(),
                    'decoded_RGBA_sha256': sha256(new_rgba).hexdigest()},
        'before': deepcopy(before), 'after': deepcopy(after),
        'pixel_dimensions': list(size), 'PNG_metadata_preserved': metadata,
        'source_alpha_values': [v for v, n in enumerate(histogram) if n],
        'zero_alpha_pixels': histogram[0], 'changed_zero_alpha_RGB_pixels': changed,
        'positive_alpha_pixels': len(rgba) // 4 - histogram[0],
        'partial_alpha_pixels': sum(histogram[1:255]),
        'all_alpha_bytes_identical': True, 'all_positive_alpha_RGBA_bytes_identical': True,
        'pointwise_premultiplied_samples_exact': True,
        'pointwise_proof': '(C_after-C_before)*alpha=0 at every decoded pixel; alpha unchanged',
        'pixel_grid_and_all_object_properties_except_asset_path_and_hash_identical': True,
        'source_asset_changed': False, 'source_reference_pixels_used': False,
        'RGB_alpha_filtering_or_PDF_error_bound_proved': False,
        'actual_application_verification_required': True,
        'limits': {'max_pixels': MAX_PIXELS, 'max_dimension': MAX_DIMENSION,
                   'max_input_bytes': MAX_INPUT_BYTES},
    }


def verify_zero_alpha_rgb_image(before, after, asset_root):
    """Read actual source/derived bytes and recheck every RGBA sample and property."""
    root = Path(asset_root).resolve()
    source, original = _object(before, root)
    target, candidate = _object(after, root)
    if source == target or target.suffix.lower() != '.png':
        raise ValueError('Derived PNG must use a separate asset path')
    return _check(before, after, source, target, original, candidate)


def derive_zero_alpha_rgb_image(obj, asset_root, output_relative_path):
    """Write a new PNG; preserve all alpha, positive-alpha RGBA, grid and placement.

    Only zero-alpha RGB becomes white. Partial alpha, holes, disconnected
    support, rotation and crop are retained without quantization or remapping.
    Filtering equivalence is not inferred; the caller saves the receipt and
    reviews/builds the revised manifest explicitly.
    """
    from PIL import Image

    root = Path(asset_root).resolve()
    source, original = _object(obj, root)
    target = _path(root, output_relative_path)
    if source == target or target.exists() or target.suffix.lower() != '.png':
        raise ValueError('Derived PNG must use a new asset path')
    size, metadata, rgba = _decode(original)
    pixels = bytearray(rgba)
    for i in range(0, len(pixels), 4):
        if pixels[i + 3] == 0:
            pixels[i:i + 3] = b'\xff\xff\xff'
    image = Image.frombytes('RGBA', size, bytes(pixels))
    buffer = BytesIO()
    image.save(buffer, format='PNG', **metadata)
    candidate = buffer.getvalue()
    if len(candidate) > MAX_INPUT_BYTES:
        raise ValueError('Derived image exceeds the encoded-byte budget')
    result = deepcopy(obj)
    result.update(path=output_relative_path, sha256=sha256(candidate).hexdigest())
    receipt = _check(obj, result, source, target, original, candidate)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(candidate)
    return result, receipt
