"""Source-preservation replay and actual-PPT correspondence, not recognition.

This optional API does not change build, semantic, output-review or acceptance
status. It independently regenerates source objects; no caller completeness
boolean can certify them. Unknown source contexts and transformations stay
UNRESOLVED, even if structural validation or a visual review passed elsewhere.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from .pdf_source_replay import (PdfSourceDescriptor, SourceReplayPolicy,
    ReplayLimits, SourceReplayError, replay_pdf_source, _read, _save, _canonical,
    _sha, _request)
from .pptx_fidelity import audit_pptx_fidelity


def _bound_path(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise SourceReplayError('Source/asset path must be relative')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise SourceReplayError('Source/asset path escapes its bound root')
    return path


def compare_replayed_scene(expected, actual, *, asset_root, limits=None):
    """Compare full source-derived objects, not just self-reported provenance.

    expected must come from replay_pdf_source(). This pure helper alone does
    not certify its origin; audit_source_fidelity() is the source-bound API.
    """
    limits = ReplayLimits() if limits is None else limits
    if not isinstance(limits, ReplayLimits):
        raise SourceReplayError('ReplayLimits required')
    failures, unresolved, objects, assets = [], [], [], []
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return {'status':'UNRESOLVED','failures':[], 'unresolved':['invalid scene record']}
    wanted, received = expected.get('objects'), actual.get('objects')
    if not isinstance(wanted,list) or not isinstance(received,list):
        return {'status':'UNRESOLVED','failures':[], 'unresolved':['objects must be explicit arrays']}
    if len(wanted)>limits.max_source_paints or len(received)>limits.max_source_paints:
        return {'status':'UNRESOLVED','failures':[], 'unresolved':['scene object budget exceeded']}
    try:
        wi, ai = [o['id'] for o in wanted], [o['id'] for o in received]
        if any(not isinstance(i,str) for i in wi+ai):
            raise ValueError('Non-string object identity')
        if wi != ai or len(ai)!=len(set(ai)):
            failures.append({'code':'source_object_inventory_or_order_mismatch',
                             'missing':sorted(set(wi)-set(ai)), 'extra':sorted(set(ai)-set(wi)),
                             'order_equal':wi==ai, 'duplicates':len(ai)!=len(set(ai))})
        by = {o['id']:o for o in received}
        for want in wanted:
            got = by.get(want['id'])
            if got is None:
                continue
            equal = _canonical(got) == _canonical(want)
            if not equal:
                failures.append({'code':'source_object_replay_mismatch','object_id':want['id'],
                                 'fields':[k for k in sorted(set(want)|set(got)) if k not in want or k not in got or _canonical(want[k])!=_canonical(got[k])]})
            objects.append({'id':want['id'],'replay_sha256':_sha(_canonical(want)),
                            'actual_sha256':_sha(_canonical(got)),'equal':equal})
            if got.get('kind')=='image':
                path=_bound_path(asset_root,got['path']);raw=_read(path,limits.max_asset_bytes)
                digest=_sha(raw)
                assets.append({'path':str(path),'sha256':digest})
                if digest!=want.get('sha256'):
                    failures.append({'code':'source_bitmap_bytes_mismatch','object_id':want['id']})
        if actual.get('canvas') != expected['canvas']:
            failures.append({'code':'source_canvas_mapping_mismatch'})
        source=actual.get('source',{})
        if source.get('sha256')!=expected['source_sha256']:
            failures.append({'code':'source_reference_declaration_mismatch'})
        path=_bound_path(asset_root,source.get('path'))
        digest=_sha(_read(path,limits.max_asset_bytes));assets.append({'path':str(path),'sha256':digest})
        if digest!=expected['source_sha256']:
            failures.append({'code':'actual_source_reference_bytes_mismatch'})
    except (OSError,ValueError,TypeError,KeyError) as error:
        unresolved.append(str(error))
    return {'schema_version':1, 'status':'FAIL' if failures else 'UNRESOLVED' if unresolved else 'VERIFIED_IN_DECLARED_SCOPE',
            'scope':'complete_source_replayed_object_geometry_style_order_and_image_bytes',
            'failures':failures,'unresolved':unresolved,'objects':objects,'asset_bindings':assets,
            'semantic_recognition':'NOT_PROVIDED','visual_acceptance':'NOT_EVALUATED'}


def audit_source_fidelity(source, *, manifest_path, resolved_scene_path, asset_root,
                          pptx_path, evidence_dir, policy=None, limits=None):
    """Freeze, independently replay and compare one limited supported source.

    evidence_dir must be new. Missing source is NOT_PROVIDED; unknown effects,
    explicit transformations without replay support and exhausted limits are
    UNRESOLVED. Mismatches are FAIL. VERIFIED_IN_DECLARED_SCOPE certifies only
    the supported origin/geometry/placement/media chain, never semantic meaning,
    rendered visual acceptance, arbitrary PDF support or lossless rasterization.
    """
    policy = SourceReplayPolicy() if policy is None else policy
    limits = ReplayLimits() if limits is None else limits
    if source is not None:
        _request(source,policy,limits)
    elif not isinstance(limits, ReplayLimits) or not isinstance(policy,SourceReplayPolicy):
        raise SourceReplayError('Typed policy and limits required')
    out=Path(evidence_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'schema_version':1,'status':'NOT_PROVIDED' if source is None else 'UNRESOLVED',
            'scope':'source_preservation_replay_and_actual_pptx_correspondence',
            'semantic_recognition':'NOT_PROVIDED','visual_acceptance':'NOT_EVALUATED',
            'user_acceptance':'pending','failures':[],'unresolved':[],
            'layers':{},'policy':asdict(policy),'limits':asdict(limits),
            'limitations':['No formula/operator recognition, source text transcription or directed-relation interpretation.',
                'Native PDF paints and expanded SVG glyph occurrences have separate identities; no glyph bijection is claimed.',
                'Derived native-image PNGs are explicit raster samples, not arbitrary-scale vector recovery.',
                'PPT coordinates are quantized; this API does not render or visually inspect the final slide.',
                'Only the explicitly supported context/OOXML profile is checked; unknown transformations stay unresolved.']}
    if source is None:
        _save(out/'source-fidelity.json',report)
        return report
    started=time.monotonic()
    try:
        inputs={}
        scenes={}
        for role,path in (('manifest',manifest_path),('resolved_scene',resolved_scene_path)):
            raw=_read(path,limits.max_scene_bytes)
            inputs[role]={'path':str(Path(path).resolve()),'sha256':_sha(raw)}
            scenes[role]=json.loads(raw)
            _save(out/(role+'-snapshot.json'),scenes[role])
        report['inputs']=inputs
        replay=replay_pdf_source(source,evidence_dir=out/'source-replay',policy=policy,limits=limits)
        report['layers']['source_replay']=replay
        if replay['status']!='VERIFIED_IN_DECLARED_SCOPE':
            report['status']=replay['status']
            report['unresolved'].extend(replay.get('unresolved',[]))
            report['failures'].extend(replay.get('failures',[]))
        else:
            expected_raw=_read(replay['expected_scene_path'],limits.max_scene_bytes)
            expected=json.loads(expected_raw)
            if _sha(expected_raw)!=replay['expected_scene_sha256'] or _sha(_canonical(expected))!=replay['expected_scene_canonical_sha256']:
                raise SourceReplayError('Fresh expected scene changed after replay')
            report['bindings']={**inputs,
                'source_pdf':{'path':str(out/'source-replay/source.pdf'),'sha256':source.pdf_sha256},
                'source_svg':{'path':str(out/'source-replay/source.svg'),'sha256':replay['source_svg_sha256']},
                'expected_fresh_scene':{'path':replay['expected_scene_path'],'sha256':replay['expected_scene_sha256']},
                'fresh_reference':{'path':str(out/'source-replay/fresh-source.png'),'sha256':replay['source_reference_sha256']}}
            for role,scene in scenes.items():
                report['layers'][role]=compare_replayed_scene(expected,scene,asset_root=asset_root,limits=limits)
            # Actual PPT must match fresh source replay, not just the submitted scene.
            elapsed=time.monotonic()-started
            remaining=limits.timeout_seconds-elapsed
            if remaining<.05:
                report['layers']['actual_pptx']={'status':'UNRESOLVED','unresolved':['overall audit time budget exceeded'],'failures':[]}
            else:
                ppt_limits={**asdict(limits),'timeout_seconds':remaining}
                report['layers']['actual_pptx']=audit_pptx_fidelity(pptx_path,expected,asset_root=asset_root,limits=ppt_limits)
            ppt_binding=report['layers']['actual_pptx'].get('inputs',{})
            if ppt_binding.get('pptx_sha256'):
                report['bindings']['actual_pptx']={'path':str(Path(pptx_path).resolve()),'sha256':ppt_binding['pptx_sha256']}
            for role,layer in report['layers'].items():
                report['failures'].extend({'layer':role,'detail':item} for item in layer.get('failures',[]))
                report['unresolved'].extend({'layer':role,'detail':item} for item in layer.get('unresolved',[]))
            states=[r['status'] for r in report['layers'].values()]
            report['status']='FAIL' if 'FAIL' in states else 'UNRESOLVED' if any(s!='VERIFIED_IN_DECLARED_SCOPE' for s in states) else 'VERIFIED_IN_DECLARED_SCOPE'
    except (OSError,ValueError,KeyError,TypeError,RecursionError) as error:
        report['unresolved'].append({'code':'fidelity_input_or_evidence_error','detail':str(error)})
        report['status']='FAIL' if report['failures'] else 'UNRESOLVED'
    report['elapsed_seconds']=time.monotonic()-started
    _save(out/'source-fidelity.json',report)
    return report
