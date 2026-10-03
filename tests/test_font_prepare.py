"""User-provided static-font extraction, identity, and glyph coverage checks."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from figure_rebuild.font_prepare import prepare_fonts, validate_profile, font_role_for_text

try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTCollection, TTFont
except ImportError:
    FontBuilder = None


def make_font(path, style='Regular'):
    bold, italic = 'Bold' in style, 'Italic' in style
    builder = FontBuilder(1000, isTTF=True)
    cmap = {code: 'char' + str(code) for code in range(32, 127)}
    names = ['.notdef', *cmap.values()]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'char32':
            pen.moveTo((0, 0)); pen.lineTo((500, 0)); pen.lineTo((500, 600)); pen.lineTo((0, 600)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': 'Unit Test Sans', 'styleName': style,
                           'fullName': 'Unit Test Sans ' + style,
                           'uniqueFontIdentifier': 'unit-test-' + style,
                           'psName': 'UnitTestSans-' + style})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200,
                     usWeightClass=700 if bold else 400,
                     fsSelection=(32 if bold else 0) | (1 if italic else 0) | (64 if not bold and not italic else 0))
    builder.font['head'].macStyle = (1 if bold else 0) | (2 if italic else 0)
    builder.setupPost(italicAngle=-12 if italic else 0)
    builder.save(path)


@unittest.skipUnless(FontBuilder is not None, 'fontTools is required by project requirements')
class FontPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.regular, self.bold = self.root / 'regular.ttf', self.root / 'bold.ttf'
        make_font(self.regular)
        make_font(self.bold, 'Bold')
        self.profile = {'family': 'Unit Test Sans',
                        'regular': {'path': str(self.regular), 'face_index': 0,
                                    'sha256': hashlib.sha256(self.regular.read_bytes()).hexdigest()},
                        'bold': {'path': str(self.bold), 'face_index': 0}}

    def test_static_faces_are_saved_and_sources_are_preserved(self):
        original = self.regular.read_bytes()
        audit = prepare_fonts(self.profile, self.root / 'render-fonts',
                              [{'id': 'label', 'kind': 'text', 'text': 'Label'}])
        self.assertEqual(self.regular.read_bytes(), original)
        self.assertEqual([face['role'] for face in audit], ['regular', 'bold'])
        for face in audit:
            with TTFont(face['renderer']) as font:
                self.assertIn(ord('A'), font.getBestCmap())
            self.assertTrue(face['glyph_coverage_checked'])
            self.assertEqual(face['binary_source'], 'user_provided')

    def test_collection_member_is_selected_explicitly(self):
        collection = TTCollection()
        collection.fonts = [TTFont(self.regular), TTFont(self.bold)]
        path = self.root / 'two-faces.ttc'
        collection.save(path)
        for face in collection.fonts:
            face.close()
        self.profile['bold'] = {'path': str(path), 'face_index': 1}
        audit = prepare_fonts(self.profile, self.root / 'collection-render')
        self.assertEqual(audit[1]['face_index'], 1)
        with TTFont(audit[1]['renderer']) as font:
            self.assertTrue(any(name.nameID == 2 and name.toUnicode() == 'Bold' for name in font['name'].names))
        self.profile['bold'].pop('face_index')
        with self.assertRaisesRegex(ValueError, 'explicit registered face_index: bold'):
            validate_profile(self.profile)

    def test_missing_glyph_has_object_id_and_no_output_directory(self):
        destination = self.root / 'missing-render'
        with self.assertRaisesRegex(ValueError, 'unreadable-label: U\\+4E2D'):
            prepare_fonts(self.profile, destination,
                          [{'id': 'unreadable-label', 'kind': 'text', 'text': '中'}])
        self.assertFalse(destination.exists())

    def test_wrong_family_hash_and_relative_path_fail(self):
        self.profile['family'] = 'Different Family'
        with self.assertRaisesRegex(ValueError, 'family does not match'):
            prepare_fonts(self.profile, self.root / 'bad-family')
        self.profile['family'] = 'Unit Test Sans'
        self.profile['regular']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            validate_profile(self.profile)
        self.profile['regular']['path'] = 'relative.ttf'
        with self.assertRaisesRegex(ValueError, 'absolute file'):
            validate_profile(self.profile)

    def test_noncollection_face_index_and_existing_output_fail(self):
        self.profile['regular']['face_index'] = 1
        with self.assertRaisesRegex(ValueError, 'face_index must be zero'):
            prepare_fonts(self.profile, self.root / 'wrong-index')
        self.profile['regular']['face_index'] = 0
        destination = self.root / 'existing'
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            prepare_fonts(self.profile, destination)

    def test_regular_face_cannot_silently_supply_the_bold_role(self):
        self.profile['bold']['path'] = str(self.regular)
        with self.assertRaisesRegex(ValueError, 'weight does not match bold role: 400'):
            prepare_fonts(self.profile, self.root / 'wrong-bold')

    def add_slanted_faces(self):
        for role, style in (('italic', 'Italic'), ('boldItalic', 'Bold Italic')):
            path = self.root / (role + '.ttf')
            make_font(path, style)
            self.profile[role] = {'path': str(path), 'face_index': 0,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_real_four_faces_are_audited_and_selected_per_object(self):
        self.add_slanted_faces()
        objects = [{'id': 'plain', 'kind': 'text', 'text': 'Plain'},
                   {'id': 'weight', 'kind': 'text', 'text': 'Bold', 'bold': True},
                   {'id': 'slope', 'kind': 'text', 'text': 'Italic', 'italic': True},
                   {'id': 'both', 'kind': 'text', 'text': 'Both', 'bold': True, 'italic': True}]
        self.assertEqual([font_role_for_text(item) for item in objects],
                         ['regular', 'bold', 'italic', 'boldItalic'])
        audit = prepare_fonts(self.profile, self.root / 'four', objects)
        self.assertEqual([entry['role'] for entry in audit], ['regular', 'bold', 'italic', 'boldItalic'])
        for face in audit:
            self.assertEqual(face['bold'], face['role'] in ('bold', 'boldItalic'))
            self.assertEqual(face['italic'], face['role'] in ('italic', 'boldItalic'))
            self.assertTrue(face['style_names'])
            self.assertTrue(face['postscript_names'])
            self.assertEqual(face['font_metrics']['units_per_em'], 1000)
            self.assertEqual(face['renderer_sha256'], hashlib.sha256(Path(face['renderer']).read_bytes()).hexdigest())
        self.assertEqual(audit[2]['checked_object_ids'], ['slope'])
        self.assertEqual(audit[3]['checked_object_ids'], ['both'])

    def test_italic_requests_without_registered_real_faces_fail_atomically(self):
        for flags, role in (({'italic': True}, 'italic'), ({'italic': True, 'bold': True}, 'boldItalic')):
            destination = self.root / ('missing-' + role)
            with self.assertRaisesRegex(ValueError, 'Missing real font face Unit Test Sans ' + role + ' for math-label'):
                prepare_fonts(self.profile, destination,
                              [{'id': 'math-label', 'kind': 'text', 'text': 'A', **flags}])
            self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob('.fonts-*')))

    def test_italic_role_rejects_upright_and_bold_italic_rejects_wrong_weight(self):
        self.profile['italic'] = self.profile['regular'].copy()
        with self.assertRaisesRegex(ValueError, 'style does not match italic role'):
            prepare_fonts(self.profile, self.root / 'synthesized')
        self.add_slanted_faces()
        self.profile['boldItalic'] = self.profile['italic'].copy()
        with self.assertRaisesRegex(ValueError, 'weight does not match boldItalic role: 400'):
            prepare_fonts(self.profile, self.root / 'wrong-bold-italic')
        self.assertFalse((self.root / 'wrong-bold-italic').exists())

    def test_upright_role_rejects_a_real_italic_face(self):
        self.add_slanted_faces()
        self.profile['regular'] = self.profile['italic'].copy()
        with self.assertRaisesRegex(ValueError, 'style does not match regular role'):
            prepare_fonts(self.profile, self.root / 'wrong-upright')

    def test_italic_glyph_coverage_uses_italic_face_not_upright(self):
        self.add_slanted_faces()
        italic_path = Path(self.profile['italic']['path'])
        with TTFont(italic_path) as font:
            for subtable in font['cmap'].tables:
                if subtable.isUnicode():
                    subtable.cmap.pop(ord('A'), None)
            font.save(italic_path)
        self.profile['italic']['sha256'] = hashlib.sha256(italic_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'italic-only: U\\+0041'):
            prepare_fonts(self.profile, self.root / 'italic-coverage',
                          [{'id': 'italic-only', 'kind': 'text', 'text': 'A', 'italic': True}])
        # Absence of an unused italic glyph does not force fallback or reject
        # the regular object's existing full coverage.
        audit = prepare_fonts(self.profile, self.root / 'regular-coverage',
                              [{'id': 'regular-only', 'kind': 'text', 'text': 'A'}])
        self.assertEqual(len(audit), 4)

    def test_style_flags_cannot_be_truthy_nonbooleans(self):
        with self.assertRaisesRegex(ValueError, 'italic must be boolean: invalid-style'):
            prepare_fonts(self.profile, self.root / 'invalid-style',
                          [{'id': 'invalid-style', 'kind': 'text', 'text': 'A', 'italic': 'true'}])

    def test_a_text_generator_retains_face_selection_and_glyph_checks(self):
        with self.assertRaisesRegex(ValueError, 'generated-label: U\\+4E2D'):
            prepare_fonts(self.profile, self.root / 'from-generator',
                          iter([{'id': 'generated-label', 'kind': 'text', 'text': '中'}]))

    def test_multiple_families_are_selected_and_unknown_family_rejected(self):
        extra_faces = []
        for role in ('Regular', 'Bold'):
            path = self.root / ('serif-' + role + '.ttf')
            make_font(path, role)
            with TTFont(path) as font:
                for name in font['name'].names:
                    if name.nameID in (1, 4, 6, 16):
                        value = name.toUnicode().replace('Unit Test Sans', 'Unit Test Serif').replace('UnitTestSans', 'UnitTestSerif')
                        name.string = value.encode(name.getEncoding())
                font.save(path)
            extra_faces.append(path)
        self.profile['additional'] = [{'family': 'Unit Test Serif',
            'regular': {'path': str(extra_faces[0]), 'face_index': 0},
            'bold': {'path': str(extra_faces[1]), 'face_index': 0}}]
        objects = [{'id': 'serif-label', 'kind': 'text', 'text': 'Serif', 'font_family': 'Unit Test Serif'}]
        audit = prepare_fonts(self.profile, self.root / 'multi', objects)
        self.assertEqual([face['family'] for face in audit], ['Unit Test Sans']*2+['Unit Test Serif']*2)
        self.assertTrue(all(Path(face['renderer']).is_file() for face in audit))
        objects[0]['font_family'] = 'Unconfigured'
        with self.assertRaisesRegex(ValueError, 'serif-label: Unconfigured'):
            prepare_fonts(self.profile, self.root / 'missing-family', objects)
        self.assertFalse((self.root / 'missing-family').exists())


if __name__ == '__main__':
    unittest.main()
