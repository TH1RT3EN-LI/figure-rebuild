# Whole-cubic winding normalization and native integer verification

`pdf_cubic_winding.py` normalizes a nonzero **fill** into a single compound path suitable for an evenodd consumer. It supports only a strict domain in which every whole cubic is isolated by its original control hull. It never flattens, subdivides, partially clips, snaps, or moves a cubic. Source commands and strokes are not modified.

`pdf_cubic_contact_winding.py` adds an explicit endpoint-contact mode. It may split a C **for proof only**, at exact t=1/2, to prove an embedded endpoint graph. Output still contains complete original Cs, possibly reversed, or proves them wholly nonboundary. No proof piece is exported as geometry.

`native_cubic_geometry.py` strictly decodes native integer `a:path` geometry and verifies a proposed integer encoding. `native_cubic_winding.py` binds the actual slide, scene and object map before this normalization can run during postprocessing. An arbitrary XML shape name or caller-supplied “restored” flag does not authorize a rewrite.

## Build integration

The build passes its resolved occupied placement separately from the object-map file. After restoring full source-pixel-EMU cubic commands, the wrapper checks their exact initial native serialization, recomputes the source control-hull box, and verifies the actual shape identity, frame, solid color, opacity, absent stroke and supported parent context. JSON numbers such as `1` and `1.0` are compared as exact numeric values; Boolean aliases and changed coordinates are rejected. This binds authoritative scene/map inputs, not the original PDF extraction.

The public wrapper and native verifier default to the original `isolated` mode. The build postprocessor explicitly selects `endpoint_contact` with `proof_split_depth=1`; it does not retry after a rejected isolated proof or reset the operation budget. The same source, frame, paint, canvas-clip and parity guards apply to either mode.

A rewrite additionally requires an exact face witness with nonzero even winding: the case where the native evenodd fill would leave a hole that the source nonzero fill includes. A supported curve without such a witness keeps its original XML. Unsupported curves also keep the complete restored path. Verified source-canvas glyphs bypass this transformation so their original clipping geometry remains intact. Polygon-only paths retain their separate existing normalization policy.

The replacement is constructed and serialized separately, then decoded and independently proved again. Only after all checks and receipt construction succeed is the single native path replaced. Its dimensions, off/ext frame, paint and other shape structure remain unchanged. Rejection preserves the entry geometry **after cubic restoration**; it does not reinstate the author's flattened intermediate. The editability receipt records applied, not-applicable and rejected cases, their scope and precision accounts. Actual source/final-PPT review remains necessary.

## APIs and coordinate types

```python
normalize_isolated_cubic_fill(
    commands, *, max_input_segments=128, max_commands=512,
    max_atomic_edges=2048, max_operations=1_000_000,
    max_probe_halvings=80,
)

verify_isolated_cubic_integer_encoding(
    source_native_commands, candidate_integer_commands, *,
    max_coordinate_error=Fraction(1, 2),
    # same budget keywords
)

normalize_endpoint_contact_cubic_fill(
    commands, *, proof_split_depth=1,
    # same budget keywords as the isolated API
)

verify_endpoint_contact_cubic_integer_encoding(
    source_native_commands, candidate_integer_commands, *,
    proof_split_depth=1, max_coordinate_error=Fraction(1, 2),
    # same budget keywords
)

# Native XML geometry only; no mutation or file access:
decode_native_cubic_path(path_element, *, max_commands=512,
                         max_operations=1_000_000)

verify_native_cubic_path_encoding(
    original_path, candidate_path, *, slide_extents,
    max_slide_coordinate_error=Fraction(1, 2),
    proof_mode='isolated', proof_split_depth=None,
    # same geometry budget keywords
)
```

Commands are explicit-closed tuple/list `('M', (x,y))`, `('L', (x,y))`, `('C', (x1,y1), (x2,y2), (x3,y3))`, and `('Z',)`. A new M requires the previous contour to be explicitly closed. Repeated Z without an active move, unsupported operators and malformed structure are rejected. Zero-length L edges are ignored only in proof records; no input is changed. Closed single-C loops are rejected. Two-edge curved lenses are outside the isolated domain; endpoint-contact depth 1 can prove some such lenses while preserving both whole Cs.

