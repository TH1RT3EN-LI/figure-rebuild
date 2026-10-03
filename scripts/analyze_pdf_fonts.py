#!/usr/bin/env python3
"""Inspect author-PDF fonts from this checkout or an installed package."""
from pathlib import Path
import sys

source = Path(__file__).resolve().parents[1] / 'src'
if source.is_dir():
    sys.path.insert(0, str(source))
from figure_rebuild.font_analysis import main

if __name__ == '__main__':
    raise SystemExit(main())
