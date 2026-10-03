# 裁剪精修和位置诊断 · 0.3.0

视觉模块使用可选 OpenCV / NumPy；在运行 CLI 的 Python 和配置用于 build 的 Python 中安装 `requirements/vision.txt`。两者可以是同一个虚拟环境。`doctor` 报告配置环境中的视觉能力；没有这些包时，原有生成流程仍运行，几何报告标记 unavailable。

## 精修已选区域

```bash
python scripts/run.py refine-crop --input /path/to/job/sources/original.png \
  --region 100 50 250 180 --output /path/to/crop-proposal.json \
  --preview /path/to/crop-preview.png
```

`region` 是原始素材像素坐标 x、y、width、height，必须在图片内；小数边界向外取整。默认背景白色，透明像素在背景上合成后检测。可用 `--background R G B`、`--tolerance 18`、`--padding 1` 调整。检测保留 ROI 内所有达到阈值的连通分量；默认 `--min-area 1` 保留孤立的一个像素，不自动清理小字、点号、坐标点。

输出包括原图哈希、检测框、带留边的 `proposed_region` 和可直接用于 v1 图片对象的 `proposed_crop`。空白区域返回 empty 与 null 提案。背景相近的线条可能被阈值漏掉，噪点也可能被保留；先核对预览中的小字、边缘、图例，再采纳。工具不更新原图或 manifest，不自动补全区域外的内容。输出和预览不覆盖已有文件。

采纳时保存修改前清单，记录稳定对象 ID、基础 revision、原因与提案文件，递增 revision 并重新 review。**改 crop 时要同步 frame，才能保持原图内容的位置和尺度。**

若图片使用同一原始参考画布，`box` 可对应提案的原图像素区域。若图片已放置到其他位置：由旧裁剪求源区域 `(x0,y0,w0,h0)`，由旧 box 的 contain 拟合求实际框 `(fx,fy,fw,fh)`，比例 `s=fw/w0=fh/h0`。新提案 `(x1,y1,w1,h1)` 的 box 应为：

```text
x = fx + (x1-x0)*s       y = fy + (y1-y0)*s
width = w1*s            height = h1*s
```

沿用旧 box 而只缩 crop 会放大或移动内部内容，不能当作无视觉变化的修边。原图字节和哈希继续保留，导出的原生裁剪仍由现有审计核对。

## 位置与局部几何诊断

```bash
python scripts/run.py diagnose --reference /path/to/reference.png \
  --rebuilt /path/to/rebuilt.png --output /path/to/geometry.json
```

直接 diagnose 使用相同像素画布的图片，输出图像哈希及平移/边缘诊断；最大可信平移范围可用 `--max-shift 32` 指定。build 的比较器会在现有放置区域内诊断，并将清单对象范围映射到实际比较画布；结果在 `comparison-metrics.json` 的 geometry 字段中。

build 使用 `comparison-scene.json` 中经过文字测量、图片 contain 拟合和描边扩展的源坐标框，兼容只有基线 anchor 的 SVG 文字。该文件是诊断派生物，不替代原 manifest。长边超过 1280 时按比例降采样分析，距离换算回比较画布像素；局部诊断有对象数和像素预算，遗漏数量在报告中明确列出。诊断不可用或输入画布不匹配时，独立 diagnose 返回非零退出码并保存原因。

平移估计的正号表示目标图内容相对原图向右 / 向下，单位为实际比较画布像素。它是诊断值，不是自动应用的补偿。未经配准的像素差图和边缘误差始终保留。空白、低相关性、超范围及不收敛不能宣称位置可靠；局部边缘误差也不证明箭头的科研语义正确。

用户要求修正位置时，根据稳定 ID 提出有限坐标修改，重新生成并检查原始差异。字体适配、抗锯齿和线宽变化都可能影响边缘指标，最终仍看实际预览；应用渲染器的差异仍需在 WPS / PowerPoint 验证。
