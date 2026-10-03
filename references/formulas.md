# 高清数学图版

`scripts/render_formulas.py` 使用真正的 pdfLaTeX 或 Tectonic 生成透明 PNG，并保留可再次排版的 TeX、矢量 PDF、引擎和字体参数及 SHA256 审计。它不读取原图像素，也不把裁剪截图当作公式重绘。

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

每个公式输出 `.tex/.pdf/.png/.json/.log/.dependencies.txt`；批量索引是 `formulas.json`。审计记录引擎、字库哈希、实际嵌入字体、TeX 的宽/高/深度、生成 PNG 墨迹框、未旋转 baseline 和采样比例。PPT 插入 PNG 时将它标记为 `generated_latex` 来源；保持来源 TeX 和 PDF，与原始论文图片分别管理。图版保留数学质量，公式本身是图片；移动和缩放可编辑，修改内容应改 TeX 后重新生成。
