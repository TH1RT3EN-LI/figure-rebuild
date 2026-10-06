# CPU preview image minification

Artifact previews use `full-source-staged-minification-v1` through Figure
Rebuild's process-local CPU Canvas adapter. The final validated PPT and its
original image bytes remain unchanged. Raw previews are still rendered at
their requested 1x, 2x and 4x dimensions; this policy filters an individual
decoded image before drawing it at that resolution.

A real Skia Canvas 3.0.8 control reduced an 80-pixel striped image to 10 pixels.
All three public `imageSmoothingQuality` settings produced the same locally
interpolated pixels and missed some thin stripes. Successive reductions of at
most two in each image dimension retained that ink. A real SGL reconstruction
likewise regained graph edges, matrix borders and loss circles at raw 1x.
This observation is version-specific and does not establish exact source or
cross-renderer pixel equivalence.

The adapter considers the full source-pixel to device-pixel linear map,
including destination dimensions, rotation and shear. A stage is selected only
when its largest singular scale is at most one half. Each intermediate CPU
canvas contains the complete decoded image, including alpha; the original
destination and active final context are retained. Intermediate stages never
inherit the final context's clip, opacity, compositing mode or transform.
There is no persistent image cache, because Canvas pixels and Image sources
can change between draws. Explicitly disabled image smoothing remains disabled.

Only complete source windows are filtered. Cropped source windows keep native
interpolation: prefiltering the full source could otherwise mix excluded ink
into a crop boundary. `render-audit.json` records such minification calls and
adds a visual-verification limitation. Negative or unsupported drawing operands
retain the native method's existing behavior.

For a source-bound cropped PDF occurrence, an explicitly selected native
occurrence render can retain the original CTM, clip and mask in a transparent
integer frame. The existing `extract_pdf_images` API supports
`native_occurrence_rendering=True, native_sampling_scale=4`; it still applies
its native-context admission rules. Such assets have a complete source window
at drawing time and can use this filter. Bind the PDF SHA, page, ROI, transform
and exact occurrence indices, retain the full extraction receipts, then check
final media bytes, frames, zero crop and whole-scene paint order. This is a new
source-derived asset selection, not permission to ignore an existing crop or
to replace a figure with a screenshot. Native clip/group support is unchanged.

Planning precedes allocation. One stage is limited to 16 million pixels and
32768 pixels per axis; one draw is limited to 24 million cumulative staging
pixels and 16 stages. A required plan exceeding these limits fails explicitly.
Stages are temporary per draw, so these limits do not claim to bound the entire
Artifact process, its decoded-image memory or every allocation in the external
renderer.

The final audit records actual filtered calls, unfiltered cropped calls,
stage counts, processed pixels, peak planned work per draw and the policy's
limits. These are sampling facts, not geometric support, RGB/alpha error bounds,
semantic recognition or user acceptance. Office application appearance remains
a separate check. The smooth 1x viewing aid still derives from the raw 4x image
and retains its distinct provenance.

Run the externally supplied native CPU control with
`FIGURE_REBUILD_TEST_ARTIFACT_ENTRY=/absolute/artifact_tool.mjs node --test tests/test_image_sampling.mjs`.
Without this optional runtime the native control skips; the core planning,
destination, crop, mutable-source and allocation regressions still run.

Complete classified-curve evidence remains in `editability.json`. It is written
as streamed compact JSON, retaining every proof field and numeric type. A real
Ansor report was 583.32 MiB with indentation and exceeded Node's single-string
limit; compact serialization removes whitespace and an extra full serialized
copy in Python. This addresses that observed report size, not arbitrary-size
JSON, total interpreter memory or every external renderer limit.

## Explicit native picture device-grid previews

`build --artifact-image-preview` opts a standalone Artifact build into
`delivered-native-picture-mupdf-device-grid-v2`. The adapter reads the actual
validated PPT's embedded PNG/JPEG bytes and integer EMU picture coordinates.
MuPDF samples each supported picture into a transparent device-aligned frame
at 1x, 2x and 4x; Artifact renders the complete mixed scene with those transient
picture assets. Each scale starts from a fresh import of the delivered PPT.
The complete native paint order is restored, including foreground shapes,
text and the existing stroke preview adapter. The delivered PPT is not
re-exported from this transient presentation. References never supply pixels.
Ordinary builds retain the existing sampling policy.

Support is restricted to plain top-level rectangular pictures with no
rotation or reflection, painted border, effects or embedded color profile.
Version 2 accepts nonnegative source-crop percentages that leave a positive
internal rectangular window. It maps the complete actual media to the native
visible frame and clips at that frame's fractional device coordinates; it
never rounds the crop to an integer media pixel box or encodes a cropped
replacement into the delivered PPT. The complete media transform is bounded
before allocation in addition to the visible render surface. Negative or
empty windows and destination insets remain unsupported. Existing immutable
version 1 builds retain their zero-crop procedure and independently replayable
definitions; the public helper defaults to version 1 unless explicitly selected.
Filtering near a crop edge can include neighboring stored samples before
clipping, as in PDF image rendering; source-window support is not a proof
of independence from excluded sample colors or of identical edge pixels.
Unsupported pictures retain ordinary Artifact sampling and an explicit audit
limitation. Grouped slides fail this opt-in request. Package, decoded-image,
device-grid and combined rendering budgets are checked before allocation.

MuPDF can filter color and mask separately, dimming both RGB and alpha. This
is a finite renderer policy, not equivalence to premultiplied Skia filtering,
an RGB/alpha error bound or independence from hidden RGB. PNG decoding can
also quantize color through premultiplication and unpremultiplication. Retain
the actual source decode receipts and inspect the delivered result at every
scale before accepting a source-derived asset selection.

The definition binds final media bytes, native picture and slide XML, actual
frames, sampled PNGs and PyMuPDF/MuPDF versions. Output review independently
recomputes it from the final PPT and checks all three applications and complete
paint order. This verifies the declared preview procedure; source appearance,
semantic editability and PowerPoint/WPS playback remain separate checks.


## Explicit shared native media grids

`build --artifact-image-shared-grid request.json` selects version 3 for a
standalone Artifact preview. The request declares integer source windows:

```json
{"schema_version":1,"groups":[{"objects":[
  {"id":"icon-a","window":[10,5,30,25]},
  {"id":"icon-b","window":[50,5,80,30]}
]}]}
```

Windows use left, top, right and bottom source pixel boundaries. Every actual
native picture must appear exactly once. Pictures in each group must contain
identical embedded media bytes. Each window must re-encode to the actual PPT
crop units. Exact rational constraints recover a common source-pixel-to-EMU
transform while preserving every native origin and extent within half an EMU.
An incompatible group fails instead of adjusting the declared geometry.

MuPDF samples complete media once per group and scale, then extracts opaque
windows at rounded native frame endpoints on that device grid. These transient
windows retain their positions in the complete mixed paint order. This avoids
changing the sampling phase by rendering each cropped image separately. The
request is frozen in the build config; output review independently repeats the
procedure using the delivered PPT and that frozen request.

This policy explicitly interprets the supplied windows as integer boundaries;
version 2 retains fractional crop semantics. Transparent windows, color
profiles, effects and other unsupported native picture states are rejected.
Integral canvas dimensions must agree with the actual native slide. Render
surfaces remain bounded to 16 million pixels and 32768 pixels per axis, and
complete grids plus extracted windows share the existing 64 Mi-pixel budget.
Each group allows at most 256 pictures; both axes together allow 131072 pair
constraints. No reference pixels enter rendering, and the editable PPT media
and coordinates are retained. Source appearance and application playback still
require separate visual verification.
