"""Bounded canonical previews sampled from the unmodified final native PDF.

The PDF is the separate LibreOffice export of the actual delivered PPTX. This
module never consumes reference pixels or changes the PDF, PPTX or direct PNGs.
"""
import hashlib
from pathlib import Path

POLICY = 'native-final-pdf-mupdf-v1'
MAX_BYTES = 128 * 1024 * 1024
MAX_SURFACE_PIXELS = 16_000_000
MAX_COMBINED_PIXELS = 64_000_000


def _json_numbers(value):
    # The Node build orchestrator writes JSON.stringify output. Integral
    # floating-point values must have the same representation on both sides;
    # preserve every nonintegral coefficient exactly, with no rounding.
    if type(value) is float and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_numbers(item) for item in value]
    return value


def sample_pdf_previews(pdf_path, dimensions):
    """Return deterministic PNG bytes and their independently replayable plan."""
    if (type(dimensions) is not dict or set(dimensions) != {'cx', 'cy'} or
            any(type(v) is not int or not 0 < v <= 2147483647 for v in dimensions.values())):
        raise ValueError('Invalid native PDF preview slide dimensions')
    plans = []
    for scale in (1, 2, 4):
        width, height = (round(dimensions[k] / 9525 * scale) for k in ('cx', 'cy'))
        if min(width, height) < 1 or max(width, height) > 32768 or width * height > MAX_SURFACE_PIXELS:
            raise ValueError('Native PDF preview render surface budget')
        plans.append((scale, width, height))
    if sum(w * h for _, w, h in plans) > MAX_COMBINED_PIXELS:
        raise ValueError('Combined native PDF preview pixel budget')
    path = Path(pdf_path).resolve()
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Native PDF preview byte budget')
    payload = path.read_bytes()
    import pymupdf as fitz
    outputs, records = {}, {}
    with fitz.open(stream=payload, filetype='pdf') as pdf:
        if not pdf.is_pdf or pdf.is_encrypted or pdf.is_repaired or len(pdf) != 1:
            raise ValueError('Native PDF preview requires one unrepaired unencrypted page')
        page = pdf[0]
        if (page.rotation or page.rect.x0 != 0 or page.rect.y0 != 0 or
                abs(page.rect.width - dimensions['cx'] / 12700) > .02 or
                abs(page.rect.height - dimensions['cy'] / 12700) > .02):
            raise ValueError('Native PDF preview page geometry differs from final slide')
        for scale, width, height in plans:
            matrix = [width / page.rect.width, 0., 0., height / page.rect.height, 0., 0.]
            pix = page.get_pixmap(matrix=fitz.Matrix(*matrix), colorspace=fitz.csRGB, alpha=False)
            if (pix.width, pix.height) != (width, height):
                raise ValueError('Native PDF preview changed planned pixel dimensions')
            pix.set_dpi(96 * scale, 96 * scale)
            data = pix.tobytes('png')
            outputs[scale] = data
            records[str(scale)] = {'width': width, 'height': height, 'matrix': matrix,
                                   'alpha': False, 'colorspace': 'DeviceRGB', 'dpi': 96 * scale,
                                   'png_sha256': hashlib.sha256(data).hexdigest()}
        page_rect = list(page.rect)
    return outputs, _json_numbers({'schema_version': 1, 'policy': POLICY,
                     'input_pdf': {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest()},
                     'slide_size_emu': dimensions, 'page_index': 0, 'page_count': 1,
                     'page_rect_points': page_rect, 'renderer': 'PyMuPDF',
                     'renderer_version': fitz.VersionBind, 'mupdf_version': fitz.VersionFitz,
                     'antialias_levels': fitz.TOOLS.show_aa_level(),
                     'scales': records, 'reference_pixels_used': False,
                     'native_delivery_modified': False, 'direct_png_exports_retained': True,
                     'source_pixel_equivalence': False, 'application_playback_verified': False})
