import test from 'node:test';
import assert from 'node:assert/strict';
import {installCpuCanvas,verifyCpuRenderer} from '../src/figure_rebuild/powerpoint/cpu_renderer.mjs';

function fakeSkia() {
  class Canvas {
    constructor(width, height, options) {
      this.width = width; this.height = height; this.options = options;
      // Reproduce the native lazy-engine trap: the constructor flag is not
      // sufficient. Only the setter selects a backend before first use.
      this.backend = 'GPU'; this.setterCalls = 0;
    }
    get gpu() { return this.backend === 'GPU'; }
    set gpu(value) { this.setterCalls += 1; this.backend = value ? 'GPU' : 'CPU'; }
    get engine() { return {renderer: this.backend, threads: 3}; }
  }
  return {Canvas};
}

test('constructor selects CPU before context use and preserves native class identity', () => {
  const skia = fakeSkia(), Original = skia.Canvas;
  const adapter = installCpuCanvas(skia, '/test/skia');
  const a = new skia.Canvas(20, 30, {textGamma: 1.8, gpu: true});
  assert.ok(a instanceof Original);
  assert.equal(a.engine.renderer, 'CPU');
  assert.equal(a.setterCalls, 1);
  assert.deepEqual(a.options, {textGamma: 1.8, gpu: false});
  assert.throws(() => { a.gpu = true; }, /cannot enable GPU/);
  assert.equal(adapter.audit().constructed_canvas_count, 1);
  assert.equal(adapter.audit().dependency_files_modified, false);
});

test('idempotent setup covers both inherited OffscreenCanvas and direct resize canvases', async () => {
  const skia = fakeSkia(), adapter = installCpuCanvas(skia);
  assert.equal(installCpuCanvas(skia), adapter);
  class OffscreenCanvas extends skia.Canvas {
    getContext() { return {fillRect() {}}; }
    async convertToBlob() { return new Blob(['encoded']); }
  }
  const receipt = await verifyCpuRenderer(adapter, OffscreenCanvas);
  const resized = new skia.Canvas(4, 4);
  assert.equal(receipt.actual_render_probe, 'PASS');
  assert.equal(resized.engine.renderer, 'CPU');
  assert.equal(adapter.audit().constructed_canvas_count, 2);
  receipt.engine.renderer = 'GPU';
  assert.equal(adapter.audit().engine.renderer, 'CPU');
});

test('late installation fails before creating an unprotected GPU canvas', async () => {
  const skia = fakeSkia(); let instantiated = false;
  class CachedOffscreenCanvas extends skia.Canvas {
    constructor() { super(); instantiated = true; }
  }
  const adapter = installCpuCanvas(skia);
  await assert.rejects(verifyCpuRenderer(adapter, CachedOffscreenCanvas), /before importing Artifact/);
  assert.equal(instantiated, false);
});

test('unsupported native backend and constructor replacement cannot silently pass', () => {
  assert.throws(() => installCpuCanvas({Canvas: class {}}), /public Canvas.gpu setter/);
  const skia = fakeSkia();
  Object.defineProperty(skia.Canvas.prototype, 'engine', {get() { return {renderer: 'GPU'}; }});
  installCpuCanvas(skia);
  assert.throws(() => new skia.Canvas(2, 2), /did not select the CPU/);
  skia.Canvas = class {};
  assert.throws(() => installCpuCanvas(skia), /replaced after installation/);
});

test('actual-render probe rejects encoding failures without manufacturing success', async () => {
  const skia = fakeSkia(), adapter = installCpuCanvas(skia);
  class BrokenCanvas extends skia.Canvas {
    getContext() { return {fillRect() {}}; }
    async convertToBlob() { throw Error('native rendering failed'); }
  }
  await assert.rejects(verifyCpuRenderer(adapter, BrokenCanvas), /native rendering failed/);
});
