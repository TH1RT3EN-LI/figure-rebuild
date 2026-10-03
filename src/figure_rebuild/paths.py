"""Resolve installed package resources, user configuration, and child runtimes."""
import os
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parent


def package_root():
    """Return the package resource directory for checkout and wheel installs."""
    return PACKAGE_ROOT


def checkout_root():
    """Return the containing skill checkout, or None for an installed package."""
    package = package_root()
    checkout = package.parent.parent
    if package.parent.name == 'src' and (checkout / 'SKILL.md').is_file():
        return checkout
    return None


def config_path():
    """Prefer an explicit profile, then an existing checkout profile, then user config."""
    configured = os.environ.get('FIGURE_REBUILD_CONFIG')
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = checkout_root()
    if checkout is not None:
        legacy = checkout / '.local' / 'figure-rebuild' / 'runtime.json'
        if legacy.is_file():
            return legacy
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base.expanduser().resolve() / 'figure-rebuild' / 'runtime.json'


def python_environment():
    """Select package resources without exposing the caller's dependency directory."""
    environment = os.environ.copy()
    environment['FIGURE_REBUILD_PACKAGE_ROOT'] = str(package_root())
    return environment
