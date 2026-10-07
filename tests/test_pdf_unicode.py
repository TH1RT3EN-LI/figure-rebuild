"""Real PDF font resources, metadata-only recovery and rejection boundaries."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from figure_rebuild.pdf_unicode import _parse_cmap, _require_native_api, repair_pdf_unicode

try:
    import pymupdf as F
except ImportError:
    F = None


def identity(path):
    return {'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def cmap(rows, *, declared=None):
    return ('''/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo <</Registry (Adobe) /Ordering (UCS) /Supplement 0>> def
/CMapName /Adobe-Identity-UCS def /CMapType 2 def
1 begincodespacerange <0000> <FFFF> endcodespacerange
''' + str(len(rows) if declared is None else declared) + ' beginbfchar\n' +
            '\n'.join(f'<{cid:04X}> <{value}>' for cid,value in rows) +
            '\nendbfchar endcmap CMapName currentdict /CMap defineresource pop end end\n').encode()


def make_font(path):
    names = ['.notdef','mathx','letterB','unusedC']
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({0x1d465:'mathx',ord('B'):'letterB',ord('C'):'unusedC'})
    glyphs = {}
    for index,name in enumerate(names):
        pen = TTGlyphPen(None)
        pen.moveTo((0,0));pen.lineTo((100+index*80,0));pen.lineTo((40+index*20,450+index*50));pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name:(500+index*30,0) for index,name in enumerate(names)})
    builder.setupHorizontalHeader(ascent=800,descent=-200)
    builder.setupNameTable({'familyName':'Unicode Probe','styleName':'Regular','fullName':'Unicode Probe','psName':'UnicodeProbe'})
    builder.setupOS2();builder.setupPost();builder.save(path)


class PdfCmapGrammarTests(unittest.TestCase):
    def test_utf16be_nonbmp_is_one_scalar_and_multiscalar_is_refused(self):
        _,rows=_parse_cmap(cmap([(1,'D835DC65')]),4096,16)
        self.assertEqual(rows[0]['unicode'],0x1d465)
        for encoded in ('D835','DC65','00410042','D8350041','0000'):
            with self.subTest(encoded=encoded),self.assertRaises(ValueError):
                _parse_cmap(cmap([(1,encoded)]),4096,16)

    def test_duplicate_count_ranges_and_budgets_are_refused(self):
        inputs=[cmap([(1,'FFFD'),(1,'0042')]),cmap([(1,'FFFD')],declared=2),
                cmap([(1,'FFFD')]).replace(b'beginbfchar',b'beginbfrange'),
                cmap([(1,'FFFD')])+b'/Evil usecmap']
        for data in inputs:
            with self.subTest(data=data),self.assertRaises(ValueError):
                _parse_cmap(data,4096,16)
        with self.assertRaises(ValueError):_parse_cmap(cmap([(1,'FFFD')]),4,16)
        with self.assertRaises(ValueError):_parse_cmap(cmap([(1,'FFFD'),(2,'0042')]),4096,1)

    def test_missing_native_capability_is_explicit(self):
        with self.assertRaisesRegex(ValueError,'native API is unsupported'):
            _require_native_api(object())


@unittest.skipUnless(F is not None, 'optional source dependencies are required for actual PDF rendering')
class PdfUnicodeRepairTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.font=self.root/'candidate.ttf';make_font(self.font)
        self.source=self.root/'source.pdf';self.output=self.root/'restored.pdf'
        with F.open() as document:
            page=document.new_page(width=120,height=80)
            self.font_xref=page.insert_font(fontname='Probe',fontfile=str(self.font))
            page.insert_text((10,30),chr(0x1d465)+'B',fontname='Probe',fontsize=12)
            self.cmap_xref=int(document.xref_get_key(self.font_xref,'ToUnicode')[1].split()[0])
            descendant=document.xref_get_key(self.font_xref,'DescendantFonts')[1]
            self.descendant=int(descendant.strip('[]').split()[0])
            # PyMuPDF omits the optional identity declaration; this fixture
            # models WPS's explicitly declared, closed CIDFontType2 scope.
            document.xref_set_key(self.descendant,'CIDToGIDMap','/Identity')
            descriptor=int(document.xref_get_key(self.descendant,'FontDescriptor')[1].split()[0])
            self.program_xref=int(document.xref_get_key(descriptor,'FontFile2')[1].split()[0])
            self.program_sha=hashlib.sha256(document.xref_stream(self.program_xref)).hexdigest()
            document.update_stream(self.cmap_xref,cmap([(1,'FFFD'),(2,'0042')]))
            document.save(self.source)

    def faces(self):
        return [dict(font_xref=self.font_xref,embedded_program_sha256=self.program_sha,candidate=identity(self.font))]

    def repair(self, **options):
        return repair_pdf_unicode(identity(self.source),self.output,self.faces(),**options)

    def mutate(self, callback):
        with F.open(self.source) as document:
            callback(document)
            document.saveIncr()

    def test_actual_nonbmp_recovery_preserves_programs_paints_and_original_bytes(self):
        original=self.source.read_bytes()
        result=self.repair()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(self.source.read_bytes(),original)
        self.assertTrue(self.output.read_bytes().startswith(original))
        self.assertEqual(result['changed_xref_objects'],[self.cmap_xref])
        self.assertEqual(result['fonts'][0]['recovered'],[{'cid':1,'gid':1,'unicode':0x1d465,'utf16be':'D835DC65'}])
        item=result['restored_painted_items'][0]
        self.assertEqual((item['character_code_hex'],item['cid'],item['gid'],item['unicode']),('0001',1,1,0x1d465))
        self.assertEqual(len(result['restored_painted_items']),1)
        with F.open(self.source) as prior,F.open(self.output) as after:
            self.assertIn(b'<0001> <D835DC65>',after.xref_stream(self.cmap_xref))
            self.assertIn(b'<0002> <0042>',after.xref_stream(self.cmap_xref))
            self.assertEqual(prior.xref_stream(self.program_xref),after.xref_stream(self.program_xref))
            self.assertIn(chr(0x1d465),after[0].get_text())
            for scale in (1,2,4):
                self.assertEqual(prior[0].get_pixmap(matrix=F.Matrix(scale,scale),alpha=True).samples,
                                 after[0].get_pixmap(matrix=F.Matrix(scale,scale),alpha=True).samples)
        self.assertFalse(result['invisible_overlay_added'])
        self.assertFalse(result['font_permissions_changed'])

    def test_pdf_and_embedded_program_hashes_fail_without_output(self):
        with self.assertRaisesRegex(ValueError,'source SHA256'):
            repair_pdf_unicode({'path':str(self.source),'sha256':'0'*64},self.output,self.faces())
        faces=self.faces();faces[0]['embedded_program_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'program SHA256'):
            repair_pdf_unicode(identity(self.source),self.output,faces)
        self.assertFalse(self.output.exists())

    def test_candidate_hash_and_collection_index_fail_without_output(self):
        faces=self.faces();faces[0]['candidate']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'Font SHA256'):
            repair_pdf_unicode(identity(self.source),self.output,faces)
        faces=self.faces();faces[0]['candidate']['face_index']=1
        with self.assertRaisesRegex(ValueError,'Standalone SFNT'):
            repair_pdf_unicode(identity(self.source),self.output,faces)
        self.assertFalse(self.output.exists())

    def test_candidate_alias_is_not_guessed(self):
        with TTFont(self.font) as font:
            for table in font['cmap'].tables:
                if table.isUnicode():table.cmap[0xe000]='mathx'
            font.save(self.font)
        with self.assertRaisesRegex(ValueError,'exact unique'):
            self.repair()
        self.assertFalse(self.output.exists())

    def test_changed_metrics_do_not_match_the_same_shape(self):
        with TTFont(self.font) as font:
            font['hmtx'].metrics['mathx']=(531,0);font.save(self.font)
        with self.assertRaisesRegex(ValueError,'exact unique'):
            self.repair()
        self.assertFalse(self.output.exists())

    def test_arbitrary_encoding_and_cid_to_gid_stream_are_refused(self):
        self.mutate(lambda doc:doc.xref_set_key(self.font_xref,'Encoding','/Identity-V'))
        with self.assertRaisesRegex(ValueError,'Identity-H'):self.repair()
        self.assertFalse(self.output.exists())
        self.mutate(lambda doc:(doc.xref_set_key(self.font_xref,'Encoding','/Identity-H'),
                                doc.xref_set_key(self.descendant,'CIDToGIDMap',f'{self.program_xref} 0 R')))
        with self.assertRaisesRegex(ValueError,'Identity CID-to-GID'):self.repair()
        self.assertFalse(self.output.exists())

    def test_shared_metadata_reference_is_refused_even_outside_a_font(self):
        # MuPDF resolves references with a mismatched generation without
        # setting is_repaired. They still alias the stream being changed.
        for generation in (0,1):
            self.mutate(lambda doc:doc.xref_set_key(doc.pdf_catalog(),'UnrelatedReference',f'{self.cmap_xref} {generation} R'))
            with self.subTest(generation=generation),self.assertRaisesRegex(ValueError,'Shared ToUnicode'):self.repair()
            self.assertFalse(self.output.exists())

    def test_stream_dictionary_usecmap_is_refused_even_with_closed_body(self):
        for value in ('/Identity-H',f'{self.cmap_xref} 0 R'):
            self.mutate(lambda doc:doc.xref_set_key(self.cmap_xref,'UseCMap',value))
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'Inherited UseCMap'):self.repair()
            self.assertFalse(self.output.exists())

    def test_unused_replacement_cid_is_not_silently_repaired(self):
        self.mutate(lambda doc:doc.update_stream(self.cmap_xref,cmap([(1,'FFFD'),(2,'0042'),(3,'FFFD')])))
        with self.assertRaisesRegex(ValueError,'visible native paint binding'):self.repair()
        self.assertFalse(self.output.exists())

    def test_null_and_already_good_mapping_are_not_repair_targets(self):
        self.mutate(lambda doc:doc.update_stream(self.cmap_xref,cmap([(1,'D835DC65'),(2,'0042')])))
        with self.assertRaisesRegex(ValueError,'replacement-character'):self.repair()
        self.mutate(lambda doc:doc.xref_set_key(self.font_xref,'ToUnicode','null'))
        with self.assertRaisesRegex(ValueError,'ToUnicode'):self.repair()
        self.assertFalse(self.output.exists())

    def test_actualtext_and_invisible_or_marked_text_are_outside_scope(self):
        with F.open(self.source) as document:
            original_catalog=document.xref_object(document.pdf_catalog())
        self.mutate(lambda doc:doc.xref_set_key(doc.pdf_catalog(),'ActualText','(replacement)'))
        with self.assertRaisesRegex(ValueError,'ActualText'):self.repair()
        self.mutate(lambda doc:doc.update_object(doc.pdf_catalog(),original_catalog))
        def marked(doc):
            content=doc[0].get_contents()[0]
            doc.update_stream(content,b'/Span BMC\n'+doc.xref_stream(content)+b'\nEMC')
        self.mutate(marked)
        with self.assertRaisesRegex(ValueError,'Marked content'):self.repair()
        self.assertFalse(self.output.exists())

    def test_actual_invisible_text_callback_is_refused_without_output(self):
        def invisible(doc):
            content=doc[0].get_contents()[0]
            original=doc.xref_stream(content)
            self.assertIn(b'BT',original)
            doc.update_stream(content,original.replace(b'BT',b'BT\n3 Tr',1))
        self.mutate(invisible)
        with self.assertRaisesRegex(ValueError,'Invisible text'):self.repair()
        self.assertFalse(self.output.exists())

    def test_work_and_render_budgets_leave_no_output(self):
        for options in ({'max_pdf_bytes':1},{'max_font_bytes':1},{'max_native_glyphs':1},
                        {'max_render_pixels':1},{'max_xref_objects':1},{'max_mappings':1}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.repair(**options)
            self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):self.repair(max_pages=True)

    def test_existing_output_and_bad_declarations_are_preserved(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'new output'):self.repair()
        self.assertEqual(self.output.read_bytes(),b'keep')
        self.output.unlink()
        for fonts in ([],self.faces()*2,[dict(self.faces()[0],unknown=True)]):
            with self.subTest(fonts=fonts),self.assertRaises(ValueError):
                repair_pdf_unicode(identity(self.source),self.output,fonts)
        self.assertFalse(self.output.exists())

    def test_unsupported_native_api_leaves_no_output(self):
        with patch('figure_rebuild.pdf_unicode._require_native_api',side_effect=ValueError('native API is unsupported')):
            with self.assertRaisesRegex(ValueError,'native API is unsupported'):self.repair()
        self.assertFalse(self.output.exists())


if __name__=='__main__':
    unittest.main()
