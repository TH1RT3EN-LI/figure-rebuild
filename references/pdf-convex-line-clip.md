# Exact fill clipping at convex line boundaries

`figure_rebuild.pdf_convex_clip.clip_fill_at_convex_line_boundaries` is an explicit geometry helper for one filled M/L/C/Z contour and a complete chain of convex linear clip paths. It does not infer PDF paint identity, font identity, context, colors, painter order or image ownership. The caller must bind those source facts independently.

```python
from figure_rebuild.pdf_convex_clip import clip_fill_at_convex_line_boundaries

result = clip_fill_at_convex_line_boundaries(
    source_root_commands,
    [{"commands": clip_root_commands, "fill_rule": "nonzero"}],
    fill_rule="nonzero",
)
exact_output_commands = result["commands"]  # coordinates are Fractions
receipt = result["provenance"]
```

All commands must already use the same root coordinate space. In particular, `SourcePaint.commands` from `extract_outlined_svg` already include the expanded instance transforms. Applying `SourcePaint.transform` to those commands again corrupts the geometry. Native glyph outlines instead start in glyph coordinates; compose their actual native instance matrix exactly once, and separately compose each actual clip's matrix. A parser paint index is not a native PDF paint sequence number.

The helper computes exact rational halfplane intersections of the supplied finite built-in numbers. Only straight segments may cross a clip plane. A cubic's complete control hull must lie inside or outside each plane; a mixed-side cubic raises `UnsupportedConvexClipError`. Retained cubic controls remain unchanged. Curves are never flattened, subdivided or intersected approximately. Missing explicit closure is treated as the implicit closure of a fill. Isolated move-only subpaths are separately counted as fill-empty; short lines or small contours are not removed by a tolerance.

Each clip has exactly one drawable linear contour, at least three nondegenerate edges, distinct vertices and nonzero area. Every vertex must lie in every oriented edge halfplane. Clockwise and counterclockwise clips are accepted; multiply-wound polygons and concave or curved clips are rejected. Both nonzero and evenodd clip rules are accepted only after this convex simple-polygon proof. Every clip is validated before an earlier empty intersection can terminate geometric work. The source supports one drawable contour; masks, strokes and compositing require separate authorization and cannot be represented by this API.

Source/output commands, clip count, clip commands, operation count and rational numerator/denominator bit sizes are bounded. Defaults are 4,096 source/output commands, 16 clips, 128 commands per clip, 200,000 operations and 2,048 bits. The same operation meter includes parsing, validation, clipping and normalized exact-coordinate digest encoding. Exhaustion or an unsupported input raises without partial output or input mutation.

The receipt records exact normalized geometry digests, input fill rules, per-plane line crossings and retained/excluded cubic hulls, empty move-only subpaths and budget use. An empty output proves the supplied fill intersection is geometrically empty. It does not prove raster equality, authorize deletion of a source paint with an unvalidated context, or establish its PDF identity. Conversion of Fractions into manifest binary64 and DrawingML integer coordinates must be recorded separately. The final native PPT still requires source/native readback and actual whole-figure 1×/2× plus matched-pitch 4× visual review.

The automatic `outline_paths` converter retains its existing conservative rejection of complex boundary intersections. This helper does not silently broaden that policy. A source-specific driver can use it only after checking the actual glyph program/GID, full native clip chain and neutral paint context, then preserve every other object and its relative order.
