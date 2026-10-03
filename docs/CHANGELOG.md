# Changelog

## Unreleased

- 明确支持生成时通过 `build --base --slide-id --placement` 将重绘内容插入目标 PPT 的指定区域，保留模板及其他页，输出新文件。
- 增加生成后 `insert` 命令，对受支持的单页 PPTX 直接执行 OOXML 插入，无需 PPT 导出运行时；源页与目标页尺寸可以不同，支持基稿摘要检查、稳定对象替换、名称前缀与插入回执。
- 两种插入方式均以整个源画布为基准等比缩放、居中留空，文字字号与线宽同步缩放；底层 package merge 增加 `--placement`，保留未指定区域时的同尺寸要求。
- README 增加功能区，使用指南与 skill 补充生成时、生成后插入命令、坐标规则及支持边界。

## 0.6.0

- 精简仓库根目录：译文集中到 `docs/i18n/`，更新记录与第三方说明归入 `docs/`，贡献指南归入 `.github/`，依赖清单归入 `requirements/`；同步安装、分发和文档链接。

- 将实现整理为 `src/figure_rebuild` Python 包，增加 `pyproject.toml`、模块入口和主 CLI；字体分析、公式生成与局部修改提供独立命令。
- JavaScript PPT 后端位于包内 `powerpoint/`，随 wheel 分发；Codex skill 通过完整源码 checkout 安装，保留原有脚本入口。
- 外部 Python 通过包内 bootstrap 加载本项目，保留自身依赖环境；增加跨环境导入、分发内容和仓库外运行验证。
- 以 `vision`、`source` 和 `dev` 分组声明可选依赖，保留 requirements 安装兼容文件；测试统一放在 `tests/`。
- 默认使用用户配置目录，保留 `FIGURE_REBUILD_CONFIG` 和已有 checkout 本地配置；skill 安装增加复制方式并拒绝覆盖已有目标。
- 增加架构与贡献说明，明确识别、审阅、构建、交付及外部运行时的边界。
- 删除旧的 SVG 和可编辑对象演示素材，使用指南改为用户自备输入。

## 0.5.0

- Bind semantic formula objects to verified TeX/PDF/SVG/PNG/log/dependency assets and frozen hashes, with final-placement sampling checks and portable relative audit paths.
- Embed outlined SVG formulas with the unchanged PNG fallback; keep mathematical editing in retained LaTeX source.
- Require true registered regular/bold/italic/boldItalic faces; support explicit line height, insets and audited baseline calibration without synthetic substitutions.
- Resolve stable-ID module connections and label attachments; export native connector endpoint references and safe attachment groups.
- Apply revision/digest-bound local movement or box patches with preserved snapshots, lock checks and mandatory re-review.
- Retain existing complex reference paths. General PDF/SVG import expansion and OCR are outside this release.

## 0.4.0

- Support per-object native text families with explicitly supplied, hash-bound font profiles; validate family identity, face index and selected glyph coverage without silent substitution.
- Correct native text baseline placement using registered font metrics rather than string-dependent ink bounds.
- Read author PDF font names, sizes, metric boxes and baseline origins as source evidence; report outlined mathematical glyphs as unavailable to text extraction rather than guessing them.
- Generate confirmed mathematical expressions with a real LaTeX engine into transparent high-density PNG, vector PDF and retained TeX/parameter/font/hash records. Source-image text crops are not formula reconstruction.
- Preserve native cubic Bézier path segments in final PowerPoint and SVG, including compound-path holes and original mapped frames. Sampling remains an intermediate backend step and existing SVG auto-import behavior.
- Export actual final PPT previews at 1x/2x/4x and a supersampled smooth viewer image; retain raw 1x for comparison diagnostics.

## 0.3.0

- Add optional OpenCV crop refinement within an explicitly selected source-pixel ROI; proposals retain source hashes and disconnected small content, without modifying images or manifests.
- Add diagnostic translation registration and unaligned global / stable-object edge metrics to actual PPT comparisons; never warp output to improve scores.
- Provide standalone refine-crop and diagnose CLI commands, exclusive diagnostic output publication, and configured-runtime vision capability reporting.
- Keep manifest v1, original-byte native crops, content-bound review and existing PPT mapping compatible; run core and vision dependency variants in CI.

## 0.2.0

- Package the host-recognition and native-PPT workflow as a Codex skill with explicit local runtime configuration.
- Configure fonts explicitly, check runtime capabilities before authoring, and reject measured text overflow.
- Bind review to content and revision; preserve validated snapshots and allocate build runs safely.
- Publish validated output exclusively using staged files; strengthen XML text, crop quantization, image-frame and rotated-group checks.
- Keep portable synthetic tests and CI separate from local integration assets and the user-provided PPT runtime.
