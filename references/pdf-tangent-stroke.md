# Exact tangent stroke support

`pdf_stroke_bounds.prove_tangent_stroke_support` proves a sufficient geometric
support for a narrowly defined smooth miter stroke. It does not clip, flatten,
rewrite or resample the source path. The existing `stroke_envelope` API and its
default results remain unchanged.

This addresses a conservative-bound failure: a smooth rounded outline can be
inside its source clip while the generic miter-limit bound extends outside it.
The BYOL input outline in `ccf-2020-07-f02` has eight exactly smooth joins and four
regular cubics. Its old axis support was about 3.129659 pt; the proved support is
about 0.312966 pt and fits the original clip with a 2.284210 pt left margin.
This permits source conversion; it does not certify the resulting slide's pixels.

## API and coordinates

```python
proof = prove_tangent_stroke_support(
    commands, source_matrix, width, miter_limit,
    linecap="butt", linejoin="miter", dasharray="none", fill="none",
)
ex, ey = map(Fraction, proof["axis_support_exact_rationals"])
```

Commands must already be in the source matrix's output coordinate system. The
complete source matrix determines stroke width scaling and is never applied to
the commands a second time. The target canvas transform is not an input to this
source-space containment proof. Consumers use the exact rational supports and
round expanded control-hull bounds outward; receipt floats are also outward
rounded but are not the authoritative support representation.

`PdfTangentStrokeProofError` means that no certificate was established. The
lowerer catches only this declared error and retains the original conservative
envelope. It does not catch unexpected programming errors. Existing clipping,
group compositing, style, mask and transform admission checks still apply.

## Sufficient domain

- M/L/C/Z commands; positive declared stroke width, miter limit at least 1,
  miter joins, butt/round caps, no fill and no dash. Zero width remains governed
  by the existing SVG zero-width policy, not PDF device-space hairline rules.
- An exact nondegenerate similarity: matrix columns have equal squared length
  and zero dot product. Rotations and reflections are allowed; nonuniform,
  skewed, singular and merely near-similar matrices are not certified.
- Every line has nonzero length. Every cubic's three derivative control vectors
  have strictly positive projections on their sum. Since the derivative is a
  Bernstein combination, it is nonzero throughout the closed parameter interval.
  This is a sufficient regularity check, not a complete test for regular cubics.
- Every actual adjacent join has exact cross product zero and positive dot
  product between its nonzero one-sided derivative directions. An implicit
  closing line and the final-to-first closing join are included. Near tangency,
  reverse directions, cusps and zero derivatives fail closed, with no epsilon.
- Empty closed subpaths, drawing directly after Z, and unsupported commands are
  not certified. Move-only subpaths remain in the hull and consume the subpath
  budget; separate subpaths do not acquire artificial joins.
- At most 256 commands, 128 actual segments and 32 subpaths. Optional caller
  budgets can only reduce these limits and must be non-boolean positive integers.
  Inputs must be finite numeric values; rational input representations are
  bounded to 4096 bits and exact receipt values to 16384 bits. The host Python
  integer formatting limit is respected: formatting failure rejects the proof
  through its dedicated exception without changing process-wide settings.
  Nonfinite support output is not certified.

Regular curve points lie in the control hull, and ordinary stroke points lie
within a half-width disk of their centerline. Exactly matching unit tangents
produce no additional join shape. Butt and round caps also fit within that disk.
Exact similarities map it to a disk of known scaled radius. Rational arithmetic
and upward integer-square-root rounding give an enclosing axis support, including
subnormal scales. Self-intersection can change coverage, but cannot extend the
union's support. See the [SVG 2 stroke shape definition](https://www.w3.org/TR/SVG2/painting.html#StrokeShape).

Square caps, sharp miters and nonuniform matrices provide actual native PDF
counterexamples to an unproved half-width shortcut. A rejected proof may still
render safely in a particular finite-scale fixture; that does not widen this
sufficient domain.

## Lowerer integration and receipts

`outline_paths` attempts the certificate for a non-rectangular original stroke
with miter join, no fill and no dash. Existing rectangle support remains in its
own branch. On success, the lowerer uses the certificate's Fraction supports
before its existing clip containment/intersection logic. On rejection, geometry
and bounds follow the unchanged conservative branch.

The `tangent_stroke_support` receipt is attached to converted source provenance
and skip records that consume the bound. It identifies the original source
paint and states `applies_to=original_source_stroke_before_clip_intersection`.
An existing later finite-butt intersection may turn a stroke into a fill; this
receipt never purports to prove that derived fill's geometry.

Successful receipts retain the method/version, all budgets and counts, source
matrix, exact radius/support, every join's source command indices and cross/dot
values, and each cubic's regularity witness. Rejections retain a reason and the
conservative fallback policy. Original clip contexts and source commands are
preserved. No new receipt permits dropping an unknown effect or changing a ROI.

## Validation and limits

`tests/test_pdf_tangent_stroke_bounds.py` covers source-excerpt conversion and
command/style/clip preservation for BYOL, legacy fallback, outside skip receipts,
later stroke-to-fill intersections, exact closure, reflections and rotations,
near tangency including the smallest subnormal, regularity failures, strict
budgets, malformed inputs, extreme numeric output and direct native PDF fixtures.
The ordinary PDF source/stroke/clip tests remain relevant integration checks.

The full-source BYOL regression is separately bound to the exact original SVG
and PDF in the external audit record; its extraction selects just one original
paint and does not replace a complete normal CLI build. A complete new candidate
must still account for every source paint and receive actual LibreOffice 1/2/4
inspection before any figure-level issue can close. This helper provides no
global claim about native curve quantization, font fidelity, visual acceptance
or a renderer's pixel identity.
