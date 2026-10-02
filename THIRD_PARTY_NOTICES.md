# Third-party and asset boundaries

The MIT license covers this repository's independently written code and authored synthetic fixtures. It does not grant rights to user inputs, paper figures, fonts, PowerPoint templates, or external runtimes.

The workflow was inspired by image-to-editable-slide tools, including cell_su7. No upstream cell_su7 implementation or service is bundled. This project performs recognition through the calling model rather than a paid recognition API.

The PPT backend uses user-supplied `@oai/artifact-tool` and Codex Presentations validation tools. Those packages are not redistributed and a public npm installation is not assumed. `@napi-rs/canvas` and `sharp` are also loaded from the supplied runtime. Python dependencies are installed separately from their package registries.

Font files must be provided by the user under suitable permissions. Microsoft YaHei and research-paper images used in local integration checks are excluded from Git. Generated local receipts may contain filesystem paths and should be reviewed before sharing.
