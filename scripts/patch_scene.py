#!/usr/bin/env python3
"""Transactionally patch one scene by stable ID from any caller directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.figure_rebuild.connections import main

if __name__ == '__main__':
    main()
