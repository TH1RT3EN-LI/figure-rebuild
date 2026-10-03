# Constant axial PDF shading

`figure_rebuild.pdf_shading.extract_constant_axial_shading` converts a strictly supported, mathematically constant PDF axial shading occurrence into one editable solid path. It does not treat a PDF `fill-shade` as an embedded image or omit white/low-coverage paint.

```python
from figure_rebuild.pdf_shading import extract_constant_axial_shading

result = extract_constant_axial_shading(
    pdf_path,
    page=21,                 # one-based PDF page
    paint_seqno=724,          # original native get_bboxlog sequence
    shading_xref=2020,       # checked against the actual native shade pointer
    region=[68, 100, 545, 557],
    source_transform=[[3, 0, -204], [0, 3, -300]],
)
object_record = result['object']
source_receipt = result['provenance']
```

The xref is a claim, not an identity shortcut: the actual selected `fill_shade` callback must carry the same native resource pointer as `pdf_load_shading`, and the complete page paint type/order/bbox sequence must match. Original resource/function dictionaries, decoded and encoded function stream hashes, source SHA, alpha, native CTM, color parameters, all original path clips, caller transform and region are recorded.

The returned `z_index` belongs to the **native PDF paint sequence**. A driver using SVG paint order must explicitly bind the two index spaces before assigning its own order; it must not insert native index 724 as if it were SVG index 724. Source SVG image serializations of shading remain separate source evidence.

Supported shading is type 2 with a single indirect type-0 sampled function, one input dimension, 8-bit samples, interpolation order 1, matching increasing function/shading Domain, and sample Encode inside the grid. Every raw sample vector must be identical. Decode and Range are applied explicitly. Streams are unfiltered or FlateDecode, with a bounded decoded length and no DecodeParms; other forms remain unsupported. Both Extend flags are preserved. With finite extension, every clipping Bézier control point must project within the corresponding axial domain; this conservatively proves the entire filled control hull is inside it. Degenerate axes, explicit shading BBox/background and AntiAlias=true are rejected.

ColorSpace must explicitly name DeviceGray, DeviceRGB or DeviceCMYK. Actual native colorspace identity and unchanged device default colorspaces are required. Native color conversion uses the original rendering intent/black-point parameters. Only a constant color representable as 8-bit RGB within native floating-point roundoff is accepted; this is not an approximation of a varying gradient. Zero CMYK converts to exact `[1,1,1]` RGB. Draw alpha must equal one; groups, external masks, pattern scopes, overprint and default-colorspace substitution are rejected.

Geometry is a finite source-page ROI intersected with original native path clips. Axis-aligned rectangular intersections are exact. At most one complex nonzero clip is accepted, and all of its original M/L/C/Z control points must lie inside all remaining rectangular clips and the ROI. Its original cubic curves and all subpaths are preserved; implicit fill closure becomes explicit path closure. Arbitrary complex intersections, evenodd complex paths, stroked/text/image-mask clips and empty intersections fail closed. No curve flattening, raster boundary, bounding-box substitute or small-contour deletion is used.

The source transform may be any finite nonsingular affine mapping, including reflection. The page must be unrotated and the ROI wholly within the PDF page. Limits apply to source bytes, sampled function bytes, native paint count, path commands, context events and stack depth. All context callback errors abort the native replay and prevent output. PyMuPDF is imported only when the function is called; required native APIs are checked. The implementation is tested with PyMuPDF 1.28.2, not a claim that every version in the optional source dependency range has those APIs.

Output target coordinates are converted once from exact rationals to binary64 floats. The receipt gives the maximum coordinate-conversion roundoff as an exact rational in target units. That conversion bound does not certify upstream native PDF parsing, DrawingML coordinate quantization or raster antialiasing.

The result establishes source-level lowering, not final PowerPoint display equivalence. DrawingML coordinate quantization and renderer antialiasing still require actual output review. In particular, source slivers invisible at 1×/2×/4× must remain present; zero sampled pixels do not prove an empty geometric region.
