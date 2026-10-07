/** Check user-provided Codex backend components without authoring a deck. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {runPythonModule} from './runtime.mjs';
import {configureCpuRenderer,verifyCpuRenderer} from './cpu_renderer.mjs';

export async function checkRuntime(runtime, packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')) {
  if (!runtime || typeof runtime !== 'object') throw Error('Runtime configuration is required');
  for (const key of ['node', 'python', 'node_modules', 'presentation_skill']) {
    if (typeof runtime[key] !== 'string' || !path.isAbsolute(runtime[key])) throw Error('Runtime path must be absolute: ' + key);
    await fs.access(runtime[key]);
  }
  const req = createRequire(path.join(runtime.node_modules, 'figure-rebuild-loader.cjs'));
  const modules = Object.fromEntries(['@oai/artifact-tool', '@napi-rs/canvas', 'sharp'].map(name => [name, req.resolve(name)]));
  const cpuAdapter = configureCpuRenderer(modules['@oai/artifact-tool']);
  const artifact = await import(pathToFileURL(modules['@oai/artifact-tool']).href);
  const cpuRenderer = await verifyCpuRenderer(cpuAdapter);
  const metrics = artifact.defaultFontMetricsProvider;
  if (!metrics || typeof metrics.getMetricsForSize !== 'function' || typeof metrics.reset !== 'function' ||
      typeof artifact.skiaPaintBaselineCompensationPx !== 'function') {
    throw Error('Configured Artifact Tool lacks required presentation baseline metrics API');
  }
  modules['skia-canvas'] = createRequire(modules['@oai/artifact-tool']).resolve('skia-canvas');
  const adapterFiles = ['mark_artifact_operation_started.mjs', 'artifact_tool_utils.mjs',
    'inspect_presentation_package_integrity.py', 'inspect_presentation_layout_geometry.py'];
  for (const name of adapterFiles) await fs.access(path.join(runtime.presentation_skill, 'container_tools', name));
  const python = JSON.parse(runPythonModule(runtime, packageRoot, 'runtime_probe',
    ['--fonts', JSON.stringify(runtime.fonts)], {encoding: 'utf8'}));
  return {status: 'PASS', backend: 'user-provided Codex Artifact Tool', modules, python,
    renderer_backend: 'CPU', cpu_renderer: cpuRenderer,
    baseline_metrics_api_available: true, renderer_behavior_requires_regression: true,
    font_family: runtime.fonts.family, bundled_dependency_redistributed: false};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const runtime = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    console.log(JSON.stringify(await checkRuntime(runtime), null, 2));
  } catch (error) {
    console.error('figure-rebuild preflight: ' + error.message);
    process.exitCode = 1;
  }
}
