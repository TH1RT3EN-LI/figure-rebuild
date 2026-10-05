"""Recover unique Unicode proposals from exact static TrueType glyph matches.

Source GIDs and embedded-program identity must be bound by the caller. A supplied
candidate face provides Unicode cmap and optional MATH/ssty aliases; its name is
never an identity proof. This API does not recognize formula layout or relations.
"""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

from fontTools.ttLib import TTLibError

from .source_font import (_BoundedPen, _BudgetList, _font_bytes,
                          _positive_integer, _static_font)


POLICY = 'exact-static-truetype-candidate-unicode-v1'


def _scalar(code):
    return type(code) is int and 0 <= code <= 0x10ffff and not 0xd800 <= code <= 0xdfff


class _Records(_BudgetList):
    def append(self, record):
        self.budget['controls'] += sum(p is not None for p in record[1])
        if self.budget['controls'] > self.budget['control_limit']:
            raise ValueError('Glyph control budget exceeded')
        super().append(record)


def _signature(font, glyph_set, name, budget, depth):
    pen = _BoundedPen(glyph_set, budget, name, depth)
    pen.value = _Records(budget)
    glyph_set[name].draw(pen)
    metrics = font['hmtx'].metrics[name]
    if len(metrics) != 2 or any(type(v) is not int for v in metrics):
        raise ValueError('Invalid horizontal metrics')
    return (font['head'].unitsPerEm,
            tuple((operation, tuple(points)) for operation, points in pen.value),
            tuple(metrics))


def _aliases(font, names, budget, include_scripts):
    aliases = defaultdict(set)
    evidence = defaultdict(list)

    def charge(count=1):
        budget['mapping_records'] += count
        if budget['mapping_records'] > budget['mapping_limit']:
            raise ValueError('Unicode mapping budget exceeded')

    tables = font['cmap'].tables if 'cmap' in font else []
    if len(tables) > 64:
        raise ValueError('Cmap table count budget exceeded')
    for table_index, table in enumerate(tables):
        charge()
        if not table.isUnicode() or table.format == 14:
            continue
        for code, name in table.cmap.items():
            charge()
            if not _scalar(code) or name not in names:
                raise ValueError('Invalid candidate Unicode cmap entry')
            if name == '.notdef':
                continue
            aliases[name].add(code)
            evidence[name].append({'unicode': code, 'kind': 'unicode_cmap',
                                   'table_index': table_index})
    if not include_scripts or 'GSUB' not in font:
        return aliases, evidence
    # Script-style equivalence is admitted only for a declared math face.
    # Arbitrary stylistic, ligature or language substitutions are not followed.
    table = font['GSUB'].table
    features = table.FeatureList.FeatureRecord if table.FeatureList else []
    lookups = table.LookupList.Lookup if table.LookupList else []
    if len(features) > 4096 or len(lookups) > 4096:
        raise ValueError('GSUB feature/lookup count budget exceeded')
    charge(len(features) + len(lookups))
    selected = [(i, f) for i, f in enumerate(features) if f.FeatureTag == 'ssty']
    if selected and 'MATH' not in font:
        raise ValueError('Script-style aliases require the MATH table')
    direct = {name: set(codes) for name, codes in aliases.items()}
    for feature_index, feature in selected:
        for lookup_index in feature.Feature.LookupListIndex:
            charge()
            if type(lookup_index) is not int or not 0 <= lookup_index < len(lookups):
                raise ValueError('Invalid ssty lookup index')
            lookup = lookups[lookup_index]
            if lookup.LookupFlag != 0:
                raise ValueError('Context-dependent ssty lookup flags are unsupported')
            if len(lookup.SubTable) > 4096:
                raise ValueError('GSUB subtable count budget exceeded')
            extension_type = None
            for subtable_index, sub in enumerate(lookup.SubTable):
                charge()
                kind = lookup.LookupType
                if kind == 7:
                    if sub.Format != 1 or sub.ExtensionLookupType == 7:
                        raise ValueError('Invalid or nested ssty extension')
                    kind = sub.ExtensionLookupType
                    if extension_type is not None and extension_type != kind:
                        raise ValueError('Inconsistent ssty extension lookup types')
                    extension_type = kind
                    sub = sub.ExtSubTable
                if kind not in (1, 3):
                    raise ValueError('Only single/alternate ssty substitutions are supported')
                mapping = sub.mapping if kind == 1 else sub.alternates
                for original, derived in mapping.items():
                    charge()
                    variants = [derived] if kind == 1 else derived
                    if original not in names or not isinstance(variants, list) or not 1 <= len(variants) <= 2:
                        raise ValueError('Invalid or unsupported ssty script levels')
                    for level, variant in enumerate(variants, 1):
                        charge()
                        if variant not in names or variant == '.notdef':
                            raise ValueError('Invalid ssty variant glyph')
                        for code in sorted(direct.get(original, ())):
                            charge()
                            aliases[variant].add(code)
                            evidence[variant].append({
                                'unicode': code, 'kind': 'math_ssty',
                                'base_glyph': original, 'script_level': level,
                                'feature_index': feature_index,
                                'lookup_index': lookup_index,
                                'lookup_type': lookup.LookupType,
                                'effective_lookup_type': kind,
                                'subtable_index': subtable_index,
                            })
    return aliases, evidence


