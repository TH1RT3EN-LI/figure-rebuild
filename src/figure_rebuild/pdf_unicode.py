"""Repair replacement-character mappings in a narrowly supported PDF export.

This changes only ToUnicode metadata after exact, unique static-font matching.
It neither recognizes text nor changes a PPTX, font program or visible glyph.
PyMuPDF is an optional source dependency and is imported only when requested.
"""
from contextlib import ExitStack
import hashlib
from pathlib import Path
import re
import tempfile

from .font_unicode import recover_glyph_unicode
from .source_font import _positive_integer


POLICY = 'exact-static-truetype-PDF-FFFD-ToUnicode-only-v1'
_SHA = re.compile(r'[a-fA-F0-9]{64}')
_CMAP = re.compile(
    r'\s*/CIDInit\s+/ProcSet\s+findresource\s+begin\s+12\s+dict\s+begin\s+'
    r'begincmap\s+/CIDSystemInfo\s*<<\s*/Registry\s*\(Adobe\)\s*'
    r'/Ordering\s*\(UCS\)\s*/Supplement\s+0\s*>>\s+def\s+'
    r'/CMapName\s+/Adobe-Identity-UCS\s+def\s+/CMapType\s+2\s+def\s+'
    r'1\s+begincodespacerange\s+<0000>\s+<FFFF>\s+endcodespacerange\s+'
    r'(?P<count>[0-9]{1,5})\s+beginbfchar\s+'
    r'(?P<body>(?:<[0-9A-Fa-f]{4}>\s+<(?:[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8})>\s*)+)'
    r'endbfchar\s+endcmap\s+CMapName\s+currentdict\s+/CMap\s+'
    r'defineresource\s+pop\s+end\s+end\s*')
_PAIR = re.compile(r'<([0-9A-Fa-f]{4})>\s+<([0-9A-Fa-f]{4}|[0-9A-Fa-f]{8})>')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _xref(document, owner, key):
    kind, value = document.xref_get_key(owner, key)
    if kind != 'xref' or not re.fullmatch(r'[1-9][0-9]* 0 R', value):
        raise ValueError('Required source PDF indirect reference: ' + key)
    return int(value.split()[0])


def _parse_cmap(data, max_cmap_bytes, max_mappings):
    if len(data) > max_cmap_bytes:
        raise ValueError('ToUnicode byte budget exceeded')
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('Unsupported ToUnicode grammar') from error
    match = _CMAP.fullmatch(text)
    if match is None:
        raise ValueError('Unsupported ToUnicode grammar; one Identity-UCS bfchar block required')
    pairs = list(_PAIR.finditer(match['body']))
    if len(pairs) != int(match['count']) or len(pairs) > max_mappings:
        raise ValueError('ToUnicode mapping count or budget differs')
    rows, seen = [], set()
    for pair in pairs:
        cid = int(pair[1], 16)
        if cid in seen:
            raise ValueError('Duplicate ToUnicode CID')
        seen.add(cid)
        try:
            value = bytes.fromhex(pair[2]).decode('utf-16-be', errors='strict')
        except UnicodeDecodeError as error:
            raise ValueError('Invalid ToUnicode UTF-16BE scalar') from error
        if len(value) != 1:
            raise ValueError('ToUnicode must map to one Unicode scalar; strings are unsupported')
        if ord(value) == 0:
            raise ValueError('NULL Unicode mappings are outside repair scope')
        start, end = pair.span(2)
        rows.append(dict(cid=cid, unicode=ord(value),
                         destination_start=match.start('body') + start,
                         destination_end=match.start('body') + end))
    return text, rows


def _matrix(value):
    return [value.a, value.b, value.c, value.d, value.e, value.f]


