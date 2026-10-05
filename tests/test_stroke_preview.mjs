import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {applyStrokePreview} from '../src/figure_rebuild/powerpoint/stroke_preview.mjs';
const digest = s => createHash('sha256').update(s).digest('hex');
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="124" height="104" viewBox="-12 -12 124 104"><path d="M0 80L50 0L100 80" fill="none" stroke="#ff0000" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const definition = () => ({schema_version:1,policy:'delivered-native-solid-unfilled-stroke-svg-v1',preview_only:true,native_delivery_modified:false,
  objects:[{id:'stroke',svg,svg_sha256:digest(svg),position:{left:28,top:28,width:124,height:104}}],unsupported:[]});
function fake() {
  let items = [{id:'back',type:'image'}, {id:'s',type:'shape',name:'stroke'}, {id:'front',type:'shape',name:'occlusion'}];
  return {elements:{get items(){return items;},deleteById(id){items=items.filter(e=>e.id!==id);},
    bringToFront(id){const item=items.find(e=>e.id===id);items=items.filter(e=>e.id!==id);items.push(item);}},
    images:{add(o){const image={id:'replacement',type:'image',...o};items.push(image);return image;}}};
}
test('transient replacement preserves full mixed occlusion order and leaves unselected content intact',()=>{
  const slide=fake(),original=slide.elements.items;const r=applyStrokePreview(slide,definition());
  assert.deepEqual(slide.elements.items.map(e=>e.id),['back','replacement','front']);
  assert.equal(slide.elements.items[0],original[0]);assert.equal(slide.elements.items[2],original[2]);
  assert.equal(r.native_delivery_modified,false);assert.equal(r.source_pixel_equivalence,false);
});
test('stale SVG, duplicate source identity, or changed position fails before any preview mutation',()=>{
  for(const change of[d=>d.objects[0].svg+=' ',d=>d.objects.push(d.objects[0]),d=>d.objects[0].position.width=Infinity,d=>d.objects[0].id='missing']){
    const d=definition(),slide=fake(),original=[...slide.elements.items];change(d);
    assert.throws(()=>applyStrokePreview(slide,d),/changed/);assert.deepEqual(slide.elements.items,original);
  }
});
test('empty supported scope retains the ordinary preview and its limitations',()=>{
  const d=definition(),slide=fake(),original=[...slide.elements.items];d.objects=[];d.unsupported=[{id:'stroke',reason:'native effect'}];
  const r=applyStrokePreview(slide,d);assert.deepEqual(slide.elements.items,original);assert.deepEqual(r.unsupported,d.unsupported);
});
test('actual Artifact CPU SVG rendering restores round cap/join and front-object occlusion',{skip:!process.env.RUNTIME_NODE_MODULES},async()=>{
  const req=createRequire(path.join(process.env.RUNTIME_NODE_MODULES,'stroke-tests.cjs'));
  const {configureCpuRenderer}=await import('../src/figure_rebuild/powerpoint/cpu_renderer.mjs');configureCpuRenderer(req.resolve('@oai/artifact-tool'));
  const {Presentation}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
  const p=Presentation.create({slideSize:{width:240,height:160}}),s=p.slides.add();s.background.fill='#ffffff';
  s.shapes.add({name:'stroke',geometry:'custom',position:{left:40,top:40,width:100,height:80},fill:'none',line:{fill:'#ff0000',width:12,style:'solid'},customPaths:[{width:100,height:80,commands:[{moveTo:{x:0,y:80}},{lineTo:{x:50,y:0}},{lineTo:{x:100,y:80}}]}]});
  s.shapes.add({name:'occlusion',geometry:'rect',position:{left:130,top:110,width:25,height:30},fill:'#0000ff',line:{fill:'none',width:0}});
  const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
  const pixels=async()=>sharp(new Uint8Array(await(await p.export({slide:s,format:'png',scale:4})).arrayBuffer())).ensureAlpha().raw().toBuffer({resolveWithObject:true});
  const before=await pixels();applyStrokePreview(s,definition());const after=await pixels();
  const pixel=(r,x,y)=>[...r.data.subarray((y*r.info.width+x)*4,(y*r.info.width+x)*4+3)];
  assert.deepEqual(pixel(before,360,118),[255,0,0]); // Original acute miter apex.
  assert.deepEqual(pixel(after,360,118),[255,255,255]); // Round join ends lower.
  assert.deepEqual(pixel(after,136,480),[255,0,0]); // Round left cap extends outward.
  assert.deepEqual(pixel(after,560,480),[0,0,255]); // Later object still covers the stroke.
});
