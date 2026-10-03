# PDF 源几何与图片实例

这些 Python API 生成有来源记录的候选对象，不能代替内容识别和实际 PPT 审阅。源 PDF 保持不变；新的素材、清单、未解决项和输出应写入新任务目录。PDF 读取需要可选的 PyMuPDF 环境。

## 路径与字形轮廓

```python
import pymupdf
from figure_rebuild.pdf_source import extract_outlined_svg, outline_paths

with pymupdf.open(pdf_path) as pdf:
    svg = pdf[page_number - 1].get_svg_image(text_as_path=True)
source = extract_outlined_svg(svg)
result = outline_paths(
    source,
    glyph_mode="outline",
    paint_ids=selected_source_ids,
    region=(x0, y0, x1, y1),
    transform=(scale, 0, 0, scale, -scale*x0, -scale*y0),
)
```

`region` 使用 PDF 页坐标，`transform` 将页坐标映射到任务源画布。返回值分别包含 `objects`、`provenance`、`skipped`、`selected_paint_ids` 和源绘制项总数。必须核对所选项、跳过项及未处理项是否覆盖目标图，不能把成功转换的子集当作整图完成。

`SourcePaint` 保留实际资源引用、展开实例身份、精确 M/L/C/Z 命令、变换、裁切和组上下文。重复绘制同一资源仍是不同实例。`paint_index` 是 SVG 绘制顺序，不能与 PDF `get_bboxlog()` 的序号直接混排。`source_text` 仅是未经确认的 Unicode 元数据；字形轮廓由实际资源引用决定，不用坐标最近邻或猜测文字寻找。

轮廓模式是显式选择：字形变成可编辑路径，`text_editable` 为 `False`，不能标成可编辑文本框。该调用不做 OCR、拼写修正或公式推断。对于原本就是位图的文字，这个接口也不会恢复作者的原生字形。

普通填充路径保留 Bezier 控制点；带笔画路径只接受能保持笔画语义的变换。不支持的裁切、混合、蒙版、资源或路径效果抛出 `UnsupportedPdfPaintError`，调用者应将其保留为未解决项。线帽、转角等原生属性的写入不代表预览后端或所有办公应用支持它们，仍需实际验证。

## 嵌入图片的完整放置与裁切

```python
from figure_rebuild.pdf_images import extract_pdf_images

images = extract_pdf_images(
    pdf_path,
    page=page_number,                    # 从 1 开始
    region=(x0, y0, x1, y1),             # PDF 页坐标
    source_transform=[[scale, 0, -scale*x0],
                      [0, scale, -scale*y0]],
    image_indices=selected_image_indices,
)
```

每项返回完整定向 PNG 的 `asset_bytes`、SHA256、`full_source_box`、可见 `box`、相对于完整素材的 `crop`、显式 `fit:"stretch"` 和来源记录。保存素材后，用这些字段创建 image 对象。不要再次把完整图片缩进已经裁过的可见框；也不要把文本字典的 bbox 当作完整图片的缩放框。

`image_indices` 指实际 image-info/SVG 图片实例序号，既不是 xref，也不是所有 SVG 绘制项的 `paint_index`。相同 xref、相同像素或相同位置都不足以证明实例相同；同一 RGB 资源也可能有不同软蒙版。接口核对绘制实例、完整变换与顺序，并以该实例实际编码的图片和软蒙版为依据。

当前支持轴对齐放置、翻转、矩形裁切和共同定位的栅格软蒙版。不支持的旋转、斜切、复杂裁切或组合效果明确抛出 `UnsupportedPdfImageError`。每个源图片资产仍是位图；不能把它称作可编辑矢量，也不能以整页截图替代未处理的文字和路径。

转换之后继续执行[细节保真与输出审阅](fidelity.md)：逐字、逐连接记录源证据，保留未知项，检查实际导出的 PPT 及其预览，再记录本次文件哈希绑定的审查。
