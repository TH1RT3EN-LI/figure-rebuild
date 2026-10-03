"""Public entrypoints and bundled backends must work outside the checkout."""
import importlib.metadata
import importlib.resources
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import figure_rebuild


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def outside_checkout(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            return subprocess.run(arguments, cwd=directory, env=environment,
                                  capture_output=True, text=True, timeout=30)

    def test_module_version_uses_installed_package_metadata(self):
        result = self.outside_checkout([sys.executable, "-m", "figure_rebuild", "--version"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "figure-rebuild " + figure_rebuild.__version__)
        self.assertEqual(importlib.metadata.version("figure-rebuild"), figure_rebuild.__version__)

    def test_all_console_commands_work_outside_checkout(self):
        scripts = Path(sys.executable).parent
        for name in ("figure-rebuild", "figure-rebuild-fonts", "figure-rebuild-formulas", "figure-rebuild-patch"):
            with self.subTest(command=name):
                command = scripts / (name + ".exe" if os.name == "nt" else name)
                self.assertTrue(command.is_file(), "Missing console entrypoint: " + str(command))
                result = self.outside_checkout([str(command), "--help"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_compatibility_scripts_work_outside_checkout(self):
        for name in ("run.py", "analyze_pdf_fonts.py", "render_formulas.py", "patch_scene.py", "install.py"):
            with self.subTest(script=name):
                result = self.outside_checkout([sys.executable, str(ROOT / "scripts" / name), "--help"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_backend_resources_are_in_the_installed_package(self):
        resources = importlib.resources.files("figure_rebuild").joinpath("powerpoint")
        for name in ("build.mjs", "preflight.mjs", "text_fit.mjs", "curves.mjs", "runtime.mjs", "placement.mjs"):
            with self.subTest(resource=name):
                self.assertTrue(resources.joinpath(name).is_file())
                self.assertTrue(resources.joinpath(name).read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
