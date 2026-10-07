# Explicit original PDF stroke sampling

Use `sample_pdf_stroke` only when the caller explicitly accepts a transparent
raster occurrence. It is not an editable path and does not prove exact cubic
clipping, live mathematical text, scientific semantics or visual acceptance.
Ordinary `outline_paths` still rejects unsupported strokes; it never silently
switches representation.

```python
from figure_rebuild.pdf_stroke_sampling import sample_pdf_stroke

row = sample_pdf_stroke(
    source_pdf,
    source_pdf_sha256=declared_pdf_sha256,
    page=declared_page_number,             # one-based
    native_sequence=declared_paint_seqno,  # complete native bboxlog sequence
    region=declared_roi_pdf_points,
    scale=2,
    native_sampling_scale=8,
    allow_native_rgb_group_sampling=False, # explicit opt-in, never inferred
)
```

The caller must independently identify the exact source occurrence. An SVG
paint index, a resource ID and the native sequence are different namespaces.
Do not select the closest bbox. Bind any crosswalk to the complete source bytes,
page, actual paint order and applicable path commands before using this API.

The helper verifies the declared SHA256, complete native paint inventory,
ordinary stroke role and original active clip identities. A fresh native device
forwards the original path, stroke, transform, color and active context callbacks;
it suppresses every independent paint. It uses the complete transparent ROI
frame so a diagnostic bbox is never promoted to a stroke-support proof. Source
bytes are unchanged. Returned PNG bytes, SHA256, box, crop and provenance can
be stored as an explicit `kind: image`, `editable: false` manifest object in
the original painter order. The API does not write assets or revise a manifest.

Only the verified MuPDF 1.28.2 provider is admitted. The page must be unrotated;
the ROI must lie inside it and align to the declared uniform positive source
scale. Sampling accepts 4 or 8 samples per source pixel, up to 64 million stored
pixels and 32,768 pixels per side. Source PDF input is bounded to 128 MiB.
Unsupported masks, patterns, non-neutral groups, non-unit alpha and overprint
fail. Original clipping is applied by MuPDF, without stroke geometry rebuilding
or dash lowering. Sampling pitch is a grid spacing, not an RGB, alpha or filtering
error bound. Full ROI storage also gives each asset a full ROI selection frame.

`allow_native_rgb_group_sampling=True` explicitly admits the existing native
RGB-group sampling domain: a neutral full-page root plus at most one RGB child
with Normal blend, unit alpha and no knockout. Isolated children and native RGB
ICC profiles can participate; their original callbacks and colorspace profiles
are forwarded. CMYK, extra nesting, masks, changed default RGB and other
compositing remain unsupported. The flag must be a boolean; omitted or false
retains strict group rejection.

This mode verifies active group IDs, bounds, original paint intervals and group
parameters against the independently inspected source context. Its receipt
preserves the full group paint counts, native profile identities,
`shared_group_split_unverified`, `required_full_figure_visual_review`, null
`rgb_alpha_error_bound` and false `exact_group_decomposition_claimed`. These
fields are required even for a source that needs no additional group admission.
Separately sampling one stroke suppresses its siblings, so this mode does not
establish compositing equivalence for the complete original group. Check the
final complete figure; retain any observed group-compositing discrepancy as an
open finding.

Inspect the final whole figure and affected junctions at 1×, 2× and 4×. Retain
the strict vector failure, sampling receipt, original context, stable core bytes,
final PPT readback and independent source comparisons. A visually accepted
sampled stroke remains a raster editability limitation; outlined math remains
distinct from recognized, editable mathematical text.
