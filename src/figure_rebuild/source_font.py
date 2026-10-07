"""Compare explicitly bound SFNT glyphs with a supplied static Unicode face.

This is a used-glyph proof, not font-name matching or PDF text recognition.
The caller must establish source program/GID identity independently. Neither
hinting, shaping, other glyphs, baseline layout nor native app support is proved.
"""
import argparse
import hashlib
import io
import json
import math
import re
from contextlib import ExitStack
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont, TTLibError


def _positive_integer(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + ' must be a positive integer')
    return value


def _font_bytes(definition, max_bytes):
    if not isinstance(definition, dict) or set(definition) - {'path', 'sha256', 'face_index'}:
        raise ValueError('Font definition requires path, SHA256 and optional face_index')
    path, expected = definition.get('path'), definition.get('sha256')
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError('Font path must be absolute')
    if not isinstance(expected, str) or not re.fullmatch('[a-fA-F0-9]{64}', expected):
        raise ValueError('Font definition requires a SHA256 digest')
    index = definition.get('face_index', 0)
    if type(index) is not int or index < 0:
        raise ValueError('Font face_index must be a nonnegative integer')
    with Path(path).open('rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Font byte budget exceeded')
    if hashlib.sha256(data).hexdigest() != expected.lower():
        raise ValueError('Font SHA256 mismatch')
    if data[:4] == b'ttcf' and 'face_index' not in definition:
        raise ValueError('Font collection requires an explicit face_index')
    if data[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'ttcf'):
        raise ValueError('Only SFNT fonts are supported; raw Type1/CFF is not an SFNT')
    if data[:4] != b'ttcf' and index != 0:
        raise ValueError('Standalone SFNT requires face_index=0')
    return data, {'path': path, 'sha256': expected.lower(), 'face_index': index, 'bytes': len(data)}


class _BudgetList(list):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def append(self, record):
        self.budget['used'] += 1
        if self.budget['used'] > self.budget['limit']:
            raise ValueError('Glyph operation budget exceeded')
        for point in record[1]:
            if point is None:  # qCurveTo's implicit last on-curve point
                continue
            if len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v)
                                      or abs(v) > 1e9 for v in point):
                raise ValueError('Invalid or excessive glyph coordinates')
        super().append(record)


class _BoundedPen(DecomposingRecordingPen):
    def __init__(self, glyph_set, budget, root, max_depth):
        super().__init__(glyph_set, skipMissingComponents=False)
        self.value = _BudgetList(budget)
        self.active = [root]
        self.max_depth = max_depth

    def addComponent(self, name, transformation, **kwargs):
        # Empty components still consume work, even if they record no contour.
        self.value.budget['used'] += 1
        if self.value.budget['used'] > self.value.budget['limit']:
            raise ValueError('Glyph operation budget exceeded')
        if name in self.active:
            raise ValueError('Cyclic source/candidate glyph component')
        if len(self.active) >= self.max_depth:
            raise ValueError('Glyph component depth budget exceeded')
        if len(transformation) != 6 or any(type(v) not in (int, float) or not math.isfinite(v)
                                           or abs(v) > 1e9 for v in transformation):
            raise ValueError('Invalid glyph component transformation')
        self.active.append(name)
        try:
            super().addComponent(name, transformation, **kwargs)
        finally:
            self.active.pop()


def _static_font(data, identity, stack):
    try:
        font = stack.enter_context(TTFont(io.BytesIO(data), fontNumber=identity['face_index'],
                                         recalcBBoxes=False, recalcTimestamp=False))
    except TTLibError as error:
        raise ValueError('Invalid SFNT face: ' + str(error)) from error
    if set(font.keys()) & {'fvar', 'gvar', 'CFF2', 'VARC', 'HVAR', 'VVAR', 'MVAR'}:
        raise ValueError('Used-glyph proof requires a static SFNT face')
    if 'head' not in font or 'hmtx' not in font or not ('glyf' in font or 'CFF ' in font):
        raise ValueError('SFNT face lacks supported contours/advance metrics')
    if ('CFF ' in font and (font.sfntVersion != 'OTTO' or 'glyf' in font)
            or 'glyf' in font and font.sfntVersion == 'OTTO'):
        raise ValueError('SFNT signature and outline tables disagree')
    units = font['head'].unitsPerEm
    if type(units) is not int or not 16 <= units <= 16384:
        raise ValueError('Invalid font units per em')
    return font, units


