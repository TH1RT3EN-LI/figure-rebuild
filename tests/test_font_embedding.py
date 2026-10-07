"""Actual font-part bytes, permissions, relationships and preservation."""
import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from fontTools.ttLib import TTFont
from figure_rebuild.font_embedding import embed_fonts,eot_bytes
from figure_rebuild.package import NS,A,P,R,REL,CT,Package,content_type
from test_font_prepare import make_font
from test_package import fixture,slide,shape,write_archive,relationships,data

class FontEmbeddingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.font=self.root/'font.ttf';make_font(self.font)
  with TTFont(self.font) as font:font['OS/2'].fsType=8;font.save(self.font)
  self.source=self.root/'parent.pptx';self.output=self.root/'embedded.pptx'
  label=shape(2,'label',text='AB')
  run=label.find('.//a:r',NS);props=E.Element('{'+A+'}rPr',b='0',i='0');E.SubElement(props,'{'+A+'}latin',typeface='Unit Test Sans');run.insert(0,props)
  self.files=fixture([(256,'ppt/slides/unusually-named.xml',slide([label]))],extras={'ppt/media/untouched.png':b'original bytes'})
  self.write()
 def write(self):write_archive(self.source,self.files)
 def face(self):return [{'family':'Unit Test Sans','role':'regular','renderer':str(self.font),'renderer_sha256':hashlib.sha256(self.font.read_bytes()).hexdigest()}]
 def test_exact_font_payload_relationship_and_slide_preservation(self):
  result=embed_fonts(self.source,self.output,self.face());record=result['embedded_faces'][0]
  with zipfile.ZipFile(self.output) as z:
   for part in self.files:
    if part not in ('ppt/presentation.xml','ppt/_rels/presentation.xml.rels','[Content_Types].xml'):self.assertEqual(z.read(part),self.files[part])
   eot=z.read(record['part']);size,fs,version,flags=struct.unpack_from('<4I',eot)
   self.assertEqual((size,fs,version,flags),(len(eot),self.font.stat().st_size,0x10000,0))
   self.assertEqual(eot[-fs:],self.font.read_bytes())
   offset=80
   for nid in (1,2,5,4):
    pad,length=struct.unpack_from('<HH',eot,offset);self.assertEqual(pad,0);offset+=4
    name=eot[offset:offset+length].decode('utf-16le');self.assertTrue(name);offset+=length
   self.assertEqual(offset,len(eot)-fs)
   presentation=E.fromstring(z.read('ppt/presentation.xml'))
   lst=presentation.find('p:embeddedFontLst',NS);self.assertEqual(len(lst),1)
   rid=lst[0].find('p:regular',NS).get('{'+R+'}id')
   rel=next(e for e in E.fromstring(z.read('ppt/_rels/presentation.xml.rels')) if e.get('Id')==rid)
   self.assertEqual(rel.get('Type'),R+'/font');self.assertEqual('ppt/'+rel.get('Target'),record['part'])
  self.assertTrue(record['editing_permission'])
 def test_preview_rights_kept_and_not_claimed_editable(self):
  with TTFont(self.font) as font:font['OS/2'].fsType=4;font.save(self.font)
  result=embed_fonts(self.source,self.output,self.face());self.assertFalse(result['embedded_faces'][0]['editing_permission'])
  with zipfile.ZipFile(self.output) as z:self.assertEqual(struct.unpack_from('<H',z.read(result['embedded_faces'][0]['part']),32)[0],4)
 def test_restricted_bitmap_only_and_contradictory_rights_refuse_before_output(self):
  for rights in (2,0x200,12):
   with self.subTest(rights=rights):
    with TTFont(self.font) as font:font['OS/2'].fsType=rights;font.save(self.font)
    with self.assertRaisesRegex(ValueError,'permissions'):embed_fonts(self.source,self.output,self.face())
    self.assertFalse(self.output.exists())
 def test_missing_used_glyph_wrong_identity_and_hash_refuse(self):
  cases=[('hash',lambda faces:faces[0].update(renderer_sha256='0'*64)),('family',lambda faces:faces[0].update(family='Other'))]
  for _,mutate in cases:
   faces=self.face();mutate(faces)
   with self.assertRaises(ValueError):embed_fonts(self.source,self.output,faces)
   self.assertFalse(self.output.exists())
  make_font(self.font,characters=[ord('A')])
  with self.assertRaisesRegex(ValueError,'lacks used Unicode'):embed_fonts(self.source,self.output,self.face())
  self.assertFalse(self.output.exists())
 def test_existing_embedding_and_destination_are_preserved(self):
  embed_fonts(self.source,self.output,self.face());before=self.output.read_bytes()
  with self.assertRaisesRegex(ValueError,'new output'):embed_fonts(self.source,self.output,self.face())
  self.assertEqual(self.output.read_bytes(),before)
  with self.assertRaisesRegex(ValueError,'Existing'):embed_fonts(self.output,self.root/'again.pptx',self.face())
 def test_inherited_fonts_refuse(self):
  root=E.fromstring(self.files['ppt/slides/unusually-named.xml']);props=root.find('.//a:rPr',NS);props.remove(props.find('a:latin',NS));self.files['ppt/slides/unusually-named.xml']=E.tostring(root);self.write()
  with self.assertRaisesRegex(ValueError,'explicit native font'):embed_fonts(self.source,self.output,self.face())
 def test_no_subset_bit_is_preserved_with_complete_font(self):
  with TTFont(self.font) as font:font['OS/2'].fsType=0x108;font.save(self.font)
  _,receipt=eot_bytes(self.font.read_bytes());self.assertTrue(receipt['editing_permission']);self.assertEqual(receipt['fsType'],0x108)
 def test_multiple_style_roles_follow_presentation_schema_order(self):
  bold=self.root/'bold.ttf';make_font(bold,'Bold')
  face={'family':'Unit Test Sans','role':'bold','renderer':str(bold),'renderer_sha256':hashlib.sha256(bold.read_bytes()).hexdigest()}
  root=E.fromstring(self.files['ppt/slides/unusually-named.xml']);paragraph=root.find('.//a:p',NS)
  import copy
  run=copy.deepcopy(paragraph.find('a:r',NS));run.find('a:rPr',NS).set('b','1');paragraph.append(run)
  self.files['ppt/slides/unusually-named.xml']=E.tostring(root);self.write()
  embed_fonts(self.source,self.output,[face,*self.face()])
  with zipfile.ZipFile(self.output) as z:
   entry=E.fromstring(z.read('ppt/presentation.xml')).find('p:embeddedFontLst/p:embeddedFont',NS)
   self.assertEqual([n.tag.rsplit('}',1)[-1] for n in entry],['font','regular','bold'])
 def test_malformed_audit_refuses_with_no_output(self):
  for faces in ({},[],[{}]):
   with self.assertRaisesRegex(ValueError,'audit'):embed_fonts(self.source,self.output,faces)
   self.assertFalse(self.output.exists())
 def test_family_aliases_share_one_font_part_and_unambiguous_content_type(self):
  with TTFont(self.font) as font:
   font['name'].setName('Alternate Family',16,3,1,0x409)
   font['name'].setName('Alternate Family',16,1,0,0)
   font.save(self.font)
  root=E.fromstring(self.files['ppt/slides/unusually-named.xml']);paragraph=root.find('.//a:p',NS)
  import copy
  run=copy.deepcopy(paragraph.find('a:r',NS));run.find('a:rPr/a:latin',NS).set('typeface','Alternate Family');paragraph.append(run)
  self.files['ppt/slides/unusually-named.xml']=E.tostring(root);self.write()
  alias=dict(self.face()[0],family='Alternate Family')
  receipt=embed_fonts(self.source,self.output,[*self.face(),alias])
  self.assertEqual(len(receipt['embedded_faces']),2)
  parts={face['part'] for face in receipt['embedded_faces']};self.assertEqual(len(parts),1)
  package=Package.read(self.output)
  for part in parts:self.assertEqual(content_type(package,part),'application/x-fontdata')
  with zipfile.ZipFile(self.output) as z:
   types=E.fromstring(z.read('[Content_Types].xml'))
   self.assertEqual(sum(t.get('PartName')=='/'+next(iter(parts)) for t in types),1)
   lst=E.fromstring(z.read('ppt/presentation.xml')).find('p:embeddedFontLst',NS)
   self.assertEqual({e.find('p:font',NS).get('typeface') for e in lst},{'Unit Test Sans','Alternate Family'})
 def inherited_scaffolding(self,kind=None,field=False):
  layout=slide([]);layout.tag='{'+P+'}sldLayout'
  master=slide([]);master.tag='{'+P+'}sldMaster'
  if kind is not None:
   body=shape(12,'inherited-label',text='Master title')
   if field:
    run=body.find('.//a:r',NS);run.tag='{'+A+'}fld';run.set('id','{01234567-89AB-CDEF-0123-456789ABCDEF}')
   (layout if kind=='layout' else master).find('p:cSld/p:spTree',NS).append(body)
  self.files['ppt/slideLayouts/layout.xml']=data(layout)
  self.files['ppt/slideMasters/master.xml']=data(master)
  self.files['ppt/slides/_rels/unusually-named.xml.rels']=relationships([{'Id':'rIdLayout','Type':R+'/slideLayout','Target':'../slideLayouts/layout.xml'}])
  self.files['ppt/slideLayouts/_rels/layout.xml.rels']=relationships([{'Id':'rIdMaster','Type':R+'/slideMaster','Target':'../slideMasters/master.xml'}])
  self.write()
 def test_reachable_master_and_layout_live_text_refuse_before_output(self):
  for kind in ('layout','master'):
   with self.subTest(kind=kind):
    self.inherited_scaffolding(kind)
    with self.assertRaisesRegex(ValueError,'Inherited master/layout live text'):embed_fonts(self.source,self.output,self.face())
    self.assertFalse(self.output.exists())
 def test_reachable_master_field_refuses_before_output(self):
  self.inherited_scaffolding('master',field=True)
  with self.assertRaisesRegex(ValueError,'Inherited master/layout live text'):embed_fonts(self.source,self.output,self.face())
  self.assertFalse(self.output.exists())
 def test_empty_reachable_layout_and_master_are_preserved(self):
  self.inherited_scaffolding()
  embed_fonts(self.source,self.output,self.face())
  with zipfile.ZipFile(self.output) as z:
   for part in ('ppt/slideLayouts/layout.xml','ppt/slideMasters/master.xml','ppt/slideLayouts/_rels/layout.xml.rels'):
    self.assertEqual(z.read(part),self.files[part])
 def test_original_font_part_collision_refuses_without_overwriting(self):
  eot,_=eot_bytes(self.font.read_bytes());part='ppt/fonts/fr-'+hashlib.sha256(eot).hexdigest()+'.fntdata'
  self.files[part]=b'original opaque payload';self.write();original=self.source.read_bytes()
  with self.assertRaisesRegex(ValueError,'collides'):embed_fonts(self.source,self.output,self.face())
  self.assertFalse(self.output.exists());self.assertEqual(self.source.read_bytes(),original)

if __name__=='__main__':unittest.main()
