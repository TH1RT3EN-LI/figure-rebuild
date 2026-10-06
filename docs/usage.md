# 使用指南

以下命令均在仓库根目录执行。项目介绍和最短开始方式见 [README](../README.md)。

从方法说明创作或把参考图复建为可编辑 PowerPoint：几何是原生路径，普通文字是独立文本框，照片保留来源审计；重绘公式使用带 LaTeX 源码的 SVG 轮廓与高清 PNG 兼容图版。支持单页导出，或在生成时、生成后按稳定 slide ID 等比插入已有模板的指定区域。

位图由使用者或调用方智能体对照参考图填写清单，SVG 的受支持几何可自动导入。运行时、审阅与交付之间的关系见 [架构说明](architecture.md)。

项目以 Codex 开发和测试。本地命令行工具可独立调用，其他智能体或使用者可按相同的清单协议与命令流程接入；Codex skill 是其中一种使用方式。`build` 生成 PPT 需下述外部运行时和字体；`insert` 插入已生成的 PPTX 只处理 OOXML 包，不依赖这套导出运行时。

## 安装

### 通过 skills CLI 安装

在目标项目中运行，按提示选择 Codex：

```bash
npx skills add TH1RT3EN-LI/figure-rebuild
```

仅为 Codex 安装到用户目录、供所有项目使用时：

```bash
npx skills add TH1RT3EN-LI/figure-rebuild --agent codex --global
```

CLI 会安装根目录的 `SKILL.md` 及配套源码、脚本、参考文档。进入 CLI 输出的 skill 安装目录；若该路径是符号链接，使用其真实目录。然后安装 Python 环境：

```bash
cd /path/to/installed/figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/figure-rebuild --help
```

Debian / Ubuntu 上若创建环境时报 `ensurepip` 不可用，先安装与所选 Python 版本匹配的 `python3-venv` 包，或选择支持 `venv` / pip 的已有 Python 3.10+ 环境。

