#!/usr/bin/env python3
"""Install this checkout as a discoverable Codex skill without replacing files."""
import argparse
import os
from pathlib import Path


def install(root, skills_dir):
    root = Path(root).resolve(); target = Path(skills_dir).expanduser() / 'figure-rebuild'
    if not (root / 'SKILL.md').is_file(): raise ValueError('Missing SKILL.md in checkout')
    if target.is_symlink() and target.resolve() == root: return target
    if target.exists() or target.is_symlink(): raise ValueError('Skill destination already exists: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True); target.symlink_to(root, target_is_directory=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-dir', default=str(Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'skills'))
    args = parser.parse_args()
    print(install(Path(__file__).resolve().parents[1], args.skills_dir))
