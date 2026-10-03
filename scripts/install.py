#!/usr/bin/env python3
"""Install this checkout as a Codex skill using a link or a portable copy."""
import argparse
import os
import shutil
from pathlib import Path


PUBLIC_DIRECTORIES = ('src', 'scripts', 'agents', 'references', 'docs', 'tests')
PUBLIC_FILES = (
    'SKILL.md', 'README.md', 'README.en.md', 'README.ko.md', 'README.es.md',
    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md',
    'CONTRIBUTING.md', 'pyproject.toml', 'MANIFEST.in', 'requirements.txt',
    'requirements-source.txt', 'requirements-vision.txt', '.gitignore',
    '.gitattributes', '.editorconfig',
)
REQUIRED_FILES = ('SKILL.md', 'src/figure_rebuild/cli.py', 'scripts/run.py')
LOCAL_DIRECTORIES = {
    '.git', '.venv', 'venv', '.local', 'jobs', 'exports', 'node_modules',
    '__pycache__', 'build', 'dist', '.cache', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', '.tox', '.nox', '.hypothesis', '.idea', '.npm', '.pnpm-store',
    '.eggs', '.yarn', 'htmlcov',
}
# Runtime fonts are supplied by the caller and are never bundled implicitly.
FONT_SUFFIXES = {'.ttf', '.otf', '.ttc', '.woff', '.woff2'}


def validate_checkout(root):
    for name in REQUIRED_FILES:
        path = root / name
        parts = Path(name).parts
        linked = any((root / Path(*parts[:index])).is_symlink()
                     for index in range(1, len(parts) + 1))
        if not path.is_file() or linked:
            raise ValueError('Missing regular skill checkout file: ' + name)


def private_entry(path):
    name = path.name
    environment = name == '.env' or name.startswith('.env.')
    sanitized = name.endswith(('.example', '.template'))
    return (
        path.is_symlink()
        or name in LOCAL_DIRECTORIES
        or name.endswith('.egg-info')
        or (environment and not sanitized)
        or path.suffix.lower() in FONT_SUFFIXES
        or path.suffix.lower() in {'.pyc', '.pyo', '.pyd'}
        or name in {'.DS_Store', 'Thumbs.db', 'Desktop.ini', '.coverage'}
        or name.startswith('.coverage.')
        or name.endswith(('.swp', '.swo', '~', '.code-workspace'))
    )


def copy_public(source, destination):
    if private_entry(source):
        return
    if source.is_dir():
        destination.mkdir()
        for entry in sorted(source.iterdir()):
            copy_public(entry, destination / entry.name)
    elif source.is_file():
        shutil.copy2(source, destination)


def install(root, skills_dir, *, copy=False):
    root = Path(root).expanduser().resolve()
    target = Path(skills_dir).expanduser().resolve() / 'figure-rebuild'
    validate_checkout(root)
    if target == root or root in target.parents:
        raise ValueError('Skill destination must be outside the checkout: ' + str(target))
    if not copy and target.is_symlink() and target.resolve() == root:
        return target
    if target.exists() or target.is_symlink():
        raise ValueError('Skill destination already exists: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    if not copy:
        target.symlink_to(root, target_is_directory=True)
        return target
    target.mkdir()
    try:
        for name in (*PUBLIC_FILES, *PUBLIC_DIRECTORIES):
            copy_public(root / name, target / name)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-dir', default=str(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'skills'))
    parser.add_argument('--copy', action='store_true', help='Copy public skill files instead of creating a symlink')
    args = parser.parse_args(argv)
    try:
        target = install(Path(__file__).resolve().parents[1], args.skills_dir, copy=args.copy)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