def _require_native_api(F):
    required = ('FzDevice2', 'FzTextSpan', 'FzMatrix', 'FzCookie', 'pdf_new_indirect',
                'll_pdf_load_font', 'll_pdf_drop_font', 'll_fz_buffer_storage_memoryview',
                'll_pdf_lookup_cmap', 'fz_concat', 'fz_run_page', 'fz_close_device')
    if not hasattr(F, 'mupdf') or any(not hasattr(F.mupdf, name) for name in required):
        raise ValueError('Installed PyMuPDF native API is unsupported for Unicode repair')
    if any(not hasattr(F.mupdf.FzDevice2, 'use_virtual_' + name)
           for name in ('fill_text', 'stroke_text', 'ignore_text', 'clip_text', 'clip_stroke_text')):
        raise ValueError('Installed PyMuPDF native text callbacks are unsupported for Unicode repair')


def _native_paints(F, path, limits):
    """Bind actual native handles to original resources and every painted item."""
    M = F.mupdf
    records, errors = [], []
    with F.open(path) as document, ExitStack() as lifetime:
        native_document = document.this.pdf_specifics()
        resources, loaded_fonts = {}, {}
        for page in document:
            for entry in page.get_fonts(full=True):
                owner = entry[0]
                kind, value = document.xref_get_key(owner, 'DescendantFonts')
                match = re.fullmatch(r'\[\s*([1-9][0-9]*) 0 R\s*\]', value) if kind == 'array' else None
                if document.xref_get_key(owner, 'Subtype') != ('name', '/Type0') or match is None:
                    raise ValueError('Native resource scope requires embedded Type0 TrueType fonts')
                descendant = int(match[1])
                if document.xref_get_key(descendant, 'Subtype') != ('name', '/CIDFontType2'):
                    raise ValueError('Native resource scope requires CIDFontType2')
                descriptor = _xref(document, descendant, 'FontDescriptor')
                program_xref = _xref(document, descriptor, 'FontFile2')
                program = document.xref_stream(program_xref)
                if not program or len(program) > limits['font_bytes']:
                    raise ValueError('Native embedded font byte budget exceeded')
                obj = M.pdf_new_indirect(native_document, owner, 0)
                loaded = M.ll_pdf_load_font(native_document.m_internal, None, obj.m_internal)
                if not loaded:
                    raise ValueError('Cannot load native source font')
                lifetime.callback(M.ll_pdf_drop_font, loaded)
                if not loaded.font or not loaded.font.buffer:
                    raise ValueError('Native source font buffer missing')
                raw = loaded.font
                actual = bytes(M.ll_fz_buffer_storage_memoryview(raw.buffer, False))
                if actual != program:
                    raise ValueError('Native font program differs from original FontFile2 stream')
                pointer = int(raw.this)
                # The two identity edges prove original two-byte character
                # code == CID == GID without recognizing page text. Confirm
                # MuPDF loaded the declared identity mapping, rather than a
                # repaired substitute or a TrueType Unicode remapping.
                if (document.xref_get_key(owner, 'Encoding') != ('name', '/Identity-H') or
                        document.xref_get_key(descendant, 'CIDToGIDMap') != ('name', '/Identity') or
                        not loaded.is_embedded or loaded.wmode != 0 or
                        loaded.cid_to_gid_len != 0 or loaded.cid_to_gid or loaded.to_ttf_cmap or
                        not loaded.encoding or loaded.encoding.cmap_name != 'Identity-H' or
                        loaded.encoding.codespace_len != 1 or loaded.encoding.wmode != 0):
                    raise ValueError('Native character-code/CID/GID identity binding refused')
                binding = dict(font_xref=owner, descendant_xref=descendant,
                               descriptor_xref=descriptor, program_xref=program_xref,
                               program_sha256=_sha(program),
                               native_program_equals_embedded_stream=True)
                if pointer in resources and resources[pointer] != binding:
                    raise ValueError('Ambiguous native font resource handle')
                resources[pointer] = binding
                loaded_fonts[pointer] = loaded
                if len(resources) > limits['fonts']:
                    raise ValueError('Native font resource budget exceeded')
        page_number = [0]
        cookie = M.FzCookie()

        class Device(M.FzDevice2):
            def __init__(self):
                super().__init__()
                for name in ('fill_text', 'stroke_text', 'ignore_text', 'clip_text', 'clip_stroke_text'):
                    getattr(self, 'use_virtual_' + name)()

            def check(self, kind, text, ctm):
                if errors:
                    return
                span = text.head
                while span:
                    wrapped = M.FzTextSpan(span)
                    native = wrapped.font()
                    raw = native.m_internal
                    if raw.flags.fake_bold or raw.flags.fake_italic or raw.flags.ft_substitute or raw.t3procs:
                        raise ValueError('Synthetic, substituted or Type3 native font refused')
                    binding = resources.get(int(raw.this))
                    if binding is None or not raw.buffer:
                        raise ValueError('Painted font has no unique embedded resource binding')
                    if _sha(bytes(M.ll_fz_buffer_storage_memoryview(raw.buffer, False))) != binding['program_sha256']:
                        raise ValueError('Painted native font program differs')
                    if span.wmode != 0:
                        raise ValueError('Vertical text is outside repair scope')
                    for index in range(span.len):
                        if len(records) >= limits['glyphs']:
                            raise ValueError('Native painted glyph budget exceeded')
                        item = wrapped.items(index)
                        if not 0 <= item.gid <= 65535:
                            raise ValueError('Ligature continuation or unmapped native GID refused')
                        loaded = loaded_fonts[int(raw.this)]
                        if M.ll_pdf_lookup_cmap(loaded.encoding, item.gid) != item.gid:
                            raise ValueError('Native character-code/CID identity mapping differs')
                        trm = wrapped.trm()
                        trm.e, trm.f = item.x, item.y
                        records.append(dict(page=page_number[0], callback=kind, item=index,
                                            **binding, character_code=item.gid,
                                            character_code_hex=f'{item.gid:04X}', cid=item.gid,
                                            gid=item.gid, unicode=item.ucs,
                                            origin=[item.x, item.y], span_matrix=_matrix(wrapped.trm()),
                                            ctm=_matrix(ctm),
                                            glyph_matrix=_matrix(M.fz_concat(trm, M.FzMatrix(ctm)))))
                    span = span.next

            def guarded(self, kind, text, ctm):
                try:
                    self.check(kind, text, ctm)
                except Exception as error:
                    errors.append(str(error))
                    cookie.set_abort()

            def fill_text(self, ctx, text, ctm, *args):
                self.guarded('fill_text', text, ctm)

            def stroke_text(self, ctx, text, stroke, ctm, *args):
                self.guarded('stroke_text', text, ctm)

            def ignore_text(self, ctx, *args):
                errors.append('Invisible text is outside repair scope')
                cookie.set_abort()

            clip_text = ignore_text
            clip_stroke_text = ignore_text

        device = Device()
        for index, page in enumerate(document):
            page_number[0] = index
            M.fz_run_page(page.this, device, M.FzMatrix(), cookie)
        M.fz_close_device(device)
        if errors or cookie.errors() or cookie.incomplete() or cookie.abort():
            raise ValueError('Native source PDF paint verification failed: ' + (errors[0] if errors else 'renderer errors'))
        return dict(fonts=sorted(resources.values(), key=lambda row: row['font_xref']), glyphs=records)


