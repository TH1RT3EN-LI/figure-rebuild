#!/usr/bin/env python3
"""Self-contained skill entrypoint; works from any current directory."""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/figure_rebuild/cli.py'), run_name='__main__')
