/** Check user-provided Codex backend components without authoring a deck. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

export async function checkRuntime(runtime) {
  if (!runtime || typeof runtime !== 'object') throw Error('Runtime configuration is required');
  for (const key of ['node', 'python', 'node_modules', 'presentation_skill']) {
    if (typeof runtime[key] !== 'string' || !path.isAbsolute(runtime[key])) throw Error('Runtime path must be absolute: ' + key);
    await fs.access(runtime[key]);
  }
  const req = createRequire(path.join(runtime.node_modules, 'figure-rebuild-loader.cjs'));
  const modules = Object.fromEntries(['@oai/artifact-tool', '@napi-rs/canvas', 'sharp'].map(name => [name, req.resolve(name)]));
  const adapterFiles = ['mark_artifact_operation_started.mjs', 'artifact_tool_utils.mjs',
    'inspect_presentation_package_integrity.py', 'inspect_presentation_layout_geometry.py'];
  for (const name of adapterFiles) await fs.access(path.join(runtime.presentation_skill, 'container_tools', name));
  const probe = `import json,sys;sys.path.insert(0,sys.argv[1]);from font_prepare import validate_profile;import PIL,fontTools;validate_profile(json.loads(sys.argv[2]));print(json.dumps({'pillow':PIL.__version__,'fonttools':fontTools.__version__}))`;
  const python = JSON.parse(execFileSync(runtime.python, ['-B', '-c', probe,
    path.dirname(fileURLToPath(import.meta.url)), JSON.stringify(runtime.fonts)], {encoding: 'utf8'}));
  return {status: 'PASS', backend: 'user-provided Codex Artifact Tool', modules, python,
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
