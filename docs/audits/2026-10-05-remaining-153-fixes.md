# 原有 153 项的继续修复记录

2026-10-05，基于请求开始时冻结的 153 项开放问题。当前状态以 [逐项台账](2026-10-04-detail-resolutions.json) 为准。

本轮完成 39 张图的来源修复及实际成品复查，关闭 57 项原问题：45 项字体、字距、基线或数学字形问题，以及 12 项渐变、箭头、图片、图注、括号或省略号问题。原 153 项中仍有 96 项未关闭；另记录 1 项共用字体交付限制，共 97 项开放。原始 296 项现在 251 项关闭、45 项开放；78 项后续发现中 26 项解决、52 项开放。所有模型关闭项的用户验收仍为 pending。

## 实现与复核

- 附加字体 family 可只提供实际使用的 italic/bold/boldItalic 等角色；默认 family 仍要求 regular/bold。每个实际使用角色的字节摘要和 Unicode 覆盖都核验，不补造未用字体角色。
- 旋转的源基线锚点用于计算原生文字框中心。非对称 inset 和 baseline offset 围绕真实源锚点旋转，正负 90°、任意角和 180°均有控制；原来的零旋转数值保持。SVG 主稿和最终 OOXML 审查使用同一含义，并独立检查原生 frame/inset/rotation。
- 新增只读 used-SFNT-glyph 比较接口，核对显式源 GID 到 Unicode 的轮廓、advance 和 side bearing；限定字节、字形、轮廓操作及复合字形深度。源 GID 由独立原生绘制上下文绑定，名称或相邻 bbox 不作为身份。CFF 与 OTTO、glyf 与 TrueType 文件头必须一致。字体导入成功不替代字形证明。
- 来源中的部分子集采用显式作业本地别名。重新编码/转换保留原程序和派生回执，使用的实际轮廓及 advance 核对，未宣称整个 family 或 hinting 等价。四份 CFF 包装件的错误 TrueType 文件头修正为 OTTO，失败及后继保留。
- RAG 源绘制 13 是 type-2 Pattern 203 中的 N=1 DeviceRGB 线性渐变。加载完整 Pattern 核对真实 native pointer，保留 Pattern Matrix、绘制 CTM、圆角三次曲线裁剪和顺序。原 9 块量化颜色轮廓替换为一条连续原生渐变路径。对颜色和 stop 编码的完整分段线性域给出精确有理数误差证明，固定上限为 1/255 RGB 分量；这不涵盖最终几何量化或渲染器差异。
- DDPM 源绘制 3 的两个实际字形绑定到嵌入字体程序、GID 1/34、真实字形矩阵和完整五层原生裁剪。新增显式凸直线裁剪接口，以精确有理数只求直线交点，保留同侧三次曲线控制点；曲线包络跨边界、非凸/重复绕向裁剪或预算耗尽均拒绝。独立 minus 字形被证明填充交集为空，箭头头部只截去长杆，最终保持可编辑曲线路径。私有早期探针曾重复应用已展开的根变换，其无效结果及纠正记录保留；生产接口的根坐标约定有独立真实 PDF 对照测试，未放宽自动 `outline_paths` 的拒绝策略。
- DUSt3R 原问题来自把完整位图缩放进已裁剪的 bbox。源图片实例 0 / 原生绘制 1 现在保留完整矩阵、实际 Matte 蒙版和原始裁剪回调，点云尺度和红色相机顶点一并恢复。新增显式原生 Matte 采样仅允许无 Decode 改变的 8 位 DeviceRGB 图片和同尺寸 8 位附加蒙版，不二次合并已解码 alpha；默认拒绝保留，其他图片/文字/路径不转发，干净 replay cookie 另行核验。采样步长为 8×，没有 RGB/alpha 误差界。原生相机和点云 4× 对照实看确认对齐，所有独立矢量坐标轴及标签未变。
- SwitchML 五支交叉箭头由 15 个真实 shade callback、完整 clip 链、实际 ICC profile、采样表和顺序绑定。五个旧颜色轮廓替换为五条独立可编辑渐变路径，绿色前景及蓝色箭头白缝恢复；原描边、139 个活动标签和其他对象不变。真实表不是两端点线性函数，首个两端点假设因超过固定预算而拒绝；后继按完整表选择分段 stops，并证明源分量代理在原控制包络内含 stops/方向编码的误差不超过 2/255。该界不涵盖 ICC 到 sRGB 插值或渲染器 RGB 等价，默认自动 shading 准入未改变。四箭头中间版因短蓝箭头仍污染白缝保持 partial，五箭头后继通过全图和 4× 复核才关闭原 D01。
- ColBERT 图 2 的 14 个、图 3 的 2 个微小位图实例分别重建为 42 个、6 个可编辑三次曲线椭圆点，并保留局部灰色背景。原有轮廓追踪使填充相互抵消，黑点变成短痕或消失。实际原生实例、完整 clip 回调及采样字节重新绑定；质心、墨迹面积和宽高比用于显式位图基元拟合，没有原始矢量恢复或逐像素等价声明。背景使用真实可见 bbox，保留把整数 PNG 存储框误作可见 bbox 的中间版及纠正记录。图 3 的第三个、位于 Document Encoder 后的位图不在本次修复范围，旧零轮廓记录未升级为可见性证明。最终 452/101 条路径、34/29 个活动标签和 14/2 个原生恒等组均独立读回；核完整嵌套叶对象顺序，而非只核顶层对象。全图 1×/2×及 7 个源/旧/新原生 4× 局部实际查看后关闭省略号问题，图 2 的原 D03 描边问题继续开放。
- Decision Transformer 恢复源 Lora-Bold 与两处 R-hat/帽号位置；ZeRO-Offload 恢复 Param update 两侧及 Update Super Node 顶部间距。CoAuthor 的 102 个活动片段恢复源字体和混色标点间隙。其同一 Libertine 字体程序在小型大写标题和普通图注中，对相同 Unicode 使用不同 GID；按实际原生字形映射分为两个兼容子集，别名身份含映射摘要，未改变来源字符或原生 item。三图均经新的完整原生字体重放、终态读回及 23 页匹配 4× 对照实看。
- 两张 3D Gaussian Splatting 图恢复 75/93 个活动片段，包括图注、旋转标题与圆点；另实看 16 页匹配 4× 对照。其 Type1 转 CFF 的确切轮廓比较因坐标量化失败，失败及后继保留。私有显式策略在原 1000-unit em 下限定每个对应控制坐标误差 ≤ 1/1024 design unit，并核全部命名字形的操作/点数结构和精确 advance；使用字形及真实 ligature 槽位另从新原生 font handle 独立重放。回执明确 contour equality 为 false，无 raster/hinting 误差界；没有放宽公共 SFNT 精确比较。
- 新增显式 `character_spacing`，以画布像素指定相邻字符的附加 advance，限定可打印 ASCII、单行、不换行、左对齐活动文字。源 Unicode 保留，SVG 禁用 kerning/ligature，原生 PPT 分为继承全部样式的逐字符 run，并编码百分之一 point 间距；用逐字注册字体测量重新计算实际 ink/advance 所需框，不能沿用整词 kerning 的测量。输出审阅将间距记录绑定源声明，不接受遗漏、改值或伪称应用已验证的回执。最终应用位置仍须独立读回；未声明的原有文字不变。
- VMamba 的 12 个标题和小字号标签绑定实际源 em、原字体 GID 以及连续 fill_text/stroke_text。MuPDF 1.28.2 SVG 的 stroke-text 宽度遗漏外层 CTM expansion；按实际原生 linewidth/matrix 修正后，独立轮廓与活动填充仍在 Office 小字号中错位。逐字符间距修正长标签宽度，另以 5 个显式私有 CFF 字形墨迹字体合并源填充和对应描边，保留 Unicode、原 hmtx advance 与源半径/join/miter，去掉 75 个中间描边路径。派生前全部原 SFNT 点位核对；终态实际原生 font handle 的 78 个使用字形与派生字形轮廓在 f32 下逐项相同，143 个活动文字对象和 411 个有序字符完整保留，221 条原路径与 58 个图片实例不变。全图 1×/2×及全部 12 个匹配 4× 标签实看后关闭 D01。原始轮廓/family 相等、FreeType stroke/组复合/像素等价均不宣称；原生文字描边被 LibreOffice 忽略的失败探针和所有后继保留。SSM 活动数学、字体交付和 PowerPoint/WPS 重开继续开放。
- SimCLR 按实际源矩阵恢复纵向 em，并显式处理 98%/98.4% 横向压缩及 Type1 FontMatrix 的斜切；四个私有变体保留源字号与受限字形映射。最终 82 个活动片段、416 个字符、10 个图片绑定和 121 条路径完整读回。LibreOffice 26.2.5.2 的 CFF 转 Type1 子集导出另将相对操作数舍入至 1/1024 design unit；全部 109 个使用字体/Unicode 对逐相对操作数与该版本上游策略精确核验，actual subset 曲线再由独立加载的实际字体字节核对 f32。它与来源到 CFF 的固定控制/advance 预算分别记录，不宣称实际子集与原 CFF 或原字体轮廓精确相等。源/实际文字位置最大观察差为横向 0.643 px、纵向 0.404 px，保留实测残差。源 ROI 从底边 300 显式扩展至 310 PDF points，完成 107 个被切字形并按原绘制顺序补回 4 个标点，完整第三行与署名显示；新增标点继承 paint 层级 33，避免排序移到末尾。全图 1×/2×及四组同网格 4× 实看通过后关闭 D01/D02/D04。第三行仍为可编辑轮廓，照片重采样 D03、自动语义及字体交付保持开放。
- ALEX 删除独立手加的重复图注，保留来源实际编码的 33 个非空格字符，以真实来源物理间距恢复单一图注基线；实际 LinLibertineTB Type1 字体转换、使用字形与终态子集逐项绑定。来源没有编码空格，不补造字符。终态原生图注位置最大观察差为横向 1.084 px、纵向 0.321 px，字形量化与子集导出舍入分别留证。另按源独立位图测量恢复 Model 1.2 至 2.4 的短陡箭头，最大误差括号的右端绑定红色标记对象，导线置于区间中点。原位图内部普通标签字体 D03 继续开放；箭头/括号只是显式位图拟合及端点关系，不宣称原始矢量恢复。
- BLIP-2 的两枚雪花及 VGGT 的两个相机原本是独立图片资源。分别删除两条手工雪花路径和 18 条手工相机分段，恢复真实图片 occurrence 0/1（paint 49/50）和 0/5（paint 1/44），绑定源完整矩阵、clip、蒙版、字节和绘制顺序。8× 原生实例采样保留透明整数框；VGGT 的真实 DeviceRGB Matte 使用已有显式准入。全部其他图片、路径、活动文字和顺序读回不变。三个图的全图 1×/2×及七张匹配 4× 局部已实际查看；原雪花分叉、相机近直立前脸及双色过渡恢复。图片明确为独立 bitmap 对象，内部不提升为可编辑矢量；局部边缘滤波差异和空 RGB/alpha 误差界保留。
- 最终 PPT 的字体、字符、源基线、frame/inset、曲线、图片字节和全部对象顺序均读回。曲线和绕向证明从原始 authoring 中间产物按完整生产前置条件重放，不重新处理终态。各图实看源全图 1×、最终原生 PPT 全图 1×/2×及匹配像素步长的 4× 局部；两批主要字体修复共实看 206 页 4× 对照。RAG 另实看源/旧/新渐变局部，白缝消失。

