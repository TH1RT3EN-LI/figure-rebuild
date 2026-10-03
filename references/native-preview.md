# Optional LibreOffice preview

`build --preview-backend libreoffice` renders the **final validated PPTX** through LibreOffice Impress **direct PNG export**, at explicit 1x, 2x and 4x pixel dimensions (96, 192 and 384 pixels per inch of slide geometry). A separate lossless PDF export supplies font and image evidence; PyMuPDF inspects that PDF and does not generate the raw previews. Artifact remains the authoring backend and the default preview backend. This option currently supports new single-slide figures; `--base` is rejected explicitly. It does not install LibreOffice, alter the PPT geometry, or save a LibreOffice roundtrip PPT.

Supply LibreOffice, Fontconfig's `fc-match`, and PyMuPDF in the configured Python environment. PyMuPDF is available through the package's optional `source` extra. Keep the existing Artifact runtime and presentation validators configured.

An external native runtime profile is a JSON object:

```json
{
  "command": ["/absolute/path/to/soffice"],
  "fc_match": "/usr/bin/fc-match",
  "environment": {},
  "timeout_seconds": 120
}
```

`command` is an argv prefix, never a shell expression. A portable LibreOffice installation may need its own `-env:BRAND_BASE_DIR=file:///...` argument and child-only `LD_LIBRARY_PATH` environment override. Use paths supplied by the caller. The adapter owns the unique `UserInstallation`, output directory and conversion arguments; do not put them in the prefix. The profile rejects unknown fields, invalid executables, protected font/profile environment overrides, and invalid timeouts.

Add `--native-preview-profile /absolute/path/native-preview.json` to the normal `configure` command. Configuration is stored in the external runtime JSON as `native_preview`. Select the backend explicitly for each build:

```sh
figure-rebuild build --manifest /absolute/job/manifest.json \
  --output /absolute/output/figure.pptx --preview-backend libreoffice
```

Omitting `--preview-backend` retains Artifact. Unknown values, including `auto`, are rejected. A requested native renderer failure does not fall back to Artifact. Configure validates the optional executables; later Artifact builds do not require those optional executable files to remain installed. New runs record the explicit backend and provenance version; existing frozen runs/reviews keep their previous binding contract.

## What is checked

- Only `validated-output/reconstruction.pptx` is converted. Its hash must remain unchanged through all PDF and direct PNG exports. The delivered PPT is the same validated input, not a renderer re-export.
- Each export has its own LibreOffice profile and output directory; each run has its own Fontconfig file/cache and font copies. Fontconfig sees only the already audited renderer font bytes. Every requested family/style must resolve to the exact copied file and SHA256 with face index0; PDF font resources must identify registered PostScript font names. Missing capabilities or substituted faces fail the build. This does not install global fonts or change the parent process environment.
- PDF filter options explicitly enable `UseLosslessCompression` and disable `ReduceImageResolution`, with boolean JSON values. Hidden slides are included and notes excluded, but only one-slide native builds are supported. The PDF must contain one unencrypted page matching the final PPT dimensions.
- Every direct PNG export requests typed `PixelWidth`/`PixelHeight` long values from the final PPT dimensions, opaque output and lossless PNG compression. Actual dimensions must match; there is no resizing or PDF rasterization. Original exported PNG files remain in the run and canonical raw previews copy their exact bytes. PDF page size can differ by up to 0.02 pt because of Impress rounding; that page size cannot affect the direct PNG dimensions. Smooth 1x is separately identified as a Lanczos reduction of 4x; it is not raw 1x evidence.
- The audit records source PPT media sizes/hashes and actual PDF image filters, stream hashes, pixel dimensions, placement transforms and effective DPI. A new DCT/JPX compressed image stream is rejected unless it is byte-identical to an original JPEG/JPX source asset. Alpha-mask encoding is also recorded. These facts do not automatically establish a one-to-one correspondence between every source asset and renderer-created image.
- `render-audit.json` binds final PPT, preview PNGs, separate evidence PDF, original direct PNG exports, exact successful commands/version output, Fontconfig and copied font evidence. It also binds a snapshot of the invoked command entrypoint's bytes; an entrypoint such as the `soffice` shell wrapper is not the entire native engine/shared-library installation. Delivery binds the audit hash and backend. `review-output` checks their identity, dimensions, native page identity, actual PDF fonts and PDF image evidence before accepting caller-supplied observations. Font resolutions must match registered faces one-to-one. Each scale is cross-bound to its exact `impress_png_Export` command, final PPT, isolated font environment, requested dimensions and original exported PNG SHA. PDF and direct PNG exports are distinct render paths and must not be relabeled as each other. Smooth1x has an explicit derivation bound to the4x SHA, Lanczos3 kernel and target dimensions, and is marked as a viewing aid rather than a raw preview.

## Interpretation and limitations

Native preview means **LibreOffice headless direct PNG-export appearance**. It does not establish Microsoft PowerPoint/WPS equivalence, interactive editability, semantic fidelity or user acceptance. A completed build leaves visual acceptance pending and native application playback unverified. Existing `review-output` rules still require a performed inspection and separate evidence for a native-application verification claim.

Actual round/square line caps and round/bevel joins can differ from Artifact2.8.59 previews. LibreOffice also has its own observed differences, including omitted join defaults, miter limits, text spacing and fine image sampling. Direct PNG can avoid gray outlines observed in the PDF rendering chain; it does not imply that every interior tile or fine detail matches the source. All such differences remain subject to visual review against the source. Do not relabel native output as an Artifact preview, suppress an observed discrepancy, or infer one-pass perfection from a successful conversion.

For a rendering discrepancy, first bind and inspect the final PPT's actual native fields; compare raw Artifact, native direct PNG, separately labeled PDF-export evidence and any supersampled viewing aid under their true backend names. A supported-looking API property is not evidence that a renderer paints it. Keep minimal open-path, closed-path, font and high-DPI raster fixtures: a workaround that fixes caps can still drop a closed edge or alter image sampling.
