"""Strict SVG clip geometry for occurrence-local PDF image clipping.

The output uses the caller's global top-left coordinates. Cubics stay cubic;
quadratics are elevated exactly to cubics. Bounds contain the transformed
control hull and are deliberately conservative rather than flattened bounds.
"""
import hashlib
import math
import re
import xml.etree.ElementTree as ET

from fontTools.pens.basePen import BasePen
from fontTools.svgLib.path import parse_path


class PdfImageClipError(ValueError):
    """A clip cannot be preserved by the supported geometry contract."""


MAX_SOURCE_BYTES = 1_000_000
MAX_PATH_TOKENS = 100_000
MAX_COMMANDS = 20_000
_IDENTITY = (1., 0., 0., 1., 0., 0.)
_NUMBER = r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
_TOKEN = re.compile(r'[A-Za-z]|' + _NUMBER)
_ARITY = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6,
          'S': 4, 'Q': 4, 'T': 2, 'Z': 0}
_SVG = 'http://www.w3.org/2000/svg'


def _tag(node):
    tag = node.tag
    if not isinstance(tag, str):
        raise PdfImageClipError('Non-element clip geometry is unsupported')
    if tag.startswith('{'):
        namespace, tag = tag[1:].split('}', 1)
        if namespace != _SVG:
            raise PdfImageClipError('Unsupported clip element namespace')
    return tag


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise PdfImageClipError('Malformed clip number') from error
    if not math.isfinite(result):
        raise PdfImageClipError('Non-finite clip number')
    return result


def _lex(value, *, commands):
    """Unlike parse_path's tokenizer, reject every unconsumed character."""
    if not isinstance(value, str) or len(value) > MAX_SOURCE_BYTES:
        raise PdfImageClipError('Clip source exceeds budget')
    result, previous_end, previous_number = [], 0, False
    for match in _TOKEN.finditer(value):
        token = match.group()
        number = not token.isalpha()
        gap = value[previous_end:match.start()]
        if gap.strip():
            if gap.strip() != ',' or not previous_number or not number:
                raise PdfImageClipError('Malformed clip token separator')
        if not commands and not number:
            raise PdfImageClipError('Malformed transform or rectangle number')
        if number:
            _finite(token)
        elif token.upper() not in _ARITY:
            raise PdfImageClipError('Unsupported clip path command: ' + token)
        result.append(token)
        if len(result) > MAX_PATH_TOKENS:
            raise PdfImageClipError('Clip token budget exceeded')
        previous_end, previous_number = match.end(), number
    if value[previous_end:].strip():
        raise PdfImageClipError('Malformed trailing clip data')
    return result


def _validate_path(data):
    tokens = _lex(data, commands=True)
    if not tokens or tokens[0].upper() != 'M':
        raise PdfImageClipError('Clip path must start with moveto')
    command, count = None, 0

    def check():
        if command is None:
            raise PdfImageClipError('Clip path is missing a command')
        arity = _ARITY[command.upper()]
        if (arity == 0 and count) or (arity and (not count or count % arity)):
            raise PdfImageClipError('Malformed clip path command coordinates')

    for token in tokens:
        if token.isalpha():
            if command is not None:
                check()
            command, count = token, 0
        else:
            count += 1
    check()


def _multiply(a, b):
    return tuple(_finite(value) for value in (
        a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
        a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
        a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]))


def _transform(value):
    result, end = _IDENTITY, 0
    for match in re.finditer(r'([A-Za-z]+)\s*\(([^()]*)\)', value or ''):
        gap = value[end:match.start()]
        if gap.strip() not in ('', ',') or (not end and gap.strip()):
            raise PdfImageClipError('Malformed clip transform')
        kind = match.group(1)
        args = [_finite(v) for v in _lex(match.group(2), commands=False)]
        if kind == 'matrix' and len(args) == 6:
            matrix = tuple(args)
        elif kind == 'translate' and len(args) in (1, 2):
            matrix = (1., 0., 0., 1., args[0], args[1] if len(args) == 2 else 0.)
        elif kind == 'scale' and len(args) in (1, 2):
            matrix = (args[0], 0., 0., args[-1], 0., 0.)
        else:
            raise PdfImageClipError('Unsupported clip transform: ' + kind)
        result = _multiply(result, matrix)
        end = match.end()
    if (value or '')[end:].strip():
        raise PdfImageClipError('Malformed trailing clip transform')
    return result


def _attributes(node, allowed):
    if set(node.attrib) - (set(allowed) | {'style'}):
        unknown = sorted(set(node.attrib) - (set(allowed) | {'style'}))
        raise PdfImageClipError('Unsupported clip attribute: ' + ', '.join(unknown))
    attributes = dict(node.attrib)
    for declaration in attributes.pop('style', '').split(';'):
        if not declaration.strip():
            continue
        if declaration.count(':') != 1:
            raise PdfImageClipError('Malformed clip style')
        key, value = (part.strip() for part in declaration.split(':', 1))
        if key not in ('clip-rule', 'fill-rule'):
            raise PdfImageClipError('Unsupported clip style: ' + key)
        attributes[key] = value
    for key in ('clip-rule', 'fill-rule'):
        if attributes.get(key, 'inherit') not in ('nonzero', 'evenodd', 'inherit'):
            raise PdfImageClipError('Unsupported ' + key)
    if node.text and node.text.strip():
        raise PdfImageClipError('Text inside clip geometry is unsupported')
    return attributes


