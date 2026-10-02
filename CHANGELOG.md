# Changelog

## 0.2.0

- Split the host-recognition and native-PPT workflow into a self-contained skill with no AutoSlides checkout dependency.
- Configure fonts explicitly, check runtime capabilities before authoring, and reject measured text overflow.
- Bind review to content and revision; preserve validated snapshots and allocate build runs safely.
- Publish validated output exclusively using staged files; strengthen XML text, crop quantization, image-frame and rotated-group checks.
- Keep portable synthetic tests and CI separate from local integration assets and the user-provided PPT runtime.