Normalization accepts **exact built-in** int, finite float, and Fraction coordinate values. A float denotes its exact binary64 value; it is not reinterpreted as a rounded decimal. Signed zero is harmless. Booleans, decimal strings, subclasses/custom number objects and nonfinite values are rejected. Pure polygon inputs and an empty fill are allowed; an empty result never authorizes discarding a source stroke.

The normalization result contains:

- `commands`: exact Fraction coordinates, with no binary64 output conversion.
- `curve_identities`: each original C's source contour/command identity, entire controls, kept/reversed/discarded decision and output command index. Rational receipt values are strings.
- `vertex_origins`: each output endpoint's original-vertex or new-L/L-intersection identity, source line pairs, and protected-C-endpoint status.
- `proof`: checked projections, hull separation, exact side-winding classifications, loop topology, operation usage and scope limitations.

The integer verifier takes **independent source commands**, not a caller proof/result. It recomputes normalization itself. Source and candidate coordinates must be integers (built-in int or integral Fraction); even integral floats are rejected. A scalar or two per-axis maximum coordinate errors may be supplied, each between zero and half a local grid unit. Only newly introduced L/L intersections may move. Surviving original vertices and all retained C controls/endpoints must remain exact integers in the correct orientation.

`proof_mode` is a closed enum: `isolated` or `endpoint_contact`. An isolated call accepts only `proof_split_depth=None`; a contact call resolves `None` to 1 and otherwise requires built-in integer 0 or 1. Booleans, unknown modes and conflicting policy values raise ValueError. Existing isolated public calls and receipt meanings are preserved.

## Strict isolated geometric domain

For each C, the three Bernstein coefficients of the derivative under a selected linear projection must be nonnegative with at least one positive. This proves strict monotonicity on the open parameter interval and injectivity. Let D be the endpoint chord. Every intermediate curve `(1-s)C+sD` stays inside the original convex control hull and remains strictly monotone under the same projection.

For every pair involving a C, exact separating-axis predicates must establish either strict separation of the whole control hulls, or intersection at just one shared endpoint of consecutive segments in the same closed contour. Adjacency comes from the actual sequence, including its cyclic last/first pair. A supporting-line proof for an adjacent pair requires at least one exposed face to be exactly that shared point. Shared endpoints across different contours are not admitted. No epsilon or sampled-curve evidence relaxes this rule.

Consequently all C-to-chord deformations preserve the boundary graph and its face winding labels; only unchanged L/L intersections remain. Exact Fraction arrangement predicates split those L edges, compute nonzero side winding, and select zero/nonzero transitions. Output chord loops must be strictly simple, mutually disjoint and consistently oriented with their complete containment matrix checked.

No output simplification is performed. A collinear vertex may identify a C endpoint and cannot be silently deleted. Every C chord must remain indivisible, and every C must either be restored whole (possibly reversing all four controls) or have a proved nonboundary classification. Undoing the isolated deformations restores the original curved filled region with one compound paint. This is an exact-rational fill result; it makes no claim about a later coordinate encoding.

## Endpoint-contact proof domain

The new mode first proves strict monotonicity of each **whole original C**. At depth 0 it uses the original hull; at depth 1 it computes two exact rational de Casteljau halves. Each half inherits the original segment identity and the original pair of endpoints. A half-to-chord homotopy stays in that half's hull and preserves the original monotone projection. Those proof halves may be used only to establish the boundary graph and winding arrangement.

Every pair involving a C proof piece must have strictly separated hulls or a proved singleton endpoint intersection. For pieces from different original segments, that shared point must be a true endpoint of **both original segments**. Only consecutive halves of the same original C may meet at the artificial proof midpoint. Thus two different Cs crossing at t=1/2 remain rejected even if both midpoint coordinates coincide, or other fill completely covers the crossing. Having one common original endpoint does not excuse a second interior contact. The singleton rule applies to each proof-piece pair; it does not categorically forbid two original Cs forming a lens with two different shared endpoints.

