"""Exact candidate recovery without trusted source text or ambiguous guesses."""
import contextlib
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.builder import buildMathTable
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont

from figure_rebuild.font_unicode import main, recover_glyph_unicode, verify_glyph_unicode


def identity(path):
    return {'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def make_font(path, *, source=False, extension=True, script_feature=True, duplicate=False,
              aliases=False, units=1000, width=400, advance=500, bearing=0, math=True):
    names = ['.notdef', 'untrusted'] if source else ['.notdef', 'base', 'script1', 'script2']
    if duplicate:
        names.append('duplicate')
    cmap = {35:'untrusted'} if source else {113:'base'}
    if not script_feature and not source:
        cmap[113]='script1'
    if aliases and not source:
        cmap[107]='base'
    fb=FontBuilder(units,isTTF=True)
    fb.setupGlyphOrder(names); fb.setupCharacterMap(cmap)
    glyphs={}
    for name in names:
        pen=TTGlyphPen(None)
        w,h=(width,580) if name in ['untrusted','script1','duplicate'] else ((340,550) if name=='script2' else (300,600))
        pen.moveTo((0,0));pen.lineTo((w,0));pen.lineTo((w,h));pen.closePath()
        glyphs[name]=pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n:(advance,bearing) for n in names})
    fb.setupHorizontalHeader(ascent=800,descent=-200)
    fb.setupNameTable({'familyName':'Arbitrary Name','styleName':'Regular','fullName':'Arbitrary Name','psName':'ArbitraryName'})
    fb.setupOS2();fb.setupPost()
    if not source and script_feature:
        body='lookup S useExtension { sub base from [script1 script2]; } S;' if extension else 'lookup S { sub base from [script1 script2]; } S;'
        addOpenTypeFeaturesFromString(fb.font,body+' feature ssty {lookup S;} ssty;')
        if math:
            buildMathTable(fb.font)
    fb.save(path)


class FontUnicodeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source.ttf';self.candidate=self.root/'candidate.ttf'
        make_font(self.source,source=True);make_font(self.candidate)

    def recover(self, **kwargs):
        return recover_glyph_unicode(identity(self.source),identity(self.candidate),[1],**kwargs)

    def test_extension_math_script_recovery_ignores_wrong_source_cmap_and_names(self):
        r=self.recover();self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['glyphs'][0]['unicode'],113)
        m=r['glyphs'][0]['candidate_matches'][0]
        self.assertEqual(m['glyph'],'script1')
        self.assertEqual(m['alias_evidence'][0]['lookup_type'],7)
        self.assertEqual(m['alias_evidence'][0]['script_level'],1)
        self.assertFalse(r['original_font_identity_proved'])
        self.assertFalse(r['expected_literals_or_source_ToUnicode_used_for_matching'])

    def test_direct_cmap_and_nonextension_script_lookup(self):
        for options in [{'script_feature':False},{'extension':False}]:
            with self.subTest(options=options):
                make_font(self.candidate,**options)
                r=self.recover();self.assertEqual(r['glyphs'][0]['unicode'],113)
                self.assertEqual(r['status'],'PASS')

    def test_second_script_alternate_keeps_exact_variant_and_level(self):
        with TTFont(self.source) as f:
            pen=TTGlyphPen(None);pen.moveTo((0,0));pen.lineTo((340,0));pen.lineTo((340,550));pen.closePath()
            f['glyf']['untrusted']=pen.glyph();f.save(self.source)
        r=self.recover();self.assertEqual(r['glyphs'][0]['unicode'],113)
        self.assertEqual(r['glyphs'][0]['candidate_matches'][0]['alias_evidence'][0]['script_level'],2)

    def test_single_math_script_substitution_recovers_first_level(self):
        with TTFont(self.candidate) as f:
            del f['GSUB']
            addOpenTypeFeaturesFromString(f,'feature ssty {sub base by script1;} ssty;')
            f.save(self.candidate)
        r=self.recover();self.assertEqual(r['glyphs'][0]['unicode'],113)
        self.assertEqual(r['glyphs'][0]['candidate_matches'][0]['alias_evidence'][0]['effective_lookup_type'],1)

    def test_script_alias_optout_leaves_unencoded_shape_unresolved(self):
        r=self.recover(include_script_alternates=False)
        self.assertEqual(r['status'],'REVIEW');self.assertIsNone(r['glyphs'][0]['unicode'])
        self.assertEqual(r['glyphs'][0]['reason'],'unicode_alias_missing_or_ambiguous')

    def test_equal_duplicate_glyphs_do_not_choose_by_name_or_order(self):
        make_font(self.candidate,duplicate=True)
        r=self.recover();self.assertEqual(r['status'],'REVIEW')
        self.assertEqual(r['glyphs'][0]['reason'],'candidate_glyph_ambiguous')
        self.assertEqual(len(r['glyphs'][0]['candidate_matches']),2)

    def test_multiple_base_unicode_aliases_are_not_guessed(self):
        make_font(self.candidate,aliases=True)
        r=self.recover();self.assertIsNone(r['glyphs'][0]['unicode'])
        self.assertEqual(r['glyphs'][0]['candidate_matches'][0]['unicode_aliases'],[107,113])

    def test_all_contours_units_advance_and_bearing_are_exact(self):
        for options in [{'width':399},{'units':2048},{'advance':501},{'bearing':1}]:
            with self.subTest(options=options):
                make_font(self.source,source=True,**options)
                r=self.recover();self.assertEqual(r['status'],'REVIEW')
                self.assertEqual(r['glyphs'][0]['reason'],'shape_or_metrics_not_found')

    def test_math_table_required_for_ssty_unicode_semantics(self):
        make_font(self.candidate,math=False)
        with self.assertRaisesRegex(ValueError,'MATH'):self.recover()
        self.assertEqual(self.recover(include_script_alternates=False)['status'],'REVIEW')

    def test_unsupported_or_context_dependent_substitution_is_rejected(self):
        with TTFont(self.candidate) as f:
            f['GSUB'].table.LookupList.Lookup[0].LookupFlag=8;f.save(self.candidate)
        with self.assertRaisesRegex(ValueError,'flags'):self.recover()
        make_font(self.candidate)
        with TTFont(self.candidate) as f:
            lookup=f['GSUB'].table.LookupList.Lookup[0]
            lookup.SubTable[0].ExtensionLookupType=7
            # Run against parser objects without serializing a deliberately
            # inconsistent binary table; the source/candidate byte gate is
            # independently covered by the hash and extension positive case.
            from figure_rebuild.font_unicode import _aliases
            with self.assertRaisesRegex(ValueError,'nested'):
                _aliases(f,set(f.getGlyphOrder()),{'mapping_records':0,'mapping_limit':131072},True)

    def test_resource_limits_and_boolean_numeric_arguments_fail_closed(self):
        for kwargs in [{'max_font_bytes':1},{'max_source_gids':0},{'max_candidate_glyphs':1},
                       {'max_operations':1},{'max_controls':1},{'max_mapping_records':1},
                       {'max_controls':True},{'max_component_depth':False},{'include_script_alternates':1}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.recover(**kwargs)

    def test_gids_notdef_duplicates_and_hash_bindings_are_required(self):
        for gids in [[],[0],[True],[1,1],[999],(1,)]:
            with self.subTest(gids=gids),self.assertRaises(ValueError):
                recover_glyph_unicode(identity(self.source),identity(self.candidate),gids)
        with self.assertRaisesRegex(ValueError,'SHA256'):
            recover_glyph_unicode({**identity(self.source),'sha256':'0'*64},identity(self.candidate),[1])

    def test_collection_face_selection_and_static_outline_boundary(self):
        collection=self.root/'candidate.ttc'
        with TTFont(self.source) as s,TTFont(self.candidate) as c:
            t=TTCollection();t.fonts=[s,c];t.save(collection)
        with self.assertRaisesRegex(ValueError,'explicit face_index'):
            recover_glyph_unicode(identity(self.source),identity(collection),[1])
        r=recover_glyph_unicode(identity(self.source),{**identity(collection),'face_index':1},[1])
        self.assertEqual(r['status'],'PASS')
        from test_source_font import cff_file
        cff_file(self.candidate)
        with self.assertRaisesRegex(ValueError,'TrueType'):self.recover()

    def test_full_typed_receipt_replay_rejects_changed_claims_and_budgets(self):
        r=self.recover()
        self.assertEqual(verify_glyph_unicode(identity(self.source),identity(self.candidate),[1],r),r)
        mutations=[lambda v:v.update(original_font_identity_proved=True),
                   lambda v:v['glyphs'][0].update(unicode=35),
                   lambda v:v['glyphs'][0]['candidate_matches'][0]['alias_evidence'][0].update(script_level=2),
                   lambda v:v.update(recorded_controls=float(v['recorded_controls'])),
                   lambda v:v['budgets'].update(operations=500001),
                   lambda v:v.update(extra_proof=True)]
        for mutate in mutations:
            v=deepcopy(r);mutate(v)
            with self.assertRaisesRegex(ValueError,'full replay'):
                verify_glyph_unicode(identity(self.source),identity(self.candidate),[1],v)

    def test_cli_retains_existing_output_and_unresolved_is_nonzero(self):
        spec=self.root/'spec.json';out=self.root/'receipt.json'
        spec.write_text(json.dumps({'source':identity(self.source),'candidate':identity(self.candidate),'source_gids':[1]}))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['--spec',str(spec),'--output',str(out)]),0)
        original=out.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(['--spec',str(spec),'--output',str(out)])
        self.assertEqual(out.read_bytes(),original)
        make_font(self.candidate,duplicate=True)
        spec.write_text(json.dumps({'source':identity(self.source),'candidate':identity(self.candidate),'source_gids':[1]}))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['--spec',str(spec),'--output',str(self.root/'unresolved.json')]),1)

    def test_cli_duplicate_input_keys_are_rejected_before_output(self):
        spec=self.root/'duplicate.json';out=self.root/'receipt.json'
        payload=json.dumps({'source':identity(self.source),'candidate':identity(self.candidate),'source_gids':[1]})
        spec.write_text(payload[:-1]+', "source_gids": [999]}')
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(['--spec',str(spec),'--output',str(out)])
        self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
