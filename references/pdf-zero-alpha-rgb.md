# Separate native-PDF zero-alpha RGB previews

`build --preview-backend libreoffice-pdf-rgb` explicitly selects version 2 of
native PDF preview provenance. It preserves the finalized PPTX, original media,
raw LibreOffice PDF and all three direct PNG exports. Canonical 1x/2x/4x previews
sample a separately named `native-preview/derived-pdf/zero-alpha-rgb-white.pdf`.
The default backend and the unmodified `libreoffice-pdf` version-1 contract stay
unchanged. Device hairlines still require the version-1 `libreoffice-pdf` backend.

The fixed policy `pdf-zero-alpha-rgb-white-v1` supports only bounded, plain
8-bit DeviceRGB images and matching 8-bit DeviceGray soft masks with identity or
inverse mask Decode. Existing Matte, predictors, color profiles, other encodings,
nondefault RGB Decode and unsupported shared parents remain untouched. At a
decoded alpha of exactly zero, hidden RGB becomes white. All partial/opaque RGB,
every alpha sample, intrinsic dimensions, image dictionaries, placement, fonts,
text and paths remain unchanged. No Matte is introduced. It does not quantize
partial alpha or change the binary-alpha policy.

For every decoded sample and background, `(C_derived - C_original) * alpha = 0`.
This is a pointwise premultiplied equivalence proof. Separately filtering RGB and
alpha can still produce different displayed pixels. No RGB/alpha filtering error
bound, source pixel equality, automatic semantic recognition or PowerPoint/WPS
acceptance follows from this proof. Inspect actual previews before accepting a
candidate, including both photos and transparent decorative images in the same
figure. An improved whole-image average cannot establish per-object fidelity.

The receipt binds both PDF files, exact mask/RGB samples and fixed resource
limits. Independent review replays the transformation and compares every PDF
object and all unrelated encoded streams, including soft masks. Only the changed
RGB stream's Filter and Length can change; save may inline Length references and
update volatile trailer ID/DocChecksum. A no-op preserves every raw byte. Rebound
hashes cannot bless altered visible RGB, alpha, page geometry, fonts or paths.
The render audit also binds the actual derived PDF sampled at each physical
page-to-canvas ratio. Delivery cross-binds the raw PDF, derived PDF and receipt.

The BLIP diagnostic demonstrated why a separate PDF operation is needed:
normalizing hidden RGB in input PNG assets survived native PPT media embedding,
but LibreOffice's PDF export returned it to zero. That input-PNG candidate was
retained as a failed experiment. The raw PDF had preserved alpha, including
partial alpha; changing only zero-alpha RGB in a separate PDF restored the filled
snowflake in the diagnostic. Each actual build still requires complete readback
and visual review before a ledger issue can close.
