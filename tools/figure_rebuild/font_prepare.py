"""Validate user-provided fonts and extract static SFNT faces with fontTools.

No font binary is distributed by this project.  Paths refer to fonts supplied
by the caller; extracted faces belong only in the ignored local build directory.
"""
import argparse
import hashlib
import json
import re
import unicodedata
import shutil
import tempfile
from pathlib import Path


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_profile(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get('family'), str) or not profile['family'].strip():
        raise ValueError('Runtime fonts requires an explicit nonempty family')
    family = profile['family']
    if any(character in family for character in ('"', '\\', '\n', '\r', '\x00')):
        raise ValueError('Font family contains unsupported quote/control characters')
    for role in ('regular', 'bold'):
        face = profile.get(role)
        if not isinstance(face, dict) or not isinstance(face.get('path'), str):
            raise ValueError('Runtime fonts requires ' + role + '.path')
        source = Path(face['path'])
        if not source.is_absolute() or not source.is_file():
            raise ValueError('Font path must be an existing absolute file: ' + role)
        index = face.get('face_index', 0)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError('Font face_index must be a nonnegative integer: ' + role)
        expected = face.get('sha256')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[a-fA-F0-9]{64}', expected)):
            raise ValueError('Font sha256 must be a 64-character hex digest: ' + role)
        if expected is not None and _hash(source) != expected.lower():
            raise ValueError('Configured font SHA256 mismatch: ' + role)
    additional = profile.get('additional', [])
    if not isinstance(additional, list):
        raise ValueError('fonts.additional must be a list')
    names = {family.casefold()}
    for extra in additional:
        if not isinstance(extra, dict) or extra.get('additional'):
            raise ValueError('Additional font profiles cannot be nested')
        validate_profile(extra)
        if extra['family'].casefold() in names:
            raise ValueError('Duplicate configured font family: ' + extra['family'])
        names.add(extra['family'].casefold())
    return profile


def _families(font):
    result = set()
    for item in font['name'].names:
        if item.nameID in (1, 16):
            try:
                result.add(item.toUnicode().strip())
            except (UnicodeError, LookupError):
                continue
    return result


def _visible_codepoints(text):
    return {ord(character) for character in text
            if unicodedata.category(character) not in ('Cc', 'Cf')
            and not 0xFE00 <= ord(character) <= 0xFE0F
            and not 0xE0100 <= ord(character) <= 0xE01EF}


def _prepare_family(profile, output_dir, objects=()):
    """Return an audit after saving explicit, static regular/bold render faces."""
    validate_profile(profile)
    try:
        from fontTools.ttLib import TTFont
    except ImportError as error:
        raise ValueError('fontTools is required; install the project Python requirements') from error
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError('Font output directory already exists; use a fresh build run')
    pending, audit = [], []
    family = profile['family']
    for role in ('regular', 'bold'):
        definition = profile[role]
        source = Path(definition['path']).resolve()
        index = definition.get('face_index', 0)
        try:
            # fontNumber identifies a TTC/OTC member.  Noncollections require 0.
            is_collection = source.read_bytes()[:4] == b'ttcf'
            if not is_collection and index != 0:
                raise ValueError('Noncollection font face_index must be zero: ' + role)
            font = TTFont(str(source), fontNumber=index if is_collection else -1, lazy=False)
        except Exception as error:
            raise ValueError('Cannot open configured font face ' + role + ': ' + str(error)) from error
        try:
            families = _families(font)
            if family.casefold() not in {name.casefold() for name in families}:
                raise ValueError('Configured font family does not match ' + role + ' face: ' + ', '.join(sorted(families)))
            if 'fvar' in font:
                raise ValueError('Variable fonts need an instantiated static face: ' + role)
            if 'OS/2' not in font:
                raise ValueError('Font face needs OS/2 weight metadata: ' + role)
            weight = font['OS/2'].usWeightClass
            if not 1 <= weight <= 1000 or (role == 'bold') != (weight >= 600):
                raise ValueError('Configured font weight does not match ' + role + ' role: ' + str(weight))
            if font['OS/2'].fsSelection & 1 or font['head'].macStyle & 2:
                raise ValueError('Configure upright font faces; italic text uses its native style flag: ' + role)
            cmap = font.getBestCmap() or {}
            text_objects = [item for item in objects if item.get('kind') == 'text'
                            and ('bold' if item.get('bold') else 'regular') == role]
            # Comparison labels must render without falling back to a system font.
            if role == 'regular':
                text_objects.append({'id': 'comparison-labels', 'text': 'Reference Editable PPT render Pixel difference'})
            for item in text_objects:
                missing = sorted(_visible_codepoints(item['text']) - set(cmap))
                if missing:
                    points = ', '.join(f'U+{point:04X}' for point in missing[:12])
                    raise ValueError('Configured font lacks glyphs for ' + item['id'] + ': ' + points)
            suffix = '.otf' if font.sfntVersion == 'OTTO' else '.ttf'
            renderer = output_dir / (role + suffix)
            pending.append((font, renderer))
            audit.append({'role': role, 'family': family, 'path': str(source),
                          'face_index': index, 'sha256': _hash(source),
                          'weight_class': weight,
                          'family_names': sorted(families), 'renderer': str(renderer),
                          'glyph_coverage_checked': True, 'binary_source': 'user_provided'})
        except Exception:
            font.close()
            for prepared_font, _ in pending:
                if prepared_font is not font:
                    prepared_font.close()
            raise
    output_dir.mkdir(parents=True)
    try:
        for font, renderer in pending:
            font.save(str(renderer))
            next(item for item in audit if item['renderer'] == str(renderer))['renderer_sha256'] = _hash(renderer)
    finally:
        for font, _ in pending:
            font.close()
    return audit


def prepare_fonts(profile, output_dir, objects=()):
    """Prepare explicit families atomically, without silently substituting one."""
    validate_profile(profile)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError('Font output directory already exists; use a fresh build run')
    profiles = [profile, *profile.get('additional', [])]
    configured = {item['family'] for item in profiles}
    for item in objects:
        if item.get('kind') == 'text' and item.get('font_family', profile['family']) not in configured:
            raise ValueError('Unconfigured font family for ' + item['id'] + ': ' + str(item.get('font_family')))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.fonts-', dir=output_dir.parent) as temp:
        prepared = Path(temp) / 'ready'
        audit = []
        for index, family_profile in enumerate(profiles):
            destination = prepared if index == 0 else prepared / ('family-' + str(index))
            selected = [item for item in objects if item.get('kind') == 'text'
                        and item.get('font_family', profile['family']) == family_profile['family']]
            entries = _prepare_family(family_profile, destination, selected)
            for entry in entries:
                relative = Path(entry['renderer']).relative_to(prepared)
                entry['renderer'] = str(output_dir / relative)
            audit.extend(entries)
        shutil.move(str(prepared), str(output_dir))
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Build config containing runtime.fonts')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        result = prepare_fonts(config['runtime'].get('fonts'), args.output_dir, manifest['objects'])
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
