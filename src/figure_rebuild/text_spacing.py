"""Extra character advances for simple editable labels; no source recognition."""
import copy
import math
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'a': A, 'p': P}


def validate_character_spacing(obj):
    if 'character_spacing' not in obj:
        return None
    label = str(obj.get('id', '(unknown)'))
    text, values = obj.get('text'), obj['character_spacing']
    if (obj.get('kind') != 'text' or not isinstance(text, str) or not text.strip() or not 1 <= len(text) <= 2000
            or any(not 0x20 <= ord(c) <= 0x7e for c in text)
            or obj.get('wrap', 'none') != 'none' or obj.get('alignment', 'left') != 'left'):
        raise ValueError('Character spacing requires a single-line left-aligned ASCII text label: ' + label)
    if not isinstance(values, list) or len(values) != len(text) - 1:
        raise ValueError('Character spacing needs one extra advance per character boundary: ' + label)
    for value in values:
        try:
            valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and abs(value) <= 1000)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('Character spacing must contain finite canvas pixel lengths within +/-1000: ' + label)
    return values


def prepare_native_character_spacing(element, obj, scale):
    """Validate without mutation; return the prepared runs and their receipt."""
    values = validate_character_spacing(obj)
    if values is None:
        return None
    label = str(obj.get('id', '(unknown)'))
    if (isinstance(scale, bool) or not isinstance(scale, (int, float))
            or not math.isfinite(scale) or scale <= 0):
        raise ValueError('Character spacing requires a finite positive placement scale: ' + label)
    if element.tag != f'{{{P}}}sp':
        raise ValueError('Character spacing requires native editable text: ' + label)
    paragraphs = element.findall('p:txBody/a:p', NS)
    if len(paragraphs) != 1:
        raise ValueError('Character spacing requires exactly one native paragraph: ' + label)
    paragraph = paragraphs[0]
    if any(c.tag not in {f'{{{A}}}{n}' for n in ('pPr', 'r', 'endParaRPr')} for c in paragraph):
        raise ValueError('Character spacing does not accept native fields or line breaks: ' + label)
    runs = paragraph.findall('a:r', NS)
    if (len(runs) != 1 or len(runs[0].findall('a:t', NS)) != 1
            or runs[0].find('a:t', NS).text != obj['text']
            or len(runs[0].findall('a:rPr', NS)) != 1):
        raise ValueError('Character spacing requires one unchanged uniformly styled authoring run: ' + label)
    props = runs[0].find('a:rPr', NS)
    factor = Decimal(str(scale)) * Decimal('75')
    points = [Decimal(str(v)) * factor for v in values]
    if any(abs(v) > Decimal('400000.5') for v in points):
        raise ValueError('Character spacing exceeds the native point range after placement: ' + label)
    native_values = [int(v.quantize(Decimal('1'), rounding=ROUND_HALF_UP)) for v in points] + [0]
    if any(abs(v) > 400000 for v in native_values):
        raise ValueError('Character spacing exceeds the native point range after placement: ' + label)
    prepared = []
    for char, spacing in zip(obj['text'], native_values):
        run = ET.Element(f'{{{A}}}r'); prop = copy.deepcopy(props)
        prop.set('kern', '400000'); prop.set('spc', str(spacing)); run.append(prop)
        text = ET.SubElement(run, f'{{{A}}}t'); text.text = char
        if char == ' ':
            text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        prepared.append(run)
    receipt = {'id': label, 'extra_advances_canvas_px': values.copy(),
               'native_spacing_hundredths_pt': native_values,
               'native_character_run_count': len(prepared), 'native_text_preserved': True,
               'automatic_kerning_disabled': True, 'native_rounding_unit_canvas_px': 1 / (75 * scale),
               'native_application_positions_verified': False, 'visual_verification_required': True}
    return paragraph, runs[0], prepared, receipt


def apply_prepared_character_spacing(prepared):
    if prepared is None:
        return None
    paragraph, old, runs, receipt = prepared
    index = list(paragraph).index(old); paragraph.remove(old)
    for i, run in enumerate(runs):
        paragraph.insert(index + i, run)
    for prop in paragraph.findall('a:pPr/a:defRPr', NS) + paragraph.findall('a:endParaRPr', NS):
        prop.set('kern', '400000'); prop.set('spc', '0')
    return receipt
