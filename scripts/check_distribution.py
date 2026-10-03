#!/usr/bin/env python3
"""Check built archives for complete backends and accidental local assets."""
import argparse
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


BACKENDS = {
    "build.mjs", "preflight.mjs", "curves.mjs", "text_fit.mjs", "runtime.mjs",
}
PRIVATE_DIRECTORIES = {
    ".git", ".local", ".venv", "venv", "jobs", "exports", "node_modules",
    "__pycache__",
}
PRIVATE_SUFFIXES = {".pyc", ".pyo", ".ttf", ".otf", ".ttc", ".woff", ".woff2"}


def check_private(names):
    for name in names:
        path = PurePosixPath(name)
        if PRIVATE_DIRECTORIES.intersection(path.parts):
            raise ValueError("Local directory in distribution: " + name)
        if path.name == ".env" or path.name.startswith(".env."):
            raise ValueError("Environment configuration in distribution: " + name)
        if path.suffix in PRIVATE_SUFFIXES:
            raise ValueError("Local font or bytecode in distribution: " + name)


def check_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        check_private(names)
        required = {
            "figure_rebuild/__init__.py", "figure_rebuild/__main__.py",
            "figure_rebuild/cli.py", "figure_rebuild/paths.py",
            "figure_rebuild/_bootstrap.py", "figure_rebuild/runtime_probe.py",
            *{"figure_rebuild/powerpoint/" + name for name in BACKENDS},
        }
        missing = required - names
        if missing:
            raise ValueError("Wheel missing runtime files: " + ", ".join(sorted(missing)))
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        entrypoints = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata) != 1 or len(entrypoints) != 1:
            raise ValueError("Wheel needs one package metadata and entrypoint manifest")
        for filename in ("LICENSE", "docs/THIRD_PARTY_NOTICES.md"):
            if not any(name.endswith(".dist-info/licenses/" + filename) for name in names):
                raise ValueError("Wheel missing license record: " + filename)
        if "figure-rebuild = figure_rebuild.cli:main" not in archive.read(entrypoints[0]).decode():
            raise ValueError("Wheel missing the main console command")
        # MJS imports are relative; every local dependency must travel with it.
        import re
        for name in names:
            if name.endswith(".mjs"):
                for relative in re.findall(r"from\s+['\"](\.[^'\"]+)['\"]", archive.read(name).decode()):
                    expected = (PurePosixPath(name).parent / relative).as_posix()
                    if expected not in names:
                        raise ValueError("Unpackaged backend dependency: " + expected)
    return len(names)


def check_sdist(path):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = {PurePosixPath(member.name).as_posix() for member in members if member.isfile()}
        roots = {PurePosixPath(name).parts[0] for name in names}
        if len(roots) != 1 or any(member.issym() or member.islnk() for member in members):
            raise ValueError("Source archive needs one ordinary directory tree")
        root = roots.pop()
        relative = {name[len(root) + 1:] for name in names}
        check_private(relative)
        required = {
            "pyproject.toml", "MANIFEST.in", "SKILL.md", "README.md", "LICENSE",
            "docs/THIRD_PARTY_NOTICES.md", "docs/CHANGELOG.md", ".github/CONTRIBUTING.md",
            "docs/i18n/README.en.md", "docs/i18n/README.es.md", "docs/i18n/README.ko.md",
            "requirements/base.txt", "requirements/source.txt", "requirements/vision.txt",
            "agents/openai.yaml", "references/scene.md",
            "scripts/run.py", "scripts/install.py", "src/figure_rebuild/cli.py",
            "src/figure_rebuild/_bootstrap.py", "src/figure_rebuild/runtime_probe.py",
            "tests/fixtures/connector-presets.xml",
            "tests/fixtures/connector-presets.LICENSE",
            "tests/fixtures/connector-presets.NOTICE",
            *{"src/figure_rebuild/powerpoint/" + name for name in BACKENDS},
        }
        if required - relative:
            raise ValueError("Source archive missing public files: " + ", ".join(sorted(required - relative)))
    return len(relative)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--sdist", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        wheel_count = check_wheel(args.wheel)
        source_count = check_sdist(args.sdist)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, "Distribution check failed: " + str(error) + "\n")
    print(f"PASS: wheel ({wheel_count} files), source archive ({source_count} files)")


if __name__ == "__main__":
    main()
