"""User-provided static-font extraction, identity, and glyph coverage checks."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.figure_rebuild.font_prepare import prepare_fonts, validate_profile

try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTCollection, TTFont
except ImportError:
    FontBuilder = None


def make_font(path, style='Regular'):
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
                     usWeightClass=700 if style == 'Bold' else 400,
                     fsSelection=32 if style == 'Bold' else 64)
    builder.font['head'].macStyle = 1 if style == 'Bold' else 0
    builder.setupPost()
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


if __name__ == '__main__':
    unittest.main()
