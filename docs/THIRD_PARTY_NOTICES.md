# Third-party and asset boundaries

The MIT license covers this repository's independently written code and authored synthetic fixtures. It does not grant rights to user inputs, paper figures, fonts, PowerPoint templates, or external runtimes.

The workflow was inspired by image-to-editable-slide tools, including cell_su7. No upstream cell_su7 implementation or service is bundled. This project performs recognition through the calling model rather than a paid recognition API.

The PPT backend uses user-supplied `@oai/artifact-tool` and Codex Presentations validation tools. Those packages are not redistributed and a public npm installation is not assumed. `@napi-rs/canvas` and `sharp` are also loaded from the supplied runtime. Python dependencies are installed separately from their package registries.

Optional vision dependencies are NumPy and opencv-python-headless, installed separately through requirements/vision.txt. OpenCV's algorithms propose geometry within caller-selected regions; they do not perform scientific interpretation or call an external recognition service.

Font files must be provided by the user under suitable permissions. Font files and original paper PDFs used in integration checks are excluded from Git. README demonstration assets are listed below. Generated local receipts may contain filesystem paths and should be reviewed before sharing.

## SAM 2 curve regression geometry

The four `cases` in `tests/fixtures/cubic-transverse-native.json` contain
integer path commands decoded from a local reconstruction of Figure 11
(source PDF page 21) in **SAM 2: Segment Anything in Images and Videos**,
Nikhila Ravi et al. Source: [the authors' paper](https://arxiv.org/abs/2408.00714).
The fixture records the reconstruction PPT's SHA256, the four curve/line
command sequences, their expected re-encoded fragments, and a reduced
geometry example for regression checks. It does not include the paper,
complete figure, photographs, fonts, or slide assets. The cases and reduced
example are source-derived geometry, not authored synthetic cases.
The repository's MIT license does not grant rights to the original figure.

## Original PDF stroke regression geometry

The six cases in `tests/fixtures/sampled-native-strokes.json` are small original
PDF stroke excerpts: three circles and a rounded frame from figures 2/3 of
[GCC: Graph Contrastive Coding for Graph Neural Network Pre-Training](https://arxiv.org/abs/2006.09963),
and two curved dashed strokes from figure 1 of
[Maximum Flow and Minimum-Cost Flow in Almost-Linear Time](https://arxiv.org/abs/2203.00671).
The fixture records source PDF hashes, pages, native paint identities, actual
control points, stroke states and rectangular clipping callbacks. It contains
no complete figure, paper, image, font or slide asset. These are source-derived
geometry excerpts; the repository's MIT license does not grant rights to the
original paper figures.

## MambaVO reconstruction demonstration

`docs/assets/mambavo-figure1-original.png` is a crop of the original Figure 1.
`docs/assets/mambavo-figure1-rebuild.gif` shows its progressive reconstruction.
Both refer to
Figure 1 from **MambaVO: Deep Visual Odometry Based on Sequential Matching
Refinement and Training Smoothing**, Shuo Wang et al., CVPR 2025.
Source: [official CVPR paper](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_MambaVO_Deep_Visual_Odometry_Based_on_Sequential_Matching_Refinement_and_CVPR_2025_paper.html).

Geometry and ordinary text were reconstructed as native PowerPoint objects;
mathematical labels were re-typeset with LaTeX. The demonstration retains the
paper's photographs, feature/depth images, and gradient image assets. It is a
canvas-only recording at twice the original drawing speed. The repository's
MIT license does not grant rights to the original paper figure or retained
third-party imagery.

## Apache POI connector preset fixture

`tests/fixtures/connector-presets.xml` is an extracted subset of
Apache POI's `presetShapeDefinitions.xml`, licensed under Apache License 2.0.
The repository's MIT license does not replace the XML's third-party terms.
The original [Apache POI license](https://raw.githubusercontent.com/apache/poi/trunk/legal/LICENSE)
and [NOTICE](https://raw.githubusercontent.com/apache/poi/trunk/legal/NOTICE)
are reproduced in `connector-presets.LICENSE` and `connector-presets.NOTICE`
beside the fixture; the NOTICE includes Apache POI's upstream attribution text.

Source: [Apache POI preset shape definitions](https://raw.githubusercontent.com/apache/poi/trunk/poi/src/main/resources/org/apache/poi/sl/draw/geom/presetShapeDefinitions.xml).
The downloaded full source SHA256 is
`4a762444d8d85876881c02a5b1dedf6f73006fcd8acb7b4e393435615b37c780`.
Changes: retain only `straightConnector1`, `bentConnector2`, `bentConnector3`
and `bentConnector4`; add a local wrapper with source/digest/license metadata;
normalize namespace prefixes and XML serialization using Python ElementTree.
The preset guide equations and path commands remain unchanged. The fixture
header prominently records these changes. It supplies an independent native
route oracle in tests; no Apache POI runtime or other source code is bundled.
