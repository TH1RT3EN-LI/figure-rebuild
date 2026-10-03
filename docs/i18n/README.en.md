<p align="center">
  <img src="../assets/logo.png" alt="Figure Rebuild logo" width="128" height="128">
</p>

<h1 align="center">Figure Rebuild</h1>

<p align="center">
  From reference figures to editable PowerPoint slides.
</p>

<p align="center">
  <a href="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml"><img src="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml/badge.svg?branch=main" alt="Core checks"></a>
  <!-- Official count badge when install statistics become available: https://skills.sh/b/TH1RT3EN-LI/figure-rebuild -->
  <a href="https://skills.sh/th1rt3en-li/figure-rebuild"><img src="https://img.shields.io/badge/skills.sh-install-111111" alt="Install with skills.sh"></a>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/License-MIT-0B6CC2?style=flat" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-0B6CC2?style=flat" alt="Python 3.10+">
</p>

<p align="center">
  <a href="../../README.md">简体中文</a> · <strong>English</strong> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a>
</p>

Figure Rebuild recreates method diagrams, flowcharts, and scientific schematics from research papers as editable PowerPoint slides, preserving the reference figure's text, layout, and connections.

Developed and tested with Codex, the project provides a local CLI and a Codex skill. The CLI also supports integration with other agents.

## Install the skill

Run in your target project:

```bash
npx skills add TH1RT3EN-LI/figure-rebuild
```

Choose Codex when prompted. To use it across projects, run `npx skills add TH1RT3EN-LI/figure-rebuild --agent codex --global`. This installs the skill, scripts, and references; configure Python dependencies and the PPT export environment using the [usage guide](../usage.md#安装).

## Preview

Figure 1 from [MambaVO (CVPR 2025)](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_MambaVO_Deep_Visual_Odometry_Based_on_Sequential_Matching_Refinement_and_CVPR_2025_paper.html).

**Original figure**

![Original Figure 1 from the MambaVO paper](../assets/mambavo-figure1-original.png)

**Rebuild process**

![Figure 1 being rebuilt on the canvas step by step](../assets/mambavo-figure1-rebuild.gif)

## Editable content

| Content | Output |
| --- | --- |
| Shapes and connections | Native PowerPoint objects |
| Plain text | Separate text boxes with editable fonts, sizes, and content |
| Math formulas | SVG / high-resolution PNG generated with LaTeX; edit the source and render again |
| Photos and heatmaps | Images you can move and crop |

Export a standalone PPTX or insert the rebuilt figure into an existing presentation.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

For an existing source checkout, you can also register it as a Codex skill with the local installer:

```bash
.venv/bin/python scripts/install.py
```

Before exporting PPTX, [configure the export environment](../usage.md#配置和检查): Node.js 20.9+, Codex Artifact Tool, the Presentations validators, and local fonts.

## Usage

The `figure-rebuild` command prepares assets, reviews scenes, and exports PPTX. Image interpretation and the scene manifest are supplied by the user or the calling agent. See the [usage guide](../usage.md) for the full workflow.

In Codex, attach a reference figure and invoke `$figure-rebuild`:

> Use $figure-rebuild to recreate this figure as an editable PPT. Preserve the original text, layout, and connections, render the formulas with LaTeX, and provide a preview for review.

Outputs include the PPTX, an exported preview, and a comparison with the original. You can continue editing individual elements.

## License

Code is licensed under [MIT](../../LICENSE). Sources and licenses for paper figures and other assets are listed in [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md).
