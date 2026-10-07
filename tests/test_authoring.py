"""Original authoring must retain declared facts and route around obstacles."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from figure_rebuild import authoring, cli
from figure_rebuild.validate import validate
from test_font_prepare import make_font

ROOT = Path(__file__).resolve().parents[1]


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fonts = {'family': 'Unit Test Sans'}
        for role, style in (('regular', 'Regular'), ('bold', 'Bold')):
            path = self.root / (role+'.ttf'); make_font(path, style)
            self.fonts[role] = {'path': str(path), 'face_index': 0}
        self.spec = json.loads((ROOT/'docs/assets/creation-pipeline.json').read_text())

    def create(self, spec=None):
        file = self.root/'spec.json'; file.write_text(json.dumps(spec or self.spec))
        job = self.root/'job'; authoring.create_job(file, job, self.fonts)
        return job, json.loads((job/'manifest.json').read_text())

    def test_examples_preserve_all_declared_labels_nodes_and_edge_directions(self):
        for name in ('pipeline', 'parallel-fusion', 'training-feedback'):
            spec = json.loads((ROOT/f'docs/assets/creation-{name}.json').read_text())
            canvas, objects, report = authoring.compose(spec, self.fonts)
            by_id = {x['id']: x for x in objects}
            self.assertEqual({r['id'] for r in report['nodes']}, {n['id'] for n in spec['nodes']})
            self.assertEqual([(e['id'], e['from'], e['to']) for e in report['edges']],
                             [(e['id'], e['from'], e['to']) for e in spec['edges']])
            for node in spec['nodes']:
                self.assertEqual(by_id['node-'+node['id']+'-label']['text'], node['label'])
            for edge in report['edges']:
                for p,q in zip(edge['points'], edge['points'][1:]):
                    for node in report['nodes']:
                        self.assertFalse(authoring.segment_hits(p,q,node['box']), (edge['id'],node['id']))
            self.assertGreater(canvas['width'], 0)

    def test_router_avoids_a_blocker_and_keeps_the_requested_endpoints(self):
        obstacle = dict(x=140,y=140,width=80,height=100)
        route = authoring._route((100,190),(260,190),[obstacle],(400,400))
        self.assertEqual(route[0],(100,190)); self.assertEqual(route[-1],(260,190))
        self.assertGreater(len(route),2)
        self.assertTrue(all(not authoring.segment_hits(a,b,obstacle) for a,b in zip(route,route[1:])))

    def test_vertical_layout_changes_reading_axis_without_reversing_edges(self):
        self.spec['direction']='TB'
        self.spec['title']='Pipeline'
        _,_,audit=authoring.compose(self.spec,self.fonts)
        self.assertEqual(len({x['box']['x'] for x in audit['nodes']}),1)
        self.assertEqual([e['from_site'] for e in audit['edges']],['bottom']*4)
        self.assertEqual([e['to_site'] for e in audit['edges']],['top']*4)

    def test_training_and_feedback_are_distinct_and_do_not_add_dependencies(self):
        spec=json.loads((ROOT/'docs/assets/creation-training-feedback.json').read_text())
        _,objects,audit=authoring.compose(spec,self.fonts)
        self.assertEqual(len(audit['edges']),len(spec['edges']))
        edges={o['id']:o for o in objects}
        self.assertNotEqual(edges['edge-objective-update']['style']['stroke'],edges['edge-targets-objective']['style']['stroke'])
        self.assertGreater(sum('moveTo' in c for c in edges['edge-objective-update']['commands']),1)

    def test_unknown_duplicate_and_unsupported_fields_reject(self):
        cases=[]
        s=copy.deepcopy(self.spec);s['edges'][0]['to']='invented';cases.append(s)
        s=copy.deepcopy(self.spec);s['nodes'][1]['stage']=0;cases.append(s)
        s=copy.deepcopy(self.spec);s['nodes'][0]['lane']=True;cases.append(s)
        s=copy.deepcopy(self.spec);s['nodes'][0]['emphasis']='yes';cases.append(s)
        s=copy.deepcopy(self.spec);s['nodes'][0]['kind']='attention-guessed';cases.append(s)
        s=copy.deepcopy(self.spec);s['nodes'][0]['kind']=[];cases.append(s)
        s=copy.deepcopy(self.spec);s['edges'][0]['kind']={};cases.append(s)
        s=copy.deepcopy(self.spec);s['edges'][0]['style']='mysterious';cases.append(s)
        for spec in cases:
            with self.subTest(spec=spec),self.assertRaises(ValueError): authoring.validate_spec(spec)

    def test_group_must_not_silently_include_an_unrelated_module(self):
        self.spec['groups']=[dict(id='false-group',label='Group',members=['input','encoder'])]
        with self.assertRaisesRegex(ValueError,'unrelated'):authoring.compose(self.spec,self.fonts)

    def test_dense_text_is_rejected_instead_of_truncated(self):
        self.spec['nodes'][0]['label']='Long descriptive text '*7
        with self.assertRaisesRegex(ValueError,'too dense'):authoring.compose(self.spec,self.fonts)

    def test_narrow_vertical_canvas_rejects_an_overflowing_title(self):
        self.spec['direction']='TB'
        with self.assertRaisesRegex(ValueError,'Title is too long'):authoring.compose(self.spec,self.fonts)

    def test_group_header_and_generated_id_collisions_reject_before_export(self):
        self.spec['groups']=[dict(id='long',label='Group header '*6,members=['encoder'])]
        with self.assertRaisesRegex(ValueError,'Group label is too long'):authoring.compose(self.spec,self.fonts)
        self.spec.pop('groups');self.spec['nodes'][1]['id']='input-label'
        for edge in self.spec['edges']:
            if edge['from']=='normalize':edge['from']='input-label'
            if edge['to']=='normalize':edge['to']='input-label'
        with self.assertRaisesRegex(ValueError,'collide'):authoring.compose(self.spec,self.fonts)

    def test_created_source_is_original_vector_and_review_stays_pending(self):
        job,m=self.create()
        self.assertEqual(m['source']['kind'],'generated_diagram')
        self.assertEqual(m['recognition']['status'],'needs_review')
        self.assertEqual(validate(m,job,False)['status'],'PASS')
        self.assertEqual(validate(m,job,True)['status'],'FAIL')
        root=ET.parse(job/m['source']['path']).getroot()
        self.assertTrue(root.findall('{http://www.w3.org/2000/svg}text'))
        self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}image'))

    def test_changed_brief_and_geometry_invalidate_the_creation_binding(self):
        job,m=self.create()
        changed=copy.deepcopy(m);changed['objects'][0]['style']['fill']='#FF0000'
        self.assertEqual(validate(changed,job,False)['status'],'FAIL')
        (job/'sources/creation-spec.json').write_text('{}')
        self.assertEqual(validate(m,job,False)['status'],'FAIL')

    def test_build_snapshot_includes_the_creation_spec_and_layout_audit(self):
        job,m=self.create();run=job/'run';run.mkdir()
        cli.freeze_assets(job,run,m)
        for relative in ('sources/creation-spec.json','authoring-audit.json',m['source']['path']):
            self.assertEqual((run/'assets'/relative).read_bytes(),(job/relative).read_bytes())

    def test_existing_job_and_duplicate_json_fields_are_preserved_and_rejected(self):
        job,_=self.create();original=(job/'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'new creation job'):authoring.create_job(self.root/'spec.json',job,self.fonts)
        self.assertEqual((job/'manifest.json').read_bytes(),original)
        file=self.root/'duplicate.json';file.write_text('{"id":"a","id":"b"}')
        with self.assertRaisesRegex(ValueError,'Duplicate JSON'):authoring.create_job(file,self.root/'new',self.fonts)
        self.assertFalse((self.root/'new').exists())


if __name__=='__main__':unittest.main()
