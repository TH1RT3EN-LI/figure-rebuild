# Unchanged source curves with equivalent fill rules

`pdf_fill.prove_evenodd_nonzero_equivalent` returns a certificate only when the
original source geometry has the same filled set under both rules. An absent
certificate keeps the paint unsupported. The existing polygon-only policy is
unchanged. This API does not rewrite, flatten, reverse or discard output curves.

The bounded cubic fallback reuses `normalize_isolated_cubic_fill` to analyze the
original winding arrangement. Every cubic must have a proved monotone projection;
its complete control hull must be separated from other segment hulls except for
allowed adjacent endpoints. The engine can then use chords for topology and
preserve the whole original curves. Its output commands are discarded here.

Exact retracing/cancellation retains the previous unsupported policy: every
atomic edge must have a nonzero winding-jump classification. The engine's
ability to cancel an edge does not widen this source-admission extension.

For every atomic edge with a nonzero winding jump, both adjacent winding values
must satisfy `bool(w) == bool(w % 2)`. The unbounded face has winding zero.
Every region with winding different from zero borders a nonzero-jump edge;
zero-multiplicity edges change neither predicate. These tests establish equal
filled sets without changing source coordinates. Oppositely oriented nested
curves can form a valid hole. Same-direction nesting with a region of winding 2
fails the test, regardless of whether a renderer happened to hide that region.

The fallback accepts at most 512 proof commands and 128 actual segments, with
2,048 atomic edges, 250,000 shared exact operations and 80 side-probe halvings.
Exact binary input coordinates, rational growth limits, curve injectivity and
control-hull isolation use the existing engine's guards. Budget and unsupported
geometry errors return no certificate; unexpected programming errors propagate.

The receipt identifies the source/output fill rules, unchanged command policy,
source contour and cubic counts, all budgets and the complete topology
certificate. The nested engine certificate describes its nonzero boundary
analysis; `topology_certificate_scope` explicitly states that its output geometry
is unused. Source fill normalization remains proof-only, including implicit
closure and repeated line/close handling. Clip, group, alpha, stroke, source
identity, destination integer encoding and actual pixels still need their own
checks.

`tests/test_pdf_fill_curve_parity.py` checks holes, reversed/reflected contours,
separate islands and a three-level nesting, source opcode/style preservation,
clip rejection, intersections, resource failures and an actual native RGBA
counterexample for same-direction nesting. Legal PDF fixtures compare unchanged
geometry under both fill operators at 1/2/4 scale and opaque/translucent alpha,
with no parser recovery warnings. Full figures still require normal CLI builds,
final package readback and actual visual review.
