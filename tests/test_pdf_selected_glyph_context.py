"""Portable fixed-PDF and bounded protocol tests for selected context facts."""
import base64
import copy
import errno
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from figure_rebuild import pdf_selected_glyph_context as api
try:
    import pymupdf as fitz
except ImportError:
    fitz = None

FIXTURE_PATH = Path(__file__).with_name('fixtures') / 'pdf-selected-glyph-context.json'
FIXTURES = json.loads(FIXTURE_PATH.read_text())
Limits = api.SelectedGlyphContextLimits
Error = api.SelectedGlyphContextError
sha = lambda data: hashlib.sha256(data).hexdigest()


def fixture(name):
    record = FIXTURES['cases'][name]
    decoded = []
    for kind in ('pdf', 'svg'):
        item = record[kind]
        data = zlib.decompress(base64.b64decode(item['zlib_base64'], validate=True))
        assert len(data) == item['bytes'] and sha(data) == item['sha256']
        decoded.append(data)
    return *decoded, record['selected']


def public(name='valid-embedded-control', **kwargs):
    pdf, svg, selected = fixture(name)
    return api.prove_selected_glyph_context(pdf, svg, selected, page=1, **kwargs)


def digest(proof):
    if 'proof_sha256' not in proof:
        return proof
    proof['proof_sha256'] = sha(json.dumps({k: v for k, v in proof.items() if k != 'proof_sha256'},
                                          sort_keys=True, separators=(',', ':'), allow_nan=True).encode())
    return proof


def mutated(fn):
    value = copy.deepcopy(FIXTURES['control_proof'])
    fn(value)
    return digest(value)


def injected(payload, *, page=1, selection=None, returncode=0, limits=Limits()):
    pdf, svg, selected = fixture('valid-embedded-control')
    def run(argv, **kwargs):
        assert argv[1] == '-I'
        assert Path(argv[2]).name == '_pdf_selected_glyph_context_worker.py'
        Path(argv[-1]).write_bytes(payload if type(payload) is bytes else json.dumps(payload, allow_nan=True).encode())
        return subprocess.CompletedProcess(argv, returncode)
    with mock.patch.object(api.subprocess, 'run', side_effect=run):
        return api.prove_selected_glyph_context(pdf, svg, selected if selection is None else selection, page=page, limits=limits)


