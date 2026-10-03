"""Run a selected package module with the configured interpreter's dependencies.

This file is invoked directly and uses only the standard library. Registering
the package's own search location avoids adding a wheel's surrounding
site-packages directory, whose binary dependencies can target another Python.
"""
import os
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print('usage: python _bootstrap.py MODULE [ARGS...]', file=sys.stderr)
        return 2
    module = arguments.pop(0)
    if not all(part.isidentifier() for part in module.split('.')):
        print('Invalid Figure Rebuild module: ' + module, file=sys.stderr)
        return 2
    package = os.path.dirname(os.path.realpath(__file__))
    # Direct script invocation adds this directory to sys.path. Package modules
    # are resolved through __path__; they need no unqualified import location.
    if sys.path and os.path.realpath(sys.path[0]) == package:
        sys.path.pop(0)
    import importlib.util
    import runpy

    specification = importlib.util.spec_from_file_location(
        'figure_rebuild', os.path.join(package, '__init__.py'),
        submodule_search_locations=[package])
    if specification is None or specification.loader is None:
        raise ImportError('Cannot load Figure Rebuild from ' + package)
    for name in list(sys.modules):
        if name == 'figure_rebuild' or name.startswith('figure_rebuild.'):
            del sys.modules[name]
    selected = importlib.util.module_from_spec(specification)
    sys.modules['figure_rebuild'] = selected
    specification.loader.exec_module(selected)
    sys.argv = ['figure_rebuild.' + module, *arguments]
    runpy.run_module('figure_rebuild.' + module, run_name='__main__', alter_sys=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