class _ClipPen(BasePen):
    def __init__(self, matrix):
        super().__init__(None)
        self.matrix = matrix
        self.commands = []
        self.points = []
        self.open = False

    def _add(self, command, *points):
        if len(self.commands) >= MAX_COMMANDS:
            raise PdfImageClipError('Clip command budget exceeded')
        transformed = []
        a, b, c, d, e, f = self.matrix
        for point in points:
            x, y = (_finite(v) for v in point)
            out = [_finite(a*x+c*y+e), _finite(b*x+d*y+f)]
            transformed.append(out)
            self.points.append(out)
        self.commands.append([command, *transformed])

    def _moveTo(self, point):
        if self.open:
            self._endPath()
        self._add('M', point)
        self.open = True

    def _lineTo(self, point):
        if not self.open:
            raise PdfImageClipError('Clip segment has no open subpath')
        self._add('L', point)

    def _curveToOne(self, p1, p2, p3):
        if not self.open:
            raise PdfImageClipError('Clip curve has no open subpath')
        self._add('C', p1, p2, p3)

    def _closePath(self):
        if not self.open:
            raise PdfImageClipError('Clip closepath has no open subpath')
        self._add('Z')
        self.open = False

    def _endPath(self):
        if self.open:
            self._closePath()


def parse_image_clip(node, matrix):
    """Preserve one SVG clipPath's filled geometry without approximation.

    ``source_xml`` is ElementTree's serialization of the supplied element, not
    a claim to retain the original document's lexical namespace formatting.
    The context matrix is composed before clipPath and child transforms.
    """
    if _tag(node) != 'clipPath':
        raise PdfImageClipError('Expected an SVG clipPath')
    try:
        matrix = tuple(_finite(v) for v in matrix)
    except TypeError as error:
        raise PdfImageClipError('Malformed clip context matrix') from error
    if len(matrix) != 6:
        raise PdfImageClipError('Clip context matrix must contain six numbers')
    source_xml = ET.tostring(node, encoding='unicode')
    source_bytes = source_xml.encode('utf-8')
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise PdfImageClipError('Clip source exceeds budget')
    attrs = _attributes(node, {'id', 'clipPathUnits', 'transform', 'clip-rule', 'fill-rule'})
    if attrs.get('clipPathUnits', 'userSpaceOnUse') != 'userSpaceOnUse':
        raise PdfImageClipError('Object-bounding-box clips are unsupported')
    children = list(node)
    if len(children) != 1:
        raise PdfImageClipError('Clip must contain exactly one path or rectangle')
    shape = children[0]
    if list(shape) or (shape.tail and shape.tail.strip()):
        raise PdfImageClipError('Nested clip geometry is unsupported')
    tag = _tag(shape)
    if tag not in ('path', 'rect'):
        raise PdfImageClipError('Clip must contain a path or rectangle')
    allowed = {'id', 'transform', 'clip-rule', 'fill-rule'}
    allowed |= {'d'} if tag == 'path' else {'x', 'y', 'width', 'height'}
    shape_attrs = _attributes(shape, allowed)
    rule = attrs.get('clip-rule', 'nonzero')
    if rule == 'inherit':
        rule = 'nonzero'
    rule = shape_attrs.get('clip-rule', 'inherit') if shape_attrs.get('clip-rule', 'inherit') != 'inherit' else rule
    matrix = _multiply(_multiply(matrix, _transform(attrs.get('transform'))),
                       _transform(shape_attrs.get('transform')))
    pen = _ClipPen(matrix)
    if tag == 'path':
        data = shape_attrs.get('d', '')
        _validate_path(data)
        try:
            parse_path(data, pen)
            pen.endPath()
        except PdfImageClipError:
            raise
        except (ValueError, TypeError, IndexError, AssertionError, OverflowError) as error:
            raise PdfImageClipError('Malformed clip path') from error
    else:
        numbers = {}
        for key in ('x', 'y', 'width', 'height'):
            tokens = _lex(shape_attrs.get(key, '0'), commands=False)
            if len(tokens) != 1:
                raise PdfImageClipError('Malformed rectangle coordinate')
            numbers[key] = _finite(tokens[0])
        x, y, w, h = (numbers[key] for key in ('x', 'y', 'width', 'height'))
        if w <= 0 or h <= 0:
            raise PdfImageClipError('Degenerate clipping rectangle')
        pen.moveTo((x, y))
        for point in ((x+w, y), (x+w, y+h), (x, y+h)):
            pen.lineTo(point)
        pen.closePath()
    if not pen.points:
        raise PdfImageClipError('Empty clip geometry')
    xs, ys = zip(*pen.points)
    return {'bounds': [min(xs), min(ys), max(xs), max(ys)],
            'commands': pen.commands, 'rule': rule,
            'source_xml_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'source_xml': source_xml, 'geometry_approximated': False}
