# Source glyphs clipped by a standalone slide canvas

`source_canvas_clip` v1 is an explicit source-replay contract for a narrow case:
filled glyph outlines that cross the original PDF figure ROI. Full M/L/C/close
commands remain native and editable; the standalone slide boundary supplies the
same viewport. This is not a general permission to draw outside the canvas and
does not claim recovered text or cross-renderer pixel equivalence.

## Declaration

```json
{
  "source_canvas_clip": {
    "version": 1,
    "mode": "standalone_slide_canvas",
    "source_pdf": {"path": "evidence/original.pdf", "sha256": "<64 lowercase hex>", "page": 1},
    "source_svg": {"path": "evidence/page-outlined.svg", "sha256": "<64 lowercase hex>"},
    "raster": {
      "pymupdf_version": "<exact installed/extraction version>",
      "scale": 2,
      "roi_pdf_points": [15, 20, 85, 42],
      "irect": [30, 40, 170, 84],
      "alpha": false,
      "colorspace": "DeviceRGB"
    },
    "objects": [{"object_id": "glyph-native-1", "source_paint_id": "svg-paint-0-1"}]
  }
}
```

Paths are confined relative to the manifest asset root. The original raster is
the manifest's existing `source` PNG with its hash, width and height. PDF pages
are one-based. ROI coordinates use the unrotated PyMuPDF page's top-left PDF
point coordinate system. The page must contain the complete ROI.

V1 requires exact pixel alignment: each binary source ROI operand multiplied
by the binary scale must be an integer under exact rational arithmetic; native
float32 operands must also be exact. The observed pristine pixmap `irect` must
match the declaration. Target commands use
`[scale,0,0,scale,-irect.x0,-irect.y0]`; canvas dimensions are the exact `irect`
width/height. Fractional caller crops that round outward are rejected rather
than silently enlarging or centering the viewport. Scale is at most 64; canvas
is at most 20000 pixels per axis and 16 million pixels total; the source PNG is
capped at 64 MiB before hashing. Replay allocation is refused before MuPDF
rasterization when the pixel budget is exceeded. Existing command limits remain
unchanged. Commands/styles compare canonical JSON values, including boolean
versus numeric types, and canvas/source pixel dimensions must be integers.
This v1 replay profile also retains the lowerer's exact numeric JSON
representation (`1` and `1.0` are distinct for commands/style comparison).
Do not reserialize or normalize source geometry to bypass a failed comparison.

## Replay and source restrictions

`verify_source_canvas_clip(manifest, root)` returns `None` without the flag, or
replays the declaration. It reopens the original PDF bytes, verifies page SVG
bytes from a fresh `get_svg_image(text_as_path=True)`, and compares original PNG
RGB samples with a pristine-document native PDF raster. SVG extraction and
raster sampling use separate PDF documents. Source receipt fields are
deterministic and contain no transient paths or timestamps.

The complete native paint-context identity is replayed. Masks and pattern
contexts potentially intersecting the ROI remain unsupported. Infinite/invalid
bbox values conservatively intersect rather than relying on MuPDF's rectangle
intersection behavior. Mask-definition paints are separately accounted as
non-page paints. V1 rejects inner clip contexts and nontrivial or unknown groups
within the ROI; this conservative guard can restrict otherwise convertible
unrelated content. The receipt states that it is a whole-ROI guard, not a
per-glyph native sequence attribution.

Selected source instances must be supported glyphs with nonzero fill, no stroke,
no dash and no source clipping. Existing strict lowerer checks still enforce
color, opacity and group/filter/mask support. Complete commands and styles are
reconstructed and compared, and source painter order among selected objects is
retained. Only the verified stable object IDs receive a manifest containment
exception. Other paths, live text and images keep their existing containment
rules. Original source evidence or commands may not be trimmed, retouched or
replaced to make the declaration pass.

## Build and native boundary

Building the clipping-dependent source as an overlay for a base deck,
nonidentity placement, inserting/merging that marked overlay into another deck,
and multislide output are forbidden. A containing group does not clip its
children. A marked standalone deck may itself remain the base for an ordinary
overlay when its existing canvas and glyph geometry remain unchanged; this does
not relocate the clipping-dependent objects or change their slide viewport.
The CLI and low-level build must reject these modes independently; manifest
metadata alone cannot enforce CLI placement arguments. Source PDF and SVG must
be included in frozen build assets. Reverify the declaration after scene
materialization, using the exact manifest bound into the receipt.

For every selected glyph, postprocess must preserve full original commands in
source-pixel EMU path coordinates, including pure M/L glyphs. It must not apply
an alternate winding representation to these objects. The native `cNvPr.descr`
has a semicolon-separated `source_canvas_clip_required=true` marker. Package
merge/insert must reject any source overlay bearing that intrinsic marker.

`verify_native_canvas_clip(pptx, manifest, fresh_source_receipt)` checks one slide
with exact `9525 * canvas` EMUs, selected top-level shape identities and painter
order, marker, complete M/L/C/close commands and all integer controls, source
control-hull frames, native solid fill/opacity and absence of stroke/effects.
Frame serialization tolerance is at most 2 EMU; path coordinates use the declared
nearest-integer source-pixel EMU quantization. This does not promise infinite
precision. The function expects a trusted fresh replay receipt; the module CLI
also recomputes it and rejects any changed saved receipt.

```
python -m figure_rebuild.source_canvas_clip --manifest manifest.json --root job \
  --output source-canvas-clip.json
python -m figure_rebuild.source_canvas_clip --manifest manifest.json --root job \
  --pptx final.pptx --source-receipt source-canvas-clip.json \
  --output native-canvas-clip.json
```

Output review must bind/recompute both receipts and retain distinct actual
Artifact and direct native previews. Raw source/final 1×, 2× and 4× visual review
remains required. Source glyph outlines are not live text; semantic fidelity
remains `NOT_PROVIDED`. Clipping permission never turns warning-only layout
checks or absent semantic evidence into fidelity approval.