Cross-contour original endpoint incidences are retained in the exact graph. Unchanged L/L intersections may be split. Curve chords cannot be partially selected or cut: all proof pieces for an original C must appear consecutively, in a single orientation, or every piece must have a proved nonboundary classification. Output endpoints retain original-vertex or genuine-L/L-intersection origins. Protected original C endpoints cannot be simplified away; proof midpoint aliases and output leaks are rejected. Output is then reparsed and reexpanded, and its exact proxy rings must reproduce the proved arrangement. Final output loops must still be simple, mutually disjoint and manifold. True C interior crossings, unproved hull intersections and nonmanifold/touching output boundaries remain unsupported.

Both modes return `boundary_classification` for the wrapper's exact parity witness and `chord_loop_topology` for independent final encoding checks. The contact receipt additionally identifies depth, proof-piece/original endpoint identities, source/output proxy limits and zero exported C fragments. It never claims renderer or RGB/alpha equivalence from these geometric proofs.

## Final integer proof

The integer verifier first recomputes this exact normalization of the **initial native integer** path. It requires the candidate command sequence to match, and checks:

1. Full C control identity, direction and implied start point; fixed protected endpoints and surviving original vertices.
2. Only new L/L intersections can move, within the declared per-axis bound. The same exact point must always receive the same integer encoding.
3. Final C projection and the selected domain predicates, on the actual candidate integers: whole-hull isolation for `isolated`, or endpoint-identity and proof-half hull predicates for `endpoint_contact`.
4. The exact-normalized and final integer chord-loop vertex correspondence, strict simplicity/disjointness, orientation and complete containment matrix.

It does not repair a bad rounded candidate and then claim success. A newly touching boundary, collapsed gap or hole, moved C endpoint, partial C, extra command or changed containment is rejected. Repeating the curve-to-chord proof on both geometries transfers the chord-loop topology comparison to the curved boundaries. Unless no new intersection moves, this preserves topology and a bounded geometry displacement, **not pointwise equality near the moved L edges or RGB/alpha equality**.

## Native XML boundary

The decoder accepts a standard-library `ElementTree.Element` containing exactly one DrawingML `a:path` node, with bounded canonical signed integer coordinates. It does not parse XML text, open files or resolve resources. Accepted path attributes are `w`, `h`, `fill`, `stroke`, `extrusionOk`; dimensions must be positive, fill must be full `norm`, and declared Boolean values must be canonical supported forms. Only standard-namespace M/L/C/Z children with exact point arity are allowed. Formulas, arc/quadratic commands, foreign/nested elements, unknown attributes, non-whitespace text or excessively long integer/text fields are rejected.

Negative points and controls outside path `w/h` are retained: these dimensions define coordinate scaling and are not treated by this API as a clipping frame. The integer magnitude limit is `2**31-1`, an implementation budget rather than a statement of the entire file format's capabilities.

The path-pair verifier decodes **both** original and final candidate XML. All path attributes and dimensions must remain unchanged. Caller-bound positive slide extents give exact per-axis scale `ext_x/w`, `ext_y/h`; local error is limited by both half a local grid unit and the declared error of at most half a slide EMU. Non-unit or anisotropic scales do not silently enlarge that cap.

This geometric API cannot see or certify a full shape's off/ext identity, fill/color/alpha, line paint, parent transform, rotation, flips, clipping/effects or original source identity. Its receipts explicitly mark those as unverified. A wrapper must inspect the actual slide root and uniquely identified native shape, verify paint and parent context, check exact initial restored opcode/point serialization against bound scene commands, verify the declared source-to-slide mapping, and reject unproved context. It must preserve canvas-clip-selected source geometry. “Parent identity” and “restored” Boolean flags from an arbitrary caller are not adequate evidence.

## Three precision accounts

