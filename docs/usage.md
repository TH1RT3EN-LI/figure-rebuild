# 使用指南

以下命令均在仓库根目录执行。项目介绍和最短开始方式见 [README](../README.md)。

把参考图复建为可编辑 PowerPoint：几何是原生路径，普通文字是独立文本框，照片保留来源审计；重绘公式使用带 LaTeX 源码的 SVG 轮廓与高清 PNG 兼容图版。支持单页导出，或按稳定 slide ID 插入已有模板。

位图由使用者或调用方智能体对照参考图填写清单，SVG 的受支持几何可自动导入。运行时、审阅与交付之间的关系见 [架构说明](architecture.md)。

项目以 Codex 开发和测试。本地命令行工具可独立调用，其他智能体或使用者可按相同的清单协议与命令流程接入；Codex skill 是其中一种使用方式。PPT 导出仍需下述外部运行时和字体。

## 安装

源码仓库：[TH1RT3EN-LI/figure-rebuild](https://github.com/TH1RT3EN-LI/figure-rebuild)。克隆后执行以下安装步骤；已有本地 checkout 可以直接运行后续步骤。

```bash
git clone https://github.com/TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

如需在 Codex 中使用 skill，再运行：

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

Python 核心需要 Python 3.10+、Pillow 和 fontTools。PPT 后端需要 Node 20.9+，以及用户提供的 `@oai/artifact-tool`、`@napi-rs/canvas`、`sharp` 和 Codex Presentations 检查器。这些外部包不随本项目分发；没有它们仍能准备素材、导入 SVG、审阅和验证清单，不能导出 PPT。Codex Desktop 可通过 `load_workspace_dependencies` 查找已有运行时。

可选视觉模块提供裁剪框精修、平移估计和对象局部边缘诊断。安装视觉依赖到实际调用 CLI / build 的 Python 环境：

```bash
.venv/bin/python -m pip install -e '.[vision]'
```

未安装时，核心流程保持可用，报告明确标记几何诊断不可用。作者 PDF 字体分析需安装 `.[source]`；它只读提取字体与位置，不自动理解整张图。LaTeX 公式生成另需外部引擎和转换工具，见 [公式说明](../references/formulas.md)。原有 `requirements.txt`、`requirements-vision.txt`、`requirements-source.txt` 保留为对应包安装方式的兼容文件。

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

```bash
.venv/bin/figure-rebuild inspect-base /path/to/base.pptx
.venv/bin/figure-rebuild build --manifest /path/to/job/manifest.json \
  --base /path/to/base.pptx --base-sha256 BASE_SHA256 \
  --slide-id NATIVE_SLIDE_ID --placement 100 150 1000 400 \
  --output /path/to/new-deck.pptx
```

坐标单位为 CSS 像素；模板尺寸由 PPT 解析。可用重复的 `--replace-id STABLE_NATIVE_NAME` 替换明确的顶层对象。保留其他页和关系，基稿先保存快照；不按文字或数组下标定位长期对象。已有同一请求的 Presentations authoring marker 时使用 `--marker-already-started`，避免重复登记。

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

## 测试与维护

```bash
.venv/bin/python -m pip install -e '.[vision,source,dev]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_*.mjs
```

GitHub Actions 跑便携检查。实际 PPT 后端集成需要本地运行时和字体，另行运行；不把 CI 核心通过描述为 PPT 导出已通过。测试使用自建素材和注明来源、许可的测试定义；README 的论文对照图单独注明来源。用户原图、原始论文 PDF、模板、字体和本地配置不入库。复现与贡献要求见 [CONTRIBUTING](../CONTRIBUTING.md)。

CI 分别安装核心、视觉与来源分析依赖执行 Python 检查；核心环境跳过需要 OpenCV 的效果测试，视觉环境验证裁剪内容保护、已知平移和局部错误诊断。

代码采用 MIT；第三方运行时与素材权利见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。发布变更见 [CHANGELOG.md](../CHANGELOG.md)。
