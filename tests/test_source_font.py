"""Hash-bound used-glyph equality, rejection boundaries, and contour budgets."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTCollection, TTFont, newTable

from figure_rebuild.source_font import _BoundedPen, compare_used_glyphs, main


def font_file(path, *, family='Test Source', units=1000, width=500, advance=600,
              bearing=0, characters=(32, 65)):
    builder = FontBuilder(units, isTTF=True)
    cmap = {code: 'char' + str(code) for code in characters}
    names = ['.notdef', *cmap.values()]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'char32':
            pen.moveTo((0, 0)); pen.lineTo((width, 0)); pen.lineTo((width, 600)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (advance, bearing) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': family, 'styleName': 'Regular', 'fullName': family,
                           'uniqueFontIdentifier': family, 'psName': family.replace(' ', '')})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)


def identity(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def cff_file(path, *, width=500):
    builder = FontBuilder(1000, isTTF=False)
    names = ['.notdef', 'A']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: 'A'})
    charstrings = {}
    for name in names:
        pen = T2CharStringPen(600, None)
        pen.moveTo((0, 0)); pen.lineTo((width, 0)); pen.lineTo((width, 600)); pen.closePath()
        charstrings[name] = pen.getCharString()
    builder.setupCFF('TestCFF', {'FullName': 'Test CFF', 'FamilyName': 'Test CFF',
                                'Weight': 'Regular'}, charstrings, {})
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': 'Test CFF', 'styleName': 'Regular',
                           'fullName': 'Test CFF', 'psName': 'TestCFF'})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)


class SourceFontTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.target = self.root/'source.ttf', self.root/'candidate.ttf'
        font_file(self.source)
        font_file(self.target, family='Unrelated Candidate Name')
        self.bindings = [{'unicode': 32, 'source_gid': 1}, {'unicode': 65, 'source_gid': 2}]

    def compare(self, **kwargs):
        return compare_used_glyphs(identity(self.source), identity(self.target), self.bindings, **kwargs)

    def test_exact_selected_glyphs_ignore_font_name_without_family_claim(self):
        result = self.compare()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['glyphs']), 2)
        self.assertTrue(all(g['equal'] for g in result['glyphs']))
        self.assertFalse(result['automatic_semantic_recognition'])
        self.assertIn('caller_required', result['source_native_context_binding'])

    def test_same_name_different_contour_is_rejected(self):
        font_file(self.target, width=499)
        result = self.compare()
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertEqual(result['glyphs'][1]['differences'], ['contours'])

    def test_advance_and_side_bearing_are_both_checked(self):
        for kwargs in ({'advance': 601}, {'bearing': 1}):
            with self.subTest(**kwargs):
                font_file(self.target, **kwargs)
                self.assertIn('horizontal_advance_or_side_bearing', self.compare()['glyphs'][1]['differences'])

    def test_em_scale_mismatch_is_not_normalized_away(self):
        font_file(self.target, units=2048)
        self.assertIn('units_per_em', self.compare()['glyphs'][1]['differences'])

    def test_missing_unicode_and_notdef_cannot_match(self):
        font_file(self.target, characters=(32,))
        self.assertEqual(self.compare()['glyphs'][1]['differences'], ['candidate_unicode_missing_or_notdef'])
        font_file(self.target)
        with TTFont(self.target) as f:
            for table in f['cmap'].tables:
                if table.isUnicode():
                    table.cmap[65] = '.notdef'
            f.save(self.target)
        self.assertEqual(self.compare()['status'], 'MISMATCH')

    def test_invalid_source_gids_unicode_and_duplicate_bindings_fail(self):
        bad = [{'unicode': 65, 'source_gid': 0}, {'unicode': 65, 'source_gid': 999},
               {'unicode': 65, 'source_gid': True}, {'unicode': True, 'source_gid': 2},
               {'unicode': 0xD800, 'source_gid': 2}, {'unicode': 0x110000, 'source_gid': 2},
               {'unicode': 65, 'source_gid': 2, 'font_name': 'guessed'}]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                compare_used_glyphs(identity(self.source), identity(self.target), [row])
        with self.assertRaisesRegex(ValueError, 'Duplicate Unicode'):
            compare_used_glyphs(identity(self.source), identity(self.target), self.bindings * 2)

    def test_hash_bytes_bindings_and_operation_budgets_fail_closed(self):
        for kwargs in ({'max_font_bytes': 1}, {'max_bindings': 1}, {'max_operations': 1},
                       {'max_operations': True}, {'max_component_depth': 0}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                self.compare(**kwargs)
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            compare_used_glyphs({**identity(self.source), 'sha256': '0'*64}, identity(self.target), self.bindings)

    def test_variable_face_and_raw_type1_are_explicitly_unsupported(self):
        with TTFont(self.target) as f:
            f['fvar'] = newTable('fvar'); f['fvar'].axes = []; f['fvar'].instances = []
            f.save(self.target)
        with self.assertRaisesRegex(ValueError, 'static SFNT'):
            self.compare()
        self.target.write_bytes(b'%!PS-AdobeFont-1.0: Test 1.0')
        with self.assertRaisesRegex(ValueError, 'Only SFNT'):
            self.compare()

    def test_static_cff_contours_and_advances_are_compared(self):
        cff_file(self.source)
        cff_file(self.target)
        bindings = [{'unicode': 65, 'source_gid': 1}]
        self.assertEqual(compare_used_glyphs(identity(self.source), identity(self.target), bindings)['status'], 'PASS')
        cff_file(self.target, width=499)
        result = compare_used_glyphs(identity(self.source), identity(self.target), bindings)
        self.assertEqual(result['glyphs'][0]['differences'], ['contours'])

    def test_sfnt_signature_must_agree_with_outline_tables(self):
        with TTFont(self.target) as font:
            font.sfntVersion = 'OTTO'
            font.save(self.target)
        with self.assertRaisesRegex(ValueError, 'signature and outline'):
            self.compare()
        cff_file(self.source)
        cff_file(self.target)
        with TTFont(self.target) as font:
            font.sfntVersion = '\x00\x01\x00\x00'
            font.save(self.target)
        with self.assertRaisesRegex(ValueError, 'signature and outline'):
            compare_used_glyphs(identity(self.source), identity(self.target), [{'unicode': 65, 'source_gid': 1}])

    def test_collection_requires_explicit_real_face_index(self):
        collection = self.root/'collection.ttc'
        with TTFont(self.source) as s, TTFont(self.target) as t:
            fonts = TTCollection(); fonts.fonts = [s, t]; fonts.save(collection)
        with self.assertRaisesRegex(ValueError, 'explicit face_index'):
            compare_used_glyphs(identity(collection), identity(self.target), self.bindings)
        result = compare_used_glyphs({**identity(collection), 'face_index': 1}, identity(self.target), self.bindings)
        self.assertEqual(result['status'], 'PASS')
        with self.assertRaises(ValueError):
            compare_used_glyphs({**identity(collection), 'face_index': 3}, identity(self.target), self.bindings)

    def test_component_cycles_empty_work_and_depth_are_bounded(self):
        class Glyph:
            def __init__(self, children): self.children = children
            def draw(self, pen):
                for name in self.children:
                    pen.addComponent(name, (1, 0, 0, 1, 0, 0))
        glyphs = {'a': Glyph(['b']), 'b': Glyph(['a'])}
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            glyphs['a'].draw(_BoundedPen(glyphs, {'used': 0, 'limit': 100}, 'a', 32))
        glyphs = {'a': Glyph(['b']), 'b': Glyph([])}
        with self.assertRaisesRegex(ValueError, 'depth budget'):
            glyphs['a'].draw(_BoundedPen(glyphs, {'used': 0, 'limit': 100}, 'a', 1))
        glyphs['a'] = Glyph(['b', 'b', 'b'])
        with self.assertRaisesRegex(ValueError, 'operation budget'):
            glyphs['a'].draw(_BoundedPen(glyphs, {'used': 0, 'limit': 2}, 'a', 32))

    def test_nonfinite_contour_cannot_enter_a_proof(self):
        pen = _BoundedPen({}, {'used': 0, 'limit': 100}, 'a', 32)
        with self.assertRaisesRegex(ValueError, 'coordinates'):
            pen.moveTo((float('nan'), 0))

    def test_cli_records_hashes_and_preserves_previous_output(self):
        spec, output = self.root/'spec.json', self.root/'proof.json'
        spec.write_text(json.dumps({'source': identity(self.source), 'candidate': identity(self.target),
                                    'bindings': self.bindings}))
        before = self.source.read_bytes(), self.target.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--spec', str(spec), '--output', str(output)]), 0)
        proof = output.read_bytes()
        self.assertEqual(json.loads(proof)['spec_sha256'], hashlib.sha256(spec.read_bytes()).hexdigest())
        self.assertEqual(before, (self.source.read_bytes(), self.target.read_bytes()))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(['--spec', str(spec), '--output', str(output)])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(output.read_bytes(), proof)


if __name__ == '__main__':
    unittest.main()
