# Bounded transverse cubic/line fill normalization

`pdf_cubic_transverse_winding.py` admits a narrow additional domain for a
nonzero compound fill whose native consumer uses even-odd filling. It does not
alter extraction, source clipping, strokes, text, or the existing isolated and
endpoint-contact mathematical domains. A geometric proof is not a pixel or
whole-figure acceptance receipt.

## Domain and proof

Input is original signed-31-bit-magnitude integer M/L/C/Z geometry, with at
most 128 segments and 512 commands. Each cubic has an independently certified
strictly monotone projection. An admitted cubic/line pair has opposite endpoint
signs and strictly same-signed Bernstein derivative coefficients for its line
side polynomial. This proves exactly one simple crossing; its parameter and
position on the finite line are isolated by bounded exact rational arithmetic.
Each original cubic and each original line has at most one event, with 1–16
events overall. Other pairs require strict hull separation or a certified
original endpoint contact. New C/C crossings, tangencies, uncertain fan order,
proper L/L intersections, partial overlapping lines, and T junctions reject.
Identical full lines retain signed multiplicity rather than being deduplicated
as paint.

Original curves are restricted algebraically at the certified roots. Shared
event identities bind both curve fragments and both line fragments. Parent
convex hull separation, strict line-side support, and same-curve monotone
projection prove the source fragment arrangement. Exact uncertainty hull and
incident-fan predicates bind that arrangement to a rational proxy. The proxy
uses exact edge-side winding labels and the signed support quotient; every
fragment has a selected/discarded classification. Face-boundary cycles are
accounted for, without calling disconnected boundary cycles distinct faces.

The selected boundary is encoded once onto the original integer grid. Each
algebraic control interval must lie within one rounding cell; shared crossing
endpoints round to one node. Complete actual integer curves are independently
reproved for injectivity, pair separation, loop orientation, and containment.
The XML verifier decodes both original and final XML and independently repeats
source normalization; it never consumes a caller's asserted proof.

## Build policy and transaction

The public native wrapper retains `proof_mode='isolated'` by default and keeps
the explicit `endpoint_contact` contract. The new explicit geometry mode is
`transverse_line`. Postprocessing requests the separate
`classified_contact_or_transverse` policy with a shared 4,000,000-work-unit
budget, after restoring original native cubics and before grouping.

Classification reads authenticated original integer commands. A certified
finite interior transverse C/L event selects the transverse proof. Without
one, the existing depth-one endpoint-contact hull domain must be proved before
selecting contact. Unknown classification rejects. Exactly one normalizer is
then called; its rejection never triggers a retry in another domain. Selection
does not use figure IDs, model names, object counts, or earlier receipts.

The wrapper independently binds the actual XML root, IDs, source commands,
map box, explicit occupied placement, initial native integer commands, frame,
path dimensions, fill, opacity, no-line state, and identity ancestors. Verified
source-canvas clipping objects remain excluded. A rewrite also requires an
exact nonzero even-winding open-face witness. A recipe change alone does not
authorize rewriting a path.

On success, one compound path is replaced while shape frame, path attributes,
paint, alpha, and surrounding XML remain unchanged. Final native XML is
decoded and reproved before mutation. Context verification and complete
receipt serialization also finish first. Any unsupported case, budget failure,
or final verifier failure retains the entry post-restoration XML bytes.

## Three separate precision accounts

1. PDF-to-scene extraction error is not recomputed by this wrapper.
2. Scene-to-initial-native error is independently reported from the actual
   restore mapping, including the existing frame tolerance and local-grid
   quantization. It is not included in a new half-unit claim.
3. Original algebraic fragment to final integer controls has one total bound
   per axis: at most 0.5 local unit **and** 0.5 slide EMU using the unchanged
   actual extent/path-dimension ratio. New controls and crossing nodes are
   included. There is no separate later L/L rounding in this domain.

The Bernstein convex-combination bound transfers the third control error to
corresponding points on the original boundary. It is not an RGB/alpha error
bound or a claim of identical antialiasing. Previously existing integer
controls and endpoints remain exact when retained; event fragments have new
controls, unlike the whole-C modes.

## Resource contract and receipts

The extended wrapper shares one counter across source/context checks,
classification, normalization, integer encoding, actual XML decoding and
independent final proof, and receipt serialization. It counts instrumented
bounded work units, not every Python instruction or Fraction primitive. Exact
arithmetic inherits checked rational-size bounds. Hard caps include 128 input
segments, 512 input commands, 2,048 atomic edges, 8,000,000 selectable work
units, 80 probe halvings, and 16–96 root-isolation steps. Native use fixes root
isolation at 80 steps. Tree and XML-text hard limits are 200,000 nodes and
16,000,000 Unicode string-length units. The legacy `max_xml_text_bytes`
parameter counts string lengths, not encoded UTF-8 bytes. Exhaustion rejects
without changing XML.

Receipts include the selected/requested policy, classification evidence,
source/native hashes and binding, complete fragment/face accounting, final
integer topology, total new control error, and shared work count. Existing
isolated/contact public success receipts remain unchanged. Pure helper proof
fields explicitly do not certify shape context, a rendered PPT, or visual
acceptance; only the wrapper binds context, and actual output review remains
separate.

Focused regressions include original integer geometry for four author paths,
exact candidate replay, forbidden events, shared-node and topology attacks,
actual XML/paint/frame/source binding, late receipt budget exhaustion with no
mutation, no fallback after classification or normalization failure, and a
synthetic ZIP postprocess transaction. A real full-deck build/render remains a
separate validation stage.
