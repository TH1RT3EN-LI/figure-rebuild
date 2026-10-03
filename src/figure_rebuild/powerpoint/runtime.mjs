/** Run this package with the dependencies belonging to the configured Python. */
import path from 'node:path';
import {execFileSync} from 'node:child_process';

export function pythonEnvironment(packageRoot, inherited = process.env) {
  if (typeof packageRoot !== 'string' || !path.isAbsolute(packageRoot)) {
    throw Error('Package resource directory must be an absolute path');
  }
  return {...inherited, FIGURE_REBUILD_PACKAGE_ROOT: packageRoot};
}

export function runPythonModule(runtime, packageRoot, module, args = [], options = {}) {
  return execFileSync(runtime.python, ['-B', path.join(packageRoot, '_bootstrap.py'), module, ...args], {
    ...options,
    env: pythonEnvironment(packageRoot, options.env ?? process.env),
  });
}
