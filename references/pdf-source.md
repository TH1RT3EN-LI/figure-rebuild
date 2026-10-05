# PDF 源几何与图片实例

这些 Python API 生成有来源记录的候选对象，不能代替内容识别和实际 PPT 审阅。源 PDF 保持不变；新的素材、清单、未解决项和输出应写入新任务目录。PDF 读取需要可选的 PyMuPDF 环境。

对无法证明可转为路径的描边，可以显式选择 [原生描边采样](pdf-stroke-sampling.md)。该接口保留原始裁切并返回透明图片，必须标记为不可编辑路径；`outline_paths` 不会自动采用这种表示。

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

解析器默认最多解析 200,000 条路径命令。已知的大型输入可显式使用 `extract_outlined_svg(svg, max_total_commands=250_000)`；允许范围为 1 到 2,000,000。失败后不会自动抬高限制。`parser_limits` 区分实际消耗的 `parsed_commands_including_failed_paths` 与保留的 `expanded_commands`：失败路径此前的解析消耗不能因被标为 unsupported 而退回预算。资源预算异常中止整个解析。单路径 200,000 命令、输入字节/节点、200,000 展开节点及 256 层输入/展开层次的独立限制继续生效；未使用的定义和元数据也受深度限制。提高预算只允许读取更多源记录，不代表绘制项已转换或验收通过。

`SourcePaint` 保留实际资源引用、展开实例身份、精确 M/L/C/Z 命令、变换、裁切和组上下文。重复绘制同一资源仍是不同实例。`paint_index` 是 SVG 绘制顺序，不能与 PDF `get_bboxlog()` 的序号直接混排。`source_text` 仅是未经确认的 Unicode 元数据；字形轮廓由实际资源引用决定，不用坐标最近邻或猜测文字寻找。

轮廓模式是显式选择：字形变成可编辑路径，`text_editable` 为 `False`，不能标成可编辑文本框。该调用不做 OCR、拼写修正或公式推断。对于原本就是位图的文字，这个接口也不会恢复作者的原生字形。

首份来源候选应直接使用这些真实路径、字形和图片实例，逐项核对转换、跳过及未处理记录。不要先手工近似可用的原始轮廓，再把修补后的旧清单称为首次生成。每次改变提取策略都创建新候选，并核对原 PDF、ROI、对象身份、绘制顺序及未受影响资产的哈希。

若原 ROI 在图注边缘截断字形，可按 [源画布裁切](source-canvas-clip.md) 显式声明并重放证明；保留完整源命令，由相同独立幻灯片边界裁切。该接口只接受其有限上下文，不能用来跳过不支持的 clip、mask 或 group，也不能手动删去越界控制点。

[选中字形的原生上下文事实](pdf-selected-glyph-context.md)可另行核对完整 PDF/SVG 字形顺序、真实嵌入字体程序、选中实例及其原生上下文。该隔离接口只返回来源事实，不授权画布裁切、图片重建或几何修改，也不替换现有整幅 ROI 检查；字形、样式、最终原生编码与实际外观仍须分别验证。

普通填充路径保留 Bezier 控制点；带笔画路径只接受能保持笔画语义的变换。不支持的裁切、混合、蒙版、资源或路径效果抛出 `UnsupportedPdfPaintError`，调用者应将其保留为未解决项。线帽、转角等原生属性的写入不代表预览后端或所有办公应用支持它们，仍需实际验证。

受支持的上下文可通过[有限范围可见性证明](pdf-visibility.md)记录完全不可见的源实例：支持精确矩形裁切的严格空交集，以及单条 butt 虚线完整支撑位于裁切区外的情形。原命令、样式和实例身份保留；未知效果、边界接触及证明预算失败不能因此跳过。

`evenodd` 仅在有几何证明时转换成原生 `nonzero`：简单多边形的内部必须互不相交、不嵌套。曲线可通过凸控制多边形证明；单一轮廓也可通过各段控制凸包严格分离、相邻段仅共享端点和单段单调投影证明。二直线加一条 cubic 的简单轮廓另有半平面证明。另可通过[有限完整曲线绕数证明](pdf-fill-equivalence.md)接受曲线嵌套：完整控制凸包必须隔离，每个原子边两侧的奇偶与非零绕数判断必须相同；同向内轮廓形成绕数 2 的区域时仍拒绝。隐式闭合、重复直线顶点与重复 close 只在填充证明副本中规范化；无绘制命令的空 moveto 子路径也仅在该副本中排除，并记录原命令索引。零长度 L/C 不会因此被当成空子路径。输出命令及描边的开口语义不变。原始 cubic 控制点保持不变，证明失败不会通过折线近似绕过。

