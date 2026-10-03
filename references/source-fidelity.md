# Bounded source-preservation replay

`figure_rebuild.source_fidelity.audit_source_fidelity()` independently replays a
PDF source and verifies submitted scene geometry and actual PPTX geometry/media.
It does **not** recognize formulas, transcribe labels, interpret directed
relations, render or visually accept the result. No caller `source_complete`
boolean can supply this evidence. This optional API does not change build,
source-content, output-review or user-acceptance status.

```python
from figure_rebuild.source_fidelity import (
    PdfSourceDescriptor, SourceReplayPolicy, ReplayLimits, audit_source_fidelity,
)

report = audit_source_fidelity(
    PdfSourceDescriptor(
        pdf_path='/data/source.pdf', pdf_sha256='<64 lowercase hex digits>',
        page=3, region=(46, 70, 300, 239), scale=2,
        # Optional byte-bound reference; fresh rendering must match it exactly.
        reference_png_path='/data/source.png', reference_png_sha256='<sha256>',
    ),
    manifest_path='/job/manifest.json',
    resolved_scene_path='/job/resolved-scene.json',
    asset_root='/job/assets',
    pptx_path='/job/actual.pptx',
    evidence_dir='/new/evidence',  # Must not already exist.
    policy=SourceReplayPolicy(), limits=ReplayLimits(),
)
```

The descriptor defines a declared source page/region and a positive uniform
PDF-to-canvas scale. It does not establish that the caller selected the intended
figure. Initial support requires pixel-aligned region edges, an unrotated page,
a white canvas and direct single-slide placement. The PDF and optional reference
are read once into private immutable snapshots before parsing. The source
worker binds PDF/SVG bytes, fonts, native paint context, source command/style/
clip/group hashes, image occurrence provenance and the freshly generated scene.

## Supported profile and identities

The first profile, `opaque_black_glyphs_native_images_v1`, supports unmasked,
unclipped opaque black native fill-text expanded into source glyph outlines,
plus actual native image occurrences. It permits at most one isolated full-page
DeviceRGB/Normal/alpha-one group. An image's original clips, attached image mask,
colorspace, groups and transform must be independently forwarded by the native
image helper and cross-checked against the source native occurrence sequence.
General `begin_mask`/`end_mask`, patterns, colored/clipped/stroked text, unknown
groups or unsupported paint kinds remain **UNRESOLVED**. Even paints outside the
requested region cannot silently relax this whole-page native-context guard.

Native PDF paint identities and expanded SVG glyph identities are different.
The API verifies each complete ledger in its own domain and does not claim a
native-paint-to-glyph bijection or infer glyph identity from a nearest bounding
box. Original whole SVG bytes are retained; serialized leaf XML is normalized,
not the original lexical `d` bytes. Replaying pinned extraction geometry is not
a formal independent verification of the PDF interpreter.

Derived native-image PNGs are explicit 8× raster samples. Their byte hashes and
placement can be verified without claiming vector recovery, independent
editability of embedded labels, or arbitrary-scale pixel losslessness.

## Comparison and status

The expected scene is generated before the submitted scenes are compared.
Every source occurrence is converted, proven skipped or unresolved; unknown
contexts cannot yield a successful partial expected scene. Full source-derived
object geometry/style/order and actual image bytes are compared to both
manifest and resolved scene. The actual PPT is compared directly with the
fresh expected scene. Explicit `source_id` is only a claimed identity, verified
with complete integer geometry, frame/crop, paint and embedded media equality.
Shape names do not establish identity.

The OOXML checker is limited to direct black filled native paths and unchanged
stretch PNGs. It checks the presentation's actual slide relationship, shape
inventory/order, complete quantized commands, paint subtrees and media. Unknown
color transforms, extra alpha, line effects, text bodies, geometry guides,
picture fillRect offsets, nested groups and nonempty root transforms remain
unresolved. The supported producer's empty root transform is explicitly treated
as identity. No inherited master/layout drawings are accepted. Coordinate
quantization is recorded separately from equality of source floating points.

An explicit transformation in `SourceReplayPolicy.transformations`, including a
future winding-normalization proof, is **UNRESOLVED** until that replay proof is
implemented. Do not remove its declaration or treat unchanged visual appearance
as proof. Without such a supported transformation, a changed source command or
placement is a mismatch.

