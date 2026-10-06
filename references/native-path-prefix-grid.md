# Finite native filled-path prefix grids

`build --artifact-path-prefix-grid request.json` is an explicit standalone
Artifact preview policy. It reads the actual final PPT, composites a continuous
prefix of filled native paths over the actual opaque RGB slide background, and
samples that prefix on a declared finite pixel grid. MuPDF then places those
pixels on each global 1x, 2x and 4x target grid. Later native objects retain their
complete mixed paint order. The editable PPT is never re-exported from these
transient preview images.

This can avoid resampling each color plane of a source-cell reconstruction
separately. A transparent plane's zero-alpha RGB is not treated as the original
source background; the actual background and all selected paints are composed
together before minification. This does not recover original author vectors or
prove source, arbitrary-scale or PowerPoint/WPS pixel equality.

```json
{
  "schema_version": 1,
  "object_ids": ["panel", "ink-color-1", "ink-color-2"],
  "frame_emu": [0, 0, 952500, 762000],
  "pixel_grid": [300, 240]
}
```

`object_ids` must name the actual first native paints, in exact order, without
skipping a paint or including a text/image object. The sampling frame uses
integer EMU (9525 per canvas pixel) and must be contained in the actual canvas.
Every selected native control point must lie inside it. The grid is a sampling
choice, not a geometry transform: frames, source controls, literal colors,
opacity, even-odd fill and native paint order come from the final PPT.

The admitted prefix is flat, visible and uses solid filled custom paths without
painted strokes, effects, geometry formulas, rotation/reflection or external
resources. Later ordinary text, pictures and paths are rendered by Artifact.
The actual active slide relationship, canvas dimensions, opaque literal RGB
background and declared order must agree. Source clipping, base decks and other
explicit image-sampling policies cannot be combined with this policy. Defaults
and the existing stroke adapter remain unchanged.

Existing limits remain: 128 MiB package expansion; 8 MiB native XML and generated
SVG; 32 MiB definition; 10,000 source objects; 4,096 commands per selected path;
200,000 selected commands; 32,768 pixels per grid axis; 16 million source or
individual target pixels; 64 Mi pixels across source and target grids. Requests
are checked before source-grid raster allocations.

The build freezes the request and binds all native shapes, complete mixed order,
sampling SVG, opaque grid PNG and target PNGs in
`artifact-path-prefix-preview.json`. Output review independently regenerates the
complete definition from the actual delivered PPT and immutable request, then
checks each application/order receipt. New hashes do not authorize substituting
source pixels, a different active slide, a changed grid or a false receipt.
Actual source-size visual inspection and user acceptance remain separate.
