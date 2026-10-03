# Changelog

## 0.3.0

- Add optional OpenCV crop refinement within an explicitly selected source-pixel ROI; proposals retain source hashes and disconnected small content, without modifying images or manifests.
- Add diagnostic translation registration and unaligned global / stable-object edge metrics to actual PPT comparisons; never warp output to improve scores.
- Provide standalone refine-crop and diagnose CLI commands, exclusive diagnostic output publication, and configured-runtime vision capability reporting.
- Keep manifest v1, original-byte native crops, content-bound review and existing PPT mapping compatible; run core and vision dependency variants in CI.

## 0.2.0

- Split the host-recognition and native-PPT workflow into a self-contained skill with no AutoSlides checkout dependency.
- Configure fonts explicitly, check runtime capabilities before authoring, and reject measured text overflow.
- Bind review to content and revision; preserve validated snapshots and allocate build runs safely.
- Publish validated output exclusively using staged files; strengthen XML text, crop quantization, image-frame and rotated-group checks.
- Keep portable synthetic tests and CI separate from local integration assets and the user-provided PPT runtime.
