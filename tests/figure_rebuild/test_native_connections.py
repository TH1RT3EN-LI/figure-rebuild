import sys
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/figure_rebuild'))
from native_connections import connect_objects, attachment_groups, NS, P, A
from connections import resolve_scene
from connections import object_bounds


def preset_vertices(element):
    """Evaluate the independent Apache POI preset XML, then native xfrm."""
    preset = element.find('p:spPr/a:prstGeom', NS)
    definition = ET.parse(Path(__file__).parent/'fixtures/connector-presets.xml').getroot().find(preset.get('prst'))
    xf = element.find('p:spPr/a:xfrm', NS)
    off, extent = xf.find('a:off', NS), xf.find('a:ext', NS)
    w, h = float(extent.get('cx')), float(extent.get('cy'))
    variables = {'l':0., 't':0., 'r':w, 'b':h, 'w':w, 'h':h, 'vc':h/2}
    def operand(value): return variables[value] if value in variables else float(value)
    def guide(node):
        op, *args = node.get('fmla').split()
        args = [operand(arg) for arg in args]
        variables[node.get('name')] = (args[0] if op == 'val' else
                                     args[0]*args[1]/args[2] if op == '*/' else
                                     (args[0]+args[1])/args[2] if op == '+/' else None)
        if variables[node.get('name')] is None: raise AssertionError('Unsupported independent preset equation')
    for node in definition.findall('a:avLst/a:gd', NS): guide(node)
    for node in preset.findall('a:avLst/a:gd', NS): guide(node)
    for node in definition.findall('a:gdLst/a:gd', NS): guide(node)
    result=[]
    for node in definition.findall('a:pathLst/a:path/*/a:pt', NS):
        x,y=operand(node.get('x')),operand(node.get('y'))
        if xf.get('flipH') in ('1','true'):x=w-x
        if xf.get('flipV') in ('1','true'):y=h-y
        p=(x+float(off.get('x')),y+float(off.get('y')))
        if not result or result[-1] != p: result.append(p)
    return result


def route_case(start,end,start_site,end_site,route='elbow',scale=1000,offset=(100,200)):
    def target(identity, point, site):
        dx,dy={'top':(50,0),'left':(0,30),'bottom':(50,60),'right':(100,30)}[site]
        return module(identity,point[0]-dx,point[1]-dy)
    source={'objects':[target('a',start,start_site),target('b',end,end_site),
      {'id':'edge','kind':'connector','from':{'id':'a','site':start_site},'to':{'id':'b','site':end_site},
       'route':route,'arrow':{'start':'stealth','end':'triangle'},
       'style':{'fill':'none','stroke':'#000000','stroke_width':2}}]}
    scene,records=resolve_scene(source);objects=scene['objects'];elements=[];mapped={}
    for index,obj in enumerate(objects):
        b=object_bounds(obj);b['width']=max(b['width'],.01);b['height']=max(b['height'],.01)
        frame=[offset[0]+b['x']*scale,offset[1]+b['y']*scale,b['width']*scale,b['height']*scale]
        elements.append(shape(obj['id'],index+1,frame));mapped[obj['id']]={'box':b,'frame':frame}
    # Exporters may leave stale flip metadata: installation must replace it.
    xf=elements[-1].find('p:spPr/a:xfrm',NS);xf.set('flipH','1');xf.set('flipV','1')
    tree=ET.Element('{' + P + '}spTree');tree.extend(elements)
    audit=connect_objects(tree,elements,objects,mapped)
    expected=[(offset[0]+p['x']*scale,offset[1]+p['y']*scale) for p in records[0]['points']]
    if all(point[0]==expected[0][0] for point in expected) or all(point[1]==expected[0][1] for point in expected):
        expected=[expected[0],expected[-1]]
    return elements[-1],audit[0],expected


def native_stroke_path(commands, width=2, rotation=0):
    element=shape('crossed-path',2,[0,0,100,100])
    properties=element.find('p:spPr',NS)
    ET.SubElement(properties,'{' + A + '}noFill')
    properties.find('a:xfrm',NS).set('rot',str(rotation))
    properties.find('a:ln',NS).set('w',str(width))
    path=properties.find('a:custGeom/a:pathLst/a:path',NS)
    for operation,points in commands:
        command=ET.SubElement(path,'{' + A + '}' + operation)
        for x,y in points:ET.SubElement(command,'{' + A + '}pt',{'x':str(x),'y':str(y)})
    return element


def shape(identity, number, box):
    x,y,w,h = box
    return ET.fromstring(f'''<p:sp xmlns:p="{P}" xmlns:a="{A}">
      <p:nvSpPr><p:cNvPr id="{number}" name="{identity}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>
      <a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="0" t="0" r="r" b="b"/><a:pathLst><a:path w="{w}" h="{h}"/></a:pathLst></a:custGeom><a:ln/></p:spPr></p:sp>''')


