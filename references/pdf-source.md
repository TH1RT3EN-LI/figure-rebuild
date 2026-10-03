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

`evenodd` 仅在有几何证明时转换成原生 `nonzero`：简单多边形的内部必须互不相交、不嵌套。曲线可通过凸控制多边形证明；单一轮廓也可通过各段控制凸包严格分离、相邻段仅共享端点和单段单调投影证明。二直线加一条 cubic 的简单轮廓另有半平面证明。隐式闭合、重复直线顶点与重复 close 只在填充证明副本中规范化，并记录回执；输出命令及描边的开口语义不变。原始 cubic 控制点保持不变，证明失败不会通过折线近似绕过。

矩形纯填充可以与矩形 clip/ROI 求精确交集，回执明确记录改变的坐标。矩形纯描边只有在四条边的保守包络都不接触裁切区时才能跳过；变换和包络用有理数与向外舍入核对，不能把近似相似变换当作精确的半线宽证明。未知滤镜、蒙版和组效果仍拒绝。支持的无效果上下文中，完全透明的绘制以及没有描边、所有控制点重合的零面积空格字形，均保留独立跳过记录。

复杂裁切可通过“对整个笔画保守边界无影响”证明解除：用精确有理数细分 Bezier 控制包络，证明裁切边界不接触目标边界框，再计算恒定绕数。支持隐式闭合、孔洞与单个 clipPath 中多个子图形的并集。跨越边界、接触边界或证明预算耗尽仍未实现精确裁切；不会删除实际有效的裁切。定义祖先上的未知样式/继承关系也明确拒绝。

虚线以原子路径为单位重置相位，输出精确的 Bezier 子曲线。混合路径中的纯 moveto 子路径没有笔画，单独记录在 `skipped_nonpainting_subpaths`；零长度线段、曲线或 `M…Z` 仍拒绝，因为它们可能绘制线帽。这个区别遵循 [SVG 笔画规则](https://www.w3.org/TR/SVG11/painting.html#StrokeProperties)。

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

`image_indices` 指实际 image-info/SVG 图片实例序号，既不是 xref，也不是所有 SVG 绘制项的 `paint_index`。相同 xref、相同像素或相同位置都不足以证明实例相同；同一 RGB 资源也可能有不同软蒙版。`xref` 仅保留为 MuPDF 内容摘要检索的候选编号，不能用它取代实例身份。像素来自真实 `fill_image` 回调的原生图像及其绑定的 ColorSpace、Decode、渲染参数与 SMask；全部绘制项的类型、顺序和边界还须与 bboxlog 一致。SVG 导出的图片重编码可能改变 CMYK 解释，不能作为原始颜色依据。

矩形裁切保留完整定向图片和 crop。复杂裁切将单个嵌入图片及真实 cubic/绕数裁切重放到隔离 PDF，再生成 8x 采样的派生 RGBA；此时 `asset_source_box` 等于可见框，crop 为零。原矩阵、源完整框、曲线和采样步长保留在来源记录中，不栅格化其他文字或路径。采样步长不是颜色或边缘误差保证，仍需实际对照源 PDF。

默认要求图片放置严格轴对齐，任何非零旋转/斜切分量都不会静默舍弃。显式 `allow_affine_rasterization=True` 可用完整图片仿射矩阵生成上述派生素材；`source_transform` 本身仍限对角缩放/翻转和平移。不支持的外部软蒙版、组合效果、非中性透明组及未知原生设备行为明确抛出 `UnsupportedPdfImageError`。原生解码实测版本为 PyMuPDF 1.28.2；运行时检查所需能力，缺失时拒绝处理。

对于需要保持原图过滤和裁切边缘的实例，可显式设置 `native_occurrence_rendering=True`。此模式直接向原生绘图设备转发所选真实图片、原 clip、绑定的 SMask、默认色彩空间及受支持的 RGB 透明组，避免先解码为 RGBA 后再放入临时 PDF 所引入的过滤差异。独立文字、路径、渐变和其他图片不转发；目标之后新增的 clip/group 也不应用到已绘制目标上。仅支持 Normal、alpha=1、无 knockout 的指定组结构，未知组合继续拒绝；仿射图片仍需单独开启 `allow_affine_rasterization`。

原生实例模式以源像素整数网格向外扩展存储框，每边透明填充小于一源像素，并在框内以 8x 采样；原始 ROI 仍作为原生裁切，扩框不能新增 ROI 外内容。`box`、`visible_frame` 和 `asset_source_box` 统一描述派生框，crop 为零；紧致原框和完整原矩阵另存于回执。8x 是采样间距，不能声称 RGB/alpha 误差上限或像素无损。Swift 原图局部与完整 PPT、SAM2 独立原生图片层的对照应分别检查，不能用一个隔离样例替代整图验收。

每个源图片资产仍是位图；派生 PNG 也不是作者原始编码字节或可编辑矢量。原 PDF 字节保持不变，不能以整页截图替代未处理的文字和路径。

转换之后继续执行[细节保真与输出审阅](fidelity.md)：逐字、逐连接记录源证据，保留未知项，检查实际导出的 PPT 及其预览，再记录本次文件哈希绑定的审查。
