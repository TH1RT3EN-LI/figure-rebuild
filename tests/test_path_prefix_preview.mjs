import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {applyPathPrefixPreview} from '../src/figure_rebuild/powerpoint/path_prefix_preview.mjs';
const hash = b => createHash('sha256').update(b).digest('hex');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');
const definition = () => ({schema_version:1,policy:'delivered-native-filled-path-prefix-grid-v1',preview_only:true,
  native_delivery_modified:false,reference_pixels_used:false,paint_order:[{id:'body',type:'shape'},{id:'front',type:'shape'}],
  request:{object_ids:['body']},objects:[{id:'body'}],previews:[{scale:1,width:1,height:1,
    position:{left:2,top:3,width:1,height:1},png_base64:png.toString('base64'),png_sha256:hash(png)}]});
function fake() {
  let items = [{id:'body-native',name:'body',type:'shape'},{id:'front-native',name:'front',type:'shape'}];
  return {elements:{get items(){return items;},deleteById(id){items=items.filter(e=>e.id!==id);},
    bringToFront(id){const e=items.find(e=>e.id===id);items=items.filter(e=>e.id!==id);items.push(e);}},
    images:{add(o){const e={id:'preview',type:'image',...o};items.push(e);return e;}}};
}
test('actual native prefix replay keeps complete front object identity and occlusion',()=>{
  const s=fake(),front=s.elements.items[1];const r=applyPathPrefixPreview(s,definition(),1);
  assert.deepEqual(s.elements.items.map(e=>e.id),['preview','front-native']);assert.equal(s.elements.items[1],front);
  assert.deepEqual(r,{scale:1,applied_object_ids:['body'],complete_mixed_paint_order_preserved:true});
});
test('native prefix order, coverage, provenance or target bytes cannot change before transient mutation',()=>{
  for(const change of [d=>d.paint_order.reverse(),d=>d.request.object_ids=['front'],d=>d.objects[0].id='other',
    d=>d.native_delivery_modified=true,d=>d.reference_pixels_used=true,d=>d.previews[0].png_sha256='0'.repeat(64),
    d=>d.previews[0].width=2,d=>d.previews[0].png_base64+=' ',d=>d.previews[0].position.left=.25]){
    const d=definition(),s=fake(),before=[...s.elements.items];change(d);
    assert.throws(()=>applyPathPrefixPreview(s,d,1));assert.deepEqual(s.elements.items,before);
  }
});
test('actual Artifact CPU opaque prefix pixels retain the later front shape at all three scales',
 {skip:!process.env.RUNTIME_NODE_MODULES},async()=>{
  const req=createRequire(path.join(process.env.RUNTIME_NODE_MODULES,'path-prefix-tests.cjs'));
  const {configureCpuRenderer}=await import('../src/figure_rebuild/powerpoint/cpu_renderer.mjs');configureCpuRenderer(req.resolve('@oai/artifact-tool'));
  const {Presentation}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
  const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
  for(const scale of [1,2,4]){
   const p=Presentation.create({slideSize:{width:20,height:20}}),s=p.slides.add();s.background.fill='#ffffff';
   s.shapes.add({name:'body',geometry:'rect',position:{left:2,top:3,width:10,height:10},fill:'#00ff00',line:{fill:'none',width:0}});
   const front=s.shapes.add({name:'front',geometry:'rect',position:{left:6,top:5,width:2,height:2},fill:'#0000ff',line:{fill:'none',width:0}});
   const bytes=await sharp({create:{width:10*scale,height:10*scale,channels:3,background:{r:255,g:0,b:0}}}).png().toBuffer();
   const d=definition();d.previews=[{scale,width:10*scale,height:10*scale,position:{left:2,top:3,width:10,height:10},
    png_base64:bytes.toString('base64'),png_sha256:hash(bytes)}];applyPathPrefixPreview(s,d,scale);
   const pixels=await sharp(new Uint8Array(await(await p.export({slide:s,format:'png',scale})).arrayBuffer())).ensureAlpha().raw().toBuffer({resolveWithObject:true});
   const pixel=(x,y)=>[...pixels.data.subarray((y*pixels.info.width+x)*4,(y*pixels.info.width+x)*4+4)];
   assert.deepEqual(pixel(4*scale,5*scale),[255,0,0,255]);assert.deepEqual(pixel(7*scale,6*scale),[0,0,255,255]);
   assert.deepEqual(pixel(scale,5*scale),[255,255,255,255]);assert.equal(s.elements.items[1],front);
  }
 });
