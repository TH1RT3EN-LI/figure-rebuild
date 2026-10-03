# 高清数学图版

`scripts/render_formulas.py` 使用真正的 pdfLaTeX 或 Tectonic 生成**字形已转轮廓的 SVG**和透明高清 PNG 回退，并保留可再次排版的 TeX、矢量 PDF、引擎和字体参数及 SHA256 审计。它不读取原图像素，也不把裁剪截图当作公式重绘。SVG 由生成的 PDF 转换而来，包含路径及内部字形引用；不依赖接收端安装数学字体。

先由调用方核对数学表达式、粗体/斜体、上下标和旋转方向，将 `confirmed:true` 写入清单。表达式仅允许有界的数学命令白名单；文档命令、文件访问、宏定义、注释和任意 TeX 程序拒绝执行。pdfLaTeX 固定关闭 shell escape，Tectonic 使用 `--untrusted`。这不是通用 TeX 文档执行接口。

```json
{"formulas":[
  {"id":"projection", "latex":"\\Pi_c(\\mathbf G_{ij}\\circ\\Pi_c^{-1}(\\mathbf p_i,\\mathbf d_i))", "confirmed":true, "font_size_px":18.93},
  {"id":"residual", "latex":"\\mathbf r_{ij}", "confirmed":true, "font_size_px":18.93, "rotation_deg":90}
]}
```

```sh
python /absolute/path/figure-rebuild/scripts/render_formulas.py \
  --spec /absolute/path/math.json --output-dir /absolute/path/assets/latex \
  --font-manifest /absolute/path/project/fonts/manifest.json \
  --engine /absolute/path/tectonic
```

字体通过外部 registry 的 `formula_rendering.manifest` 解析；也可直接传 Computer Modern 的文件 registry。每个 PFB/TFM 文件必须有登记的 SHA256。渲染将这些原始字节复制到隔离工作目录，通过引擎依赖清单确认字体实际使用，并检查 PDF 字体名；任何未登记替代字体均报错，不静默回退。运行时需 Poppler 的 `pdftocairo` 和 `pdffonts`，可选 portable Tectonic 不必更改系统环境。

`font_size_px` 是 96 px/in 坐标系中的 em；换算为 TeX 点时使用 72.27 pt/in。旋转是顺时针，支持 0/90/180/270 度。PNG 仅裁掉生成图版的透明边距，不裁参考图。默认 768 DPI，至少每个显示像素保留 8 个采样像素；指定 `display_size_px:[width,height]` 后会按目标显示尺寸自动提高 DPI，最多 4096 DPI。过大的尺寸要求会拒绝输出。不要为凑参考框而随意改变纵横比。

可选 `design_size:10` 固定使用 cmr10/cmbx10/cmmi10/cmsy10/cmex10 的 10 pt 光学设计，并缩放到指定 em，适合参考图使用固定 Computer Modern Type1 字形的情况。省略时按 TeX 标准的光学字号选择。两种模式都保留实际嵌入字体审计；必须通过原图对照选择，不能把较粗当作普遍更准确。

可选 `stroke_width_px` 在真正的 PDF 字形上使用 FillStroke，默认 0，范围 0–2 显示像素。它不改变数学排版，只对字形添加明确记录的描边，适合调用方已经对照原稿确认的粗度修整。参数换算为 PDF 点（72 pt/in），pdfLaTeX/Tectonic 由内部模板写入受控 PDF 指令，用户表达式仍不能包含 `special` 或 `pdfliteral`。PNG 从带描边的矢量 PDF 渲染，不使用位图膨胀伪造粗体。这个参数不是已识别出原始字体，应先比较候选，确认后逐元素记录。

对照原稿后，可通过 `--alphabet-font-manifest /absolute/path/mathfontregistry.json` 选择登记的 MathJax v2 TeX OpenType 字母字体。仍由 Tectonic 的真正 XeTeX 引擎排版，使用 mathspec/fontspec 加载文件，绝不调用 MathJax 浏览器渲染器。这一模式以 MathJax Main Regular/Bold/Italic 和 Math Italic 排版 Latin 变量、粗体及数字；希腊字母和运算符明确保留 Computer Modern 字库。默认模式仍是全 Computer Modern。

