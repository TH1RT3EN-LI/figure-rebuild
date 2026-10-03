// Opt-in integration fixture. All font bytes come from the configured runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const [configPath, outputDir] = process.argv.slice(2);
const runtime = JSON.parse(await fs.readFile(configPath, 'utf8'));
const req = createRequire(path.join(runtime.node_modules, 'baseline-fixture.cjs'));
process.env.RUNTIME_NODE_MODULES = runtime.node_modules;
process.env.RUNTIME_NODE = runtime.node;
process.env.RUNTIME_PYTHON = runtime.python;
const {Presentation, PresentationFile} = await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
const {GlobalFonts, createCanvas} = await import(pathToFileURL(req.resolve('@napi-rs/canvas')).href);
const {Canvas: SkiaCanvas, FontLibrary} = await import(pathToFileURL(
  createRequire(req.resolve('@oai/artifact-tool')).resolve('skia-canvas')).href);
const profiles = [runtime.fonts, ...(runtime.fonts.additional ?? [])].slice(0, 2);
if (profiles.length < 2) throw Error('Baseline rendering regression requires two configured font families');
for (const family of profiles) {
  FontLibrary.use(family.family, ['regular', 'bold', 'italic', 'boldItalic']
    .filter(role => family[role]).map(role => family[role].path));
  for (const role of ['regular', 'bold', 'italic', 'boldItalic']) {
    if (family[role] && !GlobalFonts.registerFromPath(family[role].path, family.family)) throw Error('Font registration failed');
  }
}
const ctx = createCanvas(2, 2).getContext('2d');
const samples = [], objects = [];
function add(family, size, height, mode, lines, vertical = 'top', rotation = 0, calibrated = false) {
  const i = samples.length, x = 30 + (i % 6) * 220, rowY = 20 + Math.floor(i / 6) * 130;
  ctx.font = `${size}px "${family}"`;
  const sourceBaseline = calibrated ? size * 1.35 : ctx.measureText('Mg').fontBoundingBoxAscent + .1 * size;
  const pitch = height === undefined ? size * 1.2 : height;
  const id = `probe-${String(i).padStart(3, '0')}`;
  const object = {id, kind: 'text', text: Array(lines).fill('Hgx09').join('\n'), font_family: family,
    font_size: size, alignment: 'left', vertical_alignment: vertical, wrap: 'none', style: {fill: '#000000'}};
  if (height !== undefined) object.line_height = height;
  if (calibrated) object.baseline_offset = sourceBaseline;
  const box = {x, y: rowY + 14, width: 170, height: 86};
  let baseline;
  if (mode === 'anchor') { baseline = rowY + 45; object.anchor = {x, y: baseline}; }
  else {
    object.box = box;
    object.insets = {top: 3, bottom: 7, left: 0, right: 0};
    const factor = vertical === 'middle' ? .5 : vertical === 'bottom' ? 1 : 0;
    baseline = box.y + 3 + factor * (box.height - 10 - lines * pitch) + sourceBaseline;
    if (rotation) object.rotation = rotation;
  }
  objects.push(object);
  samples.push({id, family, size, pitch, lines, mode, vertical, rotation, calibrated, x,
    baseline, box, roi: [x - 23, rowY - 3, x + 190, rowY + 123]});
}
for (const profile of profiles) {
  for (const size of [10, 20, 40]) for (const mode of ['anchor', 'box']) {
    for (const height of [undefined, size, size * 1.2]) add(profile.family, size, height, mode, 1);
  }
  for (const height of [undefined, 20, 24]) {
    add(profile.family, 20, height, 'anchor', 3);
    for (const vertical of ['top', 'middle', 'bottom']) add(profile.family, 20, height, 'box', 3, vertical);
    add(profile.family, 20, height, 'anchor', 2, 'top', 0, true);
    add(profile.family, 20, height, 'box', 2, 'middle', 0, true);
  }
  for (const vertical of ['middle', 'bottom']) {
    add(profile.family, 13.137, 15.741, 'box', 2, vertical, 17, true);
    add(profile.family, 13.137, undefined, 'box', 2, vertical, -13, false);
  }
}
const width = 1340, height = Math.ceil(samples.length / 6) * 130 + 30;
const canvas = {width, height, background: '#FFFFFF'};
await fs.mkdir(outputDir, {recursive: true});
for (const placementScale of [1, .637]) {
  const offset = placementScale === 1 ? 0 : 17;
  const dims = [Math.ceil(width * placementScale + 2 * offset), Math.ceil(height * placementScale + 2 * offset)];
  for (const density of [1, 4]) for (const physicalRendererReference of [false, true]) {
    const image = physicalRendererReference ? new SkiaCanvas(dims[0] * density, dims[1] * density)
      : createCanvas(dims[0] * density, dims[1] * density);
    const c = image.getContext('2d');
    c.fillStyle = '#fff'; c.fillRect(0, 0, image.width, image.height);
    c.scale(density, density); c.translate(offset, offset);
    if (!physicalRendererReference) c.scale(placementScale, placementScale);
    const coordinateScale = physicalRendererReference ? placementScale : 1;
    c.fillStyle = '#000'; c.textBaseline = 'alphabetic';
    for (const s of samples) {
      c.save();
      if (s.rotation) {
        const cx = (s.box.x + s.box.width / 2) * coordinateScale;
        const cy = (s.box.y + s.box.height / 2) * coordinateScale;
        c.translate(cx, cy); c.rotate(s.rotation * Math.PI / 180); c.translate(-cx, -cy);
      }
      // Reference baselines come from the source fixture, independently of FR's
      // native baseline helper. Native PPT stores font sizes in 1/100 pt. Draw
      // at that physical size to avoid confusing scaled-source font hinting
      // with baseline error; retain the original NAPI reference separately.
      const fontSize = physicalRendererReference ? Math.round(s.size * placementScale * 75) / 75 : s.size;
      c.font = `${fontSize}px "${s.family}"`;
      for (let line = 0; line < s.lines; line++) {
        c.fillText('Hgx09', s.x * coordinateScale, (s.baseline + line * s.pitch) * coordinateScale);
      }
      c.restore();
    }
    const prefix = physicalRendererReference ? 'reference-renderer' : 'reference';
    const bytes = physicalRendererReference ? await image.toBuffer('png') : image.toBuffer('image/png');
    await fs.writeFile(path.join(outputDir, `${prefix}-${placementScale}-${density}x.png`), bytes);
  }
  if (placementScale !== 1) {
    const p = Presentation.create({slideSize: {width: dims[0], height: dims[1]}});
    p.slides.add().background.fill = '#FFFFFF';
    await (await PresentationFile.exportPptx(p)).save(path.join(outputDir, 'blank-base.pptx'));
  }
}
await fs.writeFile(path.join(outputDir, 'fixture.json'), JSON.stringify({canvas, objects, samples}, null, 2));