- **NOT_PROVIDED:** no source descriptor.
- **UNRESOLVED:** unsupported context/transformation, missing optional dependency,
  malformed input or exhausted resource budget.
- **FAIL:** a bound input digest, geometry, placement, identity or media mismatch.
- **VERIFIED_IN_DECLARED_SCOPE:** every supported layer matched. Semantic
  recognition remains **NOT_PROVIDED**, visual acceptance **NOT_EVALUATED**, and
  user acceptance pending.

The lower-level `compare_replayed_scene()` does not authenticate the origin of
its expected scene. Use the combined API for source-bound results. Hash-bind
`source-fidelity.json` to any later delivery review; the API does not itself
claim that output-review or semantic checking has happened.

## Budgets, dependencies and evidence

`ReplayLimits` explicitly bounds PDF/SVG/font/asset/scene bytes, native/expanded
paint counts, commands, context events/depth, source and sampled image pixels,
reference pixels, PPT ZIP bytes/entries/expansion ratio, XML bytes/nodes, control
points, worker memory and elapsed time. The source parser additionally reports
its fixed XML-node/hierarchy/reference limits. Non-finite, boolean and invalid
budget values are rejected. Native work runs in a timeout-controlled subprocess
with Linux/POSIX CPU/address-space limits; platforms without that enforcement
remain unresolved. Optional PyMuPDF is imported only in that worker. The PPT
checker uses the standard library and enforces ZIP/XML limits before processing.
Extremely large integer API inputs and finite coordinates whose scale product
overflows raise a validation error before allocating source evidence. They do
not escape as an unhandled numeric overflow; unsupported scene coordinates in
the actual-PPT checker remain UNRESOLVED.

Evidence directories are new and are never overwritten. Private source bytes,
source SVG, expected scene, per-occurrence ledger, image assets, font hashes and
native context are retained. Runtime helper hashes are checked before/after
source replay; a changing runtime is unresolved. Actual inputs/results are
hash-bound. For a reviewed campaign, use a fixed installed wheel or immutable
code snapshot, and preserve all dependent output files.

Portable tests create real PDF and PPT ZIP fixtures and reject missing/shifted
glyphs, unchanged-image wrong placement, general alpha/luminosity masks, unknown
OOXML effects and budget violations. A real SGL regression can be enabled with
`FIGURE_REBUILD_SGL_REPLAY_FIXTURE=/absolute/fixture.json`; that JSON supplies
`source` (descriptor fields), `manifest_path`, `asset_root` and `pptx_path`.
This opt-in corpus evidence is external, not bundled in the wheel.

## CLI adapter and final artifact binding

A minimal JSON descriptor uses `pdf_path`, `pdf_sha256`, `page`, `region`
(`[x0,y0,x1,y1]` PDF points), `scale`, and optionally both
`reference_png_path` / `reference_png_sha256`. A future CLI can accept this
JSON plus `--manifest`, `--resolved-scene`, `--asset-root`, `--pptx`, and a new
`--evidence-dir`; it must report policy and budget choices and preserve the API
status. There is intentionally no CLI/build/output-review mutation in this API.

On supported replay, `source-fidelity.json.bindings` binds the exact submitted
manifest, resolved scene, private source PDF, source SVG, fresh reference,
expected fresh scene, and actual PPTX. `expected_scene_sha256` is the **file-byte**
digest; a separately named `expected_scene_canonical_sha256` hashes normalized
JSON. The final actual-PPT binding is taken from bytes actually parsed, not a
later reread. Per-layer image assets and source ledger/runtime hashes remain in
the layer reports. Unknown early inputs may have only partial bindings and can
never receive VERIFIED_IN_DECLARED_SCOPE. Existing evidence directories raise
`FileExistsError`; failure to publish the final report raises rather than
returning a successful unpublished result.

The supported producer writes inert creation IDs in known Microsoft extension
subtrees. Those exact UUID/numeric-ID structures are accepted separately from
paint. Other extension URIs, extra extension children, placeholders and media
nonvisual properties stay unresolved. Common-slide backgrounds and drawing
trees must be unique; white-background color/alpha transforms are not ignored.
Native path commands and their exact point counts must use the DrawingML
namespace, not merely matching local tag names. The fresh reference is always
rendered first from a separate pristine PDF document, avoiding metadata-query
color-cache side effects.
