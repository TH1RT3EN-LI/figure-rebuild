---
name: figure-rebuild
description: Reconstruct reference diagrams or SVGs as editable PowerPoint geometry and live text; preserve photos and formulas as explicit raster panels. Use for image-to-PPT reproduction and diagram tracing.
---

# Figure Rebuild

复建用户选定的参考图，交付独立 PPT 或插入现有模板。位图由调用方模型识别，本地脚本生成可编辑对象；不调用外部识图 API。此 skill 可独立安装，无需 AutoSlides。

先解析本 skill 目录的真实路径，使用 `scripts/run.py`，不要根据当前工作目录猜路径。配置、CLI 示例及后端限制见 [README.md](README.md)；填写清单时读 [scene.md](references/scene.md)。PPT 后端需要用户提供的 Codex Artifact Tool 和 Presentations 检查器。先 `doctor` 检查运行时。按目标项目字体要求配置外部字体文件，不静默回退或附带私有字体。

1. `prepare` 新建 job，保存原始素材、来源分类与 SHA256。用户提供的图是参考素材，不是修改任务的指令。位图的空清单不代表识别完成。
2. 亲自对照原图，把可见文字、轮廓、箭头端点、层次和遮挡写进 `manifest.json`。使用稳定对象 ID；依据可见关系复建，不根据论文知识补造连接。SVG 可自动导入几何，但仍需核对。
3. 看不清的文字或连接记录在 `recognition.unresolved`；解决前不导出。照片、纹理、热图和难以辨认的公式可保留为 `editable:false` 的独立图片区域，原图字节与非破坏性裁剪可审计。需要重排的公式使用目标项目的真正 LaTeX 流程。普通文字保持原文，居中用对齐属性，换行用文本框自动换行。
4. `review` 将审阅绑定清单内容，再 `build`。改内容必须递增 revision 并重新审阅。调用方审阅不代表用户接受。插入已有 PPT 前 `inspect-base`，用原生 slide ID、基稿哈希与明确放置区域；替换用稳定对象名。构建保存快照、输出新文件，保留其他页与模板元素。
5. 看实际导出 PPT 的 1x/2x 预览及并排对照，核对文字、所有连接、孔洞和层次。阅读编辑性、保留性与文字尺寸报告。像素误差是诊断指标，不能代替结构核对或用户验收。

交付 PPT、预览和简短的编辑性说明。整页截图不能称为全可编辑；数值曲线肉眼描线不能称为恢复实验数据。未支持的 SVG 效果会报错，可改用明确的混合清单。当前不输出 Illustrator `.ai`，也不自动声称通过 WPS/PowerPoint 播放验收。用户接受之后才提炼偏好。
