# Explicit zero-alpha RGB derivation

`figure_rebuild.image_hidden_rgb.derive_zero_alpha_rgb_image` creates a separate
RGBA PNG with white RGB only where the original alpha is exactly zero. Keep
the original asset and save the returned receipt with the revised manifest.
This helper is explicitly invoked; the existing border-trim policies and
default image preparation retain their existing behavior.

All alpha bytes and every positive-alpha RGBA sample remain identical. Pixel
dimensions, PNG density metadata, frame, crop, rotation, paint and other object
properties remain unchanged. At each decoded pixel,
`(C_after - C_before) * alpha = 0`; pointwise alpha composition over any background
is exact. Hidden RGB can affect image minification, so this proof supplies no
RGB/alpha filtering or PDF appearance bound. Review actual exported media,
image arrays and matched-scale previews before selecting a candidate.

`verify_zero_alpha_rgb_image` independently reads the actual declared source
and derived assets and repeats the complete sample/property checks. A forged
receipt, a matching dimension or an updated file hash cannot substitute for
this verification. Keep the observed hidden RGB change explicit; these two
files are not byte-identical, and no source font or semantic identity follows.

The helper accepts single-frame RGBA PNGs with a nonempty visible support and
zero-alpha RGB that needs changing. Partial alpha, holes, disconnected support
and an existing crop or rotation are retained. Color profiles and ancillary
metadata other than density require separate preservation and are rejected.
Encoded input/output is limited to 64 million bytes, decoded extent to
8 million pixels and each axis to 32768 pixels. Destinations must be new PNG
assets confined to the declared root. These extent bounds do not certify every
internal decoder allocation or an application's image filter.