class SelectedContextProtocolTests(unittest.TestCase):
    def test_api_import_does_not_load_optional_pdf_library(self):
        root = str(Path(api.__file__).resolve().parent.parent)
        code = 'import sys;sys.path.insert(0,' + repr(root) + ');import figure_rebuild.pdf_selected_glyph_context;assert "pymupdf" not in sys.modules;assert "fitz" not in sys.modules'
        completed = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_fixed_input_integrity(self):
        for name in FIXTURES['cases']:
            with self.subTest(name=name):
                pdf, svg, selected = fixture(name)
                self.assertTrue(pdf.startswith(b'%PDF-'))
                self.assertTrue(svg.startswith(b'<svg'))
                self.assertTrue(selected)

    def test_input_types_and_strict_budgets(self):
        pdf, svg, selected = fixture('valid-embedded-control')
        class Integer(int): pass
        class ByteString(bytes): pass
        class String(str): pass
        for value in (False, None, bytearray(pdf), ByteString(pdf)):
            with self.subTest(source=type(value).__name__), self.assertRaises(Error):
                api.prove_selected_glyph_context(value, svg, selected)
        for value in (False, 1., '1', Integer(1), -1, 1 << 100):
            with self.subTest(page=value), self.assertRaises(Error):
                api.prove_selected_glyph_context(pdf, svg, selected, page=value)
        for selection in (set(selected), None, selected + selected, [], ['svg-paint-0-01'], [String(selected[0])]):
            with self.subTest(selection=repr(selection)[:80]), self.assertRaises(Error):
                api.prove_selected_glyph_context(pdf, svg, selection)
        for field in fields(Limits()):
            for value in (True, 0, -1, 1., '1', Integer(1), 1 << 100):
                with self.subTest(field=field.name, value=value), self.assertRaises(Error):
                    public(limits=replace(Limits(), **{field.name: value}))

    def test_limit_ceilings(self):
        for kwargs in ({'max_pdf_bytes': 33_554_433}, {'max_svg_bytes': 8_388_609},
                       {'max_output_bytes': 1_048_577}, {'max_integer_bits': 65},
                       {'worker_seconds': 61}, {'worker_memory_bytes': 1_073_741_825}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Error):
                public(limits=replace(Limits(), **kwargs))

    def test_valid_closed_response(self):
        proof = injected({'status': 'ok', 'proof': FIXTURES['control_proof']})
        self.assertEqual(proof['schema'], api.SCHEMA)
        self.assertTrue(all(proof[key] is False for key in api.FALSE_FLAGS))

    def test_closed_top_level_and_error_envelopes(self):
        for key in FIXTURES['control_proof']:
            with self.subTest(missing=key), self.assertRaises(Error):
                injected({'status': 'ok', 'proof': mutated(lambda p: p.pop(key))})
        payloads = [None, [], {}, {'status': 'ok'}, {'status': 'ok', 'proof': None},
                    {'status': 'ok', 'proof': {}}, {'status': 'other'},
                    {'status': 'ok', 'proof': FIXTURES['control_proof'], 'extra': 0}]
        payloads += [{'status': 'error', 'error': value} for value in (44, None, [], '', 'X', 'x' * 129)]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:100]), self.assertRaises(Error):
                injected(payload)
        with self.assertRaises(Error) as raised:
            injected({'status': 'error', 'error': 'native_replay_not_clean'}, returncode=1)
        self.assertEqual(raised.exception.code, 'native_replay_not_clean')

    def test_stale_page_selection_source_and_bad_digest(self):
        good = {'status': 'ok', 'proof': FIXTURES['control_proof']}
        with self.assertRaises(Error): injected(good, page=2)
        with self.assertRaises(Error): injected(good, selection=['svg-paint-0-999'])
        with self.assertRaises(Error): injected(good, returncode=1)
        for key in ('source_pdf_sha256', 'source_svg_sha256'):
            with self.subTest(key=key), self.assertRaises(Error):
                injected({'status': 'ok', 'proof': mutated(lambda p: p.update({key: '0' * 64}))})
        with self.assertRaises(Error):
            injected({'status': 'ok', 'proof': {**FIXTURES['control_proof'], 'proof_sha256': '0' * 64}})

    def test_permissions_and_scope_reject_even_with_new_digest(self):
        for key in api.FALSE_FLAGS:
            for value in (True, 0, None, 'false'):
                with self.subTest(key=key, value=value), self.assertRaises(Error):
                    injected({'status': 'ok', 'proof': mutated(lambda p: p.update({key: value}))})
        for key, value in (('schema', 'unknown'), ('proof_scope', 'whole_roi'), ('group_claim', 'equivalent')):
            with self.subTest(key=key), self.assertRaises(Error):
                injected({'status': 'ok', 'proof': mutated(lambda p: p.update({key: value}))})

    def test_nested_counts_context_font_types(self):
        mutations = [lambda p: p.update(page=True), lambda p: p.update(complete_native_glyphs=0),
                     lambda p: p.update(complete_native_glyphs=1 << 100), lambda p: p.update(font_count=1.),
                     lambda p: p['used'].update(operations=1), lambda p: p['used'].update(font_resources=0),
                     lambda p: p['used'].update(unknown=0), lambda p: p.update(selected=[]),
                     lambda p: p['selected'].append(copy.deepcopy(p['selected'][0])),
                     lambda p: p['native_replay'].update(errors=1), lambda p: p['native_replay'].update(incomplete=1),
                     lambda p: p['native_replay'].update(aborted=True), lambda p: p['native_replay'].update(errors=False),
                     lambda p: p['native_events'][0].update(kind='begin_mask'),
                     lambda p: p.update(image_paints_with_support_unproved=[0])]
        mutations += [lambda p, key=key: p['selected'][0].pop(key) for key in FIXTURES['control_proof']['selected'][0]]
        row_changes = [('clip_ids', [1]), ('mask_depth', 1), ('tile_depth', 1), ('group_ids', [True]),
                       ('matrix', [float('nan'), 0, 0, 1, 0, 0]), ('matrix', [1 << 64, 0, 0, 1, 0, 0]),
                       ('matrix', [True, 0, 0, 1, 0, 0]), ('ucs', 0xd800), ('gid', 1 << 30)]
        mutations += [lambda p, key=key, value=value: p['selected'][0].update({key: value}) for key, value in row_changes]
        mutations += [lambda p: p['selected'][0]['font'].update(source_embedding=[]),
                      lambda p: p['selected'][0]['font'].update(program_bytes=1 << 30),
                      lambda p: p['selected'][0]['font']['source_embedding'][0].update(stream_key='FontBuffer'),
                      lambda p: p['selected'][0]['font']['source_embedding'][0].update(stream_xref=1 << 30),
                      lambda p: p['selected'][0]['font']['source_embedding'][0].update(descriptor_xref=True)]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(Error):
                injected({'status': 'ok', 'proof': mutated(mutation)})

    def test_digest_is_consistency_not_worker_authentication(self):
        # No cryptographic claim about a hostile worker: a different opaque hash
        # with a matching digest cannot be checked without the actual replay.
        proof = mutated(lambda p: p.update(complete_order_sha256='0' * 64))
        self.assertEqual(injected({'status': 'ok', 'proof': proof}), proof)

    def test_malformed_json_is_controlled(self):
        payloads = [b'\xff', b'{', b'{"status":"error","status":"ok"}',
                    b'{"status":"ok","proof":{"page":1,"page":2}}',
                    b'{"status":"ok","proof":NaN}', b'[' * 1500 + b'0' + b']' * 1500,
                    b'{"status":"ok","proof":' + b'1' * 5000 + b'}']
        for payload in payloads:
            with self.subTest(payload=payload[:50]), self.assertRaises(Error): injected(payload)

    def test_complete_storage_lifecycle_failures_are_controlled(self):
        original_temp = api.tempfile.TemporaryDirectory
        original_open, original_write = Path.open, Path.write_text
        def failure(): return OSError(errno.ENOSPC, 'injected storage failure')
        class EntryFailure:
            def __enter__(self): raise failure()
            def __exit__(self, *args): return False
        class ExitFailure:
            def __init__(self, *args, **kwargs): self.delegate = original_temp(*args, **kwargs)
            def __enter__(self): return self.delegate.__enter__()
            def __exit__(self, *args): self.delegate.__exit__(*args); raise failure()
        class CloseFailure:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self.stream.__enter__()
            def __exit__(self, *args): self.stream.__exit__(*args); raise failure()
        def open_case(name, close=False):
            def opened(path, *args, **kwargs):
                if path.name == name:
                    if not close: raise failure()
                    return CloseFailure(original_open(path, *args, **kwargs))
                return original_open(path, *args, **kwargs)
            return opened
        def write(path, *args, **kwargs):
            if path.name == 'request.json': raise failure()
            return original_write(path, *args, **kwargs)
        cases = [
            ('construction', lambda: mock.patch.object(api.tempfile, 'TemporaryDirectory', side_effect=failure())),
            ('enter', lambda: mock.patch.object(api.tempfile, 'TemporaryDirectory', return_value=EntryFailure())),
            ('write', lambda: mock.patch.object(Path, 'write_text', new=write)),
            ('stdout open', lambda: mock.patch.object(Path, 'open', new=open_case('stdout'))),
            ('stderr open', lambda: mock.patch.object(Path, 'open', new=open_case('stderr'))),
            ('cleanup', lambda: mock.patch.object(api.tempfile, 'TemporaryDirectory', new=ExitFailure)),
            ('stdout close', lambda: mock.patch.object(Path, 'open', new=open_case('stdout', True))),
            ('stderr close', lambda: mock.patch.object(Path, 'open', new=open_case('stderr', True))),
        ]
        for name, patch in cases:
            with self.subTest(phase=name), patch(), self.assertRaises(Error) as raised:
                injected({'status': 'ok', 'proof': FIXTURES['control_proof']})
            self.assertEqual(raised.exception.code, 'worker_storage_failed')
        with mock.patch.object(api.Path, 'is_file', side_effect=PermissionError('worker stat')), self.assertRaises(Error) as raised:
            public()
        self.assertEqual(raised.exception.code, 'worker_unavailable')

        # Failure while cleaning up cannot turn a worker failure/signal into a
        # proof or obscure an earlier specific refusal/interrupt.
        for name, patch in cases[-3:]:
            with self.subTest(phase=name, prior='worker error'), patch(), self.assertRaises(Error) as raised:
                injected({'status': 'error', 'error': 'native_replay_not_clean'}, returncode=1)
            self.assertIn(raised.exception.code, ('native_replay_not_clean', 'worker_storage_failed'))
            with self.subTest(phase=name, prior='worker signal'), patch(), self.assertRaises(Error) as raised:
                injected({'status': 'ok', 'proof': FIXTURES['control_proof']}, returncode=-9)
            # Log close happens before response validation, so storage can fail
            # first; either outcome is controlled and no result is returned.
            self.assertIn(raised.exception.code, ('worker_failed', 'worker_storage_failed'))
            with self.subTest(phase=name, prior='timeout'), patch(), mock.patch.object(api.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 1)), self.assertRaises(Error) as raised:
                public()
            self.assertEqual(raised.exception.code, 'worker_timeout')
            with self.subTest(phase=name, prior='interrupt'), patch(), mock.patch.object(api.subprocess, 'run', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                public()

    def test_transport_failures_and_output_budget(self):
        for fault in (OSError('launch'), subprocess.TimeoutExpired('worker', 1)):
            with mock.patch.object(api.subprocess, 'run', side_effect=fault), self.assertRaises(Error): public()
        for directory in (False, True):
            def run(argv, **kwargs):
                if directory: Path(argv[-1]).mkdir()
                return subprocess.CompletedProcess(argv, 0)
            with mock.patch.object(api.subprocess, 'run', side_effect=run), self.assertRaises(Error): public()
        with mock.patch.object(api.Path, 'is_file', return_value=False), self.assertRaises(Error) as raised: public()
        self.assertEqual(raised.exception.code, 'worker_unavailable')
        with self.assertRaises(Error): injected({'status': 'ok', 'proof': FIXTURES['control_proof']}, limits=replace(Limits(), max_output_bytes=256))


@unittest.skipIf(fitz is None or api.os.name != 'posix', 'optional PyMuPDF and POSIX worker required')
class SelectedContextActualPdfTests(unittest.TestCase):
    def test_original_embedded_control(self):
        proof = public()
        self.assertEqual(proof['complete_native_glyphs'], 1)
        self.assertEqual(proof['native_replay'], {'errors': 0, 'incomplete': 0, 'aborted': False})
        font = proof['selected'][0]['font']
        self.assertEqual(font['program_bytes'], 759720)
        self.assertEqual(font['source_embedding'][0]['stream_key'], 'FontFile2')
        self.assertGreater(font['source_embedding'][0]['descendant_xref'], 0)
        self.assertTrue(all(proof[key] is False for key in api.FALSE_FLAGS))

    def test_fixed_unembedded_base14_rejected(self):
        with self.assertRaises(Error) as raised: public('base14')
        self.assertEqual(raised.exception.code, 'font_not_source_embedded')

    def test_fixed_missing_xobject_recovery_rejected(self):
        with self.assertRaises(Error) as raised: public('missing-xobject')
        self.assertEqual(raised.exception.code, 'native_replay_not_clean')

    def test_fixed_unterminated_text_recovery_rejected(self):
        with self.assertRaises(Error) as raised: public('unterminated-literal')
        self.assertEqual(raised.exception.code, 'native_replay_not_clean')

    def test_cleanup_failure_after_actual_success_returns_no_proof(self):
        original = api.tempfile.TemporaryDirectory
        class ExitFailure:
            def __init__(self, *args, **kwargs): self.delegate = original(*args, **kwargs)
            def __enter__(self): return self.delegate.__enter__()
            def __exit__(self, *args):
                self.delegate.__exit__(*args)
                raise OSError(errno.ENOSPC, 'cleanup after actual replay')
        with mock.patch.object(api.tempfile, 'TemporaryDirectory', new=ExitFailure), self.assertRaises(Error) as raised:
            public()
        self.assertEqual(raised.exception.code, 'worker_storage_failed')

    def test_actual_resource_aliases_preserve_candidate_provenance(self):
        proof = public('embedded-two-aliases')
        self.assertEqual(proof['complete_native_glyphs'], 2)
        self.assertEqual(proof['used']['font_resources'], 2)
        self.assertEqual(proof['font_count'], 1)
        self.assertEqual(proof['selected'][0]['font'], proof['selected'][1]['font'])
        self.assertEqual(len(proof['selected'][0]['font']['source_embedding']), 1)

    def test_actual_selection_order_and_fresh_svg_binding(self):
        pdf, svg, selected = fixture('embedded-two-aliases')
        for selection in (selected[::-1], selected + selected[:1], ['svg-paint-0-999']):
            with self.subTest(selection=selection), self.assertRaises(Error):
                api.prove_selected_glyph_context(pdf, svg, selection, page=1)
        with self.assertRaises(Error) as raised:
            api.prove_selected_glyph_context(pdf, svg + b' ', selected, page=1)
        self.assertEqual(raised.exception.code, 'source_svg_bytes_mismatch')

    def test_actual_exact_and_below_resource_budgets(self):
        proof = public('embedded-two-aliases')
        for key in ('pdf_objects', 'font_resources', 'operations', 'glyphs', 'resources', 'controls', 'paints'):
            value = proof['used'][key]
            with self.subTest(key=key, boundary='exact'):
                public('embedded-two-aliases', limits=replace(Limits(), **{'max_' + key: value}))
            with self.subTest(key=key, boundary='below'), self.assertRaises(Error):
                public('embedded-two-aliases', limits=replace(Limits(), **{'max_' + key: value - 1}))
        for limit in (replace(Limits(), max_font_bytes=1), replace(Limits(), worker_memory_bytes=1), replace(Limits(), max_output_bytes=256)):
            with self.subTest(limit=limit), self.assertRaises(Error): public(limits=limit)

    @staticmethod
    def changed_pdf(change):
        pdf, _, selected = fixture('valid-embedded-control')
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            change(doc, doc[0])
            pdf = doc.tobytes(garbage=0, deflate=False, no_new_id=True)
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            svg = doc[0].get_svg_image(text_as_path=True).encode()
        from xml.etree import ElementTree as ET
        selected = []
        def walk(node, path):
            if node.tag.endswith('}use'): selected.append('svg-paint-' + '-'.join(map(str, path)))
            for index, child in enumerate(node): walk(child, path + [index])
        walk(ET.fromstring(svg), [0])
        return pdf, svg, selected

    def test_actual_ambiguous_font_descriptors_reject(self):
        cases = [(5, 'DescendantFonts', '[10 0 R 10 0 R]'), (5, 'DescendantFonts', 'null'),
                 (8, 'FontFile3', '7 0 R'), (10, 'FontDescriptor', 'null')]
        for owner, key, value in cases:
            with self.subTest(key=key, value=value):
                pdf, svg, selected = self.changed_pdf(lambda doc, page: doc.xref_set_key(owner, key, value))
                with self.assertRaises(Error): api.prove_selected_glyph_context(pdf, svg, selected, page=1)

    def test_actual_neutral_group_and_selected_clip(self):
        def group(doc, page):
            xref = doc.get_new_xref()
            doc.update_object(xref, '<< /Type /Group /S /Transparency /CS /DeviceRGB /I true /K false >>')
            doc.xref_set_key(page.xref, 'Group', f'{xref} 0 R')
        pdf, svg, selected = self.changed_pdf(group)
        proof = api.prove_selected_glyph_context(pdf, svg, selected)
        self.assertEqual(proof['selected'][0]['group_ids'], [1])
        self.assertFalse(proof['source_canvas_clip_authorized'])
        def clipped(doc, page):
            xref = page.get_contents()[0]
            doc.update_stream(xref, b'q 0 0 100 70 re W n\n' + doc.xref_stream(xref) + b'\nQ')
        pdf, svg, selected = self.changed_pdf(clipped)
        with self.assertRaises(Error) as raised: api.prove_selected_glyph_context(pdf, svg, selected)
        self.assertEqual(raised.exception.code, 'unknown_native_callback_clip_path')

    def test_native_cookie_fault_channels_and_callback_exception(self):
        from figure_rebuild import _pdf_selected_glyph_context_worker as worker
        pdf, _, _ = fixture('valid-embedded-control')
        real_run = fitz.mupdf.fz_run_page
        for field in ('errors', 'incomplete', 'abort'):
            def run(*args, field=field, **kwargs):
                result = real_run(*args, **kwargs)
                setattr(args[-1].m_internal, field, 1)
                return result
            with mock.patch.object(fitz.mupdf, 'fz_run_page', side_effect=run), self.assertRaises(Error) as raised:
                worker.capture_native(pdf, 1, worker.Budget(Limits()))
            self.assertEqual(raised.exception.code, 'native_replay_not_clean')
        with mock.patch.object(fitz.mupdf, 'fz_outline_glyph', side_effect=RuntimeError('callback')), self.assertRaises(Error) as raised:
            worker.capture_native(pdf, 1, worker.Budget(Limits()))
        self.assertEqual(raised.exception.code, 'native_callback_failure')


if __name__ == '__main__':
    unittest.main()