def compare_used_glyphs(source, candidate, bindings, *, max_font_bytes=32 * 1024 * 1024,
                        max_bindings=10000, max_operations=200000, max_component_depth=32):
    """Compare exact decomposed contours and hmtx for explicit UCS/source-GID pairs.

    ``source`` and ``candidate`` are hash-bound file definitions. ``bindings``
    contains unique ``unicode`` scalars and positive ``source_gid`` integers.
    Source cmap is deliberately not guessed: native PDF GID binding belongs to
    the caller. Candidate Unicode cmap is required; .notdef never counts.
    """
    limits = dict(font_bytes=max_font_bytes, bindings=max_bindings,
                  operations=max_operations, component_depth=max_component_depth)
    for label, value in limits.items():
        _positive_integer(value, label + ' budget')
    if not isinstance(bindings, list) or not bindings or len(bindings) > max_bindings:
        raise ValueError('Nonempty glyph bindings must fit the binding budget')
    seen = set()
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {'unicode', 'source_gid'}:
            raise ValueError('Glyph binding requires unicode and source_gid only')
        code, gid = row['unicode'], row['source_gid']
        if type(code) is not int or not 0 <= code <= 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            raise ValueError('Glyph Unicode must be a scalar integer')
        if code in seen:
            raise ValueError('Duplicate Unicode binding')
        seen.add(code)
        _positive_integer(gid, 'Source GID')
    source_data, source_identity = _font_bytes(source, max_font_bytes)
    target_data, target_identity = _font_bytes(candidate, max_font_bytes)
    budget = {'used': 0, 'limit': max_operations}
    rows = []
    with ExitStack() as stack:
        sf, sem = _static_font(source_data, source_identity, stack)
        tf, tem = _static_font(target_data, target_identity, stack)
        order, target_names = sf.getGlyphOrder(), set(tf.getGlyphOrder())
        if any(row['source_gid'] >= len(order) or order[row['source_gid']] == '.notdef'
               for row in bindings):
            raise ValueError('Source GID is missing or .notdef')
        cmap = tf.getBestCmap() or {}
        glyph_sets = [sf.getGlyphSet(), tf.getGlyphSet()]
        caches = [{}, {}]

        def outline(index, font, name):
            if name not in caches[index]:
                pen = _BoundedPen(glyph_sets[index], budget, name, max_component_depth)
                glyph_sets[index][name].draw(pen)
                metrics = font['hmtx'].metrics[name]
                if len(metrics) != 2 or any(type(v) is not int for v in metrics):
                    raise ValueError('Invalid horizontal advance/side-bearing metrics')
                caches[index][name] = (list(pen.value), metrics)
            return caches[index][name]

        for row in bindings:
            code, gid = row['unicode'], row['source_gid']
            source_name, target_name = order[gid], cmap.get(code)
            result = {**row, 'source_glyph': source_name, 'candidate_glyph': target_name,
                      'equal': False}
            if target_name is None or target_name == '.notdef' or target_name not in target_names:
                result['differences'] = ['candidate_unicode_missing_or_notdef']
            else:
                sshape, smetrics = outline(0, sf, source_name)
                tshape, tmetrics = outline(1, tf, target_name)
                differences = []
                if sem != tem:
                    differences.append('units_per_em')
                if sshape != tshape:
                    differences.append('contours')
                if smetrics != tmetrics:
                    differences.append('horizontal_advance_or_side_bearing')
                result.update(equal=not differences, differences=differences)
            rows.append(result)
    return {'schema_version': 1, 'status': 'PASS' if all(row['equal'] for row in rows) else 'MISMATCH',
            'scope': 'explicit_used_sfnt_glyph_contours_and_horizontal_metrics',
            'source': source_identity, 'candidate': target_identity,
            'source_units_per_em': sem, 'candidate_units_per_em': tem,
            'glyphs': rows, 'budgets': limits, 'recorded_operations': budget['used'],
            'source_native_context_binding': 'caller_required_not_established_here',
            'automatic_semantic_recognition': False,
            'limitations': ['No font-name, font-family, unrecorded glyph or hinting equivalence claim.',
                            'Ligature shaping, style roles, baseline layout and native app appearance require separate checks.',
                            'Raw Type1/CFF, variable fonts and .notdef are unsupported.',
                            'Input/operation budgets do not bound all parser memory.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='JSON source, candidate and explicit bindings')
    parser.add_argument('--output', required=True, help='New proof JSON; existing output is never overwritten')
    args = parser.parse_args(argv)
    try:
        data = Path(args.spec).read_bytes()
        spec = json.loads(data)
        if not isinstance(spec, dict) or set(spec) != {'source', 'candidate', 'bindings'}:
            raise ValueError('Spec requires source, candidate and bindings only')
        result = compare_used_glyphs(**spec)
        result['spec_sha256'] = hashlib.sha256(data).hexdigest()
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except (ValueError, OSError, TTLibError, KeyError) as error:
        parser.error(str(error))
    print(result['status'])
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