def recover_glyph_unicode(source, candidate, source_gids, *, include_script_alternates=True,
                          max_font_bytes=32*1024*1024, max_source_gids=8192,
                          max_candidate_glyphs=16384, max_operations=500000,
                          max_controls=2000000, max_component_depth=32,
                          max_mapping_records=131072):
    """Return proposals only when both candidate glyph and Unicode are unique.

    Exact decomposed pen operations, units-per-em, advance and side bearing are
    required. Source cmap, ToUnicode, glyph names and expected strings are never
    used to choose a match. Full candidate repertoire is searched; ambiguities,
    missing aliases and unmapped shapes remain unresolved.
    """
    limits = dict(font_bytes=max_font_bytes, source_gids=max_source_gids,
                  candidate_glyphs=max_candidate_glyphs, operations=max_operations,
                  controls=max_controls, component_depth=max_component_depth,
                  mapping_records=max_mapping_records)
    for name, value in limits.items():
        _positive_integer(value, name + ' budget')
    if type(include_script_alternates) is not bool:
        raise ValueError('include_script_alternates must be boolean')
    if type(source_gids) is not list or not source_gids or len(source_gids) > max_source_gids:
        raise ValueError('Nonempty source GIDs must fit the selection budget')
    if any(type(gid) is not int or gid <= 0 for gid in source_gids) or len(set(source_gids)) != len(source_gids):
        raise ValueError('Source GIDs must be unique positive integers')
    source_data, source_identity = _font_bytes(source, max_font_bytes)
    candidate_data, candidate_identity = _font_bytes(candidate, max_font_bytes)
    budget = {'used': 0, 'limit': max_operations, 'controls': 0,
              'control_limit': max_controls, 'mapping_records': 0,
              'mapping_limit': max_mapping_records}
    rows = []
    with ExitStack() as stack:
        sf, sem = _static_font(source_data, source_identity, stack)
        tf, tem = _static_font(candidate_data, candidate_identity, stack)
        if 'glyf' not in sf or 'glyf' not in tf:
            raise ValueError('Unicode recovery currently requires static TrueType glyf faces')
        source_order, order = sf.getGlyphOrder(), tf.getGlyphOrder()
        if len(order) > max_candidate_glyphs:
            raise ValueError('Candidate glyph count budget exceeded')
        if any(gid >= len(source_order) or source_order[gid] == '.notdef' for gid in source_gids):
            raise ValueError('Source GID is absent or .notdef')
        names = set(order)
        if len(names) != len(order):
            raise ValueError('Duplicate candidate glyph names')
        aliases, alias_evidence = _aliases(tf, names, budget, include_script_alternates)
        source_set, target_set = sf.getGlyphSet(), tf.getGlyphSet()
        selected = {gid: _signature(sf, source_set, source_order[gid], budget, max_component_depth)
                    for gid in source_gids}
        needed = set(selected.values())
        matches = defaultdict(list)
        # .notdef never establishes a Unicode proposal, but still consumes work.
        for name in order:
            shape = _signature(tf, target_set, name, budget, max_component_depth)
            if name != '.notdef' and shape in needed:
                matches[shape].append(name)
        for gid in source_gids:
            candidates = matches[selected[gid]]
            codes = sorted({code for name in candidates for code in aliases.get(name, ())})
            unique = len(candidates) == len(codes) == 1
            rows.append({'source_gid': gid, 'source_glyph': source_order[gid],
                         'status': 'UNIQUE' if unique else 'UNRESOLVED',
                         'unicode': codes[0] if unique else None,
                         'candidate_matches': [{'glyph': name, 'unicode_aliases': sorted(aliases.get(name, ())),
                                                'alias_evidence': alias_evidence.get(name, [])}
                                               for name in candidates],
                         'reason': ('exact_unique_glyph_and_unicode' if unique else
                                    'shape_or_metrics_not_found' if not candidates else
                                    'candidate_glyph_ambiguous' if len(candidates) != 1 else
                                    'unicode_alias_missing_or_ambiguous')})
    return {'schema_version': 1, 'policy': POLICY,
            'status': 'PASS' if all(r['status'] == 'UNIQUE' for r in rows) else 'REVIEW',
            'source': source_identity, 'candidate': candidate_identity,
            'source_units_per_em': sem, 'candidate_units_per_em': tem,
            'include_script_alternates': include_script_alternates,
            'candidate_glyphs_searched': len(order), 'glyphs': rows, 'budgets': limits,
            'recorded_operations': budget['used'], 'recorded_controls': budget['controls'],
            'mapping_records': budget['mapping_records'],
            'source_native_context_binding': 'caller_required_not_established_here',
            'original_font_identity_proved': False, 'hinting_equivalence_proved': False,
            'formula_layout_or_relation_recognition_performed': False,
            'expected_literals_or_source_ToUnicode_used_for_matching': False,
            'limitations': [
                'Proposals are conditional on the caller-supplied hash-bound candidate face.',
                'Source native font handle/GID binding, layout and actual output need independent validation.',
                'Only exact static TrueType contours and horizontal metrics are matched; no tolerance is used.',
                'MATH/ssty aliases preserve a candidate base Unicode but do not infer subscript/superscript position.',
                'No font-family, hinting, arbitrary shaping, formula meaning, connection or pixel-equivalence proof.',
                'Byte and emitted-operation/control budgets do not bound all font-parser memory.',
            ]}


