# Sampling a native image with an attached Matte mask

Use `extract_pdf_images(..., native_occurrence_rendering=True, allow_native_matte_sampling=True)` only when retaining an actual source bitmap occurrence. This explicit option does not reconstruct vector diagrams from a bitmap or authorize rasterizing independent text, paths or other images.

```python
from figure_rebuild.pdf_images import extract_pdf_images

image, = extract_pdf_images(
    source_pdf,
    page=4,
    region=[48, 65, 548, 197],
    source_transform=[[2, 0, -96], [0, 2, -130]],
    image_indices=[0],       # occurrence index, not a candidate resource xref
    native_occurrence_rendering=True,
    allow_native_matte_sampling=True,
)
```

This admits only a native 8-bit DeviceRGB image with an attached same-size 8-bit mask and no image/mask Decode changes. Nested masks, resampled masks, unsupported colorspaces, external masks, patterns, nonunit draw alpha and unsupported group effects remain rejected. The default decoded-image and default native-rendering modes still reject Matte. RGB group sampling is a separate opt-in with its own restrictions; this flag does not enable it.

MuPDF represents an attached Matte through the actual image's `use_colorkey` plus mask handle. Its decoder reverses preblending, while the PDF interpreter retains the actual image-mask clip callback. See the primary [image decoder](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/fitz/image.c) and [PDF image interpreter](https://github.com/ArtifexSoftware/mupdf/blob/1.28.2/source/pdf/pdf-op-run.c). The sampling helper forwards those original handles, matrices, clips and supported context callbacks unchanged. It never decodes, manually unblends or merges alpha before drawing. Resource lookup by an image-info content digest is not a mask identity proof.

Metadata extraction and drawing use separate source documents. Each occurrence starts from fresh immutable PDF bytes. Image/mask receipt decoding occurs only after every draw device closes and the sampled PNG is encoded. The original bound mask must have the same handle, transform and paint sequence as its native clip callback. The complete type/bbox paint sequence and a clean replay cookie are checked; callback failures, replay errors, aborts and incomplete runs fail without a returned asset.

The receipt records the actual native Matte combination, unchanged image/mask forwarding, absence of manual alpha recomposition, actual mask digest, complete source transform, clips, native group context and sample grid. It does not infer the original declaration's unclamped Matte values from runtime metadata. It explicitly provides no RGB/alpha error bound and no exact decomposition claim.

The storage frame is rounded outward to integer source pixels and sampled at the explicitly requested scale, default 8×. This is grid spacing, not a fidelity bound. The complete image matrix determines placement before clipping; never stretch the full bitmap into its clipped bbox. Ordinary text, vector axes and labels remain separate editable objects. Final PPT image bytes, crop/frame and object order must be read back, followed by actual whole-figure 1×/2× and matched-pitch source/output 4× review.

Tests compare black and white Matte results byte-for-byte with an independent native whole-page renderer at the same grid, check alpha is not multiplied twice, verify ROI/padding and repeated occurrences, exclude independent overlay paints, and reject Decode changes, mask resampling, CMYK, unsupported effects and incomplete replay.
