# Explicit final-PDF previews and device hairlines

Receipt numbers use the same integral-number tokens as Node's JSON output.
Nonintegral matrix coefficients retain their exact floating-point values. The
output review still distinguishes integer counts, booleans and floating-point
counts when independently comparing the actual final PDF and preview bytes.

`build --preview-backend libreoffice-pdf` exports the finalized standalone PPTX
with the existing isolated font environment and lossless LibreOffice PDF
options. MuPDF samples that unmodified PDF at 1x, 2x and 4x. These are the
canonical previews and the input to raw diagnostics. All three direct Impress
PNG exports remain separately retained and hash-bound; the ordinary
`libreoffice` backend still uses their exact bytes as canonical previews.

The frozen config selects `native_pdf_preview_version: 1`. A receipt binds the
actual PDF, native slide size, actual page dimensions, matrices, MuPDF version,
antialias levels and every PNG digest. Output review independently regenerates
the receipt and PNG bytes from the retained PDF. Rebound hashes, different
matrices, unrequested modes and substituted canonical pixels fail. No source
reference supplies preview pixels. Native PDF previews do not consume the
separate binary-alpha derived PDF, and that derivation cannot be combined with
this backend. They do not verify PowerPoint/WPS or source pixel equality.

The helper requires one unencrypted, unrepaired, unrotated PDF page whose size
matches the final slide within 0.02 PDF point. It rejects invalid integer slide
dimensions and bounds all render surfaces before opening the PDF: 32768 pixels
per axis, 16 million pixels per surface, 64 million across all scales, and
128 MiB input bytes. These bounds do not bound every internal PDF interpreter
allocation or certify every exported paint. Actual object/linewidth readback
and visual inspection remain necessary.

A path can explicitly declare `"stroke_hairline": true` with `stroke_width: 0`,
a path with solid-or-none fill, a solid stroke and positive opacity that survives native
serialization. The postprocessor restores actual DrawingML line width zero,
stroke paint and the custom path's stroke-enabled state, preserving control
coordinates and existing shape fill. Other zero-width paths retain their existing behavior. Builds
with explicit hairlines require `libreoffice-pdf`; a fixed positive width is
not silently substituted. The SVG master retains width zero and a
`data-figure-rebuild-device-hairline` intent attribute: SVG cannot render this
native device-space hairline. Artifact stroke previews report that limitation.
Output review separately reads actual top-level native paths and verifies the
declared zero width, paint, alpha and stroke-enabled state. Grouped hairline
objects are outside this readback contract. This readback records PDF linewidth
verification as false: only actual PDF paint inspection can establish it.

DrawingML zero width is not a promise that every application paints a PDF
hairline. Verify the actual exported PDF linewidth and matched-scale pixels.
LibreOffice direct PNG, native PDF/MuPDF and PowerPoint/WPS can interpret
minimum widths differently. No general linewidth, antialias, font, group or
RGB/alpha equivalence follows from selecting this backend.

Also read the actual exported joins and miter limit. In the retained BYOL audit,
the source and final DrawingML specify miter limit 10, while the exported PDF
uses about 3.8637 and clips arrowhead wings. A canonical PDF preview exposes
that exported result. Better image diagnostics alone do not close the finding;
source-sized visual inspection and actual paint-state readback remain required.
