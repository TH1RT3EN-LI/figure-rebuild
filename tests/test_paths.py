"""Configuration compatibility and imports across separately configured runtimes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from figure_rebuild import paths


class RuntimePaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'checkout' / 'src' / 'figure_rebuild'
        self.package.mkdir(parents=True)
        (self.package.parent.parent / 'SKILL.md').write_text('Test skill')

    def test_explicit_config_overrides_an_existing_checkout_profile(self):
        legacy = self.package.parent.parent / '.local' / 'figure-rebuild' / 'runtime.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}')
        selected = self.root / 'selected.json'
        with patch.object(paths, 'PACKAGE_ROOT', self.package), patch.dict(
                os.environ, {'FIGURE_REBUILD_CONFIG': str(selected)}, clear=True):
            self.assertEqual(paths.config_path(), selected)

    def test_existing_checkout_profile_remains_selected(self):
        legacy = self.package.parent.parent / '.local' / 'figure-rebuild' / 'runtime.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}')
        with patch.object(paths, 'PACKAGE_ROOT', self.package), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.checkout_root(), self.package.parent.parent)
            self.assertEqual(paths.config_path(), legacy)

    def test_new_checkout_profile_uses_xdg_without_creating_files(self):
        config_home = self.root / 'user-config'
        with patch.object(paths, 'PACKAGE_ROOT', self.package), patch.object(
                paths.sys, 'platform', 'linux'), patch.dict(
                    os.environ, {'XDG_CONFIG_HOME': str(config_home)}, clear=True):
            self.assertEqual(paths.config_path(), config_home / 'figure-rebuild' / 'runtime.json')
        self.assertFalse(config_home.exists())
        self.assertFalse((self.package.parent.parent / '.local').exists())

    def test_installed_package_does_not_treat_site_packages_as_a_checkout(self):
        installed = self.root / 'site-packages' / 'figure_rebuild'
        with patch.object(paths, 'PACKAGE_ROOT', installed), patch.object(
                paths.sys, 'platform', 'linux'), patch.object(Path, 'home', return_value=self.root), patch.dict(
                    os.environ, {}, clear=True):
            self.assertIsNone(paths.checkout_root())
            self.assertEqual(paths.config_path(), self.root / '.config' / 'figure-rebuild' / 'runtime.json')

    def test_windows_profile_uses_appdata(self):
        appdata = self.root / 'AppData' / 'Roaming'
        with patch.object(paths, 'checkout_root', return_value=None), patch.object(
                paths.sys, 'platform', 'win32'), patch.dict(
                    os.environ, {'APPDATA': str(appdata)}, clear=True):
            self.assertEqual(paths.config_path(), appdata / 'figure-rebuild' / 'runtime.json')

    def test_child_environment_preserves_existing_pythonpath_and_parent_environment(self):
        original = {'PYTHONPATH': str(self.root / 'other-packages'), 'FIGURE_TEST_ENV': 'retained'}
        with patch.dict(os.environ, original, clear=True):
            environment = paths.python_environment()
            self.assertEqual(environment['PYTHONPATH'], original['PYTHONPATH'])
            self.assertEqual(environment['FIGURE_REBUILD_PACKAGE_ROOT'], str(paths.package_root()))
            self.assertEqual(environment['FIGURE_TEST_ENV'], 'retained')
            self.assertEqual(dict(os.environ), original)

    def test_child_environment_does_not_add_a_pythonpath(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn('PYTHONPATH', paths.python_environment())

    def bootstrap_package(self, package, probe):
        package.mkdir(parents=True, exist_ok=True)
        bootstrap = package / '_bootstrap.py'
        bootstrap.write_bytes((paths.package_root() / '_bootstrap.py').read_bytes())
        (package / '__init__.py').write_text("__version__ = 'selected-package'\n")
        (package / 'probe.py').write_text(probe)
        return bootstrap

    def test_child_imports_this_package_from_an_unrelated_working_directory(self):
        probe = 'import json,figure_rebuild;print(json.dumps(figure_rebuild.__file__))'
        bootstrap = self.bootstrap_package(self.package, probe)
        with patch.object(paths, 'PACKAGE_ROOT', self.package):
            result = subprocess.run([sys.executable, '-B', str(bootstrap), 'probe'], cwd=self.root,
                                    env=paths.python_environment(), capture_output=True, text=True, check=True)
        self.assertEqual(Path(json.loads(result.stdout)).resolve(), self.package / '__init__.py')

    def test_wheel_bootstrap_uses_runtime_dependencies_and_ignores_host_siblings(self):
        host_site = self.root / 'host-site-packages'
        package = host_site / 'figure_rebuild'
        host_pillow = host_site / 'PIL'
        host_pillow.mkdir(parents=True)
        (host_pillow / '__init__.py').write_text("raise RuntimeError('Host Pillow has incompatible ABI')\n")
        (host_site / 'json.py').write_text("raise RuntimeError('Host json must not shadow stdlib')\n")
        runtime_site = self.root / 'runtime-site-packages'
        runtime_pillow = runtime_site / 'PIL'
        runtime_pillow.mkdir(parents=True)
        (runtime_pillow / '__init__.py').write_text("INSTANCE = 'runtime Pillow'\n")
        probe = ('import json,os,sys,figure_rebuild,PIL\n'
                 'print(json.dumps({"package":figure_rebuild.__version__,"pillow":PIL.INSTANCE,'
                 '"paths":sys.path,"pythonpath":os.environ["PYTHONPATH"],"args":sys.argv[1:]}))\n')
        bootstrap = self.bootstrap_package(package, probe)
        with patch.object(paths, 'PACKAGE_ROOT', package), patch.dict(
                os.environ, {'PYTHONPATH': str(runtime_site)}):
            result = subprocess.run([sys.executable, '-B', str(bootstrap), 'probe', 'a space', '--flag'],
                                    cwd=self.root, env=paths.python_environment(), capture_output=True,
                                    text=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['package'], 'selected-package')
        self.assertEqual(report['pillow'], 'runtime Pillow')
        self.assertEqual(report['pythonpath'], str(runtime_site))
        self.assertEqual(report['args'], ['a space', '--flag'])
        self.assertNotIn(str(host_site), report['paths'])
        self.assertNotIn(str(package), report['paths'])


if __name__ == '__main__':
    unittest.main()
