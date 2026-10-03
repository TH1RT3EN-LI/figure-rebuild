import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {checkRuntime} from './preflight.mjs';
import {fittedTextBox} from './text_fit.mjs';
import {flattenPath,pathBounds} from './curves.mjs';

const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const {job,run,repo,runtime,output}=config;
const assetRoot=config.asset_root??job;
await checkRuntime(runtime);
process.env.RUNTIME_NODE_MODULES=runtime.node_modules;
process.env.RUNTIME_NODE=runtime.node;
process.env.RUNTIME_PYTHON=runtime.python;
const manifest=JSON.parse(await fs.readFile(config.manifest,'utf8'));
const originalSource=path.join(assetRoot,manifest.source.path);
if(createHash('sha256').update(await fs.readFile(originalSource)).digest('hex')!==manifest.source.sha256)throw Error('Original source changed before authoring');
const req=createRequire(path.join(runtime.node_modules,'figure-rebuild-loader.cjs'));
const {Presentation,PresentationFile,FileBlob}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
const {GlobalFonts,createCanvas,loadImage}=await import(pathToFileURL(req.resolve('@napi-rs/canvas')).href);
const fontFamily=runtime.fonts.family;
const fontsDir=path.join(run,'fonts');
const fontAudit=JSON.parse(execFileSync(runtime.python,[path.join(repo,'tools/figure_rebuild/font_prepare.py'),'--config',process.argv[2],'--manifest',config.manifest,'--output-dir',fontsDir],{encoding:'utf8'}));
for(const face of fontAudit)if(!GlobalFonts.registerFromPath(face.renderer,face.family))throw Error('Could not load configured '+face.family+' '+face.role+' font face');
const fontFamilies=[...new Set(fontAudit.map(face=>face.family))];
for(const family of fontFamilies)if(!GlobalFonts.has(family))throw Error('Configured font family was not registered: '+family);
await fs.writeFile(path.join(run,'font-audit.json'),JSON.stringify(fontAudit,null,2));
const ctx=createCanvas(2,2).getContext('2d');
const sourceCanvas=manifest.canvas;
const slideCanvas=config.base?.canvas??sourceCanvas;
const requested=config.base?.placement??[0,0,sourceCanvas.width,sourceCanvas.height];
if(requested.some(x=>!Number.isFinite(x))||requested[2]<=0||requested[3]<=0)throw Error('Invalid placement');
const scale=Math.min(requested[2]/sourceCanvas.width,requested[3]/sourceCanvas.height);
const placement=[requested[0]+(requested[2]-sourceCanvas.width*scale)/2,requested[1]+(requested[3]-sourceCanvas.height*scale)/2,sourceCanvas.width*scale,sourceCanvas.height*scale];
if(placement[0]<0||placement[1]<0||placement[0]+placement[2]>slideCanvas.width+.001||placement[1]+placement[3]>slideCanvas.height+.001)throw Error('Placement outside page');
const ordered=manifest.objects.map((o,i)=>({...o,_order:i})).sort((a,b)=>(a.z_index??a._order)-(b.z_index??b._order)||a._order-b._order);
const measuredText=new Map();
for(const object of ordered.filter(object=>object.kind==='text')){
 const family=object.font_family??fontFamily;
 if(!fontFamilies.includes(family))throw Error('Unconfigured font family: '+family);
 ctx.font=`${object.italic?'italic ':''}${object.bold?'bold ':''}${object.font_size}px "${family}"`;
 measuredText.set(object.id,fittedTextBox(object,text=>ctx.measureText(text),sourceCanvas));
}
await fs.writeFile(path.join(run,'text-fit.json'),JSON.stringify({status:'PASS',font_family:fontFamily,visual_verification_required:true,objects:[...measuredText].map(([id,result])=>({id,...result}))},null,2));
execFileSync(runtime.python,[path.join(repo,'tools/figure_rebuild/export_svg.py'),'--manifest',config.manifest,'--asset-root',assetRoot,'--output',path.join(run,'reconstructed.svg'),'--font',fontAudit[0].renderer,'--bold-font',fontAudit[1].renderer,'--family',fontFamily,'--font-audit',path.join(run,'font-audit.json')],{stdio:'pipe'});
const p=Presentation.create({slideSize:{width:slideCanvas.width,height:slideCanvas.height}});
const slide=p.slides.add();slide.background.fill=sourceCanvas.background??'#FFFFFF';
const textManifest=[],objectMap=[];
function position(box,rotation=0){return {left:placement[0]+box.x*scale,top:placement[1]+box.y*scale,width:box.width*scale,height:box.height*scale,rotation};}
function paint(color='none',opacity=1){return color==='none'?'none':`${color}/${opacity*100}`;}
for(const o of ordered){
 const s=o.style??{};
 if(o.kind==='path'){
  const bounds=pathBounds(o.commands);
  const left=bounds.x,top=bounds.y,right=left+bounds.width,bottom=top+bounds.height;
  const width=Math.max(.01,right-left),height=Math.max(.01,bottom-top);
  const commands=flattenPath(o.commands).map(c=>c.close?{close:{}}:{[c.moveTo?'moveTo':'lineTo']:{x:(c.moveTo??c.lineTo).x-left,y:(c.moveTo??c.lineTo).y-top}});
  const box={x:left,y:top,width,height};
  slide.shapes.add({name:o.id,geometry:'custom',position:position(box),fill:paint(s.fill,s.opacity),line:{fill:paint(s.stroke,s.opacity),width:(s.stroke_width??0)*scale,style:'solid'},customPaths:[{width,height,commands}]});
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,box,editable:true});
 }else if(o.kind==='text'){
  const fontSize=o.font_size;
  const {box,layout}=measuredText.get(o.id);
  const shape=slide.shapes.add({name:o.id,geometry:'textbox',position:position(box,o.rotation??0),fill:'none',line:{fill:'none',width:0}});
  shape.text=o.text;
  const family=o.font_family??fontFamily;
  shape.text.style={typeface:family,fontSize:fontSize*scale,bold:o.bold??false,italic:o.italic??false,color:paint(s.fill??'#000000',s.opacity),alignment:o.alignment??'left',verticalAlignment:o.vertical_alignment??'top',autoFit:'none',wrap:o.wrap??'none',insets:{left:0,right:0,top:0,bottom:0}};
  textManifest.push({id:o.id,content:o.text,source_bbox:box,font_family:family,font_size:fontSize,rotation:o.rotation??0,alignment:o.alignment??'left',paint_order:o.z_index??o._order,measured_line_count:layout.line_count});
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,box,editable:true});
 }else if(o.kind==='image'){
  const bytes=await fs.readFile(path.join(assetRoot,o.path));
  if(createHash('sha256').update(bytes).digest('hex')!==o.sha256)throw Error('Raster asset changed');
  const ext=path.extname(o.path).slice(1).toLowerCase(),type=ext==='jpg'?'jpeg':ext;
  const crop=o.crop??{left:0,right:0,top:0,bottom:0},decoded=await loadImage(bytes);
  const croppedWidth=decoded.width*(1-crop.left-crop.right),croppedHeight=decoded.height*(1-crop.top-crop.bottom);
  const containScale=Math.min(o.box.width/croppedWidth,o.box.height/croppedHeight);
  const fitted={x:o.box.x+(o.box.width-croppedWidth*containScale)/2,y:o.box.y+(o.box.height-croppedHeight*containScale)/2,width:croppedWidth*containScale,height:croppedHeight*containScale};
  // Artifact Tool's automatic contain fit clears source crop. Freeze the
  // aspect-correct frame ourselves so the original bytes and crop survive.
  const image=slide.images.add({blob:new Uint8Array(bytes),contentType:`image/${type}`,alt:o.id,position:position(fitted),fit:'contain',crop,geometry:'rect'});
  image.lockAspectRatio=false;
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,box:fitted,requested_box:o.box,crop,editable:false});
 }else throw Error('Unsupported object kind');
}
slide.speakerNotes.textFrame.setText(`Source: ${manifest.source.uri||manifest.source.path}\nClassification: ${manifest.source.kind}. Original SHA256: ${manifest.source.sha256}. Recognition: ${manifest.recognition.provider}. ${manifest.recognition.notes||''}\nReconstruction with editable geometry and separate text; raster panels remain raster. Font adapted to configured ${fontFamily}. This reconstruction does not add new experimental evidence. Visual acceptance pending.`);
await fs.writeFile(path.join(run,'text-manifest.json'),JSON.stringify({schema_version:1,source_canvas:sourceCanvas,text_elements:textManifest},null,2));
await fs.writeFile(path.join(run,'object-map.json'),JSON.stringify({placement,objects:objectMap},null,2));
const raw=path.join(run,'artifact-authored.pptx');await (await PresentationFile.exportPptx(p)).save(raw);
const grouped=path.join(run,'grouped-overlay.pptx');
execFileSync(runtime.python,[path.join(repo,'tools/figure_rebuild/postprocess.py'),'--input',raw,'--output',grouped,'--manifest',config.manifest,'--object-map',path.join(run,'object-map.json'),'--receipt',path.join(run,'editability.json')],{stdio:'pipe'});
const candidate=path.join(run,'candidate.pptx');
if(config.base){
 const args=[path.join(repo,'tools/figure_rebuild/package.py'),'merge','--base',config.base.path,'--overlay',grouped,'--output',candidate,'--slide-id',config.base.slide_id,'--base-sha256',config.base.sha256,'--receipt',path.join(run,'preservation.json')];
 for(const name of config.base.replace_ids)args.push('--replace-id',name);
 execFileSync(runtime.python,args,{stdio:'pipe'});
}else await fs.copyFile(grouped,candidate);
const {finalizePresentation}=await import(pathToFileURL(path.join(runtime.presentation_skill,'container_tools/artifact_tool_utils.mjs')).href);
const expectedSize=[Math.round(slideCanvas.width*9525),Math.round(slideCanvas.height*9525)].join(',');
const checkedOutput=path.join(run,'validated-output','reconstruction.pptx');await fs.mkdir(path.dirname(checkedOutput),{recursive:true});
await finalizePresentation({workspaceDir:job,candidatePath:candidate,finalPath:checkedOutput,pythonExecutable:runtime.python,integrityValidatorPath:path.join(runtime.presentation_skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(runtime.presentation_skill,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu',expectedSize],fontPolicy:config.base?undefined:{basis:'design',families:fontFamilies},verifyArtifactToolImport:true,receiptPath:path.join(run,'validation.json')});
const rendered=await PresentationFile.importPptx(await FileBlob.load(checkedOutput));
let targetSlide=rendered.slides.items[0];
if(config.base){
 const info=JSON.parse(execFileSync(runtime.python,[path.join(repo,'tools/figure_rebuild/package.py'),'inspect',checkedOutput],{encoding:'utf8'}));
 const at=info.slides.findIndex(s=>String(s.slide_id??s.id)===config.base.slide_id);if(at<0)throw Error('Stable slide vanished');targetSlide=rendered.slides.items[at];
}
for(const s of [1,2,4]){const blob=await rendered.export({slide:targetSlide,format:'png',scale:s});await fs.writeFile(path.join(run,`preview-${s}x.png`),new Uint8Array(await blob.arrayBuffer()));}
// Keep the raw 1x preview for comparison diagnostics. The viewing aide uses
// supersampling so thin mathematical strokes are filtered rather than dropped.
const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
await sharp(path.join(run,'preview-4x.png')).resize(Math.round(slideCanvas.width),Math.round(slideCanvas.height),{kernel:'lanczos3'}).png().toFile(path.join(run,'preview-smooth-1x.png'));
// Use resolved source-coordinate frames: SVG text may only have a baseline
// anchor, and image contain fitting can differ from its requested box.
const comparisonScene=path.join(run,'comparison-scene.json');
const byId=new Map(ordered.map(object=>[object.id,object]));
await fs.writeFile(comparisonScene,JSON.stringify({canvas:sourceCanvas,derived_from:config.manifest,diagnostic_only:true,objects:objectMap.map(object=>{
 const original=byId.get(object.id),pad=object.kind==='path'?(original.style?.stroke_width??0)/2:0;
 const box={x:object.box.x-pad,y:object.box.y-pad,width:object.box.width+2*pad,height:object.box.height+2*pad};
 return {id:object.id,kind:object.kind,box,rotation:original.rotation??0};
})},null,2));
const compareArgs=[path.join(repo,'tools/figure_rebuild/compare.py'),'--reference',path.join(assetRoot,manifest.source.path),'--rebuilt',path.join(run,'preview-1x.png'),'--output',path.join(run,'comparison.png'),'--metrics',path.join(run,'comparison-metrics.json'),'--font',fontAudit[0].renderer,'--manifest',comparisonScene];
if(path.extname(manifest.source.path).toLowerCase()==='.svg'){
 const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
 const raster=path.join(run,'svg-reference.png');await sharp(path.join(assetRoot,manifest.source.path)).png().toFile(raster);compareArgs[compareArgs.indexOf('--reference')+1]=raster;
}
if(config.base)compareArgs.push('--region',...placement.map(String));
execFileSync(runtime.python,compareArgs,{stdio:'pipe'});
for(const asset of [{path:manifest.source.path,sha256:manifest.source.sha256},...manifest.objects.filter(o=>o.kind==='image')]){
 for(const root of new Set([assetRoot,job]))if(createHash('sha256').update(await fs.readFile(path.join(root,asset.path))).digest('hex')!==asset.sha256)throw Error('Asset changed during the build; output not published: '+asset.path);
}
const editability=JSON.parse(await fs.readFile(path.join(run,'editability.json'),'utf8'));
const delivery={output,sha256:createHash('sha256').update(await fs.readFile(checkedOutput)).digest('hex'),source_sha256:manifest.source.sha256,source_preserved:true,recognition_provider:manifest.recognition.provider,native_path_count:editability.path_count,native_text_count:editability.text_count,native_group_count:editability.native_groups.length,raster_count:editability.raster_count,visual_acceptance:'pending',application_playback_verified:false,external_recognition_api_called:false};
const deliveryCandidate=path.join(run,'delivery-candidate.json');await fs.writeFile(deliveryCandidate,JSON.stringify(delivery,null,2));
execFileSync(runtime.python,[path.join(repo,'tools/figure_rebuild/publish.py'),'--source',checkedOutput,'--output',output,'--receipt',path.join(run,'delivery.json'),'--data',deliveryCandidate],{stdio:'pipe'});
console.log(JSON.stringify({output,objects:objectMap.length,nativeGroups:editability.native_groups.length,preview:path.join(run,'preview-1x.png')}));