源字体别名依赖、扩展 Unicode 编辑范围及未嵌入字体记录为一项共用开放限制，关联 33 张已复核的图。PowerPoint/WPS 重开和播放未验收。普通文字显示修复不关闭自动公式、连接语义或作者轮廓的活动文字问题；同图中的箭头、照片、遮挡和诊断覆盖问题按原 ID 保留。

## 本轮关闭项

| 图 | 问题 ID 后缀 |
| --- | --- |
| ccf-2020-02-f02 | D01, D02 |
| ccf-2020-03-f04 | D01, D02, D04 |
| ccf-2020-04-f01 | D01, D02 |
| ccf-2020-06-f01 | D01, D02 |
| ccf-2020-09-f02 | D01 |
| ccf-2020-11-f02 | D01, D02 |
| ccf-2020-11-f03 | D01, D02 |
| ccf-2020-12-f03 | D01 |
| ccf-2020-14-f01 | D01 |
| ccf-2020-18-f01 | D01, D02, XC01 |
| ccf-2021-01-f01 | D01 |
| ccf-2021-03-f03 | D01 |
| ccf-2021-09-f02 | D01 |
| ccf-2021-10-f01 | D01, D02 |
| ccf-2021-12-f02 | D03 |
| ccf-2021-13-f01 | D02 |
| ccf-2021-13-f08 | D01, D02 |
| ccf-2021-17-f02 | D01 |
| ccf-2021-17-f05 | D02 |
| ccf-2022-01-f03 | D01 |
| ccf-2022-02-f02 | D03 |
| ccf-2022-07-f01 | D01, D02 |
| ccf-2022-12-f04 | D01 |
| ccf-2022-13-f01 | D01 |
| ccf-2023-01-f01 | D01 |
| ccf-2023-03-f01 | D01 |
| ccf-2023-05-f02 | D01, D02 |
| ccf-2023-05-f04 | D01 |
| ccf-2023-06-f01 | D01, D02 |
| ccf-2024-03-f01 | D01, D02 |
| ccf-2024-03-f02 | D01 |
| ccf-2024-03-f12 | D01 |
| ccf-2024-06-f02 | D02 |
| ccf-2024-07-f02 | D01 |
| ccf-2024-08-f02 | D01, D02 |
| ccf-2025-01-f08 | D02 |
| ccf-2025-03-f02 | D01, D02 |
| ccf-2025-05-f13 | D01 |
| ccf-2026-05-f01 | D01, D02 |