导出后的反方向问题由构建后处理负责：原 `nonzero` 复合填充在办公应用的 `evenodd` 行为下可能出现接缝孔。构建会对受支持的[直线轮廓](pdf-winding.md)和[完整三次曲线](pdf-cubic-winding.md)执行有证明的规范化，绑定实际路径、填色、位置及来源，并重新核验最终整数路径。曲线先完整恢复，再判断是否改写；拒绝项保持恢复后的源几何。不要自行分拆为重叠色块或用近似折线填洞，透明度和曲线轮廓都可能因此改变。最终 PPT 的孔洞及细接缝仍须实际检查。

单个 `M p L p` 的起终点必须在原 SVG 数字 token 的有理数域中完全相等（同样检查 H/V 的原数值），且无填充、无虚线、使用 butt 端帽时，按[SVG 描边规则](https://www.w3.org/TR/SVG2/painting.html#StrokeLinecapProperty)记录零面积跳过。颜色和效果上下文仍须通过原检查，宽度和 miter 参数不能非法。该窄规则不会把浮点解析或平移后坍缩的非零线段、近似重合、圆头/方头、闭合或复合路径当作无绘制面积；每次跳过保留来源身份、原点和证明原因。

矩形纯填充可以与矩形 clip/ROI 求精确交集，回执明确记录改变的坐标。 多个各自为凸多边形的 nonzero 纯填充轮廓也可分别与共同矩形求交，保留各轮廓的方向及叠加绕数，包括反向孔洞。使用原输入坐标的精确有理数判定和交点；最多 16 个源子路径、8,192 条命令，默认 65,536 次方向判定。输出不含曲线近似。单轮廓交点仅转换一次 binary64 并记录坐标误差；复合轮廓只要出现非零转换误差就拒绝，防止窄缝或孔洞被舍入合并。每个非退化输出仍须为同方向凸轮廓。`polygon_fill_intersection` 分别披露精确交集、方向、输出绕数与舍入范围；几何证明不等于相同抗锯齿或跨应用像素。凹多边形、曲线、描边及未经证明的 evenodd 继续拒绝。矩形纯描边只有在四条边的保守包络都不接触裁切区时才能跳过；变换和包络用有理数与向外舍入核对，不能把近似相似变换当作精确的半线宽证明。未知滤镜、蒙版和组效果仍拒绝。支持的无效果上下文中，完全透明的绘制以及没有描边、所有控制点重合的零面积空格字形，均保留独立跳过记录。

描边边界使用中心线的 Bezier 控制点凸包与实际线帽/转角的支撑范围。round/bevel 转角与 butt/round 线帽使用半线宽圆盘的仿射支撑；square 线帽包含额外的端点延伸；miter 保留保守外延。平方根通过整数运算向上取界，最终边界向外舍入，不把圆角当尖角而无故拒绝本来完整位于 clip 内的曲线。控制点凸包可能偏宽，不能据此裁改曲线。

无填充、无虚线的平滑 miter 描边可进一步通过[精确相切支撑证明](pdf-tangent-stroke.md)：仅在精确相似变换、每段导数非零、全部实际连接严格同向相切时使用半线宽支撑。证明只影响保守边界计算，不修改原曲线或裁切；未知几何、近似相切和预算失败继续使用原包络，不能通过扩大 ROI 规避。

独立水平/垂直直线的 butt 端点纯描边可与矩形求真实交集。仅接受无虚线、无填充及精确轴对齐相似变换；每个 M/L 子路径的有限笔画矩形分别核算。完全位于裁切区内时保留原描边，完全在外时保留跳过回执，实际相交时生成一个复合填充并只应用一次原描边透明度。矩形内部必须互不重叠；重复、交叉、嵌套笔画及曲线拒绝。交点的 binary64 转换必须保持全部边界坐标的严格次序，不能合并窄缝；回执披露坐标误差，不将其称为像素误差界。默认最多 8,192 条命令、256 个子路径，源命令及目标仿射映射的证据保留。

复杂裁切可通过“对整个笔画保守边界无影响”证明解除：用精确有理数细分 Bezier 控制包络，证明裁切边界不接触目标边界框，再计算恒定绕数。支持隐式闭合、孔洞与单个 clipPath 中多个子图形的并集。定义祖先上的未知样式/继承关系明确拒绝。

另支持一个窄范围的真实交集：单个 nonzero 环形纯填充与严格嵌于其内外边界之间的单个 nonzero clip。精确反向共享直线仅按一对一抵消，再证明留下的内外简单轮廓方向相反、边界严格分离且内层包含关系成立。输出保留原 clip 曲线及原孔洞曲线，不扁平化或删除小洞；其余矩形裁切和 ROI 必须包含输出控制点凸包。`annular_fill_intersection` 明确记录输出命令改变、源输入未变及证明精度范围。其他跨边界、接触边界或预算耗尽的情况仍拒绝，不通过移除裁切蒙混过去。

独立的[凸裁剪直线交点接口](pdf-convex-line-clip.md)可处理单个纯填充轮廓与完整凸直线裁剪链。它只允许直线穿越边界，完整保留留在同侧的三次曲线；曲线控制包络跨界仍拒绝。输入和输出保留精确有理数，源绘制、字体程序、实际裁剪上下文和最终原生编码由调用方另行绑定。`SourcePaint.commands` 已位于根坐标，不能再次乘 `SourcePaint.transform`。该显式接口不放宽 `outline_paths` 的默认拒绝策略。

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

`get_text("dict")` 图片块只保留为 `auxiliary_text_block` 诊断。真实 PDF 已复现该结果的 `number` 与 `get_image_info()` 的 `number` 指向不同图片；同号或相近几何均不能证明实例关联。因此未绑定候选的 `text_dict_bbox_pdf_pt` 为 `None`，候选 bbox 另存于诊断，不影响完整放置和裁切。主身份仍由完整原生/SVG 顺序、CTM、尺寸、实际解码摘要及附属蒙版共同核验，错误的主身份继续拒绝。

矩形裁切保留完整定向图片和 crop。复杂裁切将单个嵌入图片及真实 cubic/绕数裁切重放到隔离 PDF，再生成 8x 采样的派生 RGBA；此时 `asset_source_box` 等于可见框，crop 为零。原矩阵、源完整框、曲线和采样步长保留在来源记录中，不栅格化其他文字或路径。采样步长不是颜色或边缘误差保证，仍需实际对照源 PDF。

默认要求图片放置严格轴对齐，任何非零旋转/斜切分量都不会静默舍弃。显式 `allow_affine_rasterization=True` 可用完整图片仿射矩阵生成上述派生素材；`source_transform` 本身仍限对角缩放/翻转和平移。不支持的外部软蒙版、组合效果、非中性透明组及未知原生设备行为明确抛出 `UnsupportedPdfImageError`。原生解码实测版本为 PyMuPDF 1.28.2；运行时检查所需能力，缺失时拒绝处理。

对于需要保持原图过滤和裁切边缘的实例，可显式设置 `native_occurrence_rendering=True`。此模式直接向原生绘图设备转发所选真实图片、原 clip、绑定的 SMask、默认色彩空间及受支持的 RGB 透明组，避免先解码为 RGBA 后再放入临时 PDF 所引入的过滤差异。独立文字、路径、渐变和其他图片不转发；目标之后新增的 clip/group 也不应用到已绘制目标上。仅支持 Normal、alpha=1、无 knockout 的指定组结构，未知组合继续拒绝；仿射图片仍需单独开启 `allow_affine_rasterization`。

带 Matte 的来源位图可另行开启[原生 Matte 采样](pdf-native-matte-sampling.md)：`allow_native_matte_sampling=True` 仅与原生实例绘制共同使用，限定无 Decode 改变的 8 位 DeviceRGB 图片及同尺寸 8 位蒙版。保持实际图片、蒙版和裁剪回调，不二次合并已解码 alpha。该模式没有 RGB/alpha 误差界，仍须最终整图和局部实际复查；默认 Matte 拒绝策略保留。

原生实例模式以源像素整数网格向外扩展存储框，每边透明填充小于一源像素，并在框内以 8x 采样；原始 ROI 仍作为原生裁切，扩框不能新增 ROI 外内容。`box`、`visible_frame` 和 `asset_source_box` 统一描述派生框，crop 为零；紧致原框和完整原矩阵另存于回执。8x 是采样间距，不能声称 RGB/alpha 误差上限或像素无损。Swift 原图局部与完整 PPT、SAM2 独立原生图片层的对照应分别检查，不能用一个隔离样例替代整图验收。

仅在 `native_occurrence_rendering=True` 时可显式设置 `native_sampling_scale=4`；允许值为 4 或 8，默认仍为 8。两档保留相同源实例、原生上下文及整数存储框，采样倍率写入回执。更高倍率并不保证更接近源 PDF 的特定显示倍率；选择另一档后须新建资产与完整候选，分别检查实际 1×/2×/4×输出，不能沿用旧图的验收结果。

每个源图片资产仍是位图；派生 PNG 也不是作者原始编码字节或可编辑矢量。原 PDF 字节保持不变，不能以整页截图替代未处理的文字和路径。

### 精确裁掉矩形图片的全透明存储边缘

若原生导出在透明图片框边缘出现额外灰线，可显式调用 `figure_rebuild.image_alpha.derive_opaque_rect_image(image_object, job_dir, new_relative_png_path)`。接口仅接受无旋转、无 crop、`fit="stretch"` 的 RGBA PNG：alpha 必须只有 0/255，全部不透明像素必须恰好组成一个实心矩形。它生成新的 RGB PNG 和修改后的图片对象，并返回绑定原/新资产、原/新框及像素支持的回执。只删除 alpha=0 的存储像素，不按颜色删白边、不合成背景；保留全部可见 RGB 字节及 PNG 的 dpi 元数据。其他元数据、色彩配置、部分透明度、孔洞和不连续支持继续拒绝。

原图与旧清单保留；保存回执，将返回对象放入新版本清单后重新 `review → build → review-output`。裁后框由原像素比例计算，像素到画布的浮点编码误差限定为 1e-9 canvas px；实际 PPT 的 EMU 量化及原生图片变换还须独立核验。这只是已声明位图的精确支持裁切，不证明作者 PDF、采样滤波或跨应用像素等价，也不会自动处理其他图片。

部分透明度或非矩形支持可另行显式调用 `derive_rgba_border_image(image_object, job_dir, new_relative_png_path)`。该接口只裁掉外层 alpha=0 的完整行列，生成 RGBA PNG；裁后矩形内的所有 RGBA 字节（包括透明像素的 RGB）、非零 alpha 像素及 dpi 都原样保留，孔洞和不连续支持也不改变。空支持、没有可删除边缘、未知元数据/色彩配置及原有放置限制继续拒绝。回执使用 `exact-rgba-transparent-border-trim-v1`，不声称不透明矩形或 RGB 形式，也不降低原二值 helper 的准入要求。采用同一像素放置编码预算，实际原生图像边缘仍须单独复查。

转换之后继续执行[细节保真与输出审阅](fidelity.md)：逐字、逐连接记录源证据，保留未知项，检查实际导出的 PPT 及其预览，再记录本次文件哈希绑定的审查。

受支持的来源配置可另运行 [verify-source-fidelity](source-fidelity-cli.md)，从原 PDF 重新核验清单、resolved scene 和实际 PPT。退出码 0 仅表示声明范围内的来源保真；它不替代语义、视觉或用户验收。规范化路径、裁切及其他尚无完整重放证明的配置可能返回 `UNRESOLVED`，不能通过缩小报告内容改称整图通过。

使用原生 PDF 绘制项时，还应先检查[蒙版绘制归属](pdf-paint-context.md)：用于定义蒙版的黑色形状不是页面上的黑框。需要降级恒定轴向渐变时，使用[恒定渐变接口](pdf-constant-shading.md)；单位域、N=1 的 DeviceRGB 线性渐变可使用[线性渐变接口](pdf-linear-shading.md)，保留真实资源矩阵、裁剪曲线，并记录原生颜色编码误差。两者均绑定真实资源与原生绘制序号，不能把 SVG 图片序号或原生 bboxlog 序号混用。
