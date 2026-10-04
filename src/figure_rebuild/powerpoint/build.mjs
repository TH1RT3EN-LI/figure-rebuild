import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {runPythonModule} from './runtime.mjs';
import {checkRuntime} from './preflight.mjs';
import {fittedTextBox,fontFaceForText,measurePresentationBaseline} from './text_fit.mjs';
import {flattenPath,pathBounds} from './curves.mjs';
import {fitPlacement} from './placement.mjs';
import {fitImagePlacement} from './image_placement.mjs';
import {linearGradientFill} from './linear_gradient.mjs';
import {configureCpuRenderer} from './cpu_renderer.mjs';

const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const {job,run,runtime,output}=config;
const previewBackend=config.preview_backend??'artifact';
if(!['artifact','libreoffice'].includes(previewBackend))throw Error('Unknown preview backend: '+String(previewBackend));
if(config.preview_provenance_version!==undefined&&config.preview_provenance_version!==1)throw Error('Unsupported preview provenance version');
if(config.diagnostic_provenance_version!==undefined&&config.diagnostic_provenance_version!==1)throw Error('Unsupported diagnostic provenance version');
if(previewBackend==='libreoffice'&&config.base)throw Error('LibreOffice preview does not yet support base-deck slide mapping');
if(config.pdf_alpha_derivation!==undefined&&(config.pdf_alpha_derivation!=='binary-alpha-white-matte-v1'||previewBackend!=='libreoffice'))throw Error('Unsupported PDF alpha derivation policy or backend');
const packageRoot=config.package_root;
const runPython=(module,args,options={})=>runPythonModule(runtime,packageRoot,module,args,options);
const assetRoot=config.asset_root??job;
const runtimeCheck=await checkRuntime(runtime,packageRoot);
process.env.RUNTIME_NODE_MODULES=runtime.node_modules;
process.env.RUNTIME_NODE=runtime.node;
process.env.RUNTIME_PYTHON=runtime.python;
const resolvedManifest=path.join(run,'resolved-scene.json');
runPython('scene_compile',['--manifest',config.manifest,'--job',job,'--asset-root',assetRoot,'--output',resolvedManifest,'--audit',path.join(run,'semantic-audit.json')],{stdio:'pipe'});
const manifest=JSON.parse(await fs.readFile(resolvedManifest,'utf8'));
const hasCanvasClip=Object.hasOwn(manifest,'source_canvas_clip');
if(hasCanvasClip&&(config.base||config.source_canvas_clip_provenance_version!==1))throw Error('Source canvas clipping requires standalone version-1 provenance');
if(!hasCanvasClip&&config.source_canvas_clip_provenance_version!==undefined)throw Error('Canvas clipping provenance has no source declaration');
const canvasClipSourcePath=path.join(run,'source-canvas-clip.json');
const canvasClipNativePath=path.join(run,'native-canvas-clip.json');
if(hasCanvasClip)runPython('source_canvas_clip',['--manifest',resolvedManifest,'--root',assetRoot,'--output',canvasClipSourcePath],{stdio:'pipe'});
const originalSource=path.join(assetRoot,manifest.source.path);
if(createHash('sha256').update(await fs.readFile(originalSource)).digest('hex')!==manifest.source.sha256)throw Error('Original source changed before authoring');
const req=createRequire(path.join(runtime.node_modules,'figure-rebuild-loader.cjs'));
const previewImageSampling=configureCpuRenderer(req.resolve('@oai/artifact-tool')).imageSampling;
const {Presentation,PresentationFile,FileBlob,defaultFontMetricsProvider,skiaPaintBaselineCompensationPx}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
if(!defaultFontMetricsProvider||typeof defaultFontMetricsProvider.getMetricsForSize!=='function'||typeof defaultFontMetricsProvider.reset!=='function'||typeof skiaPaintBaselineCompensationPx!=='function')throw Error('Configured Artifact Tool lacks required presentation baseline metrics API');
const artifactRequire=createRequire(req.resolve('@oai/artifact-tool'));
const {FontLibrary}=await import(pathToFileURL(artifactRequire.resolve('skia-canvas')).href);
const {GlobalFonts,createCanvas,loadImage}=await import(pathToFileURL(req.resolve('@napi-rs/canvas')).href);
const fontFamily=runtime.fonts.family;
const fontsDir=path.join(run,'fonts');
const fontAudit=JSON.parse(runPython('font_prepare',['--config',process.argv[2],'--manifest',config.manifest,'--output-dir',fontsDir],{encoding:'utf8'}));
for(const face of fontAudit)if(!GlobalFonts.registerFromPath(face.renderer,face.family))throw Error('Could not load configured '+face.family+' '+face.role+' font face');
const fontFamilies=[...new Set(fontAudit.map(face=>face.family))];
for(const family of fontFamilies){
 if(!GlobalFonts.has(family))throw Error('Configured font family was not registered: '+family);
 // Register the identical audited bytes with the actual presentation renderer.
 FontLibrary.use(family,fontAudit.filter(face=>face.family===family).map(face=>face.renderer));
}
defaultFontMetricsProvider.reset();
await fs.writeFile(path.join(run,'font-audit.json'),JSON.stringify(fontAudit,null,2));
const ctx=createCanvas(2,2).getContext('2d');
const rendererContext=new OffscreenCanvas(2,2).getContext('2d');
const sourceCanvas=manifest.canvas;
const slideCanvas=config.base?.canvas??sourceCanvas;
const requested=config.base?.placement??[0,0,sourceCanvas.width,sourceCanvas.height];
const placementAudit=fitPlacement(sourceCanvas,slideCanvas,requested);
const {scale,placement}=placementAudit;
for(const object of manifest.objects.filter(o=>o.source_kind==='formula')){
 const formula=object.formula_asset.placement;
 const actualSampling=formula.sampling_scale/scale;
 if(actualSampling+1e-8<formula.min_sampling_scale)throw Error(`Formula PNG fallback sampling below ${formula.min_sampling_scale}x after slide placement: ${object.id} (${actualSampling.toFixed(4)}x)`);
 object.formula_asset.delivery_sampling_scale=actualSampling;
}
const ordered=manifest.objects.map((o,i)=>({...o,_order:i})).sort((a,b)=>(a.z_index??a._order)-(b.z_index??b._order)||a._order-b._order);
const measuredText=new Map();
for(const object of ordered.filter(object=>object.kind==='text')){
 const family=object.font_family??fontFamily;
 fontFaceForText(object,fontAudit,fontFamily);
 ctx.font=`${object.italic?'italic ':''}${object.bold?'bold ':''}${object.font_size}px "${family}"`;
 const rendererBaseline=measurePresentationBaseline(object,{context:rendererContext,fontMetricsProvider:defaultFontMetricsProvider,paintBaselineCompensation:skiaPaintBaselineCompensationPx,scale,defaultFamily:fontFamily});
 measuredText.set(object.id,fittedTextBox(object,text=>ctx.measureText(text),sourceCanvas,{rendererBaseline}));
}
const textFitReport={schema_version:1,status:measuredText.size?'PASS':'NOT_APPLICABLE',scope:'measured_live_text_only',counts:{resolved_objects:ordered.length,live_text_objects:ordered.filter(o=>o.kind==='text').length,measured_live_text_objects:measuredText.size,unmeasured_path_objects:ordered.filter(o=>o.kind==='path').length,unmeasured_image_objects:ordered.filter(o=>o.kind==='image').length},font_family:fontFamily,visual_verification_required:true,limitations:['Measures declared live text layout only; glyph outlines and text inside images are not measured.','Font metrics and layout checks do not establish source-content correctness or native application appearance.'],objects:[...measuredText].map(([id,result])=>({id,...result}))};
await fs.writeFile(path.join(run,'text-fit.json'),JSON.stringify(textFitReport,null,2));
runPython('export_svg',['--manifest',resolvedManifest,'--asset-root',assetRoot,'--output',path.join(run,'reconstructed.svg'),'--font',fontAudit[0].renderer,'--bold-font',fontAudit[1].renderer,'--family',fontFamily,'--font-audit',path.join(run,'font-audit.json')],{stdio:'pipe'});
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
  slide.shapes.add({name:o.id,geometry:'custom',position:position(box),fill:linearGradientFill(s)??paint(s.fill,s.opacity),line:{fill:paint(s.stroke,s.opacity),width:(s.stroke_width??0)*scale,style:'solid'},customPaths:[{width,height,commands}]});
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,box,editable:true});
 }else if(o.kind==='text'){
  const fontSize=o.font_size;
  const {box,layout}=measuredText.get(o.id);
  const shape=slide.shapes.add({name:o.id,geometry:'textbox',position:position(box,o.rotation??0),fill:'none',line:{fill:'none',width:0}});
  shape.text=o.text;
  const family=o.font_family??fontFamily;
  shape.text.style={typeface:family,fontSize:fontSize*scale,bold:o.bold??false,italic:o.italic??false,color:paint(s.fill??'#000000',s.opacity),alignment:o.alignment??'left',verticalAlignment:o.vertical_alignment??'top',autoFit:'none',wrap:o.wrap??'none',insets:Object.fromEntries(Object.entries(layout.insets??{left:0,right:0,top:0,bottom:0}).map(([k,v])=>[k,v*scale]))};
  textManifest.push({id:o.id,content:o.text,source_bbox:box,font_family:family,font_size:fontSize,rotation:o.rotation??0,alignment:o.alignment??'left',paint_order:o.z_index??o._order,measured_line_count:layout.line_count});
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,box,editable:true,text_layout:layout});
 }else if(o.kind==='image'){
  const bytes=await fs.readFile(path.join(assetRoot,o.path));
  if(createHash('sha256').update(bytes).digest('hex')!==o.sha256)throw Error('Raster asset changed');
  const ext=path.extname(o.path).slice(1).toLowerCase(),type=ext==='jpg'?'jpeg':ext;
  const decoded=await loadImage(bytes),imagePlacement=fitImagePlacement(o,decoded);
  const {box:fitted,crop}=imagePlacement;
  // Resolve fit ourselves; the authoring tool's automatic fit can clear crop.
  const image=slide.images.add({blob:new Uint8Array(bytes),contentType:`image/${type}`,alt:o.id,position:position(fitted),crop,geometry:'rect'});
  image.lockAspectRatio=false;
  objectMap.push({id:o.id,kind:o.kind,group_id:o.group_id,...imagePlacement,editable:false});
 }else throw Error('Unsupported object kind');
}
const liveTextObjects=ordered.filter(o=>o.kind==='text');
const liveTextFamilies=[...new Set(liveTextObjects.map(o=>o.font_family??fontFamily))];
slide.speakerNotes.textFrame.setText(`Source: ${manifest.source.uri||manifest.source.path}\nClassification: ${manifest.source.kind}. Original SHA256: ${manifest.source.sha256}. Recognition: ${manifest.recognition.provider}. ${manifest.recognition.notes||''}\nNative paths: ${ordered.filter(o=>o.kind==='path').length}; live text boxes: ${liveTextObjects.length}; embedded image assets: ${ordered.filter(o=>o.kind==='image').length}. Glyph outlines, when present, are paths and are not live text. Original raster panels remain raster.${liveTextFamilies.length?` Live text uses configured families: ${liveTextFamilies.join(', ')}.`:''} This reconstruction does not add new experimental evidence. Visual acceptance pending.`);
await fs.writeFile(path.join(run,'text-manifest.json'),JSON.stringify({schema_version:1,source_canvas:sourceCanvas,text_elements:textManifest},null,2));
await fs.writeFile(path.join(run,'object-map.json'),JSON.stringify({...placementAudit,objects:objectMap},null,2));
const raw=path.join(run,'artifact-authored.pptx');await (await PresentationFile.exportPptx(p)).save(raw);
const grouped=path.join(run,'grouped-overlay.pptx');
// The full geometry proof is already saved in editability.json. Repeating it
// on captured stdout can exceed execFileSync's buffer even for valid figures.
runPython('postprocess',['--input',raw,'--output',grouped,'--manifest',resolvedManifest,'--asset-root',assetRoot,'--object-map',path.join(run,'object-map.json'),'--placement',...placement.map(String),'--receipt',path.join(run,'editability.json'),'--quiet'],{stdio:'pipe'});
const candidate=path.join(run,'candidate.pptx');
if(config.base){
 const args=['merge','--base',config.base.path,'--overlay',grouped,'--output',candidate,'--slide-id',config.base.slide_id,'--base-sha256',config.base.sha256,'--receipt',path.join(run,'preservation.json')];
 for(const name of config.base.replace_ids)args.push('--replace-id',name);
 runPython('package',args,{stdio:'pipe'});
}else await fs.copyFile(grouped,candidate);
const {finalizePresentation}=await import(pathToFileURL(path.join(runtime.presentation_skill,'container_tools/artifact_tool_utils.mjs')).href);
const expectedSize=[Math.round(slideCanvas.width*9525),Math.round(slideCanvas.height*9525)].join(',');
const checkedOutput=path.join(run,'validated-output','reconstruction.pptx');await fs.mkdir(path.dirname(checkedOutput),{recursive:true});
// This renderer decodes SVG at frame size times devicePixelRatio. Preserve
// vector source bytes and increase real decode sampling before PPT import.
globalThis.devicePixelRatio=8;
const explicitStrokeObjects=ordered.filter(o=>o.kind==='path'&&['stroke_linecap','stroke_linejoin','stroke_miterlimit'].some(key=>key in (o.style??{}))).map(o=>o.id);
const previewLimitations=explicitStrokeObjects.length?[{code:'native_stroke_geometry_requires_application_verification',object_ids:explicitStrokeObjects,detail:'Native cap/join/miter values are written to the final PPTX. Artifact Tool 2.8.59 ignores them on preview import; preview alone cannot verify these details.'}]:[];
const nativeLayoutAudit=JSON.parse(await fs.readFile(path.join(run,'editability.json'),'utf8'));
const fractionalSpacingObjects=(nativeLayoutAudit.text_layout??[]).filter(record=>record.native_precision_review_required);
if(fractionalSpacingObjects.length)previewLimitations.push({code:'fractional_percent_multiline_spacing_requires_application_verification',status:'needs_review',object_ids:fractionalSpacingObjects.map(record=>record.id),objects:fractionalSpacingObjects.map(record=>({id:record.id,line_count:record.line_count,spacing_thousandths_percent:record.spacing_thousandths_percent,whole_percent_fallback_accumulated_loss_px:record.whole_percent_fallback_accumulated_loss_px})),detail:'Some native applications reduce percentage line spacing to whole percent. Multiline text can accumulate pitch error; inspect the intended application. The recorded loss is a whole-percent fallback model, not a universal measured error bound.'});
const varyingGradientAlpha=ordered.filter(o=>o.style?.fill_gradient&&new Set(o.style.fill_gradient.stops.map(stop=>stop.opacity??1)).size>1).map(o=>o.id);
const spacedText=ordered.filter(o=>o.kind==='text'&&Object.hasOwn(o,'character_spacing')).map(o=>o.id);
if(spacedText.length)previewLimitations.push({code:'character_spacing_requires_application_verification',status:'needs_review',object_ids:spacedText,detail:'Explicit ASCII character advances and disabled automatic kerning are written as native editable character runs. Registered-font measurement and point rounding do not prove actual application glyph positions or shaping. Inspect native output against the source.'});
if(varyingGradientAlpha.length)previewLimitations.push({code:'gradient_stop_opacity_interpolation_requires_application_verification',status:'needs_review',object_ids:varyingGradientAlpha,detail:'Native stop RGB and alpha are verified in the final PPT. Artifact Tool 2.8.59 previews interpolate varying stop alpha differently from the SVG reference and LibreOffice. Inspect native application output; preview color alone cannot verify this fill.'});
await finalizePresentation({workspaceDir:job,candidatePath:candidate,finalPath:checkedOutput,pythonExecutable:runtime.python,integrityValidatorPath:path.join(runtime.presentation_skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(runtime.presentation_skill,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu',expectedSize],fontPolicy:config.base?undefined:{basis:'design',families:fontFamilies},verifyArtifactToolImport:true,receiptPath:path.join(run,'validation.json')});
const bindFile=async file=>({path:path.resolve(file),sha256:createHash('sha256').update(await fs.readFile(file)).digest('hex')});
const finalPptBinding=await bindFile(checkedOutput);
let canvasClipProvenance;
if(hasCanvasClip){
 runPython('source_canvas_clip',['--manifest',resolvedManifest,'--root',assetRoot,'--pptx',checkedOutput,'--source-receipt',canvasClipSourcePath,'--output',canvasClipNativePath],{stdio:'pipe'});
 canvasClipProvenance={schema_version:1,source_receipt:await bindFile(canvasClipSourcePath),native_receipt:await bindFile(canvasClipNativePath),resolved_scene:await bindFile(resolvedManifest),pptx:finalPptBinding};
}
let diagnosticCoverage;
if(config.diagnostic_provenance_version===1){
 const sourceAudit=JSON.parse(await fs.readFile(path.join(run,'source-content-audit.json'),'utf8'));
 const semanticAudit=JSON.parse(await fs.readFile(path.join(run,'semantic-audit.json'),'utf8'));
 diagnosticCoverage={schema_version:1,inputs:{source:await bindFile(originalSource),manifest:await bindFile(config.manifest),resolved_scene:await bindFile(resolvedManifest),pptx:finalPptBinding},reports:{
  source_content_audit:{artifact:await bindFile(path.join(run,'source-content-audit.json')),status:sourceAudit.status,scope:'declared_invariants_only',counts:{literals_checked:sourceAudit.coverage.literals_checked,connections_checked:sourceAudit.coverage.connections_checked}},
  semantic_audit:{artifact:await bindFile(path.join(run,'semantic-audit.json')),status:semanticAudit.formulas.length+semanticAudit.connections.length?'RECORDED':'NOT_PROVIDED',scope:'declared_formula_connection_records_only',counts:{formula_records:semanticAudit.formulas.length,connection_records:semanticAudit.connections.length}},
  text_fit:{artifact:await bindFile(path.join(run,'text-fit.json')),status:textFitReport.status,scope:textFitReport.scope,counts:textFitReport.counts}
 },semantic_recognition_performed:false,source_fidelity_evaluated:false,visual_acceptance:'pending'};
}
let previewAudit;
if(previewBackend==='libreoffice'){
 previewAudit=JSON.parse(runPython('native_preview',['--config',process.argv[2]],{encoding:'utf8'}));
 previewAudit.preview_limitations.push(...previewLimitations.filter(item=>['fractional_percent_multiline_spacing_requires_application_verification','character_spacing_requires_application_verification'].includes(item.code)));
}else{
 const rendered=await PresentationFile.importPptx(await FileBlob.load(checkedOutput));
 let targetSlide=rendered.slides.items[0];
 if(config.base){
  const info=JSON.parse(runPython('package',['inspect',checkedOutput],{encoding:'utf8'}));
  const at=info.slides.findIndex(s=>String(s.slide_id??s.id)===config.base.slide_id);if(at<0)throw Error('Stable slide vanished');targetSlide=rendered.slides.items[at];
 }
 for(const s of [1,2,4]){const blob=await rendered.export({slide:targetSlide,format:'png',scale:s});await fs.writeFile(path.join(run,`preview-${s}x.png`),new Uint8Array(await blob.arrayBuffer()));}
 const imageSamplingAudit=previewImageSampling.audit();
 if(imageSamplingAudit.cropped_minification_calls_unfiltered)previewLimitations.push({code:'cropped_image_minification_requires_visual_verification',draw_calls:imageSamplingAudit.cropped_minification_calls_unfiltered,detail:'Staged minification covers complete source windows. Cropped source windows retain native interpolation to avoid mixing excluded pixels into crop edges.'});
 previewAudit={renderer:'Codex Artifact Tool',renderer_backend:runtimeCheck.renderer_backend,cpu_renderer:runtimeCheck.cpu_renderer,image_sampling:{...imageSamplingAudit,scope:'artifact_process_through_raw_preview_exports'},svg_decode_device_pixel_ratio:8,source_media_bytes_modified:false,preview_scales:[1,2,4],raw_diagnostic_scale:1,application_playback_verified:false,preview_limitations:previewLimitations,evidence:{font_audit:await bindFile(path.join(run,'font-audit.json'))}};
}
// Keep the raw 1x preview for comparison diagnostics. The viewing aide uses
// supersampling so thin mathematical strokes are filtered rather than dropped.
const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
await sharp(path.join(run,'preview-4x.png')).resize(Math.round(slideCanvas.width),Math.round(slideCanvas.height),{kernel:'lanczos3'}).png().toFile(path.join(run,'preview-smooth-1x.png'));
if(JSON.stringify(await bindFile(checkedOutput))!==JSON.stringify(finalPptBinding))throw Error('Final PPTX changed during preview rendering');
const previewBindings={};
for(const [role,filename,scale] of [['preview_1x','preview-1x.png',1],['preview_2x','preview-2x.png',2],['preview_4x','preview-4x.png',4],['preview_smooth_1x','preview-smooth-1x.png',1]]){
 const file=path.join(run,filename),size=await sharp(file).metadata();
 previewBindings[role]={...await bindFile(file),width:size.width,height:size.height,scale};
}
previewBindings.preview_smooth_1x.derivation={source_role:'preview_4x',source_sha256:previewBindings.preview_4x.sha256,kernel:'lanczos3',target_size:[previewBindings.preview_smooth_1x.width,previewBindings.preview_smooth_1x.height],is_raw_preview:false};
const renderAuditPath=path.join(run,'render-audit.json');
await fs.writeFile(renderAuditPath,JSON.stringify({...previewAudit,schema_version:1,preview_backend:previewBackend,input_pptx:finalPptBinding,previews:previewBindings,...(diagnosticCoverage?{diagnostic_coverage:diagnosticCoverage}:{}),...(canvasClipProvenance?{source_canvas_clip:canvasClipProvenance}:{})},null,2));
// Use resolved source-coordinate frames: SVG text may only have a baseline
// anchor, and image contain fitting can differ from its requested box.
const comparisonScene=path.join(run,'comparison-scene.json');
const byId=new Map(ordered.map(object=>[object.id,object]));
await fs.writeFile(comparisonScene,JSON.stringify({canvas:sourceCanvas,derived_from:config.manifest,diagnostic_only:true,objects:objectMap.map(object=>{
 const original=byId.get(object.id),pad=object.kind==='path'?(original.style?.stroke_width??0)/2:0;
 const box={x:object.box.x-pad,y:object.box.y-pad,width:object.box.width+2*pad,height:object.box.height+2*pad};
 const expected=original.box??box;
 return {id:object.id,kind:object.kind,box,expected_source_box:expected,actual_frame:object.box,rotation:original.rotation??0};
})},null,2));
const compareArgs=['--reference',path.join(assetRoot,manifest.source.path),'--rebuilt',path.join(run,'preview-1x.png'),'--output',path.join(run,'comparison.png'),'--metrics',path.join(run,'comparison-metrics.json'),'--font',fontAudit[0].renderer,'--manifest',comparisonScene];
if(path.extname(manifest.source.path).toLowerCase()==='.svg'){
 const sharp=(await import(pathToFileURL(req.resolve('sharp')).href)).default;
 const raster=path.join(run,'svg-reference.png');await sharp(path.join(assetRoot,manifest.source.path)).png().toFile(raster);compareArgs[compareArgs.indexOf('--reference')+1]=raster;
}
if(config.base)compareArgs.push('--region',...placement.map(String));
runPython('compare',compareArgs,{stdio:'pipe'});
for(const asset of JSON.parse(await fs.readFile(path.join(run,'asset-snapshot.json'),'utf8')).assets){
 for(const root of new Set([assetRoot,job]))if(createHash('sha256').update(await fs.readFile(path.join(root,asset.path))).digest('hex')!==asset.sha256)throw Error('Asset changed during the build; output not published: '+asset.path);
}
const editability=JSON.parse(await fs.readFile(path.join(run,'editability.json'),'utf8'));
const delivery={output,sha256:createHash('sha256').update(await fs.readFile(checkedOutput)).digest('hex'),source_sha256:manifest.source.sha256,source_preserved:true,recognition_provider:manifest.recognition.provider,native_path_count:editability.path_count,native_text_count:editability.text_count,native_group_count:editability.native_groups.length,raster_count:editability.raster_count,formula_count:editability.formula_count??0,svg_formula_count:editability.svg_formula_count??0,native_connector_count:editability.native_connectors?.length??0,visual_acceptance:'pending',application_playback_verified:false,external_recognition_api_called:false};
if(previewAudit.pdf_alpha_derivation)delivery.pdf_alpha_derivation={...previewAudit.pdf_alpha_derivation,original_pdf:previewAudit.evidence.native_pdf,derived_pdf:previewAudit.evidence.native_pdf_derived,receipt:previewAudit.evidence.native_pdf_alpha_receipt};
delivery.preview_backend=previewBackend;
delivery.render_audit_sha256=(await bindFile(renderAuditPath)).sha256;
if(diagnosticCoverage)delivery.diagnostic_coverage=diagnosticCoverage;
if(canvasClipProvenance)delivery.source_canvas_clip=canvasClipProvenance;
const deliveryCandidate=path.join(run,'delivery-candidate.json');await fs.writeFile(deliveryCandidate,JSON.stringify(delivery,null,2));
runPython('publish',['--source',checkedOutput,'--output',output,'--receipt',path.join(run,'delivery.json'),'--data',deliveryCandidate],{stdio:'pipe'});
console.log(JSON.stringify({output,objects:objectMap.length,nativeGroups:editability.native_groups.length,preview:path.join(run,'preview-1x.png')}));
