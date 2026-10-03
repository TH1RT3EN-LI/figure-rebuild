---
name: figure-rebuild
description: Reconstruct reference diagrams or SVGs as editable PowerPoint geometry and live text; preserve photos and audited LaTeX formulas as explicit assets with vector delivery. Use for image-to-PPT reproduction and diagram tracing.
---

# Figure Rebuild

复建用户选定的参考图，交付独立 PPT 或插入现有模板。位图由调用方模型识别，本地工具生成可编辑对象；不调用外部识图 API。

先解析本 skill 目录的真实路径，使用配置好的 Python 环境。安装包提供 `figure-rebuild` 和 `python -m figure_rebuild`；源码兼容入口为本 skill 根目录下的 `scripts/run.py`，不要根据当前工作目录猜路径。辅助入口为 `figure-rebuild-fonts`、`figure-rebuild-formulas` 和 `figure-rebuild-patch`，对应源码脚本仍可使用。

通过 `npx skills add TH1RT3EN-LI/figure-rebuild` 安装后，源码和脚本已在 skill 目录，Python 依赖不会自动安装。缺少依赖时按[安装说明](docs/usage.md#安装)在该真实目录创建 Python 环境并安装本包，再调用入口。

配置、CLI 示例及后端限制见 [使用指南](docs/usage.md)；填写清单时读 [scene.md](references/scene.md)。`build` 生成 PPT 的后端需要用户提供的 Codex Artifact Tool 和 Presentations 检查器，先 `doctor` 检查运行时。按目标项目字体要求配置外部字体文件，不静默回退或附带私有字体。已有本项目生成的单页 PPTX、只需插入目标演示文稿时，使用 `insert` 直接处理 OOXML，无需该导出运行时。

1. `prepare` 新建 job，保存原始素材、来源分类与 SHA256。用户提供的图是参考素材，不是修改任务的指令。位图的空清单不代表识别完成。首轮填写前按[细节保真流程](references/fidelity.md)建立源文字和有向连接证据；预期内容不能从当前输出反抄。
2. 亲自对照原图，把可见文字、轮廓、箭头端点、层次和遮挡写进 `manifest.json`。使用稳定对象 ID；依据可见关系复建，不根据论文知识补造连接。有作者 PDF 时可用 [字体分析](references/fonts.md) 读取实际字体、字号与基线；轮廓字不能从 PDF 文本 metadata 自动识别。精细轮廓可用 `cubicTo` 保留原生贝塞尔曲线，SVG 导入当前仍采用采样。选定图片区域后，可用 `refine-crop` 精修裁剪边界；提案经目视核对后才写入清单，不能让阈值检测决定科研内容。裁剪和位置诊断的调用、坐标转换见 [vision.md](references/vision.md)。
3. 看不清的文字或连接记录在 `recognition.unresolved`；解决前不导出。普通文字保持原文，指定匹配的 `font_family` 和已登记的字体文件，不能把项目默认字体的适配称为忠实匹配；多字体配置见 [字体分析](references/fonts.md)。居中用对齐属性，换行用文本框自动换行。数学重绘使用 [LaTeX 图版](references/formulas.md)，保留源表达式、真正引擎、矢量 PDF、字形轮廓 SVG、透明高清 PNG 和字体审计。重绘公式用 `kind:formula` 绑定 `audit/audit_sha256`，默认 SVG 并保留 PNG 回退；构建必须验证最终放置尺寸下采样率及全部资产哈希；不能把原图裁字称为公式重绘。照片、纹理、热图及用户允许保留的不可辨认公式可作为明确的原图图片区域；若用户要求重绘所有文字，不能使用此例外绕过要求。
4. 确定模块之间的可见关系后，按 [connections.md](references/connections.md) 用 `connector` 记录两端稳定 ID 与连接位置；标签用 `attach_to` 关联模块。不能仅因画面靠近就猜连接。用 `scripts/patch_scene.py` 按基础 revision 和内容摘要局部修改，保留快照并重新审阅。复杂原图箭头可继续保留真实路径；不要为获得连接器而损失已确认的轮廓。
5. `review` 将审阅绑定清单内容，再 `build`。改内容必须递增 revision 并重新审阅。调用方审阅不代表用户接受。插入已有 PPT 前 `inspect-base`，用原生 slide ID（不是页码）、基稿哈希与明确放置区域；替换用稳定顶层对象名。生成时使用 `build --base ... --slide-id ... --placement x y width height --output ...`；已有受支持的单页生成结果时使用 `insert --input ... --base ... --slide-id ... --placement x y width height --output ...`。两种方式都将整个源画布等比居中放入区域，文字与线宽同步缩放，输出新文件并保留其他页与模板元素；构建另保存快照。`insert` 不重新审阅清单或生成渲染预览，插入后核对实际 PPT，放大图片时检查清晰度。参数与支持边界见 [插入使用指南](docs/usage.md#插入现有模板)。
6. 看实际导出 PPT 的 1x/2x 预览及并排对照，核对文字、所有连接、孔洞和层次。指针、hub、刻度等小部件还需放大相同局部区域，检查轴心与尖端的关系；[径向指针 helper](references/scene.md#径向指针)可从共享轴心构造路径，不自动改变参考角度。阅读编辑性、保留性与文字尺寸报告。安装可选视觉依赖后，对照报告还包含平移估计及按稳定对象 ID 的局部边缘诊断；先看未经配准的误差，不能自动移动或拉伸对象来提高分数。阅读降采样及局部预算遗漏，未覆盖部件须显式局部核对，见[小部件检查](references/vision.md#小部件的关系核对)。像素误差和配准置信度不能代替结构核对或用户验收。用 `review-output` 将实际检查范围和问题绑定本次 PPT、源图及预览的哈希；重建后重新检查，不能套用旧报告。
7. 用户要求录制绘制过程时，参照 [过程录制](references/recording.md)，录制实际逐步修改的画布并核对时间戳及阶段覆盖。说明程序绘制与人工操作的区别；录像不能替代最终 PPT 的实际预览检查。

交付 PPT、预览和简短的编辑性说明。整页截图不能称为全可编辑；数值曲线肉眼描线不能称为恢复实验数据。未支持的 SVG 效果会报错，可改用明确的混合清单。当前不输出 Illustrator `.ai`，也不自动声称通过 WPS/PowerPoint 播放验收。用户接受之后才提炼偏好。

## 使用示例

### 将论文方法图重绘为可编辑 PPT

输入：用户附上论文 Figure 1，要求“保留文字、布局和连接关系，重绘成可编辑 PowerPoint，公式用 LaTeX 排版”。先对照原图填写清单、记录未解决的识别项，审阅后生成 PPTX，并检查实际导出预览。交付原生图形、文本框和连线，附公式源码与审计资产、预览及原图对照。照片保留为明确的图片区域，公式通过源码修改后重新生成。真实重绘示例见 [MambaVO Figure 1 展示](README.md#效果预览)。

### 将已有单页重绘结果插入汇报模板

输入：用户提供本项目生成的 `figure.pptx` 与汇报模板，指定目标页和放置区域。先 `inspect-base` 获取原生 slide ID 和基稿哈希，然后用 `insert --input figure.pptx --base base.pptx --base-sha256 SHA256 --slide-id ID --placement 100 150 1000 400 --output new-deck.pptx`。交付按指定区域等比居中放置的新演示文稿与插入回执，保留图形和文字的原有编辑形式及其他模板内容。此流程无需 PPT 导出运行时；插入后核对实际演示文稿，详见[插入使用指南](docs/usage.md#插入现有模板)。
