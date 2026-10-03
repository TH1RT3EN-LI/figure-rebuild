"""Bounded source-PDF replay, separate from reading formula or relation meaning.

PDF libraries are imported only inside an isolated worker.  This first profile
accepts opaque black unmasked glyph outlines and supported original native
image occurrences, with at most one full-page Normal RGB group.  Other native
contexts remain unresolved.  Caller completeness declarations are never used.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class SourceReplayError(ValueError):
    """Invalid replay request, or a bounded input could not be read safely."""


@dataclass(frozen=True)
class PdfSourceDescriptor:
    pdf_path: str
    pdf_sha256: str
    page: int
    region: tuple
    # This initial profile requires a positive, pixel-aligned uniform scale.
    scale: float = 2.0
    reference_png_path: str | None = None
    reference_png_sha256: str | None = None


@dataclass(frozen=True)
class SourceReplayPolicy:
    profile: str = 'opaque_black_glyphs_native_images_v1'
    glyph_mode: str = 'outline'
    native_occurrence_rendering: bool = True
    max_clip_overhang: float = 0.0001
    # Explicit postprocessing needs a separately implemented replay proof.
    transformations: tuple = ()


@dataclass(frozen=True)
class ReplayLimits:
    max_pdf_bytes: int = 134217728
    max_svg_bytes: int = 16777216
    max_font_bytes: int = 16777216
    max_total_font_bytes: int = 67108864
    max_native_paints: int = 100000
    max_source_paints: int = 200000
    max_commands: int = 200000
    max_context_events: int = 200000
    max_context_depth: int = 128
    max_image_pixels: int = 32000000
    max_total_image_pixels: int = 128000000
    max_reference_pixels: int = 16000000
    max_asset_bytes: int = 134217728
    max_scene_bytes: int = 33554432
    max_pptx_bytes: int = 134217728
    max_zip_entries: int = 2048
    max_zip_uncompressed_bytes: int = 268435456
    max_zip_entry_bytes: int = 134217728
    max_zip_expansion_ratio: int = 2000
    max_xml_bytes: int = 33554432
    max_xml_nodes: int = 500000
    max_control_points: int = 1000000
    max_worker_memory_mib: int = 2048
    timeout_seconds: float = 120.0


def _finite(value, name, low=None, high=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise SourceReplayError(name + ' must be a finite number')
    if low is not None and value < low or high is not None and value > high:
        raise SourceReplayError(name + ' is outside supported bounds')
    return value


def _request(source, policy, limits):
    if not isinstance(source, PdfSourceDescriptor) or not isinstance(policy, SourceReplayPolicy) or not isinstance(limits, ReplayLimits):
        raise SourceReplayError('Typed source, policy and limits are required')
    import re
    if not isinstance(source.pdf_path, (str, os.PathLike)) or isinstance(source.pdf_path, bool):
        raise SourceReplayError('PDF path must be a filesystem path')
    if source.reference_png_path is not None and not isinstance(source.reference_png_path, (str, os.PathLike)):
        raise SourceReplayError('Reference PNG path must be a filesystem path')
    if not isinstance(source.pdf_sha256, str) or not re.fullmatch('[0-9a-f]{64}', source.pdf_sha256):
        raise SourceReplayError('PDF SHA256 must be lowercase hexadecimal')
    if type(source.page) is not int or source.page < 1:
        raise SourceReplayError('Page must be a positive integer')
    if not isinstance(source.region, (tuple, list)) or len(source.region) != 4:
        raise SourceReplayError('Region must contain four PDF coordinates')
    r = [_finite(v, 'region coordinate') for v in source.region]
    if r[2] <= r[0] or r[3] <= r[1]:
        raise SourceReplayError('Region must have positive width and height')
    _finite(source.scale, 'scale', 0.125, 8)
    scaled = [_finite(v * source.scale, 'scaled region coordinate') for v in r]
    if any(v != round(v) for v in scaled):
        raise SourceReplayError('Initial replay profile requires pixel-aligned region edges')
    if bool(source.reference_png_path) != bool(source.reference_png_sha256):
        raise SourceReplayError('Reference PNG path and SHA must be supplied together')
    if source.reference_png_sha256 is not None and (not isinstance(source.reference_png_sha256,str) or not re.fullmatch('[0-9a-f]{64}', source.reference_png_sha256)):
        raise SourceReplayError('Reference PNG SHA256 must be lowercase hexadecimal')
    if not isinstance(policy.profile, str) or not isinstance(policy.glyph_mode, str) or type(policy.native_occurrence_rendering) is not bool:
        raise SourceReplayError('Invalid replay policy types')
    _finite(policy.max_clip_overhang, 'max_clip_overhang', 0, 0.0001)
    if not isinstance(policy.transformations, (tuple, list)):
        raise SourceReplayError('transformations must be an explicit sequence')
    for name, value in asdict(limits).items():
        if name == 'timeout_seconds':
            _finite(value, name, 0.05, 600)
        elif type(value) is not int or not 1 <= value <= 1073741824:
            raise SourceReplayError(name + ' must be a positive bounded integer')
    if not 64 <= limits.max_worker_memory_mib <= 8192:
        raise SourceReplayError('Worker memory must be in [64, 8192] MiB')
    if limits.max_commands > 2000000 or limits.max_source_paints > 200000:
        raise SourceReplayError('Source parser paint/command ceiling exceeded')
    if limits.max_svg_bytes > 16777216:
        raise SourceReplayError('Source SVG parser ceiling is 16 MiB')
    if (r[2]-r[0]) * (r[3]-r[1]) * source.scale**2 > limits.max_reference_pixels:
        raise SourceReplayError('Reference raster exceeds pixel budget')


def _read(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SourceReplayError('Input byte budget exceeded: ' + str(path))
    return data


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def _unsupported(message, **evidence):
    return {'schema_version': 1, 'status': 'UNRESOLVED',
            'scope': 'source_geometry_and_image_occurrence_replay',
            'unresolved': [{'code': message, **evidence}], 'failures': [],
            'semantic_recognition': 'NOT_PROVIDED', 'visual_acceptance': 'NOT_EVALUATED'}


def replay_pdf_source(source, *, evidence_dir, policy=None, limits=None):
    """Replay immutable source bytes in a bounded subprocess; never build PPT.

    Invalid API arguments raise SourceReplayError. Unsupported source effects,
    optional-dependency failures and exhausted budgets return UNRESOLVED. A
    provided source/reference digest mismatch returns FAIL. evidence_dir must
    not exist. The source PDF and reference are read once and copied before
    parsing; later source-path mutation cannot change this replay's inputs.
    """
    policy = SourceReplayPolicy() if policy is None else policy
    limits = ReplayLimits() if limits is None else limits
    _request(source, policy, limits)
    out = Path(evidence_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    request = {'source': asdict(source), 'policy': asdict(policy), 'limits': asdict(limits)}
    request['source']['pdf_path'] = os.fspath(source.pdf_path)
    if source.reference_png_path is not None:
        request['source']['reference_png_path'] = os.fspath(source.reference_png_path)
    try:
        raw = _read(source.pdf_path, limits.max_pdf_bytes)
        if _sha(raw) != source.pdf_sha256:
            result = _unsupported('source_pdf_sha256_mismatch')
            result['status'], result['failures'], result['unresolved'] = 'FAIL', result['unresolved'], []
            _save(out/'report.json', result)
            return result
        (out/'source.pdf').write_bytes(raw)
        request['source']['pdf_path'] = str(out/'source.pdf')
        request['original_pdf_path'] = str(Path(source.pdf_path).resolve())
        if source.reference_png_path:
            raw = _read(source.reference_png_path, limits.max_asset_bytes)
            if _sha(raw) != source.reference_png_sha256:
                result = _unsupported('reference_png_sha256_mismatch')
                result['status'], result['failures'], result['unresolved'] = 'FAIL', result['unresolved'], []
                _save(out/'report.json', result)
                return result
            (out/'reference.png').write_bytes(raw)
            request['source']['reference_png_path'] = str(out/'reference.png')
        request['evidence_dir'] = str(out)
        _save(out/'request.json', request)
        cmd = [sys.executable, '-m', 'figure_rebuild.pdf_source_replay', '--worker', str(out/'request.json')]
        with (out/'worker.stdout').open('xb') as stdout, (out/'worker.stderr').open('xb') as stderr:
            try:
                process = subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=limits.timeout_seconds,
                                         cwd=out, check=False, env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
            except subprocess.TimeoutExpired:
                result = _unsupported('source_replay_timeout', seconds=limits.timeout_seconds)
            else:
                if process.returncode or not (out/'worker-result.json').is_file():
                    result = _unsupported('source_worker_failed', returncode=process.returncode)
                else:
                    result = json.loads(_read(out/'worker-result.json', limits.max_scene_bytes))
        result['worker'] = {'argv': cmd, 'timeout_seconds': limits.timeout_seconds,
                            'memory_mib': limits.max_worker_memory_mib}
    except (OSError, ValueError, TypeError, RecursionError) as error:
        result = _unsupported('source_input_or_worker_error', detail=str(error))
    result['input_source'] = {'pdf_sha256': source.pdf_sha256, 'page': source.page,
                              'region': list(source.region), 'scale': source.scale}
    _save(out/'report.json', result)
    return result


def _image_identity(paint):
    import base64
    import re
    from xml.etree import ElementTree as ET
    node = ET.fromstring(paint.source_xml)
    if paint.unsupported:
        raise SourceReplayError('Unsupported SVG image occurrence: ' + '; '.join(paint.unsupported))
    width, height = float(node.get('width', 0)), float(node.get('height', 0))
    x, y = float(node.get('x', 0)), float(node.get('y', 0))
    for v in (width, height, x, y):
        _finite(v, 'image viewport')
    if min(width, height) <= 0:
        raise SourceReplayError('Invalid SVG image viewport')
    href = node.get('{http://www.w3.org/1999/xlink}href', node.get('href', ''))
    match = re.fullmatch(r'data:image/[\w.+-]+;base64,([\s\S]+)', href)
    if not match:
        raise SourceReplayError('Source image resource is not embedded')
    raw = base64.b64decode(re.sub(r'\s', '', match[1]), validate=True)
    a,b,c,d,e,f = paint.transform
    return {'unit_transform': [a*width,b*width,c*height,d*height,a*x+c*y+e,b*x+d*y+f],
            'encoded_sha256': _sha(raw)}


def _outside(paint, region):
    # Only the currently supported fill-only glyph profile uses this shortcut.
    if paint.kind != 'glyph' or paint.unsupported or not paint.commands:
        return False
    if paint.style.get('stroke', 'none') != 'none' or paint.style.get('filter', 'none') != 'none':
        return False
    if any(g.get('filter', 'none') != 'none' for g in paint.groups):
        return False
    points = [v for c in paint.commands for v in c[1:]]
    if not points:
        return False
    return (max(p[0] for p in points) < region[0] or min(p[0] for p in points) > region[2]
            or max(p[1] for p in points) < region[1] or min(p[1] for p in points) > region[3])


def _replay(request):
    # Imports stay here: importing this public module needs neither PyMuPDF nor PIL.
    import pymupdf as fitz
    from . import pdf_source as source_helper
    from . import pdf_images as image_helper
    from . import pdf_paint_context as context_helper
    from dataclasses import asdict as record
    source = PdfSourceDescriptor(**request['source'])
    policy, limits = SourceReplayPolicy(**request['policy']), ReplayLimits(**request['limits'])
    _request(source, policy, limits)
    out = Path(request['evidence_dir'])
    if policy.profile != 'opaque_black_glyphs_native_images_v1' or policy.glyph_mode != 'outline' or not policy.native_occurrence_rendering:
        return _unsupported('unsupported_source_replay_policy')
    if policy.transformations:
        return _unsupported('unimplemented_explicit_transformation_proof', requested=policy.transformations)
    pdf, region, scale = Path(source.pdf_path), source.region, source.scale
    if _sha(_read(pdf, limits.max_pdf_bytes)) != source.pdf_sha256:
        return _unsupported('private_source_snapshot_changed')
    started = time.monotonic()
    def tick():
        if time.monotonic()-started > limits.timeout_seconds:
            raise SourceReplayError('Source replay deadline exceeded')
    fonts, font_bytes = [], 0
    with fitz.open(pdf) as document:
        if source.page > len(document):
            return _unsupported('source_page_outside_document')
        page = document[source.page-1]
        if page.rotation:
            return _unsupported('rotated_source_page')
        page_rect = list(page.rect)
        if region[0] < page_rect[0] or region[1] < page_rect[1] or region[2] > page_rect[2] or region[3] > page_rect[3]:
            return _unsupported('source_roi_outside_page')
        # Font payloads are captured by the source PDF and independently hashed.
        # Built-in font programs have no embedded bytes; runtime version is bound.
        for xref in sorted({row[0] for row in page.get_fonts(full=True)}):
            tick()
            name, ext, kind, data = document.extract_font(xref)
            font_bytes += len(data)
            if len(data) > limits.max_font_bytes or font_bytes > limits.max_total_font_bytes:
                return _unsupported('source_font_resource_byte_budget')
            fonts.append({'xref': xref, 'name': name, 'kind': kind, 'format': ext,
                          'embedded_bytes': len(data), 'embedded_sha256': _sha(data) if data else None,
                          'basis': 'embedded font bytes' if data else 'PDF built-in font plus bound renderer runtime'})
        infos, bboxlog, texttrace = page.get_image_info(xrefs=True), page.get_bboxlog(), page.get_texttrace()
        source_pixels = sum(i['width']*i['height'] for i in infos)
        if any(i['width']*i['height'] > limits.max_image_pixels for i in infos) or source_pixels > limits.max_total_image_pixels:
            return _unsupported('source_image_pixel_budget')
        svg = page.get_svg_image(text_as_path=True)
        if len(svg.encode()) > limits.max_svg_bytes:
            return _unsupported('source_svg_byte_budget')
    # MuPDF metadata/image inspection can populate color-conversion caches.
    # The reference is the first render of a separate pristine document.
    with fitz.open(pdf) as pristine:
        pix = pristine[source.page-1].get_pixmap(matrix=fitz.Matrix(scale,scale),clip=fitz.Rect(region),alpha=False)
        pix.save(out/'fresh-source.png')
    (out/'source.svg').write_text(svg, encoding='utf-8')
    reference_sha = _sha(_read(out/'fresh-source.png', limits.max_asset_bytes))
    if source.reference_png_sha256 and reference_sha != source.reference_png_sha256:
        result = _unsupported('fresh_reference_render_differs')
        result.update(status='FAIL', failures=result['unresolved'], unresolved=[])
        return result
    expanded = source_helper.extract_outlined_svg(svg, max_total_commands=limits.max_commands)
    if len(expanded.paints) > limits.max_source_paints:
        return _unsupported('expanded_source_paint_budget')
    context = context_helper.inspect_pdf_paint_context(pdf, page=source.page, expected_bboxlog=bboxlog,
        max_paints=limits.max_native_paints, max_context_events=limits.max_context_events,
        max_context_depth=limits.max_context_depth, max_pdf_bytes=limits.max_pdf_bytes)
    _save(out/'native-context.json', context)
    unresolved, failures = [], []
    def unknown(code, **detail):
        unresolved.append({'code': code, **detail})
    for g in context['groups']:
        if not (len(context['groups']) == 1 and g['begin_paint_seqno'] == 0 and
                g['end_paint_seqno'] == context['paint_count'] and g['bbox_pdf_pt'] == page_rect and
                g['isolated'] and not g['knockout'] and g['blendmode'] == 0 and
                g['alpha'] == 1.0 and g['colorspace'] == 'DeviceRGB'):
            unknown('unsupported_native_group', group_id=g['group_id'])
    if context['masks']:
        unknown('general_native_soft_mask_not_supported')
    for paint in context['paints']:
        if paint['pattern_depth'] or paint['kind'] not in ('fill-text','fill-image'):
            unknown('unsupported_native_paint_kind_or_pattern', source_seqno=paint['source_seqno'])
        if paint['kind'] == 'fill-text' and (paint['role'] != 'normal' or paint['clip_ids']):
            unknown('native_text_mask_or_clip_needs_crosswalk', source_seqno=paint['source_seqno'])
        if paint['kind'] == 'fill-image' and (paint['mask_definition_ids'] or paint['active_mask_ids']):
            unknown('general_masked_native_image', source_seqno=paint['source_seqno'])
    textseq = {p['source_seqno'] for p in context['paints'] if p['kind']=='fill-text'}
    if ({t['seqno'] for t in texttrace} != textseq or
            any(t['type'] != 0 or t['opacity'] != 1 or tuple(t['color']) not in ((0.,),(0.,0.,0.)) for t in texttrace)):
        unknown('native_text_effect_profile_not_opaque_black')
    # Without a supported whole-page context there is no safe partial mapping
    # from native paints to expanded glyphs. Do not output misleading objects.
    if unresolved:
        return {'schema_version': 1, 'status': 'UNRESOLVED', 'unresolved': unresolved,
                'failures': [], 'native_paint_count': context['paint_count'],
                'expanded_source_paints': len(expanded.paints), 'semantic_recognition': 'NOT_PROVIDED',
                'scope': policy.profile, 'native_to_expanded_glyph_bijection_claimed': False}
    transform = (scale,0.,0.,scale,-scale*region[0],-scale*region[1])
    image_transform = [[scale,0.,transform[4]],[0.,scale,transform[5]]]
    objects, provenance, ledger, order = [], [], [], {}
    image_paints = [p for p in expanded.paints if p.kind=='image']
    if len(image_paints) != len(infos):
        return _unsupported('svg_native_image_occurrence_stream_mismatch')
    image_indices = {p.source_id:i for i,p in enumerate(image_paints)}
    black = {'fill':'#000000','stroke':'none','stroke_width':0,'opacity':1.0}
    sampled_pixels = 0
    for paint in expanded.paints:
        tick()
        entry = {'source_id': paint.source_id, 'paint_index': paint.paint_index, 'kind': paint.kind,
                 'commands_sha256': _sha(_canonical(paint.commands)),
                 'source_transform': list(paint.transform), 'style_sha256': _sha(_canonical(paint.style)),
                 'clip_context_sha256': _sha(_canonical(paint.clips)), 'group_context_sha256': _sha(_canonical(paint.groups)),
                 'state': 'unresolved'}
        ledger.append(entry)
        try:
            if paint.kind != 'image':
                if _outside(paint, region):
                    entry.update(state='skipped', reason='recomputed_control_hull_outside_roi')
                    continue
                result = source_helper.outline_paths(expanded, glyph_mode='outline', paint_ids=[paint.source_id],
                    region=region, transform=transform, max_clip_overhang=policy.max_clip_overhang)
                if any(paint.kind!='glyph' or o['style']!=black for o in result.objects):
                    raise SourceReplayError('Outside opaque black glyph profile')
                if result.objects:
                    entry.update(state='converted', object_ids=[o['id'] for o in result.objects])
                    objects.extend(result.objects); provenance.extend(result.provenance)
                    for sub,obj in enumerate(result.objects):
                        order[obj['id']] = (paint.paint_index,sub)
                else:
                    if len(result.skipped) != 1 or result.skipped[0]['source_id'] != paint.source_id:
                        raise SourceReplayError('Empty conversion lacks unique explicit skip proof')
                    entry.update(state='skipped', skip_proof=result.skipped[0])
                continue
            index = image_indices[paint.source_id]
            info, identity = infos[index], _image_identity(paint)
            if any(abs(a-b) > .002 for a,b in zip(identity['unit_transform'],info['transform'])):
                raise SourceReplayError('Ordered SVG/native image transform mismatch')
            b = info['bbox']
            if not paint.unsupported and paint.style.get('filter','none')=='none' and all(g.get('filter','none')=='none' for g in paint.groups) and (b[2]<region[0] or b[0]>region[2] or b[3]<region[1] or b[1]>region[3]):
                entry.update(state='skipped', reason='complete_native_image_bounds_outside_roi')
                continue
            # Native occurrence helper samples integer source frames at 8x.
            bw = max(0, math.ceil(min(b[2],region[2])*scale+transform[4])-math.floor(max(b[0],region[0])*scale+transform[4]))
            bh = max(0, math.ceil(min(b[3],region[3])*scale+transform[5])-math.floor(max(b[1],region[1])*scale+transform[5]))
            pixels = bw*bh*64
            sampled_pixels += pixels
            if pixels > limits.max_image_pixels or sampled_pixels > limits.max_total_image_pixels:
                raise SourceReplayError('Derived image sampling pixel budget exceeded')
            rows = image_helper.extract_pdf_images(pdf, page=source.page, region=region,
                source_transform=image_transform, image_indices=[index], allow_affine_rasterization=True,
                native_occurrence_rendering=True)
            if not rows:
                entry.update(state='skipped', reason='native_helper_proved_outside_active_clip_roi')
                continue
            if len(rows)!=1 or rows[0]['image_index']!=index:
                raise SourceReplayError('Native image occurrence identity changed')
            row=rows[0];pr=row['provenance'];receipt=pr['native_image']
            if (pr['source_pdf_sha256']!=source.pdf_sha256 or pr['svg_sha256']!=expanded.source_sha256 or
                    pr['encoded_image_sha256']!=identity['encoded_sha256'] or pr['svg_image_id']!=paint.source_element_id):
                raise SourceReplayError('Fresh image source resource identity differs')
            native=context_helper.paint_context_record(context,row['paint_seqno'],expected_kind='fill-image')
            if (receipt['paint_seqno']!=native['source_seqno'] or receipt['verified_total_source_paints']!=context['paint_count'] or
                    receipt['image_paints_forwarded']!=1 or receipt['independent_text_path_shading_other_image_paints_forwarded']!=0):
                raise SourceReplayError('Native image forwarding identity does not close')
            clips={c['clip_id']:c for c in context['clips']}
            chain=receipt['clip_chain']
            if len(chain)!=len(native['clip_ids']):
                raise SourceReplayError('Image native clip chain differs')
            for cid,forwarded in zip(native['clip_ids'],chain):
                original=clips[cid]
                if (original['kind']!=forwarded['kind'] or original['matrix']!=forwarded['matrix'] or
                        original['begin_paint_seqno']!=row['paint_seqno']):
                    raise SourceReplayError('Original image mask/clip occurrence not bound')
            if native['active_image_mask_clip_ids'] and not receipt.get('attached_mask',{}).get('actual_handle_and_matrix_and_sequence_bound'):
                raise SourceReplayError('Attached native image mask not proved')
            if receipt['source_groups_applied']!='original_callbacks_preserved_not_removed':
                raise SourceReplayError('Original native image group not forwarded')
            asset_bytes=row.pop('asset_bytes')
            if len(asset_bytes)>limits.max_asset_bytes or _sha(asset_bytes)!=row['asset_sha256']:
                raise SourceReplayError('Derived image asset bytes/hash exceed proof')
            asset=out/'assets'/('source-image-%05d.png'%index);asset.parent.mkdir(exist_ok=True);asset.write_bytes(asset_bytes)
            obj={'id':paint.source_id,'kind':'image','z_index':paint.paint_index,'path':str(asset.relative_to(out)),
                 'sha256':row['asset_sha256'],'box':row['box'],'crop':row['crop'],'fit':row['fit'],'editable':False}
            objects.append(obj);order[obj['id']]=(paint.paint_index,0)
            provenance.append({'object_id':obj['id'],'source_kind':'image','source_paint_index':paint.paint_index,
                               'native_paint_seqno':row['paint_seqno'],'image_extraction':row})
            entry.update(state='converted',object_ids=[obj['id']],native_image_paint_seqno=row['paint_seqno'])
        except (ValueError,TypeError,KeyError) as error:
            entry['reason']=str(error)
            unknown('unsupported_expanded_source_paint',source_id=paint.source_id,detail=str(error))
    ids=[p.source_id for p in expanded.paints]
    if len(ids)!=len(set(ids)) or [x['source_id'] for x in ledger]!=ids:
        unknown('source_occurrence_ledger_not_unique_complete')
    objects.sort(key=lambda obj:order[obj['id']])
    for z,obj in enumerate(objects):obj['z_index']=z
    if len({o['id'] for o in objects})!=len(objects):
        unknown('duplicate_replayed_object_identity')
    expected={'canvas':{'width':pix.width,'height':pix.height,'background':'#FFFFFF'},
              'source_sha256':reference_sha,'objects':objects}
    _save(out/'expected-scene.json',expected);_save(out/'source-ledger.json',ledger)
    _save(out/'object-provenance.json',provenance);_save(out/'font-resources.json',fonts)
    counts={state:sum(x['state']==state for x in ledger) for state in ('converted','skipped','unresolved')}
    code_modules=[source_helper,image_helper,context_helper]
    bindings={Path(m.__file__).name:_sha(_read(m.__file__,16777216)) for m in code_modules}
    return {'schema_version':1,'status':'UNRESOLVED' if unresolved else 'VERIFIED_IN_DECLARED_SCOPE',
            'scope':policy.profile,'unresolved':unresolved,'failures':failures,
            'semantic_recognition':'NOT_PROVIDED','visual_acceptance':'NOT_EVALUATED',
            'expected_scene_path':str(out/'expected-scene.json'),
            'expected_scene_sha256':_sha(_read(out/'expected-scene.json',limits.max_scene_bytes)),
            'expected_scene_canonical_sha256':_sha(_canonical(expected)),
            'source_pdf_sha256':source.pdf_sha256,'source_svg_sha256':expanded.source_sha256,
            'source_reference_sha256':reference_sha,'reference_render_basis':'separate_pristine_document_first_render','target_transform':list(transform),
            'native_paint_count':context['paint_count'],'expanded_source_paints':len(ledger),
            'source_accounting':counts,'target_objects':len(objects),
            'native_to_expanded_glyph_bijection_claimed':False,
            'source_xml_basis':'normalized serialization; original whole SVG bytes retained separately',
            'runtime':{'pymupdf':fitz.VersionBind,'helper_sha256':bindings},
            'parser_limits':expanded.parser_limits,'font_resource_bytes':font_bytes,
            'source_image_pixels':source_pixels,'derived_image_pixels':sampled_pixels,
            'evidence':{p.name:_sha(_read(p,max(limits.max_scene_bytes,limits.max_svg_bytes)))
                        for p in (out/'source.svg',out/'source-ledger.json',out/'font-resources.json',out/'native-context.json')}}


def _worker(request_path):
    request=json.loads(_read(request_path,1048576));out=Path(request['evidence_dir'])
    limits=ReplayLimits(**request['limits'])
    try:
        import resource
        cap=limits.max_worker_memory_mib*1024*1024
        resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
        seconds=max(1,math.ceil(limits.timeout_seconds))
        resource.setrlimit(resource.RLIMIT_CPU,(seconds,seconds+1))
        code_paths=sorted(Path(__file__).parent.glob('pdf_*.py'))
        before={p.name:_sha(_read(p,16777216)) for p in code_paths}
        result=_replay(request)
        after={p.name:_sha(_read(p,16777216)) for p in code_paths}
        if before!=after:
            result=_unsupported('source_replay_runtime_changed_during_call')
        else:
            result['source_runtime_sha256']=before
    except Exception as error:
        result=_unsupported('source_replay_exception',type=type(error).__name__,detail=str(error))
    _save(out/'worker-result.json',result)


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--worker':
        raise SystemExit('This module exposes replay_pdf_source(); the worker is internal.')
    _worker(sys.argv[2])
