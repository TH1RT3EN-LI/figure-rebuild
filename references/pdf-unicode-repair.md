# Repair replacement characters in an exported PDF

An office application can export the correct glyph outlines but write `U+FFFD`
in the PDF's `ToUnicode` table. Copying text or searching that PDF then loses
the character even though its visible shape is intact. `repair-pdf-unicode`
restores these mappings from an explicitly supplied, hash-bound candidate font.
It requires the optional source dependencies:

```sh
python -m pip install 'figure-rebuild[source]'
python -m figure_rebuild repair-pdf-unicode \
  --manifest /caller/export/unicode-repair.json \
  --output /caller/export/repaired.pdf \
  --receipt /caller/export/repaired.unicode.json
```

The output and receipt must be new files. The original PDF is preserved. The
manifest has exactly these top-level fields; replace the example hashes with
the SHA256 of the actual files and embedded font stream:

```json
{
  "schema_version": 1,
  "source": {
    "path": "exported.pdf",
    "sha256": "<PDF SHA256>"
  },
  "fonts": [{
    "font_xref": 14,
    "embedded_program_sha256": "<decoded FontFile2 stream SHA256>",
    "candidate": {
      "path": "fonts/candidate.ttc",
      "sha256": "<candidate file SHA256>",
      "face_index": 1
    }
  }]
}
```

Input paths resolve relative to the manifest directory. Each `font_xref` is the
actual PDF Type0 font object with damaged mappings, rather than a font name or
a page resource label. TTC candidates require the exact face index. The caller
supplies fonts; font binaries are not distributed with this project.

The command supports embedded static TrueType Type0/Identity-H fonts with
CIDFontType2 and identity CID-to-GID mapping, and one two-byte Identity-UCS
`bfchar` block. It repairs only existing `U+FFFD` destinations. Missing or NULL
mappings, arbitrary CMaps, string destinations, vertical text, CFF fonts,
variable fonts, shared `ToUnicode`, marked content, `ActualText`, Form XObjects,
synthetic fonts and ambiguous matches are refused. All painted font resources
must fit the same closed native-resource contract. A newer supported PyMuPDF
runtime may be needed when its native verification APIs are unavailable.

For each repaired CID, exact normalized outlines and horizontal metrics must
uniquely match a candidate Unicode scalar. The existing
[glyph recovery policy](font-unicode-recovery.md) also bounds supported MATH
script alternates. Font names, expected label strings and nearby characters do
not establish a match. The supplied candidate remains conditional evidence;
matching glyphs does not prove the original family or redistribution rights.

The verifier binds each actual MuPDF font handle to the original embedded
`FontFile2` bytes and checks painted CID/GID identity. Non-BMP characters are
written as real UTF-16BE surrogate pairs. Every repaired mapping must have a
visible native paint observation. All other mappings, font programs, rights,
painted GIDs, positions, transforms and other PDF objects/streams are preserved.
The new file retains the entire original PDF as its byte prefix and adds an
incremental update for the selected `ToUnicode` streams and necessary PDF
cross-reference/trailer records. No invisible text overlay is added.

Before publishing the output, the command verifies the unchanged native paint
records and every page's identical 1× RGBA pixels, within explicit byte, page,
font, mapping, glyph and preview limits. Limits bound accepted data and work;
they do not bound every native parser allocation. The JSON receipt records
hashes, recovered mappings, actual painted occurrences and each protection
check. Unsupported or failed validation publishes no repaired PDF.

This is a separate export repair step. It does not change WPS's exporter or the
editable PPTX. It does not establish arbitrary editing, complete source-font
repertoire, cross-platform fidelity or a presentation that needs no fonts
installed. Preserve the original export and both receipts when reviewing a
successor. Actual PDF text extraction and multiple-scale visual checks remain
useful independent checks.

Implementation: `src/figure_rebuild/pdf_unicode.py`; controls:
`tests/test_pdf_unicode.py` and `tests/test_pdf_unicode_cli.py`.
