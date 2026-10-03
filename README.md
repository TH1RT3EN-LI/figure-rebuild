<p align="center">
  <img src="docs/assets/logo.png" alt="Figure Rebuild logo" width="128" height="128">
</p>

<h1 align="center">Figure Rebuild</h1>

<p align="center">
  从参考图到可编辑的 PowerPoint。
</p>

<p align="center">
  <a href="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml"><img src="https://github.com/TH1RT3EN-LI/figure-rebuild/actions/workflows/core-tests.yml/badge.svg?branch=main" alt="Core checks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-0B6CC2?style=flat" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-0B6CC2?style=flat" alt="Python 3.10+">
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="docs/i18n/README.en.md">English</a> · <a href="docs/i18n/README.ko.md">한국어</a> · <a href="docs/i18n/README.es.md">Español</a>
</p>

Figure Rebuild 将论文方法图、流程图和机制示意图重绘为可编辑的 PowerPoint，保留参考图的文字、布局与连接关系。

项目以 Codex 开发和测试，提供本地命令行工具与 Codex skill。命令行接口也可用于接入其他智能体。

## 功能

| 功能 | 用法 |
| --- | --- |
| 导出独立 PPTX | `build --manifest manifest.json`，从审阅后的清单生成可编辑单页 |
| 生成时插入指定位置 | `build --manifest manifest.json --base base.pptx --slide-id ID --placement x y width height --output new-deck.pptx` |
| 生成后插入指定位置 | `insert --input figure.pptx --base base.pptx --slide-id ID --placement x y width height --output new-deck.pptx`，直接复用已生成的单页 PPTX |

两种插入方式都将整个源画布等比缩放并居中放入指定区域，文字、线宽同步缩放，保留原生对象的可编辑性、目标模板及其他页。区域比例不同时居中留空，不裁切内容。坐标使用 CSS 像素（96 px = 1 英寸）；目标页使用 `inspect-base` 查出的原生 slide ID。输出写入新文件，不覆盖原稿。

生成后的 `insert` 直接修改 PPTX 的 OOXML 包，无需配置 PPT 导出运行时。来源限本项目生成的受支持单页 PPTX，任意演示文稿、动画、图表和 OLE 等不在完整支持范围内。命令示例与边界见[插入使用指南](docs/usage.md#插入现有模板)。

## 效果预览

以 [MambaVO（CVPR 2025）](https://openaccess.thecvf.com/content/CVPR2025/html/Wang_MambaVO_Deep_Visual_Odometry_Based_on_Sequential_Matching_Refinement_and_CVPR_2025_paper.html) 的 Figure 1 为例。

**原图**

![MambaVO 论文 Figure 1 原图](docs/assets/mambavo-figure1-original.png)

**重绘过程**

![Figure 1 的画布逐步重绘过程](docs/assets/mambavo-figure1-rebuild.gif)

## 可编辑内容

| 内容 | 交付形式 |
| --- | --- |
| 图形与连线 | PowerPoint 原生对象 |
| 普通文字 | 独立文本框，可修改字体、字号和内容 |
| 数学公式 | LaTeX 生成的 SVG / 高清 PNG，修改源码后重新排版 |
| 照片与热图 | 保留图片，可移动和裁剪 |

支持导出独立 PPTX，也可将重绘结果插入现有演示文稿。

## 快速开始

需要 Python 3.10+。

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

在 Codex 中使用时，再安装 skill：

```bash
.venv/bin/python scripts/install.py
```

使用 `build` 生成 PPTX 前，还需[配置导出环境](docs/usage.md#配置和检查)：Node.js 20.9+、Codex Artifact Tool、Presentations 检查器及本地字体。对已生成的单页 PPTX 使用 `insert` 无需这套导出环境。

## 使用

`figure-rebuild` 命令提供素材准备、场景审阅和 PPTX 导出。图像理解与场景清单由使用者或调用方智能体完成，完整步骤见[使用指南](docs/usage.md)。

在 Codex 中，可附上参考图并调用 `$figure-rebuild`：

> 请用 $figure-rebuild 将这张图重绘成可编辑的 PPT。保留原图文字、布局和连线，公式用 LaTeX 重排，并提供预览供我核对。

输出包括 PPTX、导出预览和原图对照，也可继续调整局部内容。

## 项目文档

[使用指南](docs/usage.md) · [架构与目录](docs/architecture.md) · [贡献指南](.github/CONTRIBUTING.md) · [更新记录](docs/CHANGELOG.md)

## 许可

代码采用 [MIT](LICENSE) 许可。论文图示与其他素材的来源、许可见 [THIRD_PARTY_NOTICES](docs/THIRD_PARTY_NOTICES.md)。
