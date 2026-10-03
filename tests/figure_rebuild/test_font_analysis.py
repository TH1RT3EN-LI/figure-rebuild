"""Read-only PDF font identity and baseline contracts using synthetic fixtures."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.figure_rebuild.font_analysis import analyze_pdf, match_font, read_font_registry

try:
    import pymupdf
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTCollection, TTFont
except ImportError:
    pymupdf = None


def _synthetic_font(path):
    builder = FontBuilder(1000, isTTF=True)
    names = ['.notdef', 'A', 'B', 'space']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: 'A', 66: 'B', 32: 'space'})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'space':
            pen.moveTo((40, 0)); pen.lineTo((500, 0)); pen.lineTo((500, 700)); pen.lineTo((40, 700)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': 'Font Analysis Fixture', 'styleName': 'Regular',
                           'fullName': 'Font Analysis Fixture Regular',
                           'psName': 'FontAnalysisFixture-Regular', 'uniqueFontIdentifier': 'font-analysis-fixture'})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800,
                     usWinDescent=200, usWeightClass=400, fsSelection=64)
    builder.setupPost()
    builder.save(path)


@unittest.skipUnless(pymupdf is not None, 'optional requirements-source.txt is not installed')
class FontAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.font = self.root / 'fixture.ttf'
        _synthetic_font(self.font)
        self.face = {'id': 'fixture', 'path': 'fixture.ttf', 'family': 'Font Analysis Fixture',
                     'style': 'regular', 'face_index': 0,
                     'sha256': hashlib.sha256(self.font.read_bytes()).hexdigest()}
        self.registry = self.root / 'registry.json'
        self._registry([self.face])
        self.pdf = self.root / 'fixture.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=300, height=200)
            page.insert_font(fontname='fixture', fontfile=str(self.font))
            page.insert_text((30, 60), 'AB', fontsize=12, fontname='fixture')
            page.insert_text((200, 160), 'AB', fontsize=10, fontname='fixture', rotate=90)
            page.draw_circle((140, 90), 20)
            document.save(self.pdf)

    def _registry(self, entries):
        self.registry.write_text(json.dumps({'fonts': entries}), encoding='utf-8')

    def test_source_baseline_font_identity_and_input_hashes_are_preserved(self):
        before = (self.pdf.read_bytes(), self.font.read_bytes(), self.registry.read_bytes())
        report = analyze_pdf(self.pdf, region=[10, 20, 120, 100], font_registry=self.registry,
                             source_transform=[[4, 0, -5], [0, 4, 7]])
        self.assertEqual(len(report['text_spans']), 1)
        span = report['text_spans'][0]
        self.assertEqual(span['text'], 'AB')
        self.assertEqual(span['font_registry_match']['status'], 'verified_match')
        self.assertEqual(span['font_registry_match']['face']['family'], 'Font Analysis Fixture')
        self.assertEqual(span['font_registry_match']['face']['face_index'], 0)
        self.assertEqual(span['source_baseline_anchor_px'], [115, 247])
        self.assertEqual(span['source_em_horizontal_vector_px'], [48, 0])
        self.assertEqual(span['source_em_vertical_vector_px'], [0, 48])
        self.assertEqual(span['bbox_kind'], 'font_metric_not_ink')
        self.assertEqual(before, (self.pdf.read_bytes(), self.font.read_bytes(), self.registry.read_bytes()))

    def test_rotated_text_keeps_direction_and_em_vectors(self):
        report = analyze_pdf(self.pdf, source_transform=[[2, 0, 0], [0, 3, 0]])
        span = next(item for item in report['text_spans'] if item['line_direction'] == [0.0, -1.0])
        self.assertEqual(span['source_em_horizontal_vector_px'], [0, -30])
        self.assertEqual(span['source_em_vertical_vector_px'], [20, 0])

    def test_unknown_and_duplicate_identities_are_never_guessed(self):
        faces = read_font_registry(self.registry)
        self.assertEqual(match_font('Unrelated-Serif', faces)['status'], 'unresolved')
        self.assertEqual(match_font('ABCDEF+FontAnalysisFixture-Regular', faces)['status'], 'verified_match')
        self.assertEqual(match_font('FontAnalysisFixture-Regular', faces + faces)['status'], 'ambiguous')

    def test_hash_family_and_style_mismatches_block_matches(self):
        for change in ({'sha256': '0' * 64}, {'family': 'Other Family'}, {'style': 'bold'}):
            self._registry([dict(self.face, **change)])
            faces = read_font_registry(self.registry)
            self.assertEqual(faces[0]['status'], 'invalid')
            self.assertEqual(match_font('FontAnalysisFixture-Regular', faces)['status'], 'unresolved')

    def test_ttc_requires_explicit_index(self):
        collection = TTCollection()
        collection.fonts = [TTFont(self.font)]
        collection.save(self.root / 'fixture.ttc')
        collection.fonts[0].close()
        face = dict(self.face, path='fixture.ttc')
        face.pop('sha256'); face.pop('face_index')
        self._registry([face])
        self.assertEqual(read_font_registry(self.registry)[0]['status'], 'invalid')
        face['face_index'] = 0
        self._registry([face])
        self.assertEqual(read_font_registry(self.registry)[0]['status'], 'verified')

    def test_geometry_paths_do_not_create_false_font_claims(self):
        report = analyze_pdf(self.pdf, region=[115, 65, 165, 115])
        self.assertEqual(report['text_spans'], [])
        self.assertGreater(report['region_observations']['vector_drawing_count'], 0)
        self.assertFalse(report['region_observations']['vector_paths_classified_as_glyphs'])
        self.assertEqual(report['region_observations']['glyph_outline_font_status'], 'not_available_from_text_metadata')

    def test_page_region_and_transform_validation(self):
        for kwargs in ({'page': 0}, {'page': 2}, {'region': [10, 0, 1, 5]},
                       {'source_transform': [[1, 2, 0], [2, 4, 0]]},
                       {'source_transform': [[float('nan'), 0, 0], [0, 1, 0]]}):
            with self.assertRaises(ValueError):
                analyze_pdf(self.pdf, **kwargs)


if __name__ == '__main__':
    unittest.main()
