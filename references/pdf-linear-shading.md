# Source-bound linear PDF shading

`figure_rebuild.pdf_shading.extract_linear_axial_shading` recovers one strictly supported axial shading occurrence as an editable path with a continuous native gradient. It retains the original clip curves and paint identity. It does not reconstruct a gradient from sampled colors or quantized raster contours.

```python
from figure_rebuild.pdf_shading import extract_linear_axial_shading

result = extract_linear_axial_shading(
    pdf_path,
    page=2,                 # one-based source page
    paint_seqno=13,          # native get_bboxlog index, not SVG paint order
    resource_xref=203,
    resource_kind="pattern", # "shading" for a standalone Shading resource
    region=[101, 63, 511, 241],
    source_transform=[2, 0, 0, 2, -202, -126],
)
object_record = result["object"]
source_receipt = result["provenance"]
```

The selected `fill_shade` callback must contain the exact native pointer loaded from the claimed resource. The whole source paint type/order/bbox sequence is replayed and compared independently. A type-2 Pattern must be loaded as the whole Pattern resource: loading its nested Shading dictionary instead changes native identity and loses the Pattern matrix. The receipt records the parent resource, nested shading and function dictionary hashes, source PDF SHA, actual Pattern matrix, actual occurrence CTM, clip chain and caller transform. The source PDF is read without modification. A direct nested shading has no separate `shading_xref`; that field is `null`.

Supported functions are a single direct or indirect type-2 dictionary, unit Domain, exponent `N=1`, and explicit DeviceRGB. C0/C1 have three components in [0,1], with their PDF defaults when absent. Range is absent or the full unit RGB range. A type-2 function stream, nonlinear exponent, other colorspace, non-unit Domain, range clipping, explicit shading BBox/background and AntiAlias=true are unsupported. A type-2 Pattern may supply a finite nonsingular Matrix but no ExtGState. Actual native colorspace identity and unchanged device default colorspaces are verified; conversion of both RGB endpoints must be identity.

Geometry and context use the same conservative subset as [constant shading](pdf-constant-shading.md): exact rectangular clip intersections and at most one wholly contained complex nonzero clip, with all original M/L/C/Z commands and cubic controls retained. Complex intersections, complex evenodd clips, masks, transparency groups, pattern tiles, overprint and non-unit alpha fail closed. Source bytes, paints, commands, context events and depth are bounded. Callback errors abort replay. Both Extend flags are retained; an unextended end requires containment of the complete clip control hull inside that end's domain.

The gradient field is derived from the inverse of the actual occurrence CTM and resource matrix, followed by the inverse caller transform. This preserves the gradient covector under affine transforms. The final covector must be exactly horizontal or vertical; a tiny nonzero shear is not rounded into this subset. Cardinal angles have no native angle quantization error. The axis spans the actual authored path frame, including cubic controls. Clamp extensions become additional stops at their source-derived positions.

DrawingML encodes colors as 8-bit RGB and stop positions in 1/100000 units. This API explicitly allows that encoding. It does not claim exact color equality. Stops are rounded at those precisions; coincident positions merge only when their encoded colors are identical. Conflicting colors at one position fail closed. The maximum component error over the complete continuous field is proved with exact rationals at the union of source clamp points and encoded stop positions: both fields are piecewise linear, so extrema occur at those knots. An error exceeding the fixed 1/255 component budget is rejected. The receipt records all original stop positions, merges, proof knots and the exact maximum error.

The bound covers the source-native RGB field versus the encoded gradient. It excludes DrawingML geometry quantization and renderer differences. The actual final PPT requires native fill/readback verification and matched-pitch 1×/2×/4× visual review. Source corner antialiasing or absent sampled pixels are not evidence that a short contour can be deleted. The returned z_index remains in native PDF paint order; a driver using another order must bind the two order spaces explicitly.

Regression tests cover Pattern versus nested-shading identity, repeated occurrences, original clip curves, transformed gradient phase, finite extension, decimal RGB encoding, near-endpoint stop merges, tiny shear rejection, unsupported functions/context and an independently rendered PDF comparison.