## 验证与证据

冻结源码快照 007 含 248 个输入文件。完整来源环境 1135 项 Python 检查通过，8 项可选环境检查跳过；Node 55 项中 54 通过、1 项可选控制跳过。wheel 80 文件、源码包 254 文件载荷检查通过；在仓库外安装该 wheel 后再次执行相同 1135 项检查，所有加载的 figure_rebuild 模块都来自安装目录，8 项跳过。GitHub CI 另按推送后的准确 head 核验。

后续快照 008/009 分别含 252/254 个输入文件。DDPM 裁剪后完整 Python 检查为 1148 项，Matte 支持后为 1158 项，均通过、各 8 项可选检查跳过；新增 13 项凸裁剪和 10 项 Matte 控制。Matte 测试用独立原生整页绘制核对同网格结果，黑/白 Matte、alpha、ROI、重复实例、未烧入其他绘制、Decode/重采样/CMYK 拒绝及 incomplete replay 均覆盖。以上整图关闭只对应逐项台账中的实际证据。

快照 010 的 188 个运行时、测试和构建输入与完整测试通过的 009 逐字节相同。更新后的 wheel 含 81 文件、源码包含 260 文件，载荷核验通过；wheel 的 74 个运行时文件与快照相同。在仓库外安装该 wheel 后，1158 项检查再次通过、8 项可选检查跳过，实际加载模块全部来自安装目录。首次私有测试启动器在运行测试前发生语法错误，原失败记录保留，后继启动器未改变安装包或测试。安装包 SHA-256 为 `e2d9f8955283c524e7821a97eefb884018c1a56940754f08b2e24b63edbd02ac`。