补充 registry 的 `fonts` 应包含 `MathJax_Main-Regular`、`MathJax_Main-Bold`、`MathJax_Main-Italic`、`MathJax_Math-Italic` 四个稳定 ID，各项登记 `path`、`sha256`、`postscript_name` 和 `face_index:0`。文件必须位于 registry 目录内。渲染器核对原始 OTF 字节和字体内部 PostScript 名，使用精确文件名加载，并验证 PDF 中每个嵌入字体都有登记文件的实际依赖；替代字体报错。记录官方来源 URL 和许可证，随 job 保存字体及 registry。官方字体可从 [MathJax 2.7.9 TeX OTF](https://github.com/mathjax/MathJax/tree/2.7.9/fonts/HTML-CSS/TeX/otf) 获取；这是可选字体资产，不覆盖项目默认 font manifest。字体推断仍需说明其来自曲线形态匹配，而非作者原文档的字体 metadata。

每个公式输出 `.tex/.pdf/.svg/.png/.json/.log/.dependencies.txt`；批量索引是 `formulas.json`。v2 审计的 `asset_path_base:"audit_directory"` 明确表示输出路径相对该公式 JSON 所在目录，便于整个 job 搬迁；渲染 API 返回的 `assets` 也遵循此规则。v2 审计绑定所有六个输出文件的 SHA256，同时记录引擎、字库哈希、实际嵌入字体、实际依赖、TeX 的宽/高/深度、生成 PNG 墨迹框、TeX baseline 原点、旋转和采样比例。字体 metadata 有单独内容哈希，构建阶段通过固定审计和依赖关系验证来源，无须再次访问外部字体或执行 TeX。

SVG 的 `vector_effective_viewbox` 使用 96 px/in 坐标系，边界与 PNG 生成墨迹框一致。0/90/180/270 度旋转使用真正的几何变换，两种交付形式共享最终宽高，不会因切换 SVG 而跳位。校验拒绝 SVG 中的活文字、字体样式、图片、script、foreignObject、DTD 和外部引用；只接受明确轮廓几何和已存在的内部引用。

SVG 根 `width/height` 使用高清 PNG 的实际像素宽高，`viewBox` 仍保持自然墨迹坐标。对于按 SVG intrinsic 尺寸栅格化的工具，这提供至少 8x 的源采样。PPT 的显式 frame 决定显示尺寸，公式几何、纵横比和基线不因此改变。审计保留 `svg_intrinsic_pixels`、`svg_intrinsic_rasterization_scale` 及 `vector_geometry.intrinsic_pixel_policy:"match_png_fallback"`，绑定实际 SVG 字节；构建不偷改已审计素材。

Artifact Tool 按 PPT 显示 frame × `globalThis.devicePixelRatio` 先解码 SVG，增加 intrinsic 尺寸本身不会改善这个后端的高倍预览。0.5 构建在真实 PPT 重导入前显式设定 SVG 解码采样为 8，并保存 `render-audit.json`；源媒体字节、PPT frame 与原始 1x 诊断尺寸不改。实际对照已验证同一 PPT 中只有 SVG 区域清晰度改变，PNG/文本/连线不变。这是渲染器采样设置，不替换预览图片，也不代表 WPS/PowerPoint 已验收。

公式在 figure manifest 中使用专门的 `kind:"formula"`，不再伪装成一般截图图片：

```json
{"id":"projection-label", "kind":"formula",
 "audit":"assets/latex/projection.json", "audit_sha256":"<该审计文件的SHA256>",
 "representation":"svg", "baseline_anchor":{"x":210,"y":135}, "font_size":18.93}
```

`representation` 缺省是 `svg`，可显式选 `png`。PPT 在 SVG 之外保留同一位置的高清 PNG 回退；公式图版可移动和缩放，内容修改则重新生成 TeX 图版。SVG 图版的字形是曲线，不冒充 PowerPoint 公式编辑器可改的原生公式。修改普通图形或文字不需要重排公式。

定位必须选 `box:{x,y,width,height}` 或 `baseline_anchor:{x,y}` 中的一种。`box` 默认按墨迹纵横比 contain 居中，不拉伸；显式 `font_size` 则保持真实显示 em，并要求图版能装入 box，不静默缩小字号。baseline 使用真正 TeX 原点和墨迹裁边偏移，只支持未旋转图版；旋转图版使用 box，禁止推测旋转后的文本基线。helper 兼容旧四项 box 数组，但返回标准 box 字典。

最终显示尺寸会重新核验 PNG 回退的采样，SVG 模式也必须满足至少 8x。放大 em 或 box 导致采样不足时构建失败，调用方应以更高 DPI 重新生成；不能只引用生成时的旧采样结果。表达式、颜色、字号、字重/字库或源文件变化后必须生成新审计并重新固定哈希。普通图片不得直接更换成 `kind:"formula"` 来跳过数学审计。

`formula_asset.resolve_formula_asset(element, job_dir, asset_root=None)` 是只读集成入口；返回 `placement`、SVG/PNG 的 job 相对路径、审计及 `hash_files`。`asset_root` 可传冻结 job 镜像，所有声明绝对路径先对原 job 归一化，再从冻结镜像读取。越出 job、审计哈希变化、输出哈希变化、损坏的 PNG/PDF/SVG、TeX 内容与审计不一致、无字体依赖来源、PNG/SVG 边界不同或最终采样不足均拒绝。

```sh
python -m figure_rebuild.formula_asset \
  --job /absolute/path/job --asset-root /absolute/path/frozen-job \
  --input /absolute/path/formula-elements.json
```

旧 v1 audit 仅在显式 `representation:"png"` 时兼容，仍须固定审计、TeX、PDF、PNG 字节并验证内容与字体 metadata；它没有完整 SVG/日志/基线原点审计，baseline 或默认 SVG 需要重新生成 v2。原图、生成图版、TeX 和 PDF 始终分别管理。
