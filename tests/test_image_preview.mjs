import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {applyImagePreview} from '../src/figure_rebuild/powerpoint/image_preview.mjs';
const hash = s => createHash('sha256').update(s).digest('hex');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');
const position = {left:2,top:3,width:10,height:10};
const definition = () => ({schema_version:1,policy:'delivered-native-picture-mupdf-device-grid-v1',preview_only:true,
  native_delivery_modified:false,reference_pixels_used:false,unsupported:[],paint_order:[{id:'picture',type:'image'},{id:'front',type:'shape'}],
  objects:[{id:'picture',native_position:{...position},previews:[{scale:1,width:1,height:1,position:{left:2,top:3,width:1,height:1},png_base64:png.toString('base64'),png_sha256:hash(png)}]}]});
function fake() {
  let items = [{id:'native-picture',name:'picture',type:'image',position:{...position}},{id:'native-front',name:'front',type:'shape'}];
  return {elements:{get items(){return items;},deleteById(id){items=items.filter(e=>e.id!==id);},
    bringToFront(id){const e=items.find(e=>e.id===id);items=items.filter(e=>e.id!==id);items.push(e);}},
    images:{add(o){const e={id:'replacement',type:'image',...o};items.push(e);return e;}}};
}
test('actual native picture replacement retains front occlusion and explicit scale receipt',()=>{
  const slide=fake(), front=slide.elements.items[1], receipt=applyImagePreview(slide,definition(),1);
  assert.deepEqual(slide.elements.items.map(e=>e.id),['replacement','native-front']);
  assert.equal(slide.elements.items[1],front); assert.equal(receipt.scale,1);
  assert.equal(receipt.complete_mixed_paint_order_preserved,true);
});
test('version2 transient window supports its own policy and rejects mixed provenance',()=>{
  const d=definition();d.schema_version=2;d.policy='delivered-native-picture-mupdf-device-grid-v2';
  d.objects[0].native_source_crop_units={l:20000,t:10000,r:20000,b:10000};
  assert.deepEqual(applyImagePreview(fake(),d,1).applied_object_ids,['picture']);
  d.policy='delivered-native-picture-mupdf-device-grid-v1';const s=fake(),original=[...s.elements.items];
  assert.throws(()=>applyImagePreview(s,d,1),/Invalid/);assert.deepEqual(s.elements.items,original);
});
test('version3 shared-grid windows retain native front occlusion and reject policy downgrade',()=>{
  const d=definition();d.schema_version=3;d.policy='delivered-native-picture-mupdf-device-grid-v3';
  const s=fake(),front=s.elements.items[1];applyImagePreview(s,d,1);
  assert.deepEqual(s.elements.items.map(e=>e.id),['replacement','native-front']);assert.equal(s.elements.items[1],front);
  d.policy='delivered-native-picture-mupdf-device-grid-v2';assert.throws(()=>applyImagePreview(fake(),d,1),/Invalid/);
});
test('version4 source interval samples retain all mixed paint identities and reject policy downgrade',()=>{
  const d=definition();d.schema_version=4;d.policy='delivered-native-picture-mupdf-device-grid-v4';
  const s=fake(),front=s.elements.items[1];applyImagePreview(s,d,1);
  assert.deepEqual(s.elements.items.map(e=>e.id),['replacement','native-front']);assert.equal(s.elements.items[1],front);
  d.policy='delivered-native-picture-mupdf-device-grid-v3';assert.throws(()=>applyImagePreview(fake(),d,1),/Invalid/);
});
test('version5 explicit RGB group samples preserve mixed occlusion and reject policy downgrade',()=>{
  const d=definition();d.schema_version=5;d.policy='delivered-native-picture-mupdf-device-grid-v5';
  const s=fake(),front=s.elements.items[1];applyImagePreview(s,d,1);
  assert.deepEqual(s.elements.items.map(e=>e.id),['replacement','native-front']);assert.equal(s.elements.items[1],front);
  d.policy='delivered-native-picture-mupdf-device-grid-v4';const unchanged=fake(),original=[...unchanged.elements.items];
  assert.throws(()=>applyImagePreview(unchanged,d,1),/Invalid/);assert.deepEqual(unchanged.elements.items,original);
});
test('changed native identity, position, order, PNG or target grid fails before mutation',()=>{
  for(const change of [d=>d.paint_order.reverse(),d=>d.objects[0].id='missing',
    d=>d.objects[0].native_position.left+=.01,d=>d.objects[0].previews[0].png_sha256='0'.repeat(64),
    d=>d.objects[0].previews[0].width=2,d=>d.objects[0].previews[0].png_base64+=' ',
    d=>d.objects.push(d.objects[0])]){
    const d=definition(),s=fake(), original=[...s.elements.items];change(d);
    assert.throws(()=>applyImagePreview(s,d,1),/changed/);assert.deepEqual(s.elements.items,original);
  }
});
test('unsupported pictures keep native imported objects and their ordinary sampler',()=>{
  const d=definition(),s=fake(),original=[...s.elements.items];d.objects=[];d.unsupported=[{id:'picture',reason:'crop'}];
  assert.deepEqual(applyImagePreview(s,d,1).applied_object_ids,[]);assert.deepEqual(s.elements.items,original);
});
test('actual Artifact CPU copies the sampled grid and preserves alpha and front occlusion',
  {skip:!process.env.RUNTIME_NODE_MODULES},async()=>{
    const req=createRequire(path.join(process.env.RUNTIME_NODE_MODULES,'image-preview-tests.cjs'));
    const {configureCpuRenderer}=await import('../src/figure_rebuild/powerpoint/cpu_renderer.mjs');configureCpuRenderer(req.resolve('@oai/artifact-tool'));
    const {Presentation}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
    const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
    const original=await sharp({create:{width:80,height:80,channels:4,background:{r:0,g:255,b:0,alpha:1}}}).png().toBuffer();
    const sampled=await sharp({create:{width:10,height:10,channels:4,background:{r:255,g:0,b:0,alpha:.5}}}).png().toBuffer();
    for(const version of [4,5]) {
    const p=Presentation.create({slideSize:{width:40,height:30}}),s=p.slides.add();s.background.fill='#ffffff';
    const image=s.images.add({blob:new Uint8Array(original),contentType:'image/png',position:{...position},geometry:'rect'});image.name='picture';
    s.shapes.add({name:'front',geometry:'rect',position:{left:6,top:5,width:2,height:2},fill:'#0000ff',line:{fill:'none',width:0}});
    const d=definition();d.schema_version=version;d.policy=`delivered-native-picture-mupdf-device-grid-v${version}`;
    d.objects[0].native_source_crop_units={l:21234,t:12345,r:20123,b:15432};
    d.objects[0].previews[0]={scale:1,width:10,height:10,position:{...position},png_base64:sampled.toString('base64'),png_sha256:hash(sampled)};
    const snapshot=Buffer.from(original);applyImagePreview(s,d,1);
    const pixels=await sharp(new Uint8Array(await(await p.export({slide:s,format:'png',scale:1})).arrayBuffer())).ensureAlpha().raw().toBuffer({resolveWithObject:true});
    const pixel=(x,y)=>[...pixels.data.subarray((y*pixels.info.width+x)*4,(y*pixels.info.width+x)*4+4)];
    assert.deepEqual(pixel(4,5),[255,127,127,255]);assert.deepEqual(pixel(7,6),[0,0,255,255]);
    assert.deepEqual(pixel(1,5),[255,255,255,255]);assert.deepEqual(original,snapshot);
    }
  });
