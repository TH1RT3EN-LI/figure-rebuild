# Explicit native RGB group sampling

`extract_pdf_images(..., native_occurrence_rendering=True,
allow_native_rgb_group_sampling=True)` enables a bounded approximation for
embedded image occurrences inside RGB transparency groups. Both flags default
to `False`; the new flag requires native occurrence rendering. Image rotation
or shear still requires `allow_affine_rasterization=True` independently.

This mode forwards the original native image handle, its attached soft mask,
clips, colorspace, color parameters, and group callbacks to MuPDF. It does not
replace an ICC colorspace with DeviceRGB or claim that their profiles are
equivalent. Independent vector, text, shading, and other image paints remain
separate. The returned asset is sampled raster content and is not an editable
author vector.

## Admission and identity

The additional group support is limited to a neutral full-page root and at most
one child. The root satisfies the existing strict rule: its original colorspace
is absent or pointer-identical to native DeviceRGB. The child must be an actual
native RGB type with three components, Normal blend, unit alpha, no knockout,
and finite bounds. It may be isolated or nonisolated and may use an ICC RGB
profile. A name containing “RGB”, or three components alone, is insufficient;
Lab and CMYK groups are rejected. The native default RGB colorspace must still
be pointer-identical to DeviceRGB both at group entry and at the selected image
paint: an inner Form can change defaults without opening another group. Deeper nesting, external masks, pattern
contexts, image draw opacity, overprint, Matte, unmatched mask callbacks, and
the other existing unsupported cases remain rejected. A selected unsupported
occurrence fails even when it lies outside the requested region.

The paint-time DefaultRGB guard also applies to the existing strict native
occurrence mode. This closes a prior guard gap without admitting a new
colorspace in that mode; a supported group cannot authorize a later inner Form
to change DefaultRGB outside the selected image's supported context.

The complete native paint type/bbox sequence must match the source bboxlog.
Selection binds the actual image paint, transform, decoded digest and mask
handle; the xref reported by image-info remains a candidate, never an occurrence
identity. Identical RGB data with different masks stay distinct. Each active
group's complete begin/end paint interval and all paint-kind counts are recorded,
including nested paints and independent paints omitted from the selected asset.

The receipt includes actual colorspace name/type/components, native profile MD5,
default RGB identity/profile digest at group entry and image paint, PyMuPDF/MuPDF versions, source PDF SHA-256
and page, selected paint sequence, mask and clip context, and asset hash. Native
profile MD5 is runtime evidence, not an ICC equivalence proof; the full source
bytes are bound by SHA-256. No reference profile allowlist is used.

## Accuracy and review

Eight samples per source pixel describe grid spacing only. The outward integer
frame can add less than one source pixel of transparent padding per edge; the
actual native ROI clip remains applied. Neither statement bounds RGB or alpha
error.

Rendering individual images separately can change a shared group's compositing
result, including color conversion and alpha rounding. Keeping the original
group callback does not prove that recomposing its pieces equals rendering the
original group once. Therefore every enabled result records
`required_full_figure_visual_review: true`, `rgb_alpha_error_bound: null` and
`exact_group_decomposition_claimed: false`. Groups with independent paints also
record `shared_group_split_unverified: true`. These fields are mandatory even
when a small test image happens to compare identically. The caller must retain
this uncertainty and review the reconstructed full figure; extraction success
does not close a fidelity issue or approve a PowerPoint output.

Real PDF tests cover Pillow-generated sRGB ICC groups shared by images, vectors
and text; unchanged native profile callbacks; distinct soft masks sharing RGB;
independent native-page references; rejected three-component Lab / CMYK,
knockout, blend, opacity, excess depth and non-neutral root; missing native APIs;
and tampered paint identity. Sampling results should be assessed using both
alpha and premultiplied RGB, since straight RGB at near-zero alpha can exaggerate
visible differences. No measured fixture maximum is a general error bound.

## Native image rendering state

Metadata extraction and native pixel rendering use separate PDF documents. Native occurrence rendering opens a fresh document from the original source bytes for every selected occurrence, preserving actual paint-order/resource checks while avoiding cache state introduced by SVG export, text dictionaries, image-hash queries, or a prior occurrence's receipt decoding. The native image handle is forwarded before decoded pixels are requested for audit: receipt image/mask decoding runs only after native drawing is closed and the PNG is encoded. A receipt-decoding error rejects the occurrence.

`provenance.native_image.render_document_state` is `fresh_source_bytes_per_occurrence`; `metadata_and_render_documents_separated` is true; `pixel_metadata_capture_phase` is `after_all_native_draw_devices_closed_and_png_encoded`. These describe the public `extract_pdf_images` native path. Direct low-level callers of `render_native_pdf_image` must provide a fresh PDF document themselves.

This addresses a reproduced clipped/interpolated-image state effect in PyMuPDF 1.28.2. It does not change source content or native colorspace/group/attached-mask policies. Eight samples per source pixel remains a sampling pitch, not a color or alpha error bound. Shared-group decomposition still needs full-figure source/PPT review.
