# Third-party and asset boundaries

The MIT license covers this repository's independently written code and authored synthetic fixtures. It does not grant rights to user inputs, paper figures, fonts, PowerPoint templates, or external runtimes.

The workflow was inspired by image-to-editable-slide tools, including cell_su7. No upstream cell_su7 implementation or service is bundled. This project performs recognition through the calling model rather than a paid recognition API.

The PPT backend uses user-supplied `@oai/artifact-tool` and Codex Presentations validation tools. Those packages are not redistributed and a public npm installation is not assumed. `@napi-rs/canvas` and `sharp` are also loaded from the supplied runtime. Python dependencies are installed separately from their package registries.

Optional vision dependencies are NumPy and opencv-python-headless, installed separately through requirements-vision.txt. OpenCV's algorithms propose geometry within caller-selected regions; they do not perform scientific interpretation or call an external recognition service.

Font files must be provided by the user under suitable permissions. Microsoft YaHei and research-paper images used in local integration checks are excluded from Git. Generated local receipts may contain filesystem paths and should be reviewed before sharing.

`tests/figure_rebuild/fixtures/connector-presets.xml` is an extracted subset of
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
