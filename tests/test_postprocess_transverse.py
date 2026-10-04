"""Single-shape ZIP transaction coverage; no renderer or whole-figure claim."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as E
from zipfile import ZipFile

from figure_rebuild import postprocess
from figure_rebuild.native_cubic_winding import A,P
from test_native_cubic_transverse import transverse_fixture
from test_native_cubic_winding import fixture,find,ROUND


class PostprocessTransverseTests(unittest.TestCase):
    def run_zip(self,parts,placement=True):
        root,manifest,mapping,place=parts
        shape=find(root,'.//p:sp');original=deepcopy(shape)
        path=find(root,'.//a:path');path.clear();path.attrib.update(w='1',h='1')
        E.SubElement(E.SubElement(path,A+'moveTo'),A+'pt',x='0',y='0')
        E.SubElement(E.SubElement(path,A+'lnTo'),A+'pt',x='1',y='1');E.SubElement(path,A+'close')
        with TemporaryDirectory() as folder:
            p=Path(folder);source=p/'source.pptx';output=p/'output.pptx';receipt=p/'receipt.json';mp=p/'manifest.json';om=p/'map.json'
            mp.write_text(json.dumps(manifest));om.write_text(json.dumps(mapping))
            with ZipFile(source,'w') as z:
                z.writestr('ppt/slides/slide1.xml',E.tostring(root));z.writestr('unrelated.bin',b'unchanged')
            before=[f.read_bytes() for f in (source,mp,om)]
            result=postprocess.process(source,output,mp,receipt,object_map=om,occupied_placement=place if placement else None)
            self.assertEqual([f.read_bytes() for f in (source,mp,om)],before)
            self.assertEqual(json.loads(receipt.read_text()),result)
            with ZipFile(output) as z:
                self.assertEqual(z.read('unrelated.bin'),b'unchanged')
                actual=E.fromstring(z.read('ppt/slides/slide1.xml'))
            return result,find(actual,'.//p:sp'),original

    def test_normal_postprocess_selects_transverse_from_restored_integer_commands(self):
        result,actual,original=self.run_zip(transverse_fixture())
        row=result['native_winding_fills'][0]
        self.assertEqual(row['status'],'applied',row)
        self.assertEqual(row['proof_mode'],'transverse_line')
        self.assertEqual(row['requested_proof_policy'],'classified_contact_or_transverse')
        self.assertEqual(row['source_domain_selection']['selected_proof_mode'],'transverse_line')
        for tag in ('xfrm','solidFill','ln'):
            self.assertEqual(E.tostring(actual.find(P+'spPr/'+A+tag)),E.tostring(original.find(P+'spPr/'+A+tag)))
        self.assertEqual(len(actual.findall('.//'+A+'path')),1)
        self.assertTrue(row['final_geometry_verification']['geometry']['actual_integer_candidate_reproved'])

    def test_missing_binding_retains_complete_restored_geometry(self):
        result,actual,original=self.run_zip(transverse_fixture(),placement=False)
        row=result['native_winding_fills'][0]
        self.assertEqual(row['status'],'rejected')
        self.assertEqual(E.tostring(actual.find(P+'spPr')),E.tostring(original.find(P+'spPr')))

    def test_no_event_selects_strict_contact_once_and_preserves_correct_fill(self):
        result,actual,original=self.run_zip(fixture(ROUND))
        row=result['native_winding_fills'][0]
        self.assertEqual(row['status'],'not_applicable',row)
        self.assertEqual(row['proof_mode'],'endpoint_contact')
        self.assertEqual(E.tostring(actual.find(P+'spPr')),E.tostring(original.find(P+'spPr')))

if __name__=='__main__':unittest.main()
