# Original PDF paint context

`get_drawings()` is a geometry inventory, not a list of visible page objects.
It can include a black rectangle used only to define an alpha soft mask. The
rectangle's black RGB does not make it a black page fill. Conversely, a white
paint after `end_mask` is still masked until the corresponding `pop_clip`.
Ignoring either fact can introduce a border or erase other artwork.

Use the optional source dependency and the maintained context API before
converting geometry from an older PDF adapter:

```python
from figure_rebuild.pdf_paint_context import (
    inspect_pdf_paint_context, paint_context_record,
)

context = inspect_pdf_paint_context("source.pdf", page=3)
record = paint_context_record(context, 153, expected_kind="fill-path")
if record["role"] == "mask_definition":
    # Retain this source occurrence and its definition context in the audit.
    # It must not become an independent page object.
    pass
elif not record["page_object_allowed"]:
    # Keep a truthful unresolved item; do not emit this paint without its mask.
    unresolved.append(record)
else:
    # The caller must still implement original clipping/group/color semantics.
    convert_supported_geometry(record)
```

The PDF's exact opened bytes, one-based page and SHA256 are recorded. Every
native paint callback is bound to its original sequence by exact equality of
the **complete** paint type/bbox sequence against `get_bboxlog()`. An optional
`expected_bboxlog` must match the entire source page too. No closest rectangle,
resource-xref deduplication, or figure-specific whitelist establishes identity.
Repeated uses of one mask resource remain distinct occurrences.

Each paint has a role, active definition IDs, active mask IDs, clip/group IDs,
and explicit unresolved reasons. Definitions and subsequent mask use have
separate lifetimes. Nested definitions, masks used within an outer mask, and
image-mask clips are recorded. Pattern paints cannot pass as ordinary page
objects. Normal non-mask paints retain their source sequence and bounding box;
`normal` does not approve arbitrary transparency, clipping or color conversion.

This API intentionally implements **no mask reduction**, including an apparent
single opaque rectangular alpha mask. Such a reduction needs its own exact
geometry, opacity, clipping, group and compositing proof. Until supported,
masked content stays `active_mask_unsupported` and cannot be emitted as an
unmasked object. The primary source geometry workflow remains outlined SVG via
`pdf_source`; this module does not add another converter or CLI.

PyMuPDF is imported only when inspection is called. Required native APIs,
one-based pages, unrotated coordinates, finite context values and a fully
balanced callback stack are checked. Defaults limit source bytes to 128 MiB,
paints to 100,000, context events to 200,000, and combined context depth to 128.
Budget exhaustion or incomplete identity raises `PdfPaintContextError`; it
never returns a partial report marked verified. A source/context report is not
visual approval of a generated PPT.

For a freshly bound report, [bounded native clip visibility](pdf-visibility.md)
can separately certify that an active conservative clip support is strictly
outside the ROI. This preserves every original role, unresolved reason and mask
record. The version-checked local control hull, complete paint identity, clip
span and float32 interval transform are required; an approximate rectangle
predicate or an unbound page-space bbox cannot replace them.
