#!/usr/bin/env python3
"""Read-only author-PDF font evidence entrypoint, independent of caller cwd."""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] /
                      'tools/figure_rebuild/font_analysis.py'), run_name='__main__')
