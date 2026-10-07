/** Keep Artifact's Skia canvases off the Vulkan cleanup thread during exit.
 * This changes only the current Node process's module exports, never dependency
 * files. Install before importing Artifact so both OffscreenCanvas and its
 * internal image-resize canvases use the same constructor.
 */
import {createRequire} from 'node:module';
import {installImageMinification} from './image_sampling.mjs';

const adapters = new WeakMap();
export const CPU_RENDERER_NOTICE = 'CPU rendering avoids the Skia Vulkan/NVIDIA exit cleanup race. CPU and GPU antialiasing can differ; preview hashes and visual reviews must be regenerated for this backend.';

export function installCpuCanvas(skia, modulePath = 'skia-canvas') {
  if (!skia || typeof skia.Canvas !== 'function') throw Error('CPU renderer requires skia-canvas Canvas');
  if (adapters.has(skia)) {
    const adapter = adapters.get(skia);
    if (skia.Canvas !== adapter.Canvas) throw Error('CPU renderer constructor was replaced after installation');
    return adapter;
  }
  const OriginalCanvas = skia.Canvas;
  const gpu = Object.getOwnPropertyDescriptor(OriginalCanvas.prototype, 'gpu');
  if (typeof gpu?.set !== 'function' || typeof gpu?.get !== 'function') {
    throw Error('CPU renderer requires the public Canvas.gpu setter');
  }
  let constructed = 0, engine;
  class FigureRebuildCpuCanvas extends OriginalCanvas {
    constructor(width, height, options = {}) {
      super(width, height, {...options, gpu: false});
      // v3.0.8's constructor flag alone does not set its lazy native engine.
      // The public setter explicitly installs the CPU engine before rendering.
      gpu.set.call(this, false);
      const actual = this.engine;
      if (actual?.renderer !== 'CPU' || gpu.get.call(this) !== false) {
        throw Error('Configured Skia Canvas did not select the CPU renderer');
      }
      constructed += 1;
      engine = {...actual};
    }
    get gpu() { return gpu.get.call(this); }
    set gpu(value) {
      if (value) throw Error('Figure Rebuild CPU renderer cannot enable GPU rendering');
      gpu.set.call(this, false);
    }
  }
  skia.Canvas = FigureRebuildCpuCanvas;
  if (skia.Canvas !== FigureRebuildCpuCanvas) throw Error('Could not install the CPU Canvas constructor');
  const adapter = {
    Canvas: FigureRebuildCpuCanvas,
    audit: () => ({backend: 'CPU', adapter: 'figure-rebuild-cpu-canvas-v1',
      skia_module: modulePath, constructed_canvas_count: constructed, engine: engine ? {...engine} : null,
      dependency_files_modified: false, process_local: true,
      preview_regeneration_required: true, antialiasing_notice: CPU_RENDERER_NOTICE}),
  };
  adapters.set(skia, adapter);
  return adapter;
}

export function configureCpuRenderer(artifactEntry) {
  const requireArtifact = createRequire(artifactEntry);
  const modulePath = requireArtifact.resolve('skia-canvas');
  const adapter = installCpuCanvas(requireArtifact('skia-canvas'), modulePath);
  const imageSampling = installImageMinification(adapter.Canvas);
  return {...adapter, imageSampling, audit: () => ({...adapter.audit(), image_sampling: imageSampling.audit()})};
}

export async function verifyCpuRenderer(adapter, OffscreenCanvasClass = globalThis.OffscreenCanvas) {
  // Detect a late installation rather than letting an already captured original
  // constructor initialize the GPU while trying to inspect its engine.
  if (typeof OffscreenCanvasClass !== 'function' ||
      !(OffscreenCanvasClass.prototype instanceof adapter.Canvas)) {
    throw Error('Artifact OffscreenCanvas did not capture the CPU Canvas adapter; install it before importing Artifact');
  }
  const canvas = new OffscreenCanvasClass(2, 2);
  const context = canvas.getContext('2d');
  if (!context || typeof canvas.convertToBlob !== 'function') throw Error('Artifact CPU canvas cannot render');
  context.fillStyle = '#123456';
  context.fillRect(0, 0, 2, 2);
  const blob = await canvas.convertToBlob({type: 'image/png'});
  if (!blob || blob.size <= 0 || canvas.engine?.renderer !== 'CPU') throw Error('Artifact CPU rendering probe failed');
  return {...adapter.audit(), scope: 'preflight_probe', actual_render_probe: 'PASS'};
}
