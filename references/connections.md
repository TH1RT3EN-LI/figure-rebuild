# Stable connections and local movement

Connections are explicitly declared relationships. Do not infer them from an
image or automatically replace faithful path arrows in an existing scene.
Complex source arrow paths can keep their original geometry and stable IDs.

```json
{
  "id": "module-flow", "kind": "connector", "z_index": 12,
  "from": {"id": "module-a", "site": "right"},
  "to": {"id": "module-b", "site": "left"},
  "route": "elbow",
  "arrow": {"start": "none", "end": "triangle"},
  "style": {"fill": "none", "stroke": "#000000", "stroke_width": 2}
}
```

Endpoints identify objects, never array indices or display text. Endpoint
targets need a positive-area explicit box or native path bounds. Anchor-only
text and uncompiled baseline formulas cannot serve as connector targets.
Sites map to native rectangle connection indices:
top=0, left=1, bottom=2, right=3. Native custom paths need matching connection
sites supplied by the exporter. Routes support `straight` and `elbow`, and
arrow endpoints support `none`, `triangle`, `stealth`, and `arrow`. Geometry is
derived from the current targets, so a connector does not store commands/box.
Elbow routes use a midpoint when both sites face the same axis, otherwise one
orthogonal bend. This is intentionally limited routing; use faithful paths for
complex source routes, crossings and branches.

A text/image/formula label can explicitly follow a module or another label:

```json
{
  "id": "module-label", "kind": "text", "text": "Module",
  "box": {"x": 100, "y": 100, "width": 120, "height": 40},
  "font_size": 20,
  "attach_to": {"id": "module-a", "site": "center", "offset": {"x": 0, "y": 0}}
}
```

For a boxed label, its center is placed at the target site plus offset. For
anchor text or a formula with `baseline_anchor`, its baseline is placed there.
Attachment sites additionally
support `center`; anchor-only attachment targets support center only. Label
chains resolve in dependency order and cannot cycle or attach to a connector.
Diagram flow can legitimately contain cycles; only attachment dependency
cycles are rejected. An explicit label movement changes its attachment offset,
so later movement of its module preserves the user's local adjustment.

`resolve_scene()` returns an isolated materialized scene and connector records.
Attached positions resolve first. Connectors become same-ID paths only in that
temporary scene; their records retain from/to IDs, site indices, arrow styles,
resolved points and geometry for native OOXML connections. `attachment_records()`
retains label/target IDs for safe exporter grouping. It does not reorder the
authored scene or change strokes. Exporter grouping can cross intervening
objects only after proving their paint does not intersect the moved member.
Persist the
semantic original, rather than saving the temporary connector paths.

Native OOXML connections use `p:cxnSp`, endpoint IDs and explicit site indices.
Standard presets preserve the initial route: `straightConnector1` for a line,
`bentConnector3` with adj1=50000 for H-V-H, `bentConnector4` with adj1=0 and
adj2=50000 for V-H-V, `bentConnector2` for H-V, and `bentConnector3` with adj1=0
for V-H. Native horizontal/vertical flips preserve reversed endpoints; equal
axes reduce to a monotone straight line with at most one EMU quantization.
The test fixture executes the original [Apache POI preset definitions](https://raw.githubusercontent.com/apache/poi/trunk/poi/src/main/resources/org/apache/poi/sl/draw/geom/presetShapeDefinitions.xml)
independently of the exporter across all endpoint quadrants. Unsupported
routes reject rather than silently changing their shape. Dragging and client
rerouting still require playback acceptance in the intended Office client;
native IDs and presets alone do not prove every client's behavior.

Attachment groups preserve child frames with an identity group transform.
Conservative visual bounds reject paint-order changes. When a stroke-only
custom path's large frame overlaps a label, read-only adaptive native-curve
subdivision and segment-to-rectangle distance can prove the actual stroke is
separate. The envelope includes subdivision error, width, miter and cap bounds;
the authored curve remains native. Filled geometry, arrowheads and unsupported
path paint retain conservative rejection. This avoids treating an L-shaped
line's empty interior as painted while still rejecting an actual crossing.

## Transactional patches

```json
{
  "base_revision": 3,
  "base_digest": "content_digest of the exact base scene",
  "reason": "Move module B to improve spacing",
  "operations": [
    {"op": "translate", "id": "module-b", "dx": 24, "dy": 0}
  ]
}
```

Operations are strictly `translate` with finite dx/dy, or `setbox` with an
explicit x/y/width/height box. `setbox` works on existing boxes or positive-area
paths; it transforms every path endpoint and cubic control point without
changing stroke width. It does not implicitly turn baseline text or formulas
into a box. Formula translation preserves its frozen assets and changes its
baseline only; formula attachment uses the same baseline relation as text.
Edit the endpoint module instead of moving a derived connector. Operations
resolve label chains sequentially. Unchanged IDs, order, styles, content,
source images and user assets stay unchanged.

Locks are `locked: true` on an object or `locks: [{"id": "module-a"}]` on the
manifest. A locked label or connector also prevents indirect geometry changes
caused by moving its target. Unknown locks, dangling targets, unsupported sites,
attachment cycles, stale bases and invalid output geometry reject the whole
patch before any state changes.

```sh
python /path/to/figure-rebuild/scripts/patch_scene.py \
  --manifest /path/to/job/manifest.json \
  --patch /path/to/job/proposals/move-module.json \
  --output /path/to/job/manifest-r4.json
```

The output must be a fresh filename in the same job directory, preserving asset
relative paths. Input is never overwritten. Successful writes first preserve
the exact base bytes and an audit under `history/scene-patches/`, then atomically
publish the new manifest with exclusive creation. Revision increments once and
review/acceptance bookkeeping clears to `needs_review`. The audit records base
revision/digest, reason, stable affected IDs and resolved geometry before/after.
Re-review and build the new manifest. A patch does not itself claim user visual
acceptance or that the PowerPoint exporter has preserved native relationships.