The wrapper keeps these separate:

1. Retained source extraction coordinates to frozen scene binary64 commands, including extractor precision. The mathematical proof starts from the exact values supplied to this API; it does not recover an author's unobserved PDF real numbers.
2. Scene commands to the initially restored native integers, including point rounding and actual off/ext/w/h effects. This pre-existing error is not zeroed or relabeled as the normalizer's error.
3. Additional normalization encoding: native C geometry and surviving old integer vertices stay unchanged; only new L/L intersections may move, under the local/slide cap and exact topology reproof.

For local coordinates `(u,v)`, use exact Fraction slide coordinates `off_x+u*ext_x/w`, `off_y+v*ext_y/h` for the final checks. A polygon-only float/source proof does not establish native curve topology. `rgb_alpha_error_bound` remains null, and actual final PPT/source comparison is still required.

## Resource and failure contract

A single operation counter covers parsing, hull construction, projection/separation predicates, exact line arrangement, ring validation, receipt formatting and integer revalidation. Each retained rational intermediate is bounded at 8,192 bits; input numerators/denominators are bounded at 4,096 bits. Exact arithmetic and comparison may temporarily multiply two bounded integer operands but never accumulates an unchecked Fraction. Hull sorting/comparisons, orientation, dot/cross products and supporting-face checks are charged, not just an outer pair loop. Geometry uses the existing checked polygon predicates for intersections, winding and loop validation. Native decoding and its geometry proof share the total budget rather than resetting it independently.

The maintained integration uses one `_Arithmetic` counter through initial XML decoding/binding, normalization, candidate serialization, final XML re-decoding, geometry verification and receipt serialization. `_normalize_isolated_with_budget`, contact `_normalize_with_budget`, and native `_verify_native_cubic_path_encoding_with_budget` are private trusted composition interfaces. Their receipt operation counts are **phase deltas of the already-spent shared counter**, not instructions to spend those operations again. Geometry strategy functions are internal; no public callback or caller receipt can supply proof admission. A failed proof has no independent fresh-budget fallback.

At contact depth d, source proxy segments are capped at `max_input_segments * 2**d`; output/candidate segments remain bounded by `max_atomic_edges`, with at most `max_atomic_edges * 2**d` proof proxies and `max_atomic_edges*3+16` output commands. Depth is limited to 0 or 1, so subdivision cannot recurse or grow without this bound. All added half construction, contact predicates, chain validation and reproof use the same checked arithmetic/counter. Default public budgets are source segments 128, commands 512, atoms 2048, operations 1,000,000 and side-probe halvings 80.

Input containers/arity are checked before traversal. Command nesting is fixed by the accepted grammar; there is no recursive untrusted geometry parser. Receipt serialization traverses only internally constructed, bounded records. Native XML checks bound command/point counts and integer/text lengths before numeric conversion or further traversal.

Unsupported source geometry, integer encoding, bit/operation exhaustion or host decimal-formatting limits raise `UnsupportedPdfCubicWindingError` (the shared `UnsupportedPdfWindingError` class) with a reason code. The host's integer-string limit is never changed. Invalid budget/error-policy configuration raises ValueError; there is no partial success or caller mutation. Default budgets are explicit in the APIs; callers can choose tighter budgets.

The maintained tests cover C identity/reversal, protected collinear endpoints, holes, nonboundary C removal, same-winding overlap, exact tiny gaps, all conservative rejection boundaries, integer intersection collapse, non-unit native scale, actual XML re-decoding, nonfinite/types/bit/operation/formatter guards and an optional actual-PDF f/f* alpha oracle. Historical SAM feasibility establishes two yellow arrows in the strict isolated domain. The endpoint-contact proof additionally supports three Repeat paths in their independently decoded original native integer domain, preserving all whole C controls. Four dotted-arrow paths remain rejected for true curve-interior contacts. These are bounded source/native geometry results, not a final-PPT acceptance claim. Native integer rounding can change blocker counts, so original source-domain counts must not be reused as native-domain evidence.
