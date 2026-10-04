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