def module(identity,x,y):
    return {'id':identity,'kind':'path','commands':[{'moveTo':{'x':x,'y':y}},
      {'lineTo':{'x':x+100,'y':y}},{'lineTo':{'x':x+100,'y':y+60}},
      {'lineTo':{'x':x,'y':y+60}},{'close':{}}],
      'style':{'fill':'#CCCCCC','stroke':'#000000','stroke_width':1}}


class NativeConnections(unittest.TestCase):
    def assert_route(self,start,end,start_site,end_site,preset,route='elbow'):
        native,audit,expected=route_case(start,end,start_site,end_site,route)
        self.assertEqual(native.find('p:spPr/a:prstGeom',NS).get('prst'),preset)
        actual=preset_vertices(native)
        self.assertEqual(len(actual),len(expected))
        for a,b in zip(actual,expected):
            # Integer EMU rounding, including one-EMU degenerate-axis extent.
            self.assertLessEqual(abs(a[0]-b[0]),1.5)
            self.assertLessEqual(abs(a[1]-b[1]),1.5)
        self.assertTrue(audit['initial_route_verified'])
        self.assertEqual(native.find('.//a:headEnd',NS).get('type'),'stealth')
        self.assertEqual(native.find('.//a:tailEnd',NS).get('type'),'triangle')
        self.assertEqual(native.find('.//a:stCxn',NS).get('id'),'1')
        self.assertEqual(native.find('.//a:endCxn',NS).get('id'),'2')

    def test_straight_all_quadrants_match_independent_preset_definition(self):
        for end in ((300,240),(-100,240),(300,-80),(-100,-80)):
            with self.subTest(end=end):self.assert_route((100,80),end,'right','left','straightConnector1','straight')

    def test_hvh_all_quadrants_preserve_initial_route(self):
        for end in ((300,240),(-100,240),(300,-80),(-100,-80)):
            with self.subTest(end=end):self.assert_route((100,80),end,'right','left','bentConnector3')

    def test_vhv_all_quadrants_preserve_initial_route(self):
        for end in ((300,240),(-100,240),(300,-80),(-100,-80)):
            with self.subTest(end=end):self.assert_route((100,80),end,'bottom','top','bentConnector4')

    def test_horizontal_then_vertical_all_quadrants_preserve_initial_route(self):
        for end in ((300,240),(-100,240),(300,-80),(-100,-80)):
            with self.subTest(end=end):self.assert_route((100,80),end,'right','top','bentConnector2')

    def test_vertical_then_horizontal_all_quadrants_preserve_initial_route(self):
        for end in ((300,240),(-100,240),(300,-80),(-100,-80)):
            with self.subTest(end=end):self.assert_route((100,80),end,'bottom','left','bentConnector3')

    def test_axis_aligned_routes_discard_renderer_padding_and_stale_flips(self):
        for sites in (('right','left'),('bottom','top'),('right','top'),('bottom','left')):
            for end in ((300,80),(-100,80),(100,240),(100,-80)):
                with self.subTest(sites=sites,end=end):
                    self.assert_route((100,80),end,*sites,'straightConnector1')

    def test_endpoint_ids_sites_and_arrow_survive_native_conversion(self):
        source={'objects':[module('a',20,30),module('b',250,110),
          {'id':'edge','kind':'connector','from':{'id':'a','site':'right'},'to':{'id':'b','site':'left'},
           'route':'elbow','arrow':{'start':'none','end':'triangle'},
           'style':{'fill':'none','stroke':'#000000','stroke_width':2}}]}
        scene,_=resolve_scene(source);objects=scene['objects']
        boxes=[{'x':20,'y':30,'width':100,'height':60},{'x':250,'y':110,'width':100,'height':60},
               {'x':120,'y':60,'width':130,'height':80}]
        elements=[shape(obj['id'],i+1,[b['x'],b['y'],b['width'],b['height']]) for i,(obj,b) in enumerate(zip(objects,boxes))]
        tree=ET.Element('{' + P + '}spTree');tree.extend(elements)
        audit=connect_objects(tree,elements,objects,{obj['id']:{'box':b} for obj,b in zip(objects,boxes)})
        self.assertEqual(tree[-1].tag,'{' + P + '}cxnSp')
        self.assertEqual(tree[-1].find('.//a:stCxn',NS).attrib,{'id':'1','idx':'3'})
        self.assertEqual(tree[-1].find('.//a:endCxn',NS).attrib,{'id':'2','idx':'1'})
        self.assertEqual(tree[-1].find('.//a:tailEnd',NS).get('type'),'triangle')
        self.assertEqual(tree[-1].find('.//a:prstGeom',NS).get('prst'),'bentConnector3')
        self.assertEqual(len(tree[0].findall('.//a:cxn',NS)),4)
        self.assertTrue(audit[0]['native'])

    def test_label_groups_preserve_child_frames_and_reject_overlap_reordering(self):
        objects=[{'id':'module'},{'id':'unrelated'},{'id':'label','source_attachment':{'id':'module'}}]
        elements=[shape(o['id'],i+1,[i*100,0,80,40]) for i,o in enumerate(objects)]
        tree=ET.Element('{' + P + '}spTree');tree.extend(elements)
        frames=[ET.tostring(e.find('p:spPr/a:xfrm',NS)) for e in elements]
        def bounds(e,name):
            i=next(i for i,o in enumerate(objects) if o['id']==name)
            return (i*100,0,i*100+80,40)
        audit,taken,_=attachment_groups(tree,elements,objects,3,bounds)
        self.assertEqual(audit[0]['members'],['module','label'])
        self.assertEqual(taken,{0,2})
        self.assertEqual(frames,[ET.tostring(e.find('p:spPr/a:xfrm',NS)) for e in elements])
        tree=ET.Element('{' + P + '}spTree');tree.extend(elements)
        with self.assertRaisesRegex(ValueError,'paint order'):
            attachment_groups(tree,elements,objects,3,lambda e,name:(0,0,80,40))

    def grouping_fixture(self,path):
        objects=[{'id':'module'},{'id':'path'},{'id':'label','source_attachment':{'id':'module'}}]
        elements=[shape('module',1,[0,40,20,20]),path,shape('label',3,[40,40,20,20])]
        tree=ET.Element('{' + P + '}spTree');tree.extend(elements)
        def bounds(e,name):return {'module':(0,40,20,60),'path':(-8,-8,108,108),'label':(40,40,60,60)}[name]
        return tree,elements,objects,bounds

    def test_stroke_only_l_path_bbox_overlap_is_provably_safe(self):
        path=native_stroke_path([('moveTo',[(0,100)]),('lnTo',[(100,100)]),('lnTo',[(100,0)])])
        tree,elements,objects,bounds=self.grouping_fixture(path)
        before=ET.tostring(path)
        audit,_,_=attachment_groups(tree,elements,objects,3,bounds)
        self.assertTrue(audit[0]['paint_order_verified'])
        self.assertEqual(ET.tostring(path),before)

    def test_actual_line_or_thick_stroke_crossing_still_rejects(self):
        for y,width in ((50,2),(65,12)):
            path=native_stroke_path([('moveTo',[(0,y)]),('lnTo',[(100,y)])],width)
            tree,elements,objects,bounds=self.grouping_fixture(path)
            with self.subTest(y=y,width=width),self.assertRaisesRegex(ValueError,'label crosses path'):
                attachment_groups(tree,elements,objects,3,bounds)

    def test_cubic_control_hull_overlap_does_not_replace_actual_curve_check(self):
        for controls,reject in ((((0,100),(100,100),(100,0),(100,0)),False),
                                (((0,0),(0,100),(100,0),(100,100)),True)):
            path=native_stroke_path([('moveTo',[controls[0]]),('cubicBezTo',list(controls[1:]))])
            tree,elements,objects,bounds=self.grouping_fixture(path)
            if reject:
                with self.assertRaisesRegex(ValueError,'paint order'):attachment_groups(tree,elements,objects,3,bounds)
            else:self.assertTrue(attachment_groups(tree,elements,objects,3,bounds)[0][0]['paint_order_verified'])

    def test_rotated_stroke_only_path_uses_native_world_coordinates(self):
        path=native_stroke_path([('moveTo',[(0,100)]),('lnTo',[(100,100)]),('lnTo',[(100,0)])],rotation=5400000)
        tree,elements,objects,bounds=self.grouping_fixture(path)
        self.assertTrue(attachment_groups(tree,elements,objects,3,bounds)[0][0]['paint_order_verified'])

    def test_filled_or_unsupported_paths_keep_conservative_rejection(self):
        for mode in ('filled','arc','arrow'):
            path=native_stroke_path([('moveTo',[(0,100)]),('lnTo',[(100,100)]),('lnTo',[(100,0)])])
            properties=path.find('p:spPr',NS)
            if mode=='filled':properties.remove(properties.find('a:noFill',NS))
            if mode=='arc':ET.SubElement(properties.find('a:custGeom/a:pathLst/a:path',NS),'{' + A + '}arcTo',{'wR':'50','hR':'50','stAng':'0','swAng':'5400000'})
            if mode=='arrow':ET.SubElement(properties.find('a:ln',NS),'{' + A + '}tailEnd',{'type':'triangle'})
            tree,elements,objects,bounds=self.grouping_fixture(path)
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'paint order'):
                attachment_groups(tree,elements,objects,3,bounds)


if __name__=='__main__': unittest.main()
