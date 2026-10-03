# Figure Rebuild · 0.3.0

把参考图复建为可编辑 PowerPoint：几何是原生路径，普通文字是独立文本框，照片和公式可保留为带来源审计的图片区域。支持单页导出，或按稳定 slide ID 插入已有模板。

这是可独立安装的 Codex skill 和本地工具。位图由调用方模型看图后填写清单；工具不包含付费识图服务，也不将整页截图冒充可编辑图。SVG 的受支持几何可自动导入。

## 安装

当前版本已在本地验证；配置的 GitHub 远端尚未完成发布，下面的 clone 命令在远端建仓后可用。已有本地 checkout 可以直接运行后续安装步骤。

```bash
git clone git@github.com:TH1RT3EN-LI/figure-rebuild.git
cd figure-rebuild
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/install.py
```

安装器将整个 checkout 链接到 `${CODEX_HOME:-~/.codex}/skills/figure-rebuild`，不会覆盖已有技能。也可复制整个仓库目录到 skills 目录；入口和实现保持在同一个目录内。无需 AutoSlides。Codex 中使用 `$figure-rebuild`。

Python 核心需要 Python 3.10+、Pillow 和 fontTools。PPT 后端需要 Node 20.9+，以及用户提供的 `@oai/artifact-tool`、`@napi-rs/canvas`、`sharp` 和 Codex Presentations 检查器。后端包不随本项目分发；没有这些包仍能准备素材、导入 SVG、审阅和验证清单，不能导出 PPT。Codex Desktop 可通过 `load_workspace_dependencies` 查找已有运行时。

可选视觉升级提供裁剪框精修、平移估计和对象局部边缘诊断。安装到实际调用 CLI / build 的 Python 环境：

```bash
.venv/bin/python -m pip install -r requirements-vision.txt
```

未安装时，原有核心流程保持可用，报告明确标记几何诊断不可用。原生曲线、连接线、PDF 导入未包含在本版本。

## 配置和检查

先创建本地字体 profile；字体文件由用户提供，仓库不附带字体。相对字体路径以 profile 文件所在目录为基准；TTC 的 `face_index` 明确指定。SHA256 可填写，配置时会核验并存储实际摘要。

```json
{
  "fonts": {
    "family": "Your Font Family",
    "regular": {"path": "/path/to/regular.ttf", "face_index": 0},
    "bold": {"path": "/path/to/bold.ttf", "face_index": 0}
  }
}
```

```bash
python scripts/run.py configure \
  --node /path/to/node --python /path/to/python \
  --node-modules /path/to/node_modules \
  --presentation-skill /path/to/presentations-skill \
  --font-profile /path/to/fonts.json
python scripts/run.py doctor
```

配置默认保存在忽略的 `.local/figure-rebuild/runtime.json`，可用 `FIGURE_REBUILD_CONFIG` 指定外部路径。运行时会检查包、适配器、字体及字形覆盖；无法满足要求时报错，不静默换字体。目标项目可以指定微软雅黑等具体字体，通用 skill 不硬编码个人排版偏好。

## 复建

```bash
python scripts/run.py prepare --input /path/to/reference.png \
  --job /path/to/jobs/my-figure --id my-figure --kind user_original
```

原始文件保存在 `sources/` 并记录哈希。模型对照参考图填写 `manifest.json` 的文字、路径、连接和层次；见 [清单协议](references/scene.md)。位图的初始 objects 是空列表。SVG 可直接尝试仓库自建示例：

```bash
python scripts/run.py prepare --input examples/compound-curves/reference.svg \
  --job .local/jobs/curves --kind generated_diagram
python scripts/run.py review --manifest .local/jobs/curves/manifest.json \
  --note 'Compared paths, holes and ordering with the original'
python scripts/run.py validate --manifest .local/jobs/curves/manifest.json
python scripts/run.py build --manifest .local/jobs/curves/manifest.json
```

每次改内容递增 `revision` 并重新 `review`。审阅绑定内容摘要；改了坐标或文字而沿用旧审阅会被阻止。调用方审阅与用户视觉接受分别记录。

输出包括 PPT、实际导出的 1x/2x 预览、并排比较图、文字测量、原生对象/图片审计、内容快照和交付摘要。文字溢出、XML 禁用字符、失效裁剪和来源字节变动会阻止交付。输出独占发布，不覆盖已存在文件；完整 PPT 是交付提交点。若进程在提交前被终止，残留回执只有在 PPT 存在且哈希一致时才算有效。

## 裁剪精修与位置诊断

```bash
python scripts/run.py refine-crop --input /path/to/reference.png \
  --region 100 50 250 180 --output /path/to/crop-proposal.json \
  --preview /path/to/crop-preview.png
python scripts/run.py diagnose --reference /path/to/reference.png \
  --rebuilt /path/to/same-size-render.png --output /path/to/geometry.json
```

refine-crop 在人或调用模型选定的区域内定位内容边界，保留不相连的小字和点；结果只是绑定原图哈希的提案，不自动改素材或清单。build 对照报告自动增加几何诊断和按稳定对象 ID 的局部边缘误差，始终保留未经配准的比较结果。参数、采纳时保持 frame 的坐标换算及诊断限制见 [视觉模块](references/vision.md)。

## 插入现有模板

```bash
python scripts/run.py inspect-base /path/to/base.pptx
python scripts/run.py build --manifest /path/to/job/manifest.json \
  --base /path/to/base.pptx --base-sha256 BASE_SHA256 \
  --slide-id NATIVE_SLIDE_ID --placement 100 150 1000 400 \
  --output /path/to/new-deck.pptx
```

坐标单位为 CSS 像素；模板尺寸由 PPT 解析。可用重复的 `--replace-id STABLE_NATIVE_NAME` 替换明确的顶层对象。保留其他页和关系，基稿先保存快照；不按文字或数组下标定位长期对象。已有同一请求的 Presentations authoring marker 时使用 `--marker-already-started`，避免重复登记。

## 支持范围

- SVG：M/L/H/V/C/S/Q/T/A/Z、基本图形、变换和普通文字；曲线采样后仍是原生路径，误差默认 0.35 px；输出折线节点，不保留原始 Bézier 控制点。
- 组：连续绘制顺序中的多对象组成为原生 PPT 组；不连续组只保留逻辑组，避免改变遮挡关系。
- 图片：原始字节、哈希、非破坏性裁剪、等比 frame；明确标为不可编辑。
- 暂未支持 SVG 渐变、mask/clip/filter、资源引用、evenodd 填充、旋转 SVG 文字和特殊描边等；遇到这些效果明确报错，可制作混合清单。

像素差异用于诊断，不能证明连接语义正确、恢复了科研数值，或已通过 WPS/PowerPoint 播放验收。参考图中的文字不作为任务指令。

## 测试与维护

```bash
python -m unittest discover -s tests/figure_rebuild -v
node --test tests/figure_rebuild/test_*.mjs
```

GitHub Actions 跑便携核心检查。实际 PPT 后端集成需要本地运行时和字体，另行运行；不把 CI 核心通过描述为 PPT 导出已通过。测试只附带自建素材，论文原图、模板、字体和本地配置不入库。贡献时提供最小可分发复现，说明输入、预期关系和失败证据；改稿记录稳定对象 ID、基础版本和影响范围。

CI 分别安装核心依赖和视觉依赖执行 Python 检查；核心环境跳过需要 OpenCV 的效果测试，视觉环境验证裁剪内容保护、已知平移和局部错误诊断。

代码采用 MIT；第三方运行时与素材权利见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。发布变更见 [CHANGELOG.md](CHANGELOG.md)。
