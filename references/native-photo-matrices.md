# Exact delivered-photo matrices in a separate PDF

`build --preview-backend libreoffice-pdf-photos` explicitly selects native PDF
preview provenance version 3. It retains the finalized PPTX, original media,
raw LibreOffice PDF and three direct PNG exports. The separate zero-alpha RGB
PDF and its pointwise receipt remain available. A second separate PDF restores
eligible opaque photographs to their actual delivered PPT coordinates; canonical
previews sample that second PDF. Both operations and the sampled input are
independently replayed and cross-bound in output review and delivery receipts.
Existing backends, binary-alpha policy and device-hairline requirements remain
unchanged.

The fixed `native-opaque-photo-matrix-v1` policy currently supports a fontless,
unrotated, standalone slide with top-level, ungrouped pictures and plain bounded
8-bit PDF images. Only uncropped, uneffected, unrotated, single-frame opaque
RGB/RGBA PNGs without color/profile metadata qualify. A photograph's complete
actual PDF RGB must exactly match its actual delivered media; any soft mask must
be exactly opaque. Partial-alpha symbols and unsupported pictures retain their
RGB-stage matrix and an explicit reason. Their samples and native delivery remain
untouched. Live-text PDFs, nested image invocation contexts, ambiguous resources,
unknown content syntax and incomplete paint correspondence fail closed.

The transformation replaces only the six numerical operands in an isolated
`q ... cm /ImN Do Q` image invocation. Exact native integer EMU coordinates are
mapped using the actual PDF page-to-slide ratios. Other content bytes remain
unchanged. An independent verifier replays every replacement, compares every
other PDF object and encoded stream, checks all image/mask bytes, compares all
actual nonimage drawings, and reads back actual derived photo frames under a
fixed 0.001-canvas-pixel guard. A separate 0.125-pixel guard admits the initial
image-to-PPT correspondence only after exact RGB and opaque-alpha identity.
This does not relax the source-path/control guard or change a declared PPT frame.
Files, decoded work, image extents, package expansion, XML and content streams
have fixed resource limits; callers may only lower them.

The retained BLIP diagnostic found that the entire original photo RGB array was
already exact, while LibreOffice's exported matrix shifted/shrank its frame by
approximately 0.02–0.06 canvas pixel. That offset was amplified by 4x sampling.
Using actual delivered coordinates removed the export drift in the diagnostic;
it did not replace photo pixels or use the source reference for generation.
Separate zero-alpha RGB normalization restored filled snowflakes. The photo
operation does not move those partial-alpha symbols.

This is an explicit preview repair with retained raw evidence. Final PPT playback
still depends on the target application. Native coordinate rounding and finite
filtering can produce small source differences even after matrix repair. Neither
pixel equality, a universal filtering bound, original font identity, automatic
semantic recognition nor PowerPoint/WPS acceptance is granted. Review every
actual figure and relevant scale before closing its specific issue.
