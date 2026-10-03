"""Local secrets stay ignored while curated media and audit evidence remain visible."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('git'), 'Git is required to verify ignore patterns')
class RepositoryHygieneTests(unittest.TestCase):
    def test_gitignore_separates_local_state_from_public_evidence(self):
        ignored = (
            '.local/figure-rebuild/runtime.json', 'jobs/demo/manifest.json',
            'exports/demo/result.pptx', '.env', 'src/.env.production',
            '.venv/bin/python', 'src/figure_rebuild/__pycache__/cli.pyc',
            'dist/figure_rebuild.whl', 'node_modules/example/index.js',
        )
        public = (
            'src/figure_rebuild/powerpoint/build.mjs', 'pyproject.toml', 'LICENSE',
            'THIRD_PARTY_NOTICES.md', '.env.example', 'docs/.env.production.template',
            'docs/assets/demo.pptx', 'docs/assets/demo.gif', 'docs/assets/demo-audit.json',
            'tests/fixtures/demo.pptx',
            'tests/fixtures/connector.LICENSE', 'tests/fixtures/connector.NOTICE',
            'tests/fixtures/font.ttf', 'docs/build/guide.md',
        )
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            shutil.copy2(REPO / '.gitignore', sandbox / '.gitignore')
            subprocess.run(['git', 'init', '-q', str(sandbox)], check=True)
            for expected, paths in ((True, ignored), (False, public)):
                for name in paths:
                    with self.subTest(path=name):
                        result = subprocess.run(
                            ['git', 'check-ignore', '--no-index', '-q', name], cwd=sandbox,
                        )
                        self.assertIn(result.returncode, (0, 1))
                        self.assertEqual(result.returncode == 0, expected)


if __name__ == '__main__':
    unittest.main()