快照 011/012 的运行时、测试与构建输入为 190 个。逐字符间距及审阅绑定修复后，Python 共 1166 项：1158 通过、8 跳过；Node 共 58 项：57 通过、1 跳过。Node 输入在两快照之间逐字节未变，因此保留完整通过记录。wheel 含 82 文件、源码包 262 文件，75 个 wheel 运行时文件与快照相同；仓库外安装 wheel 后再次执行 1166 项 Python 检查，结果相同，加载模块全部来自安装目录。

论文、字体、PPT、完整预览和所有失败历史保留在外部 `figure-build-data/work/detail-audit-20261003`。以下摘要均相对于该审计根目录；图的逐项成品绑定、输出审阅、native readback 和原图/预览摘要已加入台账。仓库不分发私有论文或字体二进制。

| 外部证据 | SHA-256 |
| --- | --- |
| `reports/remaining-153-001/START-LEDGER.json` | `6f5334fb1ba2f6206abee40d818ef59f4158b1effadd5960f5b137789bcf30e2` |
| `reports/remaining-153-001/LEDGER-RESULT-001.json` | `958185037739c4463ebeb30a5df79b0822238f77da89da2a249e94cacd148e76` |
| `reports/remaining-153-001/LEDGER-RESULT-002.json` | `26fbb8338da88ee98a711347fc3b00ea2303fe139f346b6a29199ee64ace9ab1` |
| `reports/remaining-153-001/LEDGER-RESULT-003.json` | `7bebac3bd45d3ed2d76690be18ae1b8b940b20bd93406ef5ead8837f1f926ff5` |
| `reports/remaining-153-001/LEDGER-RESULT-004.json` | `b6383e4473f5eb9f1184141b17ad73277d6993b9c73bfd6e874d937d0b253732` |
| `reports/remaining-153-001/LEDGER-RESULT-005.json` | `de8d6351753afee005aff6812f414f20601641d2fd6fe4099a3f9fd7d1c9f001` |
| `reports/remaining-153-001/FONT-REVIEW-RESULTS-001.json` | `7b67ddd1a16b48c61ec2eda9975aabccfd0169d1f71b737916633dc453594820` |
| `reports/remaining-153-001/FONT-REVIEW-RESULTS-003.json` | `db1e065d8d70457c231793c66bb1a10d89f5b3d9e5697ed82461d28c08493ccd` |
| `reports/remaining-153-001/FONT-REVIEW-RESULTS-004.json` | `c05d1c0682669e57d9fac7e7ead4c4211a9a4ff6815f45a335e5af6cec2728f1` |
| `reports/remaining-153-001/FREEZE-007.json` | `ce542605ea70e928f27e0e9cd8a658a340619806ecedc97c0e874239aa8d9c9c` |
| `reports/remaining-153-001/full-tests-007.json` | `446c29e79163bac18181d917bf6af0ce550f9ea3811c9a91b4b9489490ae90eb` |
| `reports/remaining-153-001/wheel-tests-007.json` | `2fd271d25ca0788c72a7df278b1f96695c7fa11ea4656b1907514b8f644bbf98` |
| `reports/remaining-153-001/anchor-foundation-node-tests.stdout` | `22971789c7e67ba5f6256b67ca6dbb8ab887e1844030a3b75766510ff224cde8` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-001.json` | `cc497be1393831411c99ed466cd45836c2e0d503d77ea1810db5ab6dbf9df9e6` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-002.json` | `f8efe6d264f1e043f47b6cbad967b025e1f3db1a0afead105b03aa8b77668316` |
| `reports/remaining-153-001/LEDGER-RESULT-006.json` | `62965da2b317d7aae0804d6638f105b95185e65880032d0b805b1c37844cb38b` |
| `reports/remaining-153-001/LEDGER-RESULT-007.json` | `79e10896966ca0855afcfdc00e268ce128b27605c37cc49c4692cd8d77eb31c7` |
| `reports/remaining-153-001/FREEZE-008.json` | `56d634856519cbab3ab9e3e8f7f3341a77e70c623859b942df0ed9758483f6ae` |
| `reports/remaining-153-001/FREEZE-009.json` | `8e53324f03b2976b8c6ed3157ae2a586742fcc7ab591135a83b86b6fc7435729` |
| `reports/remaining-153-001/full-tests-008.json` | `3d36d16265814b712e48e03a393bf09ca17689b7a02932954e231f29d390a9b2` |
| `reports/remaining-153-001/full-tests-009.json` | `3360fe2b489007b9199676bd76f4c1d08a895cd00b057b967155be4005a13130` |
| `reports/remaining-153-001/DDPM-NATIVE-CONVEX-CLIP-PROOF-001.json` | `6dfc3da90a4da1ae6c80f255523f6f52cd3fc646efdb8eac556fe66018d3f695` |
| `reports/remaining-153-001/DDPM-CONVEX-CLIP-CORRECTION-002.json` | `cd9411e2eca5470d976953a7503aaedba62ab12afa656d5e5b47cbf8b25d8a35` |
| `reports/remaining-153-001/FREEZE-010.json` | `adccee1c855bc588aff3eb20b4e5785ccf1c332577908d5f39f02cd44e248a6b` |
| `reports/remaining-153-001/package-check-010.json` | `109c41660ec39c96db50773a8d7aa21d5158b4b527ab76d9ca838715b0c83350` |
| `reports/remaining-153-001/wheel-tests-010-retry001.json` | `fe374f8cceedaa318ad8cf22dee9a7844fc095921eb4477c0fa2f65e5ab983c9` |
| `reports/remaining-153-001/wheel-loaded-modules-010.json` | `675408d87eb8bc516febb26709cb17fd87bd2d68e9d87f08c203e373f339518a` |
| `reports/remaining-153-001/LEDGER-RESULT-008.json` | `09439e4d79e8faa4a05160116c26758f4ec89f6fb4ed53206391d72b4bb3a8bb` |
| `reports/remaining-153-001/SWITCHML-NATIVE-GRADIENT-PROOF-004.json` | `d76d82f827d674abec9049ec5a6ffe5559eddf617415a7f01ef59c208d133827` |
| `reports/remaining-153-001/LEDGER-RESULT-009.json` | `15962652e8db2606b804b1fddfe493e008d0ed2867812ce9cd5b8a68de03e769` |
| `reports/remaining-153-001/LEDGER-RESULT-010.json` | `8b6c4c28ea57200700d07c48787993670fa04ceec394a22a5a62ca4639cf7061` |
| `reports/remaining-153-001/COLBERT-DOT-PROPOSALS-001.json` | `afa1e63477bfde1a64a7ac980e3666b44571f20ecd5f22c8ff9d0de4070f4049` |
| `reports/remaining-153-001/COLBERT-F03-DOT-PROPOSALS-001.json` | `9fb0f164191d32c2da3d50e10b9a13ec889391690a9d0d3c8b2e9b0b3dd4a38b` |
| `reports/remaining-153-001/COLBERT-DOT-BACKGROUND-FRAME-CORRECTION-001.json` | `afa28e3ddb3a376addc4f03c4378325506f877c13fd273c8eb87a51ea1616a86` |
| `reports/remaining-153-001/LEDGER-RESULT-011.json` | `26ecfe1e476f53c771b77c0730a0b25d9bc57f10421097812cf739fec6434239` |
| `reports/remaining-153-001/LEDGER-RESULT-012.json` | `c949dc2dd7b83cc4e242761f4af162bfe5fad1e3b5a0755c2b0795da483bf441` |
| `reports/remaining-153-001/FONT-REVIEW-RESULTS-005.json` | `ec8023927ec07bc0308b22ee3156bcf2c7f15c3d184ced8dc1e019cd7fa39534` |
| `reports/remaining-153-001/FONT-REVIEW-RESULTS-006.json` | `6dc271a8ac27bc54401854bac23c67e49b71fd77a9b2c1b51c0d15d794fb4c19` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-003.json` | `596cf6d6d18f9a92aa638141440d35e92d0156116514810294aabf6206e35a6d` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-004.json` | `e27011e733c53c7f947c4f757da453f815595b51765b3d6ab990c2c785b08919` |
| `reports/remaining-153-001/SOURCE-FONT-NATIVE-GID-PARTITIONS-001.json` | `334d4bb755d99647da1181c9a4750b4e74bd2685cb1702a4af9c23e8b2fa5beb` |
| `reports/remaining-153-001/TYPE1-BOUNDED-GEOMETRY-CONVERSION-001.json` | `bd04790708bb4cfd22f2eca6ebed7168e8bef3f74bdd966ee7aee3dcec5cb806` |
| `reports/remaining-153-001/FONT-BINDING-REPLAY-RESULTS-010.json` | `5293ae0fa12598eaa50e3c9363a0adb4e22773324591f7c661e85217144c4442` |
| `reports/remaining-153-001/FONT-BINDING-REPLAY-RESULTS-010gsv2.json` | `287e55ad52157bef60ac230959e605e137f2feab0b76bb1c677b9da294475557` |
| `reports/remaining-153-001/VMAMBA-SOURCE-EM-BINDING-001.json` | `83dbeb77d7ed738664ba29a965b85ed665b4868b6caacf8bf7a2cee4c68b4d0a` |

