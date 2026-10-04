import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {installCpuCanvas} from '../src/figure_rebuild/powerpoint/cpu_renderer.mjs';
import {installImageMinification, planImageMinification} from '../src/figure_rebuild/powerpoint/image_sampling.mjs';

const identity = {a: 1, b: 0, c: 0, d: 1};
function fakeCanvas() {
  const canvases = [], draws = [];
  class Canvas {
    constructor(width, height) {
      this.width = width; this.height = height;
      this.context = {
        canvas: this, imageSmoothingEnabled: true, transform: {...identity},
        getTransform() { return this.transform; },
        drawImage(source, ...args) {
          draws.push({canvas: this.canvas, source, sourceVersion: source?.version, args});
          return 'native-result';
        },
      };
      canvases.push(this);
    }
    getContext() { return this.context; }
  }
  return {Canvas, canvases, draws};
}

test('large reduction prefilters complete source and preserves final device destination', () => {
  const {Canvas, draws} = fakeCanvas(), sampling = installImageMinification(Canvas);
  const canvas = new Canvas(20, 30), context = canvas.getContext('2d');
  const source = {width: 80, height: 40};
  assert.equal(context.drawImage(source, 0, 0, 80, 40, 3.25, 7.5, 10, 5), 'native-result');
  assert.deepEqual(draws.map(draw => draw.args), [
    [0, 0, 80, 40, 0, 0, 40, 20], [0, 0, 40, 20, 0, 0, 20, 10],
    [0, 0, 20, 10, 0, 0, 10, 5], [0, 0, 10, 5, 3.25, 7.5, 10, 5],
  ]);
  assert.equal(draws.at(-1).canvas, canvas);
  assert.equal(context.imageSmoothingEnabled, true);
  assert.equal(sampling.audit().filtered_draw_calls, 1);
  assert.equal(sampling.audit().staging_canvas_count, 3);
  assert.equal(sampling.audit().staging_pixels_processed, 1050);
});

test('full-source 3/5-argument calls account for the actual transform', () => {
  const {Canvas, draws} = fakeCanvas(); installImageMinification(Canvas);
  const c = new Canvas(10, 10).getContext('2d');
  c.transform = {a: 0, b: .125, c: -.125, d: 0};
  c.drawImage({width: 80, height: 40}, 2, 3);
  assert.deepEqual(draws.at(-1).args, [0, 0, 10, 5, 2, 3, 80, 40]);
  c.transform = identity;
  c.drawImage({width: 80, height: 40}, 2, 3, 10, 5);
  assert.deepEqual(draws.at(-1).args, [0, 0, 10, 5, 2, 3, 10, 5]);
});

test('odd dimensions and a single-pixel axis retain complete source extents', () => {
  const plan = planImageMinification(81, 41, [0, 0, 10, 5], identity);
  assert.deepEqual(plan.stages, [{width: 41, height: 21}, {width: 21, height: 11}, {width: 11, height: 6}]);
  assert.deepEqual(plan.destination, [0, 0, 10, 5]);
  const thin = planImageMinification(80, 1, [0, 0, 10, .125], identity);
  assert.deepEqual(thin.stages, [{width: 40, height: 1}, {width: 20, height: 1}, {width: 10, height: 1}]);
});

test('shear, anisotropic magnification, small reductions and invalid calls keep native semantics', () => {
  // Both columns are shorter than .5, but their combined singular scale is >.5.
  assert.equal(planImageMinification(80, 80, [0, 0, 80, 80], {a: .4, b: 0, c: .4, d: .1}), null);
  assert.equal(planImageMinification(80, 80, [0, 0, 10, 160], identity), null);
  assert.equal(planImageMinification(80, 80, [0, 0, 60, 60], identity), null);
  for (const args of [[0], [0, 0, -10, 10], [0, 0, NaN, 10], [0, 0, 0, 10], ['0', 0]]) {
    assert.equal(planImageMinification(80, 80, args, identity), null);
  }
  assert.equal(planImageMinification(80, 80, [0, 0, 10, 10], {a: Infinity, b: 0, c: 0, d: 1}), null);
});

test('cropped windows and explicit pixel sampling are untouched and separately counted', () => {
  const {Canvas, canvases, draws} = fakeCanvas(), sampling = installImageMinification(Canvas);
  const c = new Canvas(10, 10).getContext('2d'), source = {width: 80, height: 40};
  const cropped = [1, 2, 60, 30, 3, 4, 6, 3];
  c.drawImage(source, ...cropped);
  assert.deepEqual(draws[0].args, cropped);
  assert.equal(canvases.length, 1);
  assert.equal(sampling.audit().cropped_minification_calls_unfiltered, 1);
  c.imageSmoothingEnabled = false;
  c.drawImage(source, 0, 0, 10, 5);
  assert.deepEqual(draws[1].args, [0, 0, 10, 5]);
  assert.equal(canvases.length, 1);
  assert.equal(sampling.audit().filtered_draw_calls, 0);
});

