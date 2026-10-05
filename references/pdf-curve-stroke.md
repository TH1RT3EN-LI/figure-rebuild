# Bounded curve stroke support

`pdf_stroke_bounds.prove_bounded_curve_stroke_support` is an explicit, read-only
support certificate for M/L/C/Z strokes. It keeps the original commands and
source clipping. It complements the narrower exact tangent certificate; the
automatic `outline_paths` admission policy is unchanged.

```python
from fractions import Fraction
from figure_rebuild.pdf_stroke_bounds import prove_bounded_curve_stroke_support

proof = prove_bounded_curve_stroke_support(
    commands, source_matrix, width, miter_limit,
    linecap="butt", linejoin="miter", dasharray=[3, 1], bounding_depth=6,
)
bounds = tuple(map(Fraction, proof["stroke_bounds_exact_rationals"]))
```

Commands are already in the source matrix's output coordinates. The exact
nondegenerate similarity only scales width; do not apply it to the controls
again. To certify original user-space callbacks, pass the identity matrix and
transport the resulting box by a separately verified source transform. The
target canvas transform is separate from the source clip check.

Dyadic de Casteljau subdivision gives exact enclosing centerline control
hulls. It tightens bounds without flattening or replacing any emitted curve.
Each cubic must have a bounded derivative certificate: exact subdivision of
its quadratic derivative hull gives nonnegative Bernstein projections with a
positive coefficient on every interval. All interior interval endpoints must
have nonzero derivatives. Zero derivatives at original endpoints require a
finite, nonzero one-sided tangent limit. Interior cusps, unresolved regularity
and constant curves fail closed.

The centerline tube uses the transformed half-width radius. Each miter is
bounded at its actual join point using a rational lower cosine and an upward
square root, or the declared source miter cutoff. A remote sharp join does not
inflate an unrelated curve extremum. Round and bevel joins stay in the tube.
Butt and round caps are supported; square caps and other joins are refused.
Exact rational square roots remain exact even beyond 53 significant bits.

Positive dash arrays with any phase paint a subset of this solid support.
That fact does not locate dashes, lower their geometry or verify dash phase.
Preserve the actual source cap/dash-cap/end-cap and run a separate dash
lowering audit. Zero or negative entries and device-space hairlines are outside
this interface. Move-only butt subpaths and exact zero butt lines are ignored
only in the support proof, with receipts; source commands remain unchanged.
Empty closed subpaths and ambiguous round-cap degeneracies are refused.

The fixed limits are 256 commands, 128 segments, 32 subpaths, depth 8 and a
shared total of 4,096 regularity/control-bound subdivision nodes. Callers may
only reduce these budgets. Input rationals are bounded to 4,096 bits; receipt
values to 16,384 bits and the host integer formatting limit. Finite outward
floats are diagnostics; exact rational strings establish containment.
`PdfCurveStrokeProofError` means no certificate was established. It is not
permission to discard clipping or expand the ROI.

The receipt does not admit groups, masks, blend modes or unknown source
effects. Compare it against every actual source clip and ROI. A true partial
intersection still requires an independent supported clipping construction.
Neither this helper nor a local background restoration proves equal pixels,
native curve quantization, PowerPoint/WPS behavior or semantic editability.

The six source-derived cases in `tests/fixtures/sampled-native-strokes.json`
include three strictly contained strokes and three with real partial support.
Tests retain that distinction, check unchanged controls, endpoint tangents,
regularity subdivision, local miters, exact boundary contact, positive dashes,
coordinate conventions, malformed inputs and resource failures. Independent
raw PDF strokes check rendered support without the project renderer. Complete
source/PPT/native-preview comparison remains a separate integration audit.
