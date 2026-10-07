# Selected native glyph context facts

`figure_rebuild.pdf_selected_glyph_context` provides an explicit source-bound context-facts API. It independently replays a PDF page and binds every visible SVG glyph in full paint order before returning facts for the requested occurrences. It does not change the existing whole-ROI source-canvas policy or authorize any reconstruction or edit.

```python
from figure_rebuild.pdf_selected_glyph_context import (
    SelectedGlyphContextError,
    SelectedGlyphContextLimits,
    prove_selected_glyph_context,
)

facts = prove_selected_glyph_context(
    source_pdf_bytes,
    source_svg_bytes,
    ["svg-paint-0-1"],
    page=1,
    limits=SelectedGlyphContextLimits(),
)
```

Sources must be exact `bytes`. Page is a positive one-based builtin integer. Selection is an exact list/tuple of unique canonical SVG occurrence positions, ordered as in the complete source paint stream. The identifiers describe SVG tree positions; an SVG `id` or bounding-box collision alone does not bind a native glyph. The SVG must exactly match a fresh `text_as_path=True` export of that page under the installed PDF interpreter. Different interpreter exports may differ, so retain the PDF/SVG pair and the receipt's `pymupdf` version together.

The module imports without the optional PDF dependency. Actual work requires the `source` extra (PyMuPDF with the native `FzDevice2` APIs) and POSIX process resource limits. The implementation and fixed examples are tested with PyMuPDF 1.28.2. Missing bindings, an unavailable worker, timeout, malformed output or an unsupported source cannot return a proof.

## Receipt and supported context

The closed receipt schema is `selected-native-glyph-context-v1`. It includes PDF/SVG SHA-256, page, exact requested selection, complete paint/glyph/font/outline counts, source-order digest, selected native paint/span/item/gid/Unicode identities, binary32 matrices, outline hashes, source font embedding chains, bounded context events and groups, resource spending, and actual native replay status. `proof_sha256` is canonical JSON self-consistency over the remaining fields.

All six broader authorization fields are always `False`:

- `whole_roi_scope_proved`
- `image_reconstruction_proved`
- `geometry_or_style_proved`
- `source_canvas_clip_authorized`
- `rgb_pixel_equivalence_proved`
- `existing_v1_whole_roi_default_replaced`

This initial context profile admits opaque black horizontal text with exact native glyph/program/outline and full SVG-order binding. Selected glyphs have no active clip, mask or tile. A single full-page isolated DeviceRGB, Normal blend, unit-alpha, non-knockout group may be recorded only when both its source declaration and native callbacks agree. Its presence does not authorize removing that group. Unknown drawing/context callbacks, child groups or effects still reject across the replayed page; selecting fewer glyphs does not bypass those guards.

Original image paints may appear in the order accounting with `image_paints_with_support_unproved`. A native attached mask must immediately belong to its image and close before subsequent text. These facts are not an image-extraction, image-rendering, whole-source coverage, or editable-image proof. An image occurrence cannot be selected as a glyph.

## Embedded font provenance

For every enumerated page font resource the worker follows the source font xref, a single Type0 descendant where applicable, a FontDescriptor, and exactly one FontFile/FontFile2/FontFile3 stream. It loads the original indirect font object, retains the native font descriptor through replay, and requires exact source stream bytes to match that object's native font buffer. Actual `fill_text` font handles must match those retained native pointers and program bytes. Merely finding the same digest in some unrelated embedded stream is insufficient.

Each selected font records a `source_embedding` list containing candidate `font_xref`, `descendant_xref` (zero for non-Type0), `descriptor_xref`, `stream_xref` and `stream_key`. The list preserves resources sharing one native font handle; it does not pretend to identify a unique resource-name alias for each occurrence. Native handles are not merged by program digest.

Admission is conservative: unsupported subtypes/direct descriptor forms, multiple descendants, ambiguous program keys, missing embedding, and mismatched buffers reject. A listed but unused unembedded resource also rejects. This sufficient condition does not imply that every unused resource affects the selected glyph. Source enumeration and decoded font processing run inside the bounded worker.

## Replay and trust limits

`native_replay` records zero native cookie errors/incomplete and an unset abort flag after actual replay. Repaired documents and reported replay errors reject. It is not a claim that the interpreter emitted no warning or that every original PDF semantic structure is valid. In particular, malformed ToUnicode/CID metadata can warn without changing the cookie status. The receipt's Unicode is the native interpreter/SVG identity fact, potentially U+FFFD; it does not validate the author's intended Unicode mapping.

The parent checks closed schemas, exact types, request source/page/selection, all-false authorization flags, bounds and accounting, known contexts, and canonical digest. Duplicate JSON keys, nonfinite JSON constants, unexpected fields and malformed success/error messages reject with `SelectedGlyphContextError`. Tests recompute digests on invalid facts to ensure digest agreement alone does not admit them.

The trust boundary is the installed local API/worker pair. The digest is not cryptographic authentication of a malicious worker able to fabricate a fully self-consistent response, and process resource limits are not a sandbox for hostile installed code. The worker is a file from the same installed package, invoked with Python `-I`; its package import location does not come from the caller's working directory or `PYTHONPATH`.

Geometry/style equivalence, viewport/source-canvas clipping, source coverage, native integer encoding, image support, and destination rendering remain separate proofs. An earlier source-specific ALEX diagnostic observed selected white-background RGB changes up to 1/255 when bypassing a group. That is an observation for that experiment, not a general error bound or an API pixel-equivalence claim.

## Bounds and failure contract

`SelectedGlyphContextLimits` controls source/output bytes, selected count, PDF objects, font resource entries and bytes, native paints/glyphs/outlines/control points, SVG nodes, context events/depth, operations, numeric magnitude/lexeme length, and isolated worker CPU/memory. Defaults include 8 MiB PDF, 2 MiB SVG, 16,384 PDF objects, 64 font resources, 8,192 glyphs, 256 outlines, 256 KiB output, 12 seconds CPU and 512 MiB address space. Hard ceilings also bound input/output sizes, worker resources and integer width.

Budgets require positive builtin integers; booleans and integer subclasses reject. Source enumeration consumes the shared operation counter. The parent imposes a wall timeout and bounded result read; the child applies address-space, CPU, file-size and open-file limits. Source/worker failures raise `SelectedGlyphContextError` with a bounded string `code`; no partial proof is returned, and caller bytes and source files are not written. Temporary-directory, request-file and log entry/exit failures use `worker_storage_failed`; unavailable worker metadata uses `worker_unavailable`. Cleanup must finish before a proof can return. If a specific worker error or process interrupt has already occurred, a later cleanup failure preserves it. Some bounded numeric-parser failures can share a diagnostic code with an unsupported numeric lexeme; rejection does not imply partial success.

The fixed test corpus contains the original real missing-XObject and unterminated-text recovery cases, a valid embedded DejaVu control, unembedded Base14, and two actually painted aliases of a source-embedded font. PDF/SVG bytes are compressed into JSON solely so the existing source-distribution fixture rules include them. Each decoded blob has an exact size and SHA-256 check; the embedded DejaVu license accompanies it. Tests also cover actual source mutations, callback/cookie failures, resource boundaries, and parent protocol/transport faults.
