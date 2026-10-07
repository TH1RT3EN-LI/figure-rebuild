# Optional binary-alpha PDF derivation

Some separately rendered LibreOffice PDF exports show gray image-extent lines
that are absent from the same PPT's direct PNG exports. Lossless compression
alone does not prevent these lines. A controlled PDF made from the same PNG
reproduced the artifact; reversing the soft-mask encoding did not remove it.
The observations support separately filtered RGB and alpha as the cause for
the tested binary-mask cases. They do not diagnose every renderer or seam.

Request a separate derived PDF explicitly:

```sh
figure-rebuild build --manifest /absolute/job/manifest.json \
  --output /absolute/output/figure.pptx --preview-backend libreoffice \
  --pdf-alpha-derivation binary-alpha-white-matte-v1
```

The raw lossless PDF remains `native-preview/pdf/reconstruction.pdf`. The
derived document is `native-preview/derived-pdf/binary-alpha-white-matte.pdf`,
with an independently replayable `receipt.json` beside it. The final PPT,
its media and the direct PNG exports are unchanged by this derivation. No
derived PDF rasterization supplies or replaces a canonical raw PNG preview.
Omitting the flag retains the existing export behavior. Artifact builds and
unknown policies are rejected before a build run is allocated.

## Exact sample domain

The policy admits direct 8-bit DeviceRGB image XObjects and direct 8-bit
DeviceGray soft masks of identical dimensions. Every direct owner of a shared
mask must qualify, including unused owners. RGB Decode must be the default;
mask Decode may be `[0 1]` or `[1 0]`. Streams must be raw or one unpredicted
Flate stream. Existing Matte, nonbinary alpha, constant alpha, other color
spaces, encodings and unsupported parent contexts are retained with reasons.
Forms/signatures and encrypted inputs are rejected.

For alpha 1, the original decoded RGB samples are retained. For alpha 0,
decoded RGB is replaced by exact white samples, and the mask declares
`Matte [1 1 1]`. All encoded mask bytes and Decode values stay unchanged.
The PDF preblending equation is `c' = m + alpha * (c - m)`, applied after
Filter and Decode. With white matte, those two binary endpoints require no
rounding and preserve every decoded premultiplied RGBA sample. The definition
and matching-dimension restrictions are in Adobe's
[PDF Reference 1.6, section 7.5.4, pages 522–524](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.6.pdf).

This is an exact discrete sample proof. It is **not** an RGB/alpha filtering
error bound, a whole-image appearance proof, or PowerPoint/WPS verification.
Half-alpha samples are not rounded into this policy. Review the resulting
PDF at 1x/2x and critical 4x regions under its real derivation name. A retained
mask or residual subdivision keeps its associated finding open.

## Replay and budgets

The verifier rebuilds the plan from the pristine raw PDF and checks every
derived RGB sample, mask stream, image dictionary, unrelated PDF object and
encoded stream. It also checks scalar/array objects, page content, fonts and
trailer fields. Rebinding a changed derived PDF's hash cannot bypass replay.
RGB stream Filter/Length serialization may change; an explicit null
DecodeParms is preserved even when a tiny stream remains uncompressed.
Indirect Length values may be inlined while retaining their effective byte
length. Only volatile trailer ID and the stale LibreOffice DocChecksum are
excluded from unrelated-field equality. The source file itself is never
overwritten; failed candidate files remain available for diagnosis.

Default ceilings are 64,000,000 input bytes, 10,000 objects, 2,048 images,
32,000,000 pixels per eligible image, and 256,000,000 charged decoded work
bytes. Count source RGB, mutable candidate and immutable RGB copy for every
shared owner before decoding. Flate expansion must exactly fit the declared extent, with no
trailing stream data. Overrides can only lower these positive integer limits;
booleans are rejected. These are file/operation/buffer limits, not a bound on
all memory used inside PyMuPDF's native parser.

The immutable config, render audit, delivery and output review all bind the
requested policy, raw PDF, derived PDF and replay receipt. Counts retain exact
JSON integer types. A derived declaration or evidence pair added to an
unrequested Artifact build, or a derived entry added to a legacy delivery, is
rejected. Model inspection and user acceptance remain separate.
