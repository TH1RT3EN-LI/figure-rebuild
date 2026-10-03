import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {pythonEnvironment, runPythonModule} from '../src/figure_rebuild/powerpoint/runtime.mjs';

test('runtime imports preserve inherited paths and do not mutate the parent environment', () => {
  const packageRoot = path.resolve('src/figure_rebuild');
  const inherited = {PYTHONPATH: '/existing/python-path', CUSTOM_VALUE: 'retained'};
  const environment = pythonEnvironment(packageRoot, inherited);
  assert.equal(environment.PYTHONPATH, inherited.PYTHONPATH);
  assert.equal(environment.FIGURE_REBUILD_PACKAGE_ROOT, packageRoot);
  assert.equal(environment.CUSTOM_VALUE, 'retained');
  assert.deepEqual(inherited, {PYTHONPATH: '/existing/python-path', CUSTOM_VALUE: 'retained'});
  assert.equal(Object.hasOwn(pythonEnvironment(packageRoot, {}), 'PYTHONPATH'), false);
  assert.throws(() => pythonEnvironment('relative/package'), /absolute path/);
});

const python = process.platform === 'win32' ? 'python' : 'python3';
const hasPython = spawnSync(python, ['--version']).status === 0;
test('configured Python imports the selected package from another directory and receives arguments', {
  skip: hasPython ? false : 'Python is unavailable in this Node test environment',
}, () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'figure-rebuild-runtime-'));
  try {
    const packageRoot = path.join(temporary, 'resources', 'figure_rebuild');
    const workingDirectory = path.join(temporary, 'unrelated-cwd');
    fs.mkdirSync(packageRoot, {recursive: true});
    fs.mkdirSync(workingDirectory);
    fs.copyFileSync(new URL('../src/figure_rebuild/_bootstrap.py', import.meta.url), path.join(packageRoot, '_bootstrap.py'));
    fs.writeFileSync(path.join(packageRoot, '__init__.py'), '');
    fs.writeFileSync(path.join(packageRoot, 'probe.py'),
      'import json,os,sys\nprint(json.dumps({"argv":sys.argv[1:],"cwd":os.getcwd(),"env":os.environ["CUSTOM_VALUE"]}))\n');
    const result = JSON.parse(runPythonModule({python}, packageRoot, 'probe', ['a space', '--flag'], {
      cwd: workingDirectory,
      encoding: 'utf8',
      env: {...process.env, PYTHONPATH: path.join(temporary, 'existing-path'), CUSTOM_VALUE: 'retained'},
    }));
    assert.deepEqual(result.argv, ['a space', '--flag']);
    assert.equal(result.cwd, workingDirectory);
    assert.equal(result.env, 'retained');
    assert.equal(fs.existsSync(path.join(packageRoot, '__pycache__')), false);
  } finally {
    fs.rmSync(temporary, {recursive: true, force: true});
  }
});

test('wheel sibling dependencies do not enter the configured Python import path', {
  skip: hasPython ? false : 'Python is unavailable in this Node test environment',
}, () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'figure-rebuild-wheel-runtime-'));
  try {
    const hostSite = path.join(temporary, 'host-site-packages');
    const packageRoot = path.join(hostSite, 'figure_rebuild');
    const runtimeSite = path.join(temporary, 'runtime-site-packages');
    fs.mkdirSync(packageRoot, {recursive: true});
    fs.mkdirSync(path.join(hostSite, 'PIL'));
    fs.mkdirSync(path.join(runtimeSite, 'PIL'), {recursive: true});
    fs.copyFileSync(new URL('../src/figure_rebuild/_bootstrap.py', import.meta.url), path.join(packageRoot, '_bootstrap.py'));
    fs.writeFileSync(path.join(packageRoot, '__init__.py'), "__version__ = 'selected-package'\n");
    fs.writeFileSync(path.join(hostSite, 'PIL', '__init__.py'), "raise RuntimeError('Host Pillow has incompatible ABI')\n");
    fs.writeFileSync(path.join(hostSite, 'json.py'), "raise RuntimeError('Host json must not shadow stdlib')\n");
    fs.writeFileSync(path.join(runtimeSite, 'PIL', '__init__.py'), "INSTANCE = 'runtime Pillow'\n");
    fs.writeFileSync(path.join(packageRoot, 'probe.py'),
      'import json,os,sys,figure_rebuild,PIL\nprint(json.dumps({"package":figure_rebuild.__version__,"pillow":PIL.INSTANCE,"paths":sys.path,"pythonpath":os.environ["PYTHONPATH"]}))\n');
    const result = JSON.parse(runPythonModule({python}, packageRoot, 'probe', [], {
      cwd: temporary,
      encoding: 'utf8',
      env: {...process.env, PYTHONPATH: runtimeSite},
    }));
    assert.equal(result.package, 'selected-package');
    assert.equal(result.pillow, 'runtime Pillow');
    assert.equal(result.pythonpath, runtimeSite);
    assert.equal(result.paths.includes(hostSite), false);
    assert.equal(result.paths.includes(packageRoot), false);
  } finally {
    fs.rmSync(temporary, {recursive: true, force: true});
  }
});