| `reports/remaining-153-001/FREEZE-011.json` | `efe6f9207d9fe1ba669f2aa280750cd1d494e651f128d3a18ed629a3c8a1b05d` |
| `reports/remaining-153-001/FREEZE-012.json` | `1c1a901795edbc8b29a2dd9ac80d4adbc07cb4ff08f886d8990a5d7f06290ef8` |
| `reports/remaining-153-001/full-tests-and-package-014.json` | `44a4c0357202ba1012ce7db26f875059f7ec67718780cf91b42f9c4942e9ff55` |
| `reports/remaining-153-001/LEDGER-RESULT-013.json` | `7785fec9781287677ad637cda0b305ed5d7ecc53821573bc25ae0f7bc918b7e9` |
| `reports/remaining-153-001/VMAMBA-REVIEW-RESULT-027.json` | `13174e32b60ccc1ccb329d16bdd90e2e4b3f9bf23b715dfc48ebf77f5f561e7b` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-005.json` | `1622485306ff0ea26d4b35c7f0434022d17d0b7cf64006710dc7e9dfa508c4ec` |
| `reports/remaining-153-001/VMAMBA-SOURCE-PAINT-PAIR-NATIVE-MEDIA-026.json` | `68708ac739230cd9e1503e0a3976ec146c2abf504d681632dc2ecbbdafb736c1` |
| `reports/remaining-153-001/LEDGER-RESULT-014.json` | `75db001613a2fcdff36e63517cf8615050f4dd0e320075832ae32d7bcc0fdc1b` |
| `reports/remaining-153-001/SIMCLR-REVIEW-RESULT-024.json` | `f576bdd722faf61d4b5a48cb4f731648909dd065eaf0c88165bbadc46133fc6a` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-006.json` | `0b9497f29409e0aab0f7ada52366320881689aa02fd86cb13163172793efe42c` |
| `reports/remaining-153-001/LEDGER-RESULT-015.json` | `b3aaa2bcc4a0c7c99fb8d6df15135381f4bc10a80a384904c0451eadeb7ce94d` |
| `reports/remaining-153-001/GEOMETRY-REVIEW-RESULT-015.json` | `7014803925e8fdc6d429c38e7a8ae2644c6060a5a3e97467ec96d35c523dc923` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-007.json` | `8aab53e71a0bb4ec079fa4ba335f59b242f0c46e78f1a4f894697360069bd3fe` |
