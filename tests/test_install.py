"""Skill installation preserves existing files and excludes local state."""
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('figure_rebuild_installer', REPO / 'scripts/install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = self.base / 'checkout'
        self.skills = self.base / 'skills'
        for name in installer.REQUIRED_FILES:
            self.write(name)

    def write(self, name, content='public content'):
        path = self.checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def symlink(self, target, path, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            self.skipTest('Symlinks are unavailable: ' + str(exc))

    def test_symlink_installation_is_idempotent(self):
        try:
            target = installer.install(self.checkout, self.skills)
        except OSError as exc:
            self.skipTest('Symlinks are unavailable: ' + str(exc))
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), self.checkout.resolve())
        self.assertEqual(installer.install(self.checkout, self.skills), target)

    def test_existing_destination_is_never_replaced(self):
        target = self.skills / 'figure-rebuild'
        target.mkdir(parents=True)
        sentinel = target / 'existing.txt'
        sentinel.write_text('keep me', encoding='utf-8')
        for copy in (False, True):
            with self.subTest(copy=copy), self.assertRaisesRegex(ValueError, 'already exists'):
                installer.install(self.checkout, self.skills, copy=copy)
        self.assertEqual(sentinel.read_text(), 'keep me')

    def test_broken_destination_symlink_is_never_replaced(self):
        self.skills.mkdir()
        target = self.skills / 'figure-rebuild'
        self.symlink(self.base / 'missing', target, directory=True)
        for copy in (False, True):
            with self.subTest(copy=copy), self.assertRaisesRegex(ValueError, 'already exists'):
                installer.install(self.checkout, self.skills, copy=copy)
        self.assertTrue(target.is_symlink())

    def test_existing_file_is_never_replaced(self):
        self.skills.mkdir()
        target = self.skills / 'figure-rebuild'
        target.write_text('existing skill file', encoding='utf-8')
        for copy in (False, True):
            with self.subTest(copy=copy), self.assertRaisesRegex(ValueError, 'already exists'):
                installer.install(self.checkout, self.skills, copy=copy)
        self.assertEqual(target.read_text(), 'existing skill file')

    def test_incomplete_checkout_fails_before_destination_creation(self):
        (self.checkout / 'src/figure_rebuild/cli.py').unlink()
        with self.assertRaisesRegex(ValueError, 'src/figure_rebuild/cli.py'):
            installer.install(self.checkout, self.skills, copy=True)
        self.assertFalse(self.skills.exists())

    def test_required_file_cannot_be_an_external_symlink(self):
        source = self.checkout / 'src/figure_rebuild/cli.py'
        source.unlink()
        outside = self.base / 'outside-cli.py'
        outside.write_text('external code', encoding='utf-8')
        self.symlink(outside, source)
        with self.assertRaisesRegex(ValueError, 'regular skill checkout file'):
            installer.install(self.checkout, self.skills, copy=True)
        self.assertFalse(self.skills.exists())

    def test_destination_inside_checkout_is_rejected(self):
        for copy in (False, True):
            with self.subTest(copy=copy), self.assertRaisesRegex(ValueError, 'outside the checkout'):
                installer.install(self.checkout, self.checkout / 'skills', copy=copy)
        self.assertFalse((self.checkout / 'skills').exists())

    def test_copy_keeps_public_assets_and_licenses_but_excludes_local_state(self):
        public = (
            'README.md', 'docs/i18n/README.en.md', 'docs/i18n/README.ko.md',
            'docs/i18n/README.es.md', 'docs/CHANGELOG.md', '.github/CONTRIBUTING.md',
            'LICENSE', 'docs/THIRD_PARTY_NOTICES.md', '.gitignore',
            'requirements/base.txt', 'requirements/source.txt', 'requirements/vision.txt',
            'src/figure_rebuild/powerpoint/build.mjs', 'docs/assets/demo.pptx',
            'docs/assets/demo.gif', 'docs/assets/demo-audit.json',
            'docs/demonstration/manifest.json', 'docs/.env.example',
            'tests/fixtures/connector.xml', 'tests/fixtures/connector.LICENSE',
            'tests/fixtures/connector.NOTICE',
        )
        private = (
            '.git/config', '.local/runtime.json', '.venv/bin/python',
            'jobs/demo/manifest.json', 'exports/result.pptx', '.env',
            'unlisted-private.json', 'src/figure_rebuild/__pycache__/cli.pyc',
            'src/figure_rebuild/local.egg-info/PKG-INFO',
            'scripts/.env.production', 'docs/node_modules/example/index.js',
            'docs/local-font.ttf', 'docs/.DS_Store',
        )
        for name in (*public, *private):
            self.write(name)
        target = installer.install(self.checkout, self.skills, copy=True)
        self.assertFalse(target.is_symlink())
        for name in (*installer.REQUIRED_FILES, *public):
            with self.subTest(public=name):
                self.assertEqual((target / name).read_bytes(), (self.checkout / name).read_bytes())
        for name in private:
            with self.subTest(private=name):
                self.assertFalse((target / name).exists())
        with self.assertRaisesRegex(ValueError, 'already exists'):
            installer.install(self.checkout, self.skills, copy=True)

    def test_copy_does_not_follow_external_symlinks(self):
        outside = self.base / 'private.txt'
        outside.write_text('private', encoding='utf-8')
        docs = self.checkout / 'docs'
        docs.mkdir()
        self.symlink(outside, docs / 'linked.txt')
        self.symlink(self.base, docs / 'linked-directory', directory=True)
        target = installer.install(self.checkout, self.skills, copy=True)
        self.assertEqual(list((target / 'docs').iterdir()), [])

    def test_copy_failure_removes_only_its_new_destination(self):
        self.skills.mkdir()
        sentinel = self.skills / 'other-skill.txt'
        sentinel.write_text('keep me', encoding='utf-8')
        with patch.object(installer.shutil, 'copy2', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                installer.install(self.checkout, self.skills, copy=True)
        self.assertFalse((self.skills / 'figure-rebuild').exists())
        self.assertEqual(sentinel.read_text(), 'keep me')
        self.assertTrue((self.checkout / 'SKILL.md').is_file())

    def test_main_accepts_explicit_argv(self):
        with patch.object(installer, 'install', return_value=self.skills / 'figure-rebuild') as install:
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(installer.main(['--skills-dir', str(self.skills), '--copy']), 0)
        install.assert_called_once_with(REPO, str(self.skills), copy=True)
        self.assertIn('figure-rebuild', output.getvalue())

    def test_copied_checkout_entrypoint_runs_from_another_directory(self):
        target = installer.install(REPO, self.skills, copy=True)
        other = self.base / 'unrelated'
        other.mkdir()
        result = subprocess.run(
            [sys.executable, '-B', str(target / 'scripts/run.py'), '--help'],
            cwd=other, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
