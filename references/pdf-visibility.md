# Bounded source visibility proofs

Visibility evidence answers whether a source occurrence can affect a declared
ROI. It does not authorize removing masks, changing geometry or declaring a
figure correct. Retain the original occurrence, context and unresolved reasons;
store any exclusion certificate separately.

## Original native clip support

```python
from figure_rebuild.pdf_paint_context import inspect_pdf_paint_context
from figure_rebuild.pdf_visibility import prove_native_clip_disjoint

context = inspect_pdf_paint_context(pdf_path, page=page_number)
record = context["paints"][native_sequence]
certificate = prove_native_clip_disjoint(context, record, region)
if certificate is not None:
    outside_roi.append({"original_record": record, "certificate": certificate})
else:
    # Continue normal conversion/context checks or retain an unresolved item.
    pass
```

`region` is `[x0,y0,x1,y1]` in PDF page coordinates. The report must retain its
complete ordered native paint inventory and canonical `context_sha256` binding.
That digest detects changed report content; it is not independent proof of an
arbitrary supplied report. Generate it freshly from the bound original PDF.

For the verified MuPDF 1.28.2 provider, `clip_path` records contain a conservative
extent from `fz_bound_path(path,NULL,identity)`. This is the complete local
control hull, not a near-rectangle predicate or a rounded page-space rectangle.
Exact rational intervals enclose every float32 multiply/add under the original
matrix. Non-finite values, non-float32 inputs, magnitudes above `2**24`, singular
matrices and empty hulls are not certified. Unverified provider versions leave
`extent_not_certified` and receive no extent certificate.

A certificate requires an active clip ID and its exact half-open paint span,
strict separation from the ROI, no mask definition/use or pattern, and at most
one verified neutral full-page group. Touching bounds, unknown groups and
malformed identities receive no certificate. A nonrectangular clip can have a
useful outer hull; an ROI inside that hull cannot be excluded by this API.
`role`, `page_object_allowed`, attached-mask ownership and original unresolved
reasons remain unchanged. Other image/effect proofs still apply.

The provider contracts are bound to the versioned
[MuPDF path implementation](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/path.c)
and [geometry implementation](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/geometry.c).

## Exact SVG exclusion

`outline_paths` automatically records a `visibility_certificate` when either
of these narrowly supported source facts proves an occurrence invisible:

- The conservative bounds of supported active rectangular clips and the ROI
  have a strictly empty intersection. This check can precede irrelevant affine
  stroke conversion, but it cannot bypass unsupported effects.
- A single nonzero straight dashed stroke has no fill, butt caps, positive
  width/dash lengths, an exact uniform diagonal transform and no joins. Its
  whole segment plus half-width support is strictly outside the effective
  clip/ROI bounds. The dash geometry and general miter envelope are unchanged.

`SourcePaint.exact_transform` and each clip's `exact_transform` retain rational
values computed from lexical SVG matrices alongside the existing float geometry.
Unsupported transform syntax or numeric-budget failure disables the certificate;
it does not replace ordinary conversion coordinates. Rational values are bounded
to 4,096 bits, transform text to 4,096 characters, and the certificate's linear
clip/segment parser to 64 tokens. Unknown clip ancestors, filters, masks, group
effects, invalid styles, square/round caps and multi-segment dashed strokes do
not use the straight-stroke proof.

Every skipped source ID remains in accounting. No commands, styles or source PDF
bytes change. Review the complete source inventory and actual exported PPT at
1x/2x/4x before closing a visible defect.
