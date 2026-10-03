"""Render reviewed math with a real TeX engine and audited external CM fonts.

The expression grammar is deliberately smaller than TeX: document commands,
file access, macro definitions and shell operations are never accepted. This
module does not recognize images and never reads pixels from a reference image.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image


MATH_COMMANDS = frozenset('''mathbf mathrm mathit mathsf mathtt mathcal text
frac dfrac tfrac sqrt left right vert Vert lvert rvert lVert rVert
Pi pi Gamma gamma Delta delta Sigma sigma Omega omega Lambda lambda
Theta theta Phi phi Psi psi alpha beta eta rho tau epsilon varepsilon
mu nu xi zeta kappa chi upsilon varphi varrho vartheta
sum prod int iint iiint oint lim min max sin cos tan exp log ln
operatorname underset overset substack displaystyle textstyle
circ cdot times pm mp div in notin subset subseteq supset supseteq
le leq ge geq neq approx sim simeq equiv propto to mapsto
infty partial nabla ell ldots cdots vdots ddots forall exists emptyset
top bot perp parallel hat widehat bar overline underline vec dot ddot
quad qquad thinspace medspace thickspace negthinspace
big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
underbrace overbrace langle rangle lbrace rbrace'''.split())
CONTROL_SYMBOLS = frozenset(',;:!{}| ')
ALLOWED_CHARACTERS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 {}()[]|+-=,.:*/^_!<>\t\n\r')
SAFE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _positive(value, name, maximum=4096):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f'{name} must be finite, positive and at most {maximum}')
    return float(value)


def validate_expression(expression, *, confirmed=False):
    """Accept only a confirmed, bounded expression in the supported math grammar."""
    if confirmed is not True:
        raise ValueError('Math transcription must be confirmed by the host before rendering')
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 4096:
        raise ValueError('Expression must contain 1 to 4096 characters')
    if '^^' in expression:
        raise ValueError('TeX character-code escape syntax is not permitted')
    depth = 0
    index = 0
    while index < len(expression):
        character = expression[index]
        if character == '\\':
            index += 1
            if index == len(expression):
                raise ValueError('Trailing TeX escape')
            if expression[index].isalpha() and expression[index].isascii():
                end = index
                while end < len(expression) and expression[end].isalpha() and expression[end].isascii():
                    end += 1
                command = expression[index:end]
                if command not in MATH_COMMANDS:
                    raise ValueError(f'Unsupported or unsafe TeX command: {command}')
                index = end
                continue
            if expression[index] not in CONTROL_SYMBOLS:
                raise ValueError('Unsupported TeX control symbol')
            index += 1
            continue
        if character not in ALLOWED_CHARACTERS:
            raise ValueError(f'Unsupported TeX character: {character!r}')
        if character == '{':
            depth += 1
            if depth > 32:
                raise ValueError('Math expression nesting exceeds 32 groups')
        elif character == '}':
            depth -= 1
            if depth < 0:
                raise ValueError('Unbalanced math groups')
        index += 1
    if depth:
        raise ValueError('Unbalanced math groups')
    return expression.strip()


def resolve_math_fonts(manifest_path):
    """Resolve a project font registry or a direct CM registry, verifying bytes."""
    manifest = Path(manifest_path).expanduser().resolve()
    data = json.loads(manifest.read_text(encoding='utf-8'))
    project_manifest = manifest
    if 'formula_rendering' in data:
        formula = data['formula_rendering']
        if formula.get('family') != 'Computer Modern':
            raise ValueError('This renderer supports explicitly registered Computer Modern only')
        relative = formula.get('manifest')
        if not isinstance(relative, str):
            raise ValueError('Project formula rendering registry must name its font manifest')
        manifest = _child(project_manifest.parent, relative)
        data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('family') != 'Computer Modern' or not data.get('files'):
        raise ValueError('Font registry must contain Computer Modern font files')
    records = []
    names = set()
    for item in data['files']:
        path = _child(manifest.parent, item.get('file'))
        if path.suffix.lower() not in ('.pfb', '.tfm') or not re.fullmatch(r'[A-Za-z0-9_-]+\.(pfb|tfm)', path.name):
            raise ValueError('Math font assets must be named PFB or TFM files')
        if path.name in names:
            raise ValueError(f'Duplicate math font filename: {path.name}')
        digest = _sha(path)
        if digest != item.get('sha256'):
            raise ValueError(f'Font hash mismatch: {path.name}')
        names.add(path.name)
        records.append({'path': str(path), 'name': path.name, 'sha256': digest})
    required = {'cmr10.tfm', 'cmr10.pfb', 'cmmi10.tfm', 'cmmi10.pfb', 'cmsy10.tfm', 'cmsy10.pfb', 'cmex10.tfm', 'cmex10.pfb'}
    if not required.issubset(names):
        raise ValueError('Registered Computer Modern assets are incomplete for default math rendering')
    return {'family': 'Computer Modern', 'registry': str(project_manifest),
            'registry_sha256': _sha(project_manifest), 'font_manifest': str(manifest),
            'font_manifest_sha256': _sha(manifest), 'files': records}


def resolve_alphabet_fonts(manifest_path):
    """Resolve explicitly registered MathJax TeX OTF alphabets for real XeTeX."""
    from fontTools.ttLib import TTFont
    manifest = Path(manifest_path).expanduser().resolve()
    registry = json.loads(manifest.read_text(encoding='utf-8'))
    entries = {item.get('id'): item for item in registry.get('fonts', [])}
    records = []
    for identity in ('MathJax_Main-Regular', 'MathJax_Main-Bold', 'MathJax_Main-Italic', 'MathJax_Math-Italic'):
        item = entries.get(identity)
        if not item or item.get('face_index', 0) != 0:
            raise ValueError(f'MathJax mode requires a registered OTF alphabet: {identity}')
        declared = Path(item.get('path', ''))
        if declared.is_absolute():
            try:
                relative = str(declared.resolve().relative_to(manifest.parent))
            except ValueError as error:
                raise ValueError('Alphabet assets must reside below their registry directory') from error
        else:
            relative = str(declared)
        path = _child(manifest.parent, relative)
        if path.suffix.lower() != '.otf' or _sha(path) != item.get('sha256'):
            raise ValueError(f'Alphabet OTF/hash mismatch: {identity}')
        with TTFont(path, lazy=True) as font:
            postscript = font['name'].getDebugName(6)
        if postscript != identity or item.get('postscript_name') != identity:
            raise ValueError(f'Alphabet PostScript name mismatch: {identity}')
        records.append({'path': str(path), 'name': identity + '.otf', 'id': identity,
                        'sha256': item['sha256'], 'postscript_name': postscript,
                        'source_url': item.get('source_url'), 'license': item.get('license')})
    return {'family': 'MathJax TeX alphabets', 'registry': str(manifest),
            'registry_sha256': _sha(manifest), 'files': records}


def _child(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError('Font manifest file path is missing')
    path = Path(relative)
    root = Path(root).resolve()
    resolved = (root / path).resolve()
    if path.is_absolute() or root not in resolved.parents:
        raise ValueError('Font assets must reside below their manifest directory')
    return resolved


def resolve_engine(engine=None):
    candidate = engine or shutil.which('pdflatex') or shutil.which('tectonic')
    if not candidate:
        raise ValueError('No real TeX engine found; configure an explicit pdflatex or tectonic binary')
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        found = shutil.which(str(path))
        if not found:
            raise ValueError('Configured TeX engine is unavailable')
        path = Path(found)
    path = path.resolve()
    name = path.name.lower()
    if name not in ('pdflatex', 'tectonic'):
        raise ValueError('Supported TeX executables are pdflatex and tectonic')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('Configured TeX engine is not executable')
    return path, name


def _source(expression, font_size_px, color, padding_pt, design_size=None, stroke_width_px=0, engine_kind='pdflatex', alphabet_mode=False):
    # TeX points are 1/72.27 inch; the figure coordinate system is 96 px/inch.
    points = font_size_px * 72.27 / 96
    opening, closing = _stroke_source(stroke_width_px, engine_kind)
    return r'''\RequirePackage{fix-cm}
\documentclass[border=FR_PADDINGpt]{standalone}
\usepackage[OT1]{fontenc}
\usepackage{amsmath,xcolor}
\renewcommand{\rmdefault}{cmr}
\renewcommand{\sfdefault}{cmss}
\renewcommand{\ttdefault}{cmtt}
FR_DESIGN
FR_ALPHABET_MODE
\definecolor{ink}{HTML}{FR_COLOR}
\begin{document}
\setbox0=\hbox{\color{ink}\fontsize{FR_SIZE}{FR_SIZE}\selectfont FR_STROKE_OPEN$\displaystyle FR_EXPRESSION$FR_STROKE_CLOSE}
\typeout{FRMETRICS width=\the\wd0 height=\the\ht0 depth=\the\dp0}
\box0
\end{document}
'''.replace('FR_PADDING', f'{padding_pt:.8f}').replace('FR_COLOR', color[1:]).replace('FR_SIZE', f'{points:.8f}').replace('FR_DESIGN', _design_source(design_size)).replace('FR_ALPHABET_MODE', _alphabet_source(alphabet_mode)).replace('FR_STROKE_OPEN', opening).replace('FR_STROKE_CLOSE', closing).replace('FR_EXPRESSION', expression)


def _alphabet_source(enabled):
    if not enabled:
        return ''
    return r'''\usepackage{mathspec}
\setmainfont[Path=./,BoldFont=MathJax_Main-Bold.otf,ItalicFont=MathJax_Main-Italic.otf]{MathJax_Main-Regular.otf}
\setmathsfont(Latin)[Path=./,ItalicFont=MathJax_Math-Italic.otf]{MathJax_Math-Italic.otf}
\setmathsfont(Digits)[Path=./]{MathJax_Main-Regular.otf}
\setmathrm[Path=./,BoldFont=MathJax_Main-Bold.otf,ItalicFont=MathJax_Main-Italic.otf]{MathJax_Main-Regular.otf}'''


def _stroke_source(stroke_width_px, engine_kind):
    if (isinstance(stroke_width_px, bool) or not isinstance(stroke_width_px, (int, float))
            or not math.isfinite(stroke_width_px) or not 0 <= stroke_width_px <= 2):
        raise ValueError('stroke_width_px must be finite and in [0,2]')
    if not stroke_width_px:
        return '', ''
    width_pdf_pt = stroke_width_px * 72 / 96
    if engine_kind == 'tectonic':
        return (f'\\special{{pdf:literal direct q 2 Tr {width_pdf_pt:.8f} w}}',
                r'\special{pdf:literal direct Q}')
    if engine_kind == 'pdflatex':
        return f'\\pdfliteral direct{{q 2 Tr {width_pdf_pt:.8f} w}}', r'\pdfliteral direct{Q}'
    raise ValueError('Text stroke requires a supported TeX engine')


def _design_source(design_size):
    if design_size is None:
        return ''
    if design_size != 10 or isinstance(design_size, bool):
        raise ValueError('Supported explicit Computer Modern design_size is 10')
    # Some source figures use a fixed Type1 optical design scaled to all em
    # sizes. Select it explicitly rather than substituting 12/17 pt programs.
    return r'''\DeclareFontShape{OT1}{cmr}{m}{n}{<->cmr10}{}
\DeclareFontShape{OT1}{cmr}{bx}{n}{<->cmbx10}{}
\DeclareFontShape{OML}{cmm}{m}{it}{<->cmmi10}{}
\DeclareFontShape{OMS}{cmsy}{m}{n}{<->cmsy10}{}
\DeclareFontShape{OMX}{cmex}{m}{n}{<->cmex10}{}'''


def engine_command(engine_path, name, tex, output_dir):
    """Argument vectors only: no shell interpolation or shell-escape permission."""
    if name == 'tectonic':
        return [str(engine_path), '--untrusted', '--reruns', '0', '--keep-logs', '--keep-intermediates',
                '--makefile-rules', str(output_dir / 'dependencies.d'), '--outdir', str(output_dir), str(tex)]
    return [str(engine_path), '-interaction=nonstopmode', '-halt-on-error', '-no-shell-escape',
            '-recorder', '-output-directory', str(output_dir), str(tex)]


def _run(command, cwd, env=None, timeout=180):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'Formula rendering command failed: {command[0]}: {error}') from error
    if result.returncode:
        raise ValueError(f'Formula rendering failed ({result.returncode}): {result.stdout[-5000:]}\n{result.stderr[-5000:]}')
    return result.stdout + result.stderr


def _metrics(log):
    match = re.search(r'FRMETRICS\s*width=([0-9.]+)pt\s*height=([0-9.]+)pt\s*depth=([0-9.]+)pt', log)
    if not match:
        raise ValueError('TeX did not provide formula box metrics')
    return dict(zip(('width_pt', 'height_pt', 'depth_pt'), map(float, match.groups())))


def _tight_rgba(image, rotation):
    rgba = image.convert('RGBA')
    box = rgba.getchannel('A').getbbox()
    if box is None:
        raise ValueError('Formula rendered no visible ink')
    tight = rgba.crop(box)
    if rotation:
        tight = tight.rotate(-rotation, expand=True)
    return tight, box


def _embedded_base(name):
    return name.split('+')[-1].removesuffix('-Identity-H')


def _embedded_fonts(report, allowed_fonts=()):
    names = []
    for line in report.splitlines()[2:]:
        if line.strip():
            names.append(line.split()[0])
    if not names:
        raise ValueError('Formula PDF has no embedded fonts')
    for name in names:
        base = _embedded_base(name)
        if not re.fullmatch(r'CM(?:R|BX|MI|SY|EX)[0-9]+', base.upper()) and base not in allowed_fonts:
            raise ValueError(f'Unexpected formula font, refusing fallback: {name}')
    return names


def render_formula(expression, *, asset_id, output_dir, font_manifest, engine=None,
                   confirmed=False, font_size_px=24, color='#000000', rotation_deg=0,
                   min_scale=8, display_size_px=None, dpi=768, padding_pt=1,
                   design_size=None, stroke_width_px=0, alphabet_font_manifest=None, overwrite=False, timeout=180):
    """Produce transparent ink-tight PNG, vector PDF, TeX and an audit record.

    Rotation is clockwise. PNG sampling is at least ``min_scale`` pixels per
    intended display pixel; it is raised automatically for explicit display
    sizes. Original reference crops are neither an input nor an output.
    """
    expression = validate_expression(expression, confirmed=confirmed)
    if not isinstance(asset_id, str) or not SAFE_ID.fullmatch(asset_id) or '..' in asset_id:
        raise ValueError('Asset ID must be a simple filename without traversal')
    font_size_px = _positive(font_size_px, 'font_size_px', 512)
    min_scale = _positive(min_scale, 'min_scale', 64)
    if min_scale < 8:
        raise ValueError('Formula assets require at least 8 pixels per display pixel')
    dpi = _positive(dpi, 'dpi', 4096)
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
        raise ValueError('Formula color must be #RRGGBB')
    if rotation_deg not in (0, 90, 180, 270) or isinstance(rotation_deg, bool):
        raise ValueError('Formula rotation must be 0, 90, 180 or 270 degrees')
    if isinstance(padding_pt, bool) or not isinstance(padding_pt, (int, float)) or not math.isfinite(padding_pt) or not 0 <= padding_pt <= 20:
        raise ValueError('padding_pt must be finite and in [0,20]')
    if display_size_px is not None:
        if not isinstance(display_size_px, (tuple, list)) or len(display_size_px) != 2:
            raise ValueError('display_size_px must contain width and height')
        display_size_px = [_positive(v, 'display_size_px', 20000) for v in display_size_px]
    _design_source(design_size)
    _stroke_source(stroke_width_px, 'pdflatex')
    fonts = resolve_math_fonts(font_manifest)
    alphabet_fonts = resolve_alphabet_fonts(alphabet_font_manifest) if alphabet_font_manifest else None
    engine_path, name = resolve_engine(engine)
    if alphabet_fonts and name != 'tectonic':
        raise ValueError('Registered OpenType alphabets require the XeTeX-compatible Tectonic engine')
    cairo = shutil.which('pdftocairo')
    pdffonts = shutil.which('pdffonts')
    if not cairo or not pdffonts:
        raise ValueError('Formula rendering needs Poppler pdftocairo and pdffonts')
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    extensions = ('tex', 'pdf', 'png', 'json')
    if not overwrite and any((output / f'{asset_id}.{ext}').exists() for ext in extensions):
        raise ValueError('Formula output exists; use a new asset ID or explicit overwrite')
    source = _source(expression, font_size_px, color, padding_pt, design_size, stroke_width_px, name, alphabet_fonts is not None)
    engine_version = _run([str(engine_path), '--version'], output, timeout=10).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='figure-formula-') as temporary:
        work = Path(temporary)
        # Only exact registered PFB/TFM bytes are exposed as local font assets.
        for item in fonts['files']:
            shutil.copyfile(item['path'], work / item['name'])
        if alphabet_fonts:
            for item in alphabet_fonts['files']:
                shutil.copyfile(item['path'], work / item['name'])
        tex = work / f'{asset_id}.tex'
        tex.write_text(source, encoding='utf-8')
        environment = dict(os.environ, TEXFONTS=str(work) + os.pathsep, T1FONTS=str(work) + os.pathsep,
                           openin_any='p', openout_any='p')
        command = engine_command(engine_path, name, tex, work)
        chatter = _run(command, work, environment, timeout)
        log_path = work / f'{asset_id}.log'
        log = log_path.read_text(errors='replace') if log_path.exists() else chatter
        metrics = _metrics(log)
        pdf = work / f'{asset_id}.pdf'
        if not pdf.is_file():
            raise ValueError('TeX completed without a PDF')
        font_report = _run([pdffonts, str(pdf)], work, timeout=20)
        embedded = _embedded_fonts(font_report, [item['postscript_name'] for item in alphabet_fonts['files']] if alphabet_fonts else ())
        dependency_file = work / ('dependencies.d' if name == 'tectonic' else f'{asset_id}.fls')
        dependencies = dependency_file.read_text(errors='replace') if dependency_file.exists() else ''
        local_used = [item for item in fonts['files'] if item['name'] in dependencies]
        local_alphabet_used = [item for item in alphabet_fonts['files'] if item['name'] in dependencies] if alphabet_fonts else []
        # Registered local metrics and Type1 files must appear in the engine's
        # dependencies; matching PDF family names alone is insufficient.
        if not any(item['name'].endswith('.tfm') for item in local_used):
            raise ValueError('TeX dependency audit did not confirm registered local font metrics and Type1 fonts')
        used_type1 = {item['name'].lower() for item in local_used if item['name'].endswith('.pfb')}
        used_alphabets = {item['postscript_name'] for item in local_alphabet_used}
        for embedded_name in embedded:
            base = _embedded_base(embedded_name)
            required_program = base.lower() + '.pfb'
            if required_program not in used_type1 and base not in used_alphabets:
                raise ValueError(f'Embedded font lacks a registered local Type1 dependency: {embedded_name}')
        render_dpi = max(dpi, 96 * min_scale)
        if render_dpi > 4096:
            raise ValueError('Requested sampling scale requires more than maximum 4096 DPI')
        page = None
        for attempt in range(3):
            prefix = work / 'rendered'
            _run([cairo, '-png', '-transp', '-singlefile', '-r', f'{render_dpi:.4f}', str(pdf), str(prefix)], work, timeout=30)
            with Image.open(prefix.with_suffix('.png')) as rendered:
                page = rendered.copy()
            ink, bbox = _tight_rgba(page, rotation_deg)
            natural = [ink.width * 96 / render_dpi, ink.height * 96 / render_dpi]
            target = display_size_px or natural
            scale = min(ink.width / target[0], ink.height / target[1])
            if scale >= min_scale:
                break
            render_dpi = math.ceil(render_dpi * min_scale / scale * 1.01)
            if render_dpi > 4096:
                raise ValueError('Requested display size requires more than maximum 4096 DPI')
        else:
            raise ValueError('Could not meet formula sampling requirement')
        final_png = output / f'{asset_id}.png'
        ink.save(final_png)
        shutil.copyfile(tex, output / f'{asset_id}.tex')
        shutil.copyfile(pdf, output / f'{asset_id}.pdf')
        # Keep logs and dependencies under stable names for later inspection.
        (output / f'{asset_id}.log').write_text(log, encoding='utf-8')
        (output / f'{asset_id}.dependencies.txt').write_text(dependencies, encoding='utf-8')
        pixels_per_tex_pt = render_dpi / 72.27
        baseline_full_px = (padding_pt + metrics['height_pt']) * pixels_per_tex_pt
        baseline_ink_px = baseline_full_px - bbox[1]
        audit = {'schema_version': '1', 'asset_id': asset_id, 'kind': 'generated_latex',
                 'latex': expression, 'transcription_confirmed': True, 'engine': name,
                 'engine_path': str(engine_path), 'engine_version': engine_version,
                 'engine_sha256': _sha(engine_path), 'shell_escape': False,
                 'font_family': 'Computer Modern' if not alphabet_fonts else 'MathJax TeX alphabets + Computer Modern Greek/symbols',
                 'font_size_px': font_size_px,
                 'design_size': design_size,
                 'stroke_width_px': stroke_width_px, 'stroke_width_pdf_pt': stroke_width_px * 72 / 96,
                 'font_size_tex_pt': font_size_px * 72.27 / 96, 'color': color.upper(),
                 'rotation_deg': rotation_deg, 'dpi': render_dpi,
                 'min_sampling_scale': min_scale, 'actual_sampling_scale': scale,
                 'display_size_px': target, 'natural_display_size_px': natural,
                 'png_pixels': [ink.width, ink.height], 'rendered_page_pixels': list(page.size),
                 'alpha_bbox_px': list(bbox), 'baseline_ink_unrotated_px': baseline_ink_px,
                 'tex_box': metrics, 'font_registry': fonts,
                 'alphabet_font_registry': alphabet_fonts,
                 'registered_font_dependencies': [item['name'] for item in local_used],
                 'registered_alphabet_font_dependencies': [item['name'] for item in local_alphabet_used],
                 'embedded_fonts': embedded, 'reference_crop_used': False,
                 'assets': {ext: {'path': str(output / f'{asset_id}.{ext}'), 'sha256': _sha(output / f'{asset_id}.{ext}')}
                            for ext in ('tex', 'pdf', 'png')}}
        (output / f'{asset_id}.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return audit


def render_batch(specification, *, output_dir, font_manifest, engine=None, alphabet_font_manifest=None, overwrite=False):
    """Render a confirmed JSON batch; reject all bad expressions before writing."""
    items = specification.get('formulas') if isinstance(specification, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('Formula batch must contain 1 to 200 formulas')
    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Formula specification must be an object')
        validate_expression(item.get('latex'), confirmed=item.get('confirmed', False))
        identity = item.get('id')
        if not isinstance(identity, str) or not SAFE_ID.fullmatch(identity) or '..' in identity:
            raise ValueError('Formula ID is invalid')
        ids.append(identity)
    if len(ids) != len(set(ids)):
        raise ValueError('Formula IDs must be unique')
    audits = []
    for item in items:
        audits.append(render_formula(item['latex'], asset_id=item['id'], output_dir=output_dir,
                                     font_manifest=font_manifest, engine=engine, confirmed=True,
                                     font_size_px=item.get('font_size_px', 24), color=item.get('color', '#000000'),
                                     rotation_deg=item.get('rotation_deg', 0), display_size_px=item.get('display_size_px'),
                                     dpi=item.get('dpi', 768), min_scale=item.get('min_scale', 8),
                                     padding_pt=item.get('padding_pt', 1), design_size=item.get('design_size'),
                                     stroke_width_px=item.get('stroke_width_px', 0),
                                     alphabet_font_manifest=alphabet_font_manifest,
                                     overwrite=overwrite))
    path = Path(output_dir).expanduser().resolve() / 'formulas.json'
    path.write_text(json.dumps({'schema_version': '1', 'formulas': audits}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'manifest': str(path), 'count': len(audits), 'formulas': audits}