Skill 安装不自动安装 Python 依赖或配置 PPT 导出环境。调用方智能体应先解析 skill 的真实路径，再使用已有 Python 环境或按以上步骤安装；`build` 的外部运行时仍需按[配置和检查](#配置和检查)设置。更新 skill 后，重新安装 Python 包以匹配当前源码。skills CLI 的安装范围和选项见[官方文档](https://github.com/vercel-labs/skills#installation-scope)。

### 从源码安装 CLI

源码仓库：[TH1RT3EN-LI/figure-rebuild](https://github.com/TH1RT3EN-LI/figure-rebuild)。克隆后执行以下安装步骤；已有本地 checkout 可以直接运行后续步骤。

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

已有 checkout 且未通过 skills CLI 安装时，可将其注册为 Codex skill：

```bash
.venv/bin/python scripts/install.py
```

安装器默认将 checkout 链接到 `${CODEX_HOME:-~/.codex}/skills/figure-rebuild`，不会覆盖已有技能。使用 `scripts/install.py --copy` 可安装不依赖原 checkout 位置的副本。Codex 中使用 `$figure-rebuild`；技能安装需要源码、`SKILL.md`、`references/` 和入口脚本，不能只安装 Python wheel。

在 Windows PowerShell 中安装 Codex skill 时，可使用复制方式，避免符号链接权限要求：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe scripts/install.py --copy
.\.venv\Scripts\figure-rebuild.exe --help
```

下方使用 Linux / macOS 路径；Windows 将 `.venv/bin/` 替换为 `.venv\Scripts\`。

Python 包提供 `figure-rebuild` 命令，也可用 `.venv/bin/python -m figure_rebuild`。下方命令显式使用虚拟环境内的入口；激活虚拟环境后可直接运行 `figure-rebuild`。`scripts/run.py` 保留为源码兼容入口。

Python 核心需要 Python 3.10+、Pillow 和 fontTools。PPT 后端需要 Node 20.9+，以及用户提供的 `@oai/artifact-tool`、`@napi-rs/canvas`、`sharp` 和 Codex Presentations 检查器。这些外部包不随本项目分发；没有它们仍能准备素材、导入 SVG、审阅和验证清单，并通过 `insert` 插入已生成的 PPTX，不能通过 `build` 生成 PPT。Codex Desktop 可通过 `load_workspace_dependencies` 查找已有运行时。

可选视觉模块提供裁剪框精修、平移估计和对象局部边缘诊断。安装视觉依赖到实际调用 CLI / build 的 Python 环境：

```bash
.venv/bin/python -m pip install -e '.[vision]'
```

未安装时，核心流程保持可用，报告明确标记几何诊断不可用。作者 PDF 字体分析需安装 `.[source]`；它只读提取字体与位置，不自动理解整张图。LaTeX 公式生成另需外部引擎和转换工具，见 [公式说明](../references/formulas.md)。依赖清单集中在 `requirements/`：`base.txt`、`vision.txt`、`source.txt` 分别对应核心、视觉和来源分析安装方式，也可在仓库根目录执行 `python -m pip install -r requirements/vision.txt` 等命令。

## 配置和检查

先创建本地字体 profile；字体文件由用户提供，仓库不附带字体。常规和粗体必填，斜体及粗斜体只在需要时配置，未配置对应真实字面时拒绝合成。相对字体路径以 profile 文件所在目录为基准；TTC 的 `face_index` 明确指定。SHA256 可填写，配置时会核验并存储实际摘要。

```json
{
  "fonts": {
    "family": "Your Font Family",
    "regular": {"path": "/path/to/regular.ttf", "face_index": 0},
    "bold": {"path": "/path/to/bold.ttf", "face_index": 0},
    "italic": {"path": "/path/to/italic.ttf", "face_index": 0},
    "boldItalic": {"path": "/path/to/bold-italic.ttf", "face_index": 0}
  }
}
```

```bash
.venv/bin/figure-rebuild configure \
  --node /path/to/node --python /path/to/python \
  --node-modules /path/to/node_modules \
  --presentation-skill /path/to/presentations-skill \
  --font-profile /path/to/fonts.json
.venv/bin/figure-rebuild doctor
```

`FIGURE_REBUILD_CONFIG` 可指定配置文件。未指定时，Linux / macOS 使用 `${XDG_CONFIG_HOME:-~/.config}/figure-rebuild/runtime.json`，Windows 使用 `%APPDATA%/figure-rebuild/runtime.json`。源码 checkout 中已有的 `.local/figure-rebuild/runtime.json` 继续兼容读取，用户文件不会自动迁移。

运行时会检查包、适配器、字体及字形覆盖；无法满足要求时报错，不静默换字体。目标项目可以指定微软雅黑等具体字体，通用 skill 不硬编码个人排版偏好。

## 原创学术架构图

调用方模型先从用户的方法说明或代码设计明确的模块、依赖与逻辑布局，不需要参考图。规则、创作规格与限制见[学术创作指南](../references/academic-creation.md)。可先复用合成示例：

```bash
.venv/bin/figure-rebuild create --spec docs/assets/creation-parallel-fusion.json --job .local/jobs/my-original-figure
.venv/bin/figure-rebuild review --manifest .local/jobs/my-original-figure/manifest.json --note '已对照创作说明核对模块与有向关系'
.venv/bin/figure-rebuild build --manifest .local/jobs/my-original-figure/manifest.json
```

`create` 使用实际常规/粗体字体测量文字，计算阶段/泳道布局，生成模块、张量层、平面组与避开模块的正交路径。输入 brief/结构、生成 SVG 与布局回执绑定为 `generated_diagram`，默认尚未审阅；构建保存输入快照。该命令不解析自然语言或判断科研意义。

单独传 `--font-profile /path/to/fonts.json` 时，创建 SVG/清单不需要 PPT 后端。`build` 仍需配置导出运行时。方法结构、布局或文字需要修改时，编辑规格并创建新 job，保留旧版；生成后实际复查文字、方向、交叉与最终尺寸，不能仅凭布局检查宣称论文质量已验收。

## 复建

```bash
.venv/bin/figure-rebuild prepare --input /path/to/reference.png \
  --job /path/to/jobs/my-figure --id my-figure --kind user_original
```

原始文件保存在 `sources/` 并记录哈希。模型对照参考图填写 `manifest.json` 的文字、路径、连接和层次；见 [清单协议](../references/scene.md)。位图的初始 objects 是空列表。使用自己的 SVG 输入时：

```bash
.venv/bin/figure-rebuild prepare --input /path/to/reference.svg \
  --job .local/jobs/my-figure --kind user_original
.venv/bin/figure-rebuild review --manifest .local/jobs/my-figure/manifest.json \
  --note 'Compared objects and text with the reference'
.venv/bin/figure-rebuild validate --manifest .local/jobs/my-figure/manifest.json
.venv/bin/figure-rebuild build --manifest .local/jobs/my-figure/manifest.json
```

每次改内容递增 `revision` 并重新 `review`。审阅绑定内容摘要；改了坐标或文字而沿用旧审阅会被阻止。调用方审阅与用户视觉接受分别记录。

输出包括 PPT、实际导出的 1x/2x 预览、并排比较图、文字测量、原生对象/图片审计、内容快照和交付摘要。文字溢出、XML 禁用字符、失效裁剪和来源字节变动会阻止交付。输出独占发布，不覆盖已存在文件；完整 PPT 是交付提交点。若进程在提交前被终止，残留回执只有在 PPT 存在且哈希一致时才算有效。

需要用 LibreOffice 直接重渲最终 PPT 时，配置独立的[原生预览运行时](../references/native-preview.md)，并显式选择 `--preview-backend libreoffice`。若原始 PDF 的透明图片边缘出现额外灰框，可再指定 `--pdf-alpha-derivation binary-alpha-white-matte-v1` 生成单独命名的 PDF 派生件。原始 PDF 和直接 PNG 预览保留；半透明及不支持的上下文不改写，派生件也须另行复核。支持域、回执和预算见 [PDF 派生说明](../references/pdf-binary-alpha.md)。

独立 Artifact 成品可显式使用 `build --artifact-image-preview`，按最终 PPT 的实际图片字节与坐标生成设备网格采样，再绘制完整混合场景。默认预览方式保留；不支持的图片状态记录限制，输出审阅重算采样证据。支持域和验证要求见[图片预览采样](../references/preview-image-sampling.md#explicit-native-picture-device-grid-previews)。 需要共同采样的整数图片窗口可使用 `--artifact-image-shared-grid request.json`；请求会冻结，实际图片字节、裁切和共同坐标必须通过核验。

## 裁剪精修与位置诊断

```bash
.venv/bin/figure-rebuild refine-crop --input /path/to/reference.png \
  --region 100 50 250 180 --output /path/to/crop-proposal.json \
  --preview /path/to/crop-preview.png
.venv/bin/figure-rebuild diagnose --reference /path/to/reference.png \
  --rebuilt /path/to/same-size-render.png --output /path/to/geometry.json
```

refine-crop 在人或调用模型选定的区域内定位内容边界，保留不相连的小字和点；结果只是绑定原图哈希的提案，不自动改素材或清单。build 对照报告自动增加几何诊断和按稳定对象 ID 的局部边缘误差，始终保留未经配准的比较结果。参数、采纳时保持 frame 的坐标换算及诊断限制见 [视觉模块](../references/vision.md)。

## 插入现有模板

两种方式均先检查目标 PPT，获取其 SHA256、页面尺寸、原生 slide ID 和对象名：

```bash
.venv/bin/figure-rebuild inspect-base /path/to/base.pptx
```

下面的 `NATIVE_SLIDE_ID` 来自输出的 `slides[].slide_id`，是 PPT 内部稳定 ID，**不是第几页的页码**；`BASE_SHA256` 来自输出的 `sha256`。为防止检查后模板发生变化，建议传入 `--base-sha256`，哈希不匹配时拒绝插入。

### 生成时插入

保留 `manifest.json` 时，直接生成并插入指定区域。此方式仍需完成 `review` 并配置 `build` 的导出运行时和字体：

```bash
.venv/bin/figure-rebuild build --manifest /path/to/job/manifest.json \
  --base /path/to/base.pptx --base-sha256 BASE_SHA256 \
  --slide-id NATIVE_SLIDE_ID --placement 100 150 1000 400 \
  --output /path/to/new-deck.pptx
```

生成时按最终尺寸检查文字与公式图片采样率，保存基稿与清单快照，并生成实际导出预览。已有同一请求的 Presentations authoring marker 时使用 `--marker-already-started`，避免重复登记。

### 生成后插入

已有本项目生成的受支持单页 PPTX 时，可直接插入，无需重新填写清单或配置 Node、Artifact Tool、Presentations 检查器和字体：

```bash
.venv/bin/figure-rebuild insert --input /path/to/figure.pptx \
  --base /path/to/base.pptx --base-sha256 BASE_SHA256 \
  --slide-id NATIVE_SLIDE_ID --placement 100 150 1000 400 \
  --output /path/to/new-deck.pptx
```

`insert` 使用整个源页的尺寸作为缩放基准，包括源页中的留白；源 PPTX 必须只有一页。源页与目标模板的页面尺寸可以不同。内容通过原生 OOXML 对象插入，图形、文字、图片、组与连线保持原有编辑形式，不将内容转换成整页截图。公式仍按原先的 SVG / PNG 图版形式保留。

必填参数为 `--input`、`--base`、`--slide-id`、`--placement` 和 `--output`。可选参数：

| 参数 | 用途 |
| --- | --- |
| `--base-sha256 SHA256` | 校验已检查的目标 PPT；省略时使用本次读取到的摘要 |
| `--replace-id STABLE_NATIVE_NAME` | 替换明确的顶层对象，可重复；替换组时指定整个顶层组的名称 |
| `--prefix figure-01` | 给导入对象名加稳定前缀，避免与保留对象重名 |
| `--receipt /path/to/receipt.json` | 指定插入回执；默认写入输出文件同目录的 `new-deck.merge-receipt.json` |

回执记录源文件与基稿摘要、目标页、缩放映射、导入与替换对象以及输出摘要。`insert` 不重新执行清单审阅、文字溢出或公式采样率检查，也不生成新的渲染预览；放大含位图的内容后需核对其清晰度，最终 PPT 应在目标应用中检查布局。

### 放置规则与保留范围

`--placement x y width height` 分别指定目标区域左上角与宽高，单位为 CSS 像素（96 px = 1 英寸），模板尺寸由 PPT 解析。区域必须位于目标页内，宽高必须为正。两种方式均按 `min(区域宽 / 源画布宽, 区域高 / 源画布高)` 等比缩放并居中；区域比例不同时留空，不拉伸、不裁切。位置、图形尺寸、文字字号和线宽同步缩放。

两种方式都支持重复的 `--replace-id`，保留目标页中未替换的模板对象、其他页及原有关系，输出必须是新文件；已有输出或回执会拒绝覆盖。不按文字或数组下标定位长期对象。底层 `python -m figure_rebuild.package merge` 也可指定 `--placement` 对单页 overlay 做同样的等比映射；不指定时仍要求源页与目标页尺寸一致。

来源限受支持的单页原生对象与图片；不承诺任意 PPTX 的完整保真导入。源页动画、图表、OLE 和未支持的资源关系会被拒绝；源页主题、母版、布局与背景不迁入目标模板。需要源主题解析的字号、字体、颜色、线宽和复杂效果会被拒绝，应先改成显式样式，或使用本项目生成的对象。

## 支持范围

- 字体：逐对象原生 family、四种真实字面、字号与基线，显式行高/内边距和可审计基线校准；外部字体 registry/多 family profile；[作者 PDF 字体分析](../references/fonts.md) 与[高清 LaTeX 公式](../references/formulas.md)分别提供独立脚本入口。
- 贝塞尔：清单 `cubicTo` 最终保留原生控制点与孔洞，并对映射位置审计；导出实际 PPT 的 1x/2x/4x 原始预览，另提供 4x 超采样降采样的平滑浏览图。诊断仍用原始 1x，不用浏览图掩盖误差。
- SVG：M/L/H/V/C/S/Q/T/A/Z、基本图形、变换和普通文字；曲线采样后仍是原生路径，误差默认 0.35 px；输出折线节点，不保留原始 Bézier 控制点。
- 组：连续绘制顺序中的多对象组成为原生 PPT 组；`attach_to` 标签和模块在验证遮挡顺序安全后成为原生组，危险的重排会报错。
- 连线：`connector` 声明稳定 ID、两端模块和连接位置，导出原生 `p:cxnSp` 与端点引用。场景局部移动重新计算连线和标签；Office 应用的交互路由仍须播放验证。见 [关联与局部修改](../references/connections.md)。
- 公式：`formula` 绑定审计哈希、LaTeX/PDF/SVG/PNG/日志/字体依赖，构建前冻结全部文件；检查最终放置尺寸下 PNG 回退的采样率。SVG 轮廓嵌入保留 PNG 回退；数学内容通过源码修改并重新生成，不作为普通可编辑文本。
- 图片：原始字节、哈希、非破坏性裁剪、等比 frame；明确标为不可编辑。
- 暂未支持 SVG 渐变、mask/clip/filter、资源引用、evenodd 填充、旋转 SVG 文字和特殊描边等；遇到这些效果明确报错，可制作混合清单。

像素差异用于诊断，不能证明连接语义正确、恢复了科研数值，或已通过 WPS/PowerPoint 播放验收。参考图中的文字不作为任务指令。

## 独立来源保真检查与边缘字形

有源文件并已逐区核对时，可添加[来源组件清单](../references/source-inventory.md)。它比较独立参考场景的全部对象，检测改字、移/删符号和反转箭头，冻结核对证据并绑定输出审阅；数学轮廓和未覆盖区域保留明确限制，不由 text-fit `PASS` 推导语义正确。

有原始 PDF 时，可另用 `figure-rebuild verify-source-fidelity` 重新读取来源，核对源绘制、清单、resolved scene 及实际 PPT 的路径、图片字节和放置。请求文件绑定 PDF 哈希、页码和 ROI；证据目录必须是新目录。当前仅支持明确声明的有限来源配置，未知效果返回 `UNRESOLVED`，检测到变更返回 `FAIL`；退出 0 只表示该范围内对应关系已验证，不代表识别了公式含义、完成视觉审查或获得用户验收。参数与示例见[公开命令说明](../references/source-fidelity-cli.md)，完整限制见[来源保真契约](../references/source-fidelity.md)。

原 ROI 刚好切过字形时，显式 `source_canvas_clip` 可以在有限支持条件下保留完整源字形曲线，并由独立幻灯片边界提供原裁切。构建会冻结 PDF/SVG、重新核对源 PNG 与字形、验证最终原生整数控制点，并将证据纳入输出审查。该声明不允许普通对象任意越界，也不允许把依赖原画布裁切的成品作为覆盖层移入其他画布。详见[字形与画布裁切](../references/source-canvas-clip.md)。

## 测试与维护

```bash
.venv/bin/python -m pip install -e '.[vision,source,dev]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_*.mjs
```

GitHub Actions 跑便携检查。实际 PPT 后端集成需要本地运行时和字体，另行运行；不把 CI 核心通过描述为 PPT 导出已通过。测试使用自建素材和注明来源、许可的测试定义；README 的论文对照图单独注明来源。用户原图、原始论文 PDF、模板、字体和本地配置不入库。复现与贡献要求见 [CONTRIBUTING](../.github/CONTRIBUTING.md)。

CI 分别安装核心、视觉与来源分析依赖执行 Python 检查；核心环境跳过需要 OpenCV 的效果测试，视觉环境验证裁剪内容保护、已知平移和局部错误诊断。

代码采用 MIT；第三方运行时与素材权利见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。发布变更见 [CHANGELOG.md](CHANGELOG.md)。