test('bounded allocation fails before any canvas is allocated or native draw is attempted', () => {
  const {Canvas, canvases, draws} = fakeCanvas(); installImageMinification(Canvas);
  const c = new Canvas(10, 10).getContext('2d');
  assert.throws(() => c.drawImage({width: 10000, height: 10000}, 0, 0, 10, 10), /staging budget/);
  assert.equal(canvases.length, 1); assert.equal(draws.length, 0);
});

test('mutable decoded sources are sampled again and context setup is idempotent', () => {
  const {Canvas, draws} = fakeCanvas(), sampling = installImageMinification(Canvas);
  const canvas = new Canvas(10, 10), c = canvas.getContext('2d'), draw = c.drawImage;
  assert.equal(canvas.getContext('2d').drawImage, draw);
  assert.equal(installImageMinification(Canvas), sampling);
  const source = {width: 80, height: 40, version: 1};
  c.drawImage(source, 0, 0, 10, 5);
  source.version = 2; c.drawImage(source, 0, 0, 10, 5);
  assert.deepEqual(draws.filter(item => item.source === source).map(item => item.sourceVersion), [1, 2]);
  const audit = sampling.audit(); audit.limits.max_stages = 999;
  assert.equal(sampling.audit().limits.max_stages, 16);
  Canvas.prototype.getContext = () => null;
  assert.throws(() => installImageMinification(Canvas), /replaced after installation/);
});

test('staging and native rendering failures propagate without a successful sampling receipt', () => {
  const {Canvas} = fakeCanvas(), sampling = installImageMinification(Canvas);
  const c = new Canvas(10, 10).getContext('2d');
  c.drawImage(null, 1); // native behavior, no fabricated source metadata
  const source = {width: 80, get height() { throw Error('source decoding failed'); }};
  assert.throws(() => c.drawImage(source, 0, 0, 10, 5), /source decoding failed/);
  assert.equal(sampling.audit().filtered_draw_calls, 0);
});

// The authoring runtime is supplied externally; core CI must not install or
// redistribute it. Local release checks enable the real CPU pixel controls.
const artifactEntry = process.env.FIGURE_REBUILD_TEST_ARTIFACT_ENTRY;
test('actual CPU pixels retain thin ink, alpha, crop isolation and changing source content',
  {skip: !artifactEntry}, () => {
    const skia = createRequire(artifactEntry)('skia-canvas'), Original = skia.Canvas;
    const adapter = installCpuCanvas(skia);
    installImageMinification(adapter.Canvas);
    const canvas = (w, h, filtered = false) => {
      const c = new (filtered ? adapter.Canvas : Original)(w, h); c.gpu = false; return c;
    };
    const source = canvas(80, 80), ink = source.getContext('2d');
    ink.fillStyle = 'white'; ink.fillRect(0, 0, 80, 80);
    ink.fillStyle = 'black'; for (let x = 0; x < 80; x += 3) ink.fillRect(x, 0, 1, 80);
    const raw = canvas(10, 10), target = canvas(10, 10, true);
    raw.getContext('2d').drawImage(source, 0, 0, 10, 10);
    target.getContext('2d').drawImage(source, 0, 0, 10, 10);
    const baseline = raw.getContext('2d').getImageData(0, 0, 10, 1).data;
    const result = target.getContext('2d').getImageData(0, 0, 10, 1).data;
    assert.ok([...baseline].some((v, i) => i % 4 === 0 && v === 255));
    // Eight source pixels contain either two or three black stripes. Their
    // filtered gray values differ; every output sample must still retain ink.
    assert.ok([...result].every((v, i) => i % 4 !== 0 || (v > 150 && v < 200)));
    const alpha = canvas(8, 8), a = alpha.getContext('2d');
    a.fillStyle = '#ff0000'; a.fillRect(0, 0, 1, 8);
    const small = canvas(1, 1, true), smallContext = small.getContext('2d');
    smallContext.drawImage(alpha, 0, 0, 1, 1);
    const pixel = smallContext.getImageData(0, 0, 1, 1).data;
    assert.deepEqual([...pixel].slice(0, 3), [255, 0, 0]);
    assert.ok(pixel[3] >= 30 && pixel[3] <= 34);
    const cropRaw = canvas(1, 1), cropFiltered = canvas(1, 1, true);
    for (const c of [cropRaw, cropFiltered]) c.getContext('2d').drawImage(alpha, 1, 0, 7, 8, 0, 0, 1, 1);
    assert.deepEqual(cropFiltered.getContext('2d').getImageData(0, 0, 1, 1).data,
      cropRaw.getContext('2d').getImageData(0, 0, 1, 1).data);
    a.clearRect(0, 0, 8, 8); a.fillStyle = '#0000ff'; a.fillRect(0, 0, 8, 8);
    smallContext.clearRect(0, 0, 1, 1); smallContext.drawImage(alpha, 0, 0, 1, 1);
    assert.deepEqual([...smallContext.getImageData(0, 0, 1, 1).data], [0, 0, 255, 255]);
  });
