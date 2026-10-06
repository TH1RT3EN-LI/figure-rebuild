# Actual native path SVG previews

`build --preview-backend native-svg` renders an explicitly selected standalone
path-only PPT through MuPDF. It reads the final delivered slide's custom-path
coordinates, solid RGB fills, alpha, stroke width, caps, joins, miter limits and
paint order. Three fresh complete-canvas samples produce raw 1x, 2x and 4x
previews. The delivered editable PPT is unchanged. No original PDF or reference
image is an input to this renderer.

Support requires one actual active slide, integral canvas dimensions and a
flat list of visible custom paths. Each path has explicit move, line, cubic
and close commands, solid fill or no fill, and an explicit solid stroke or no
stroke. Compound fills use native even-odd interpretation. Source dashes and
arrowheads must already be expanded into solid native geometry. Live text,
pictures, groups, formulas, rotation, reflection, effects, gradients, source
canvas clipping and template insertion are rejected by this opt-in backend.

The definition records the actual PPT, slide XML, each native shape and the
complete generated SVG hashes. The receipt binds all three raw PNGs and the
PyMuPDF/MuPDF versions. Output review regenerates the SVG from the actual PPT
and manifest, then independently renders every raw PNG. Rebinding a changed
preview checksum does not pass that independent replay.

Existing package and XML limits remain. Paths allow at most 4096 commands each
and 200000 combined commands. The generated SVG contains only literal solid
paints and bounded leaf paths; external resources and executable elements are
rejected before decoding. A render surface allows 16 million pixels and 32768
pixels per axis; the three complete grids share a 64 Mi-pixel budget.

This preview procedure needs source comparisons at every scale. It does not
prove source pixel equivalence, original font identity, automatic semantic
structure or PowerPoint/WPS playback. Geometry and native editability remain
separate from raster appearance and must retain their own source evidence.