def _same_data(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_data(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_data(a, b) for a, b in zip(left, right))
    return left == right


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON input key')
        result[key] = value
    return result


def verify_glyph_unicode(source, candidate, source_gids, receipt, **options):
    """Recompute from current hash-bound programs and compare the whole typed receipt."""
    actual = recover_glyph_unicode(source, candidate, source_gids, **options)
    if not _same_data(actual, receipt):
        raise ValueError('Unicode recovery receipt differs from independent full replay')
    return actual


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='Hash-bound source/candidate definitions and source_gids')
    parser.add_argument('--output', required=True, help='New receipt JSON; existing output is retained')
    args = parser.parse_args(argv)
    try:
        with Path(args.spec).open('rb') as stream:
            raw = stream.read(2097153)
        if len(raw) > 2097152:
            raise ValueError('Spec byte budget exceeded')
        spec = json.loads(raw, object_pairs_hook=_unique_object)
        if type(spec) is not dict or set(spec) - {'source', 'candidate', 'source_gids', 'include_script_alternates'} or not {'source', 'candidate', 'source_gids'} <= set(spec):
            raise ValueError('Spec requires source, candidate and source_gids, with optional include_script_alternates')
        result = recover_glyph_unicode(**spec)
        with Path(args.output).open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except (ValueError, OSError, TTLibError, KeyError, TypeError, AttributeError, IndexError) as error:
        parser.error(str(error))
    print(result['status'])
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