def _checked_native_paints(F, path, limits):
    try:
        return _native_paints(F, path, limits)
    except AttributeError as error:
        raise ValueError('Installed PyMuPDF native API is unsupported for Unicode repair') from error


def repair_pdf_unicode(source, output_path, fonts, *, include_script_alternates=True,
                       max_pdf_bytes=32*1024*1024, max_font_bytes=32*1024*1024,
                       max_pages=16, max_xref_objects=100000, max_fonts=32,
                       max_cmap_bytes=256*1024, max_mappings=8192,
                       max_native_glyphs=100000, max_render_pixels=16000000):
    """Restore only FFFD bfchar mappings, with exact candidate and native proofs.

    ``source`` has an absolute PDF ``path`` and ``sha256``. Every ``fonts`` row
    declares ``font_xref``, ``embedded_program_sha256`` and a static Unicode
    ``candidate`` definition accepted by recover_glyph_unicode. The supported
    source encoding is Type0/Identity-H, CIDFontType2/Identity CID-to-GID, with
    one two-byte Identity-UCS bfchar block. NULL/missing mappings, strings,
    ambiguous shapes/aliases and arbitrary CMaps are refused. Supplied candidate
    identity is conditional evidence, not proof of an original font's identity.

    Validation failures publish no output. A successful NEW output retains all
    original PDF bytes as its prefix and appends only repaired ToUnicode streams.
    Font rights, outlines, glyph IDs, content streams and positions are preserved.
    Budgets limit accepted data/work, not all native/parser memory allocations.
    """
    limits = dict(pdf_bytes=max_pdf_bytes, font_bytes=max_font_bytes, pages=max_pages,
                  xref_objects=max_xref_objects, fonts=max_fonts, cmap_bytes=max_cmap_bytes,
                  mappings=max_mappings, glyphs=max_native_glyphs, render_pixels=max_render_pixels)
    for name, value in limits.items():
        _positive_integer(value, name + ' budget')
    if type(include_script_alternates) is not bool:
        raise ValueError('include_script_alternates must be boolean')
    if type(source) is not dict or set(source) != {'path', 'sha256'}:
        raise ValueError('PDF source requires absolute path and SHA256')
    if not isinstance(source['path'], str) or not Path(source['path']).is_absolute() or not isinstance(source['sha256'], str) or not _SHA.fullmatch(source['sha256']):
        raise ValueError('PDF source requires absolute path and SHA256')
    destination = Path(output_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Repair requires a new output PDF')
    with Path(source['path']).open('rb') as handle:
        data = handle.read(max_pdf_bytes + 1)
    if len(data) > max_pdf_bytes or not data.startswith(b'%PDF-'):
        raise ValueError('PDF input byte budget or signature refused')
    if _sha(data) != source['sha256'].lower():
        raise ValueError('PDF source SHA256 mismatch')
    if type(fonts) is not list or not fonts or len(fonts) > max_fonts:
        raise ValueError('Nonempty font declarations must fit the font budget')
    seen = set()
    for row in fonts:
        if type(row) is not dict or set(row) != {'font_xref', 'embedded_program_sha256', 'candidate'}:
            raise ValueError('Font declaration requires font_xref, embedded_program_sha256 and candidate')
        owner = row['font_xref']
        if type(owner) is not int or owner <= 0 or owner in seen:
            raise ValueError('Font xrefs must be unique positive integers')
        seen.add(owner)
        if not isinstance(row['embedded_program_sha256'], str) or not _SHA.fullmatch(row['embedded_program_sha256']):
            raise ValueError('Embedded font program SHA256 is required')
    try:
        import pymupdf as F
    except ImportError as error:
        raise ImportError('PDF Unicode repair requires the optional source dependencies') from error
    _require_native_api(F)
    with tempfile.TemporaryDirectory(prefix='figure-rebuild-pdf-unicode-') as directory:
        scratch = Path(directory)
        original, repaired = scratch/'original.pdf', scratch/'repaired.pdf'
        original.write_bytes(data)
        repairs, used_streams, mappings = [], set(), {}
        with F.open(original) as document:
            if document.is_encrypted or document.is_repaired or not 1 <= document.page_count <= max_pages or document.xref_length() > max_xref_objects:
                raise ValueError('Encrypted/repaired PDF or page/xref budget refused')
            # ActualText can override ToUnicode semantics. Marked content and
            # forms are deliberately outside this closed export grammar.
            for number in range(1, document.xref_length()):
                if '/ActualText' in document.xref_object(number):
                    raise ValueError('ActualText is outside repair scope')
                if document.xref_get_key(number, 'Subtype') == ('name', '/Form'):
                    raise ValueError('Form XObjects are outside repair scope')
            for page in document:
                for content in page.get_contents():
                    if re.search(rb'/ActualText|(?<![A-Za-z])B[MD]C(?![A-Za-z])', document.xref_stream(content)):
                        raise ValueError('Marked content is outside repair scope')
            for row in fonts:
                owner = row['font_xref']
                if owner >= document.xref_length() or document.xref_get_key(owner, 'Subtype') != ('name', '/Type0') or document.xref_get_key(owner, 'Encoding') != ('name', '/Identity-H'):
                    raise ValueError('Repair requires Type0 Identity-H source font')
                kind, value = document.xref_get_key(owner, 'DescendantFonts')
                match = re.fullmatch(r'\[\s*([1-9][0-9]*) 0 R\s*\]', value) if kind == 'array' else None
                if match is None:
                    raise ValueError('Exactly one indirect descendant font is required')
                descendant = int(match[1])
                if document.xref_get_key(descendant, 'Subtype') != ('name', '/CIDFontType2') or document.xref_get_key(descendant, 'CIDToGIDMap') != ('name', '/Identity'):
                    raise ValueError('Repair requires CIDFontType2 Identity CID-to-GID mapping')
                descriptor = _xref(document, descendant, 'FontDescriptor')
                program_xref = _xref(document, descriptor, 'FontFile2')
                program = document.xref_stream(program_xref)
                if not program or len(program) > max_font_bytes or _sha(program) != row['embedded_program_sha256'].lower():
                    raise ValueError('Embedded font program SHA256 or byte budget differs')
                stream_xref = _xref(document, owner, 'ToUnicode')
                if document.xref_get_key(stream_xref, 'UseCMap')[0] != 'null':
                    raise ValueError('Inherited UseCMap is outside repair scope')
                if stream_xref in used_streams:
                    raise ValueError('Shared ToUnicode stream refused')
                reference = re.compile(r'(?<![0-9])' + str(stream_xref) + r' [0-9]+ R(?![A-Za-z0-9])')
                for number in range(1, document.xref_length()):
                    references = reference.findall(document.xref_object(number))
                    if references and (number != owner or len(references) != 1):
                        raise ValueError('Shared ToUnicode stream refused')
                used_streams.add(stream_xref)
                text, cmap = _parse_cmap(document.xref_stream(stream_xref), max_cmap_bytes, max_mappings)
                bad = [entry['cid'] for entry in cmap if entry['unicode'] == 0xfffd]
                if not bad or 0 in bad:
                    raise ValueError('Positive replacement-character source CIDs are required')
                program_path = scratch/f'font-{owner}.ttf'
                program_path.write_bytes(program)
                recovery = recover_glyph_unicode(
                    {'path':str(program_path), 'sha256':_sha(program)}, row['candidate'], bad,
                    include_script_alternates=include_script_alternates, max_font_bytes=max_font_bytes,
                    max_source_gids=max_mappings)
                if recovery['status'] != 'PASS' or any(entry['unicode'] == 0xfffd for entry in recovery['glyphs']):
                    raise ValueError('Replacement glyph has no exact unique candidate Unicode')
                mapping = {entry['source_gid']:entry['unicode'] for entry in recovery['glyphs']}
                replacement = text
                for entry in reversed(cmap):
                    if entry['cid'] in mapping:
                        encoded = chr(mapping[entry['cid']]).encode('utf-16-be').hex().upper()
                        replacement = replacement[:entry['destination_start']] + encoded + replacement[entry['destination_end']:]
                mappings[owner] = mapping
                repairs.append(dict(font_xref=owner, descendant_xref=descendant,
                                    program_xref=program_xref, embedded_program_sha256=_sha(program),
                                    ToUnicode_xref=stream_xref,
                                    original_ToUnicode_sha256=_sha(document.xref_stream(stream_xref)),
                                    repaired_ToUnicode_sha256=_sha(replacement.encode('ascii')),
                                    recovered=[dict(cid=cid, gid=cid, unicode=code,
                                                    utf16be=chr(code).encode('utf-16-be').hex().upper())
                                               for cid, code in sorted(mapping.items())],
                                    candidate=recovery['candidate'],
                                    exact_recovery={key:value for key,value in recovery.items() if key != 'source'},
                                    replacement=replacement.encode('ascii')))
        before = _checked_native_paints(F, original, limits)
        observations = {owner:set() for owner in mappings}
        for item in before['glyphs']:
            owner = item['font_xref']
            if owner in mappings:
                if item['unicode'] == 0xfffd:
                    if item['gid'] not in mappings[owner]:
                        raise ValueError('Painted replacement character has no declared CID binding')
                    observations[owner].add(item['gid'])
                elif item['gid'] in mappings[owner]:
                    raise ValueError('Parsed CID mapping differs from native painted Unicode')
        if any(observations[owner] != set(mapping) for owner, mapping in mappings.items()):
            raise ValueError('Every replacement CID must have a visible native paint binding')
        repaired.write_bytes(data)
        with F.open(repaired) as document:
            if not document.can_save_incrementally():
                raise ValueError('Source PDF cannot be saved incrementally')
            for row in repairs:
                document.update_stream(row['ToUnicode_xref'], row['replacement'], compress=False)
            document.saveIncr()
        output = repaired.read_bytes()
        if not output.startswith(data):
            raise ValueError('Original PDF byte prefix was not preserved')
        after = _checked_native_paints(F, repaired, limits)
        if before['fonts'] != after['fonts'] or len(before['glyphs']) != len(after['glyphs']):
            raise ValueError('PDF native fonts or painted glyph count changed')
        changes = []
        for index, (prior, current) in enumerate(zip(before['glyphs'], after['glyphs'])):
            expected = dict(prior)
            if prior['font_xref'] in mappings and prior['unicode'] == 0xfffd:
                expected['unicode'] = mappings[prior['font_xref']][prior['gid']]
                changes.append(dict(paint_index=index, **current, prior_unicode=0xfffd,
                                    unicode_origin='exact_unique_hash_bound_candidate_cmap_or_MATH_ssty'))
            if expected != current:
                raise ValueError('Native paint changed beyond the declared Unicode mapping')
        render_checks = []
        with F.open(original) as prior, F.open(repaired) as current:
            if prior.xref_length() != current.xref_length():
                raise ValueError('PDF object set changed')
            changed = []
            for number in range(1, prior.xref_length()):
                if prior.xref_object(number) != current.xref_object(number) or prior.xref_stream(number) != current.xref_stream(number):
                    changed.append(number)
            if set(changed) != used_streams:
                raise ValueError('PDF objects changed beyond selected ToUnicode streams')
            for page_number in range(prior.page_count):
                page = prior[page_number]
                rect = page.rect
                if rect.width <= 0 or rect.height <= 0 or rect.width*rect.height > max_render_pixels:
                    raise ValueError('PDF preview pixel budget exceeded')
                left = page.get_pixmap(alpha=True)
                right = current[page_number].get_pixmap(alpha=True)
                if left.width*left.height > max_render_pixels or (left.width,left.height,left.n,left.stride,left.samples) != (right.width,right.height,right.n,right.stride,right.samples):
                    raise ValueError('PDF visible RGBA bytes changed or preview budget exceeded')
                render_checks.append(dict(page=page_number, scale=1, dimensions=[left.width,left.height],
                                          rgba_sha256=_sha(left.samples), all_RGBA_bytes_equal=True))
        for row in repairs:
            del row['replacement']
        receipt = dict(schema_version=1, policy=POLICY, status='PASS',
                       source=dict(path=source['path'],sha256=_sha(data),bytes=len(data)),
                       output=dict(path=str(destination),sha256=_sha(output),bytes=len(output)),
                       fonts=repairs, native_fonts=before['fonts'], restored_painted_items=changes,
                       total_painted_items=len(before['glyphs']), changed_xref_objects=changed,
                       entire_original_PDF_bytes_preserved_as_prefix=True,
                       all_other_xref_objects_and_streams_equal=True,
                       native_programs_gids_positions_and_paint_matrices_preserved=True,
                       render_checks=render_checks, invisible_overlay_added=False,
                       font_permissions_changed=False, original_font_identity_proved=False,
                       WPS_exporter_modified=False, PPTX_modified=False, budgets=limits,
                       limitations=['Only the declared finite PDF export and supplied hash-bound candidate faces are proved.',
                                    'No font redistribution permission, source-family identity, arbitrary shaping or cross-platform guarantee.',
                                    'Only one two-byte Identity-UCS bfchar block and replacement-character entries are supported.',
                                    'Byte/work budgets do not bound all native font/PDF parser memory allocations.'])
        # Publish only after every semantic, object, native and rendering check.
        created = False
        try:
            with destination.open('xb') as handle:
                created = True
                handle.write(output)
        except OSError:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return receipt
