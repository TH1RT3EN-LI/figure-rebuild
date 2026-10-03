"""Inspect font dependencies in the Python interpreter configured for building."""
import argparse
import json

from .font_prepare import validate_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fonts', required=True, help='Configured font profile JSON')
    args = parser.parse_args(argv)
    import PIL
    import fontTools

    validate_profile(json.loads(args.fonts))
    print(json.dumps({'pillow': PIL.__version__, 'fonttools': fontTools.__version__}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
