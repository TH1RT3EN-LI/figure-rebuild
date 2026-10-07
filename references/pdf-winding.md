# Native polygon winding normalization

The scene path model uses nonzero fill semantics. Some native consumers can display holes in overlapping or self-overlapping contours of one custom path. The postprocessor handles a bounded polygon subset by replacing its filled region with a proved equivalent compound path. It does not edit the source manifest, rasterize a diagram, flatten curves, or split an original paint into independently painted loops.

## Geometry API

`figure_rebuild.pdf_winding.normalize_nonzero_polygons(commands, max_coordinate_error=0, ...)` accepts only M/L/Z fill commands. Implicit fill closure and harmless duplicate vertices are handled in a proof copy. Initial close without a move, curves, ambiguous touching output boundaries and exhausted budgets raise `UnsupportedPdfWindingError`. Exact Fraction intersections split overlapping/retraced segments. Exact side probes classify original nonzero winding and retain only the boundary between filled and unfilled regions. Output loops must be simple, mutually disjoint, correctly oriented and have a proved containment matrix. Reverse-winding holes survive; repeated walks and same-direction overlaps do not apply alpha twice.

Default export requires exactly representable binary64 vertices. A positive explicit coordinate budget permits only bounded rounding with a second exact simplicity, separation, orientation and containment proof. This preserves topology; nonzero rounding is not pointwise geometry identity and carries no RGB/alpha error guarantee. The default budgets are 256 input segments, 2048 atomic edges, one million operation charges and 80 side-probe halvings. Input rationals are limited to 4096 numerator/denominator bits and retained intermediates to 8192. Area accumulation and simplification share the operation budget. Decimal receipt formatting respects the host's Python digit limit and fails explicitly rather than modifying that limit.

`verify_simple_loop_topology(reference_commands, candidate_commands, ...)` verifies corresponding loops after another coordinate encoding, including exact Fraction coordinates reflected from native integers. It checks strict simplicity/disjointness, orientation, complete containment, and per-vertex error. Callers must separately enforce their declared error bound.

## Maintained postprocessing subset

`native_winding.normalize_native_polygon_fill` is called for path objects by `postprocess.process`. Its receipt is stored in `editability.json` under `native_winding_fills`. It requires a declared solid nonzero fill, no source stroke, a matching native sRGB fill/serialized opacity, explicit native line noFill, one custom path, a mapped frame, and axis-aligned unflipped coordinates. Effects and ambiguous paint are not normalized. Cubic paths retain the existing cubic restoration flow and receive a `curves_not_supported` receipt. A rejected or inapplicable winding operation retains its input native geometry; no partial replacement is published.

The slide parent transform must be verified identity. The authoring exporter's absent or parameter-free root group transform is treated as identity; nonempty transforms must have identical off/chOff and ext/chExt with no rotation/flip. Grouping added later by the existing postprocessor uses an identity mapping.

The original shape ID, painter order, frame, fill, alpha and line XML remain unchanged. Each successfully normalized paint is still one compound `a:path`, including any holes or islands. The unchanged original manifest remains the source of stroke/paint intent. This integration accepts only no-stroke fills; the lower-level geometry API does not authorize replacing an original stroke with the normalized fill boundary.

## Unified precision policy

The policy identifier is `polygon_nonzero_half_ulp_and_1_over_1024_slide_emu_v1`. The binary64 intersection budget is the minimum of half an ULP at the maximum absolute input coordinate (at least 1) and 1/1024 of a slide EMU converted to input scene units. This is one project policy derived from coordinate magnitude and placement scale, not a per-figure tolerance. Actual coordinate error is recorded independently of the permitted bound.

The actual existing native frame must differ from the exact nominal placement frame by at most 2 EMU in each component, matching the existing frame guard. Its off/ext values are retained. New path dimensions equal the actual integer extents, so one local path unit is one slide EMU. For each normalized point, local integer coordinates are rounded from the exact nominal slide position minus the actual integer offset. This explicitly compensates frame-offset rounding rather than adding its error again. Small negative local coordinates are supported and tested; the implementation conservatively limits all native integers to signed 31-bit magnitude without claiming that this is the full schema range.

After serialization, the actual XML point/dimension integers are decoded and reflected into scene coordinates with Fractions. The full loop topology is revalidated. Integer encoding may move each coordinate by at most 0.5 slide EMU **relative to the binary64 normalized intermediate**. The receipt separately reports the earlier exact-arrangement-to-binary64 error and their combined bound, which can reach 0.5009765625 EMU. It does not mislabel that total as 0.5 EMU. Original frame errors, opacity serialization, normalized geometry, final grid proof and both error stages remain auditable.

These proofs do not establish identical antialiasing across renderers. Successful conversion still requests visual review of the actual finalized PPT. A retained unsupported polygon or curve is not silently declared fixed.
