#!/usr/bin/env python3
"""Render host-confirmed formulas without depending on the caller's directory."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/figure_rebuild'))
from formula_render import render_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='JSON with confirmed math transcriptions')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--font-manifest', required=True, help='External project font registry or Computer Modern registry')
    parser.add_argument('--engine', help='Explicit pdflatex or tectonic executable')
    parser.add_argument('--alphabet-font-manifest', help='Optional audited MathJax TeX OTF alphabet registry; requires Tectonic')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    try:
        specification = json.loads(Path(args.spec).read_text(encoding='utf-8'))
        result = render_batch(specification, output_dir=args.output_dir, font_manifest=args.font_manifest,
                              engine=args.engine, alphabet_font_manifest=args.alphabet_font_manifest,
                              overwrite=args.overwrite)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, f'Formula rendering error: {error}\n')
    print(json.dumps({'manifest': result['manifest'], 'count': result['count']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
