# 原有 153 项的继续修复记录

2026-10-05 开始，2026-10-06 更新。基于请求开始时冻结的 153 项开放问题。当前状态以 [逐项台账](2026-10-04-detail-resolutions.json) 为准。

本轮完成 84 张图的来源修复及实际成品复查，关闭 126 项原问题：55 项字体、字距、基线、数学字形或活动文字问题，以及 71 项渐变、箭头、图片、图注、边框、括号、省略号、来源诊断或预览描边问题。原 153 项中仍有 27 项未关闭；本轮另发现字体交付限制、SAM 2 白色路径接缝、InstructGPT 圆形边框独立编辑及 ColBERT 原生裁剪白底细接缝四项开放问题，共 31 项开放。原始 296 项现在 288 项关闭、8 项开放；83 项后续发现中 60 项解决、23 项开放。新增发现中的白色填充透明缺口另已解决，不计入原 153 项的 126 项关闭数。所有模型关闭项的用户验收仍为 pending。

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
- Swift 五支环路箭头绑定原生图片 paint 11/142/144/154/158、各自完整仿射矩阵、实际附加蒙版及对应 clip；删除十条等宽手绘箭身/头，恢复渐细、软边与开口分叉。原 87 个活动片段、406 个有序字符和 167 条其他路径不变。源 RGB 根组和继承色空间的非隔离子组本来在严格准入内；误启用更窄 RGB 扩展策略的失败尝试保留，最终使用严格原生政策，没有放宽接口。
- CoAuthor 两个浏览器图片原生 paint 4/5 替换 15 条整数台阶与近似配色路径，恢复暗橄榄色圆角、淡蓝框条和三枚实心控制点；102 个活动对象、1215 个有序字符与 55 条其他路径保留。两图全图 1×/2×及七张匹配 4× 局部实看通过；原 bitmap 明确为 bitmap，插值、扩展字体交付和自动语义限制不提升为等价证明。
- ZeRO-Offload 的 102 个真实图案图片实例按完整 CTM、源 mask 和实际 clip 重新采样，纠正把完整 64×64 棋盘 tile 缩入可见局部 bbox 的错误。源 4× 本身具有细密点纹；原清单“均匀灰色”描述的是 1× 外观，未据此捏造纯色源填充。原生圆框保留，粗交叉纹恢复为来源细纹。两支 12 M 箭头的 native fill_path 133/197 另独立读取；旧 get_drawings 矩形重建反转了箭身相对头部的绕向，抵消重叠填充。真实 M/L/Z、完整 clip 无作用证明、源实际 ICC 句柄与色程序、非零绕向和源顺序一并恢复。最终非零填充到简单边界的回执从实际 authoring 独立重放，55 个活动对象、310 个字符、60 条其他路径和 102 个图片绑定全部核验。全图 1×/2×及五张同网格 4× 实看确认细纹与连续蓝箭头；颜色分量编码界不扩展为栅格等价。
- SAM 2 图 7 的实际 Type3 字体只含 `d1` 度量程序，可见字形另由三条路径绘制。沿真实 Form 的 F1 资源引用和完整资源栈绑定原生字体 handle；新建同 xref 包装件的早期身份比较失败并保留。95 个非空格字符的原始路径起点标记与真实 Unicode/GID 顺序逐项对应，所有标记及轮廓能精确重组整条原路径。三个私有逐字符 CFF 墨迹字体及物理字距恢复 9 行、98 个普通 Unicode 字符，包含三个源编码空格，不使用整词 PUA。Type3 文字层的累计 advance 较粗，以可见起点作为排版来源；固定来源起点对应界为 0.3 local point，使用字形重复控制和 CFF 编码界分别为 0.001 canvas px，实际重复控制差小于 0.000002 px。终态实际子集相对操作数和 advance 按 LibreOffice 26.2.5.2 舍入独立核验，再从实际 handle 读回全部字形；源/终态原点最大观察差为横向 0.648 px、纵向 0.266 px。另从真实 M/L/C/Z 恢复十条卡片边框，保留非零绕向、完整矩阵、ICC 转色及 no-op clip 证明，十个原图片及其他对象未变。全图 1×/2×和三组标题、两组卡片 4× 实看后关闭 D01/D02。原始字体/family、hinting/raster 等价不宣称，当时关联 34 张图的字体交付和扩展编辑限制继续开放。
- SAM 2 图 2 的六个 frame/step 标签恢复为源粗体衬线活动文字，40 个普通 Unicode 字符包含六个源编码空格；图 3 的十条标签路径恢复为 14 行、94 个字符，分别保留六个粗体和四个常规样式。两图绑定真实 Form F1 资源、metrics-only Type3、可见逐字符起点和完整原路径重组，逐实际子集操作数及真实 native handle 核验；源/终态原点最大观察差分别为 0.611/0.397 px、0.167/0.497 px。图 3 的原 ToUnicode 将 prompt/mask/points/box 的首字母错误编码为大写，可见源墨迹却为小写；四处显式人工确认的更正保留原始元数据、大小写诊断和早期拒绝版本，未称原编码 Unicode 全部相同。两图全图 1×/2×及全部 16 个标签 4× 对照实看。
- SAM 2 图 2 的八个实际照片实例按完整 CTM、真实 clip/mask 和原顺序以严格政策重新采样，显式 8× 整数透明框匹配原生可见框，纠正 contain 将完整照片缩入裁剪框的错误。frame 3 step 2 的黑边消除；frame 2 两步保留源照片纹理，源 4× 本身也具有细网纹，不捏造去网格或通用滤波等价。最终八个照片 4× 实看与媒体字节、位置、顺序读回通过，D01/D02/D03 关闭。
- 同图另修复五支箭头及五个加减号的白色底层透明缺口。八个原生不透明白色复合绘制的 170 条原子路径逐项证明 winding 为 0/1 或共线零内部，因此非零填充是其精确集合并；最多两次精确有理数 de Casteljau 二分仅在证明几何中使用，输出完整原 C、矩阵、no-op clip 和原 paint 顺序保留。全部十个 4× 局部实看确认原透明缺口消失，210 个最终叶对象与八个原生恒等组完整读回。分组后箭头底层仍有细微抗锯齿接缝，新增 E02 保持开放；一形状多 path 的私有预览探针仍保留接缝，未作交付。集合并等价没有升级为连续过滤、alpha 合成或像素等价，公共准入和资源预算未放宽。
- InstructGPT 的八处箭杆由真实轴向渐变和透明蒙版恢复为可编辑渐变路径。实际 shade 绑定完整 PatternType2 资源，不能将 21 个 SVG/image-info 实例误认作 21 次普通图片绘制；原生流为五张图片和 16 次 shading。显式 Type4 符号证明限定源 RGB 常量和灰度输入分段函数，精确求相邻矩形并与蒙版交集；灰度输入代理误差不超过 1/10000，不涵盖 ICC、蒙版 alpha 或像素等价。旧蒙版定义 153 不再作为可见矩形输出，真实不透明蒙版另裁剪白底和右侧反馈曲线；该私有曲线见证用精确有理数隔离单调交点并记录控制/相位界，公共准入未变。五个小型 8× 来源合成图保留照片、笔、青蛙的原背景、圆形裁剪、Multiply 和圆形边界，不能称为仅提取图片。圆边框目前在这些位图内，独立编辑记为新增 E01 开放限制。71 个图注片段和 346 个字符不变，实际图注绘制 246 的 45 个字形从源程序、别名、应用子集及真实 native handle 重新核验；同一字体在页上其他正文中的使用被按原绘制身份排除。完整 306 个对象的文字、230 条路径、五个媒体和顺序读回；全图 1×/2×及 14 组同网格 4× 实看后关闭 D01/D02/V3-R01。
- 最终 PPT 的字体、字符、源基线、frame/inset、曲线、图片字节和全部对象顺序均读回。曲线和绕向证明从原始 authoring 中间产物按完整生产前置条件重放，不重新处理终态。各图实看源全图 1×、最终原生 PPT 全图 1×/2×及匹配像素步长的 4× 局部；两批主要字体修复共实看 206 页 4× 对照。RAG 另实看源/旧/新渐变局部，白缝消失。

源字体别名依赖、扩展 Unicode 编辑范围及未嵌入字体记录为一项共用开放限制，关联 40 张已复核的图。PowerPoint/WPS 重开和播放未验收。普通文字显示修复不关闭自动公式、连接语义或其他作者轮廓的活动文字问题；同图中的箭头、照片、遮挡和诊断覆盖问题按原 ID 保留。


- Maximum Flow 图 2 将 128 个活动片段、273 个字符绑定到 19 个实际嵌入的 Computer Modern/Latin Modern Type1 程序，并从原生矩阵区分 43 个横纵 em 变体。恢复真实粗体/斜体、纵向 em、独立下标基线及 ASCII 物理字距；UPM16000 私有别名仍分别限定 1/1024 原设计单位控制点与 1/32 advance 编码界，不宣称原轮廓、hinting 或像素相等。两处原清单中的箭头/成员符号保留正确 Unicode，但源 ToUnicode 实际错误返回 `!`/`2`，人工字形绑定与原始元数据分开记录。全部 116 个使用字体/Unicode 子集对及 273 个实际原生 font handle/GID 逐项核验；位置最大观察差为全图横向 0.905 px、纵向 0.511 px，主体数学标签为 0.156/0.464 px。外部后端单次字体策略仍上限 16；以四个连续绘制顺序的受限构建件和公开原生插入接口组装整图，逐件字体检查、所有 344 个最终对象的次序、变换、字形及 cubic/winding/stroke 回执分别重放，216 条其他路径逐字节保留。整页包/布局检查与实际字形核对通过，但未声称整页 43 字体族策略检查或单次整图构建通过。重复登记的同角色字体只在 SFNT head 保存时间/校验值上有差异，其余表及 head 字段相同后选择一个登记角色。曾误载入旧 helper 的组装/审阅失败及所有后继保留；最终实际加载的所有模块均绑定冻结 012。原图 1×、最终原生 1×/2×与十张同网格 4× 数学、编号和三行图注局部实际查看后关闭 D01。原有轮廓字和自动公式层级未升级；来源字体跨应用交付继续开放。

- Segment Anything 图 1 的五个真实图片实例恢复全部 CTM、裁剪、alpha 和绘制顺序，后两层马图的左边及顶部细条重新出现；Vision Mamba 图 2 的输入图恢复源近景裁剪，九块 patch 均由不同的原生裁剪实例采样，保留非空间排序的真实绘制顺序及来源的间隔，消除重复完整缩略图和重复位置；SAM3D 图 5 的七个图片实例保留真实圆角 clip，恢复输入照片、黑底蒙版和 Stage3 两张照片圆角。三图使用未放宽的严格原生政策，显式 8× 采样不升级为像素/滤波等价。全部其他文字、路径及字体 profile 字节不变；101/134 个活动对象的 664/565 个实际原生字形轮廓、advance、矩阵与此前源绑定成品在 f32 下完全相同，原关闭证据摘要逐项核验。三图完整对象、媒体和绘制顺序读回；源全图 1×、最终原生 1×/2×及全部十个匹配 4× 局部实看后关闭三个图片问题。该图片修复阶段尚未恢复 SAM3D 普通标签，D02 后由下面的活动文字修复关闭。

- SAM3D 图 5 的 26 条原字形路径按真实逐字符 M/Z 标记完整分割并精确重组，三个私有墨迹字体恢复 28 个完整活动字符串、278 个普通 Unicode 字符，其中 252 个非空格字形。来源没有字体或 Unicode 层，文本明确为人工确认；派生 advance 与无墨迹空格位置明确为合成度量，不能宣称源字体、原编码空格或原字宽。重复/编码控制界固定 0.001 canvas px，实测最大 0.000930；首个负字距方案的 19.26 px 原生累积偏移保留为失败。后继采用低于观测字符起点间距的合成 advance 与非负字距，真实原生字形、应用子集操作数及全部起点分别核验，最大观察差为 0.154/0.297 px。另发现原有灰箭头尖端白缺口，作为新增 E01 已解决：完整原生 M/L/C/Z 与 no-op clip 恢复，五个非零/偶奇填充差异由既有严格 cubic normalizer 证明后改写单一路径，三条无差异箭头保持 XML。冗余 editable 来源标志令中间版准入拒绝，删除该可选标志没有放宽策略；29 子路径集合并探针仍有内部抗锯齿接缝，未选为终态。最终单一路径改写同时消除白缺口和观察到的接缝。74 个最终对象、39 条路径及七张照片完整读回，源 1×、原生 1×/2×与全部 16 组 4× 字词/箭头实看后关闭 D02；新增 E01 的修复不计入原 153 项的 75 项。自动识别、原始字体与跨应用交付继续开放。

- DeepSpeed-Inference 图 1 恢复 132 个真实 Calibri/Calibri Light 标签，九组实际源程序/GID 与完整供给字体逐字形核验。X/Y 相邻描边原宽 0.401140 需乘真实外层均匀缩放 0.280632；旧 SVG 扩张墨迹漏乘该项。两条厚填充轮廓替换为真实原生三次曲线描边，保留 miter 10、顺序和三层裁剪的包络 no-op 证明。Office 的 tt/ti/ft 连字不在源编码字形中，逐字符 run 仍可能被合并；五份完整字体副本只删除 GSUB 并改别名，其余轮廓/度量/hint 表保持字节相同。初始负字距方案仍有约 1 px 累积差，最终四份度量变体为 91 个标签显式声明 90% hmtx，并用非负字距补偿真实源起点，41 个其他标签保留源度量。557 个实际原生字形的源轮廓在 f32 下相同，advance 分别与声明的原始或合成字体匹配；132 标签起点的最大观察差为 0.333/0.541 px，不宣称一般 hinting/像素界。全部 1420 个对象、194 条活动文字和 1226 条路径的几何、文字框、描边和顺序重放通过。源 1×、原生 1×/2×及全部十组同网格 4× 局部实看后关闭 D01/D02；另外 62 条原文字未升级来源身份。完整 cmap 保留，跨应用嵌入与编辑验收继续开放。

- 九张图的 237 个二值 alpha 图片支持被证明恰好为实心矩形，显式裁掉全部透明存储像素并同步映射图片框，保留每个可见 RGB 字节和 dpi 元数据。新公开 helper 由安装的 wheel 独立重放，得到完全相同的资产和对象框；不合成背景，不按白色删边。全部 3205 个最终对象及图片媒体、crop、框和顺序读回；未变路径直接对照已验证父成品的全部原生属性，有差异才独立重放。实际原生 PDF 的 237 份 RGB 样本数组均相同且无软蒙版，236 份尺寸相同；一份纯色 CPU 块的 126 个相同颜色样本被原生导出复用为 9×14，原为 14×9，单独证明相同常量颜色场与完整放置框。PPT 框差小于 2 EMU，原生 PDF 框的观察差最大 0.057 canvas px，低于预先指定的 0.15 检查界。实看九张源 1×、原生 1×/2×、独立 PDF 1×及全部 23 组匹配 4× 局部后，Fawkes 括号、触发数字/DNN、CoCoNuT 训练小网络、FlashAttention2、AST 示例、ZeRO 表、MAE 图片、CodaMosa 图底边及 CPU tile grid 的九项新增灰框/灰线问题关闭。调色板编码、custom-picture 裁剪和纯色尺寸严格相等的失败均保留。半透明或非矩形图片、原始采样/滤波、文字活动性、Artifact 采样及跨应用验收没有升级。公开 helper 的十项控制测试与安装 wheel 重放通过，分发检查为 wheel 84 / sdist 277 文件；完整工作区 1188 项测试曾通过（8 跳过），最终 filename/大整数守卫另由十项安装测试覆盖。

- SAM2 图 8 的全原生回调表确认外框是填充与描边共同处于一个透明组，内框则仅填充处于透明组、描边随后独立且不透明。旧 `get_drawings` 聚合的 fs 行遮蔽了组/clip 边界。外框以精确裁切矩形并集一次应用源透明度，内框恢复源真实裁切填充与完整不透明描边；98 个其他对象及 41 个活动文字、309 个字符的实际原生 trace（除 paint seqno）、字体程序全部与父成品相同。实际 PPT 的透明度、颜色、路径、cap/join 和顺序读回，全图 1×/2×及六组匹配 4× 边框实看通过，D01 关闭。源颜色编码界与几何编码界分别记录，未宣称跨应用像素相同。
- GPT-3 图 1.1 以独立 RGBA 政策裁掉仅 alpha=0 的存储行列，所有 253/254/255 样本、RGBA 与 dpi 保留；不将部分透明度变为不透明。安装 wheel 独立重放相同资产和框，实际 PPT 的媒体、crop、框及 523 条未变路径读回。原生 PDF 的 RGB 与解码 alpha 样本逐字节相同；其反向 mask 与 `[1 0]` Decode 通过代数及独立 Pixmap 解码双重核验。全图 1×/2×及六组匹配 4× 边缘实看后，新增原生灰框问题关闭。公开 helper 的完整工作区 1193 项测试通过（8 跳过），14 项安装测试及 wheel 84 / sdist 277 文件检查通过。初次 style 字段、raw authoring 图片名称和 alpha 值集假设失败均保留。

- BYOL 两块不透明底色和三条灰框按完整原生绘制上下文的实际 f32 颜色绑定，用明确的乘 255 后截断编码修正 PDF→SVG 的一个色阶舍入误差。全 394 路径及顺序不变，实际 PPT 的 389 个其他对象属性相同，全部 408 条原生 PDF 绘制除五处颜色外相同。五个内部颜色控制与源图相同，全图 1×/2×和七组匹配 4× 实看通过后关闭 FLAT-COLOR；透明合成、灰线 AA 和虚线弧端点继续开放。原初 tuple/根路径选择证明失败保留；没有采用整体 RGB 减一。

- LightGCN 六个 Layer 层名从实际 Cambria Math 程序/GID和缺失 ToUnicode 中恢复为普通 ASCII 活动文字。完整供给字体和源使用墨迹比较相同，原始源度量保留，定位别名明确声明 90% 合成 hmtx及非负字符间距；源没有编码空格，仅保留物理词间隙。终态 36 个新活动字形的原墨迹/声明 advance 核对，字符起点最大观察差 0.235/0.095 px。324 个其他对象及原有 322 个实际字形控制、advance、矩阵和 RGBA不变。其余 104 个数学字形独立列为 64 组来源轮廓，明确原 Unicode/方程源与语义编辑限制；四个源绑定反例检出改层名、删数学字形、移字形和变色。全图及七组 4× 局部实看后按原清单允许的明确轮廓限制关闭 D03；D02、通用自动识别与字体交付仍开放。

- MTGNN 与 Ansor 的来源诊断门禁接入维护代码。独立从原 PDF/SVG 重新生成的 952/2755 个对象与原核对场景完全一致，原图 1× 像素也相同；所有对象、绘制顺序、几何和样式参与来源比较，不受 256 对象局部边缘预算限制。各六个反例在结构校验仍 PASS 时被来源门禁拒绝。原生全对象读回的源控制点最大观察误差均约 0.000105 canvas px；885/2630 个其他原生对象属性不变，67/125 次已应用填充改写的原/终态真实路径摘要逐项匹配。字形轮廓和代码/数学语义编辑限制保留，清单仍为 REVIEW；空 formula 数组或 text-fit 不再代替来源覆盖。全图、原生 2× 与七/十七组原尺寸匹配 4× 局部实看后关闭两项 D03。新增 Python 全量 1214 项、Node 58 项与已安装 wheel 52 项检查通过；包装 85/280 文件。首次 8 MiB 参考 JSON 限额误用、显式零 transform guard 失败及过宽图注被缩放的查看记录均保留，后继使用既有 32 MiB 场景限额与原尺寸分片；对象和几何证明预算不变。

- LDM 的 17 组数学标记和 VMamba 的五组 SSM 数学表达明确登记为轮廓或活动运算符/轮廓变量混合表达。76/28 条数学路径分别由真实 PDF 绘制及原生字体 GID/matrix、源 SVG 控制点独立绑定；不补造方程源、LaTeX 或公式语义编辑。数学清单接入维护门禁，全部 271/422 个来源对象参与比较，四个结构仍 PASS 的改字形/删字形/移位/变色反例被拒绝，状态保持 REVIEW。终态所有原生属性、顺序、实际媒体和 258/411 个真实字形的控制点、advance、矩阵及 RGBA 未变，独立 PDF 的全部路径绘制相同。源数学控制点编码误差小于固定 0.001 px。源/父成品全部十七/五组 4× 实看，后继全图、2× 和各两组 4× 实看，其他数学图像像素逐一证明与已查看父成品相同。按原问题允许的明确轮廓限制关闭两项 D02。LDM 的零宽 hairline 旧替代宽度另行披露，未升级为设备/像素等价；区域误含已裁切图形、图片 relationship ID 与 OPC 包根绝对路径的首次读回失败均保留。

- SGL、ZeRO-Infinity、ControlNet、SparseCore、Titans MAC、Mooncake 与 OctoTools 七张图接入来源组件诊断，分别比较全部 188/442/488/45/329/621/905 个对象、完整顺序、样式、路径与实际图片字节/放置框。新原 PDF/SVG 重放独立匹配所有矢量，图片继承不可变且已独立源核对的父成品。位图内公式、标签、箭头可共享同一目标对象，明确为 raster；不冒充独立可编辑数学或拓扑。OctoTools 采用真实 DisplayList ROI scissor 排除页外绘制，889 条路径在原预算内重放；首轮全页 SVG 超出命令预算的失败保留，未放宽预算。各五个结构仍 PASS 的删符号、移位、错误符号、实际改图片像素和删关系反例被维护来源门禁拒绝；位图符号反例是改像素，不宣称自动读出算子。最终所有原生属性/顺序、实际 media/crop 读回；固定控制点守卫 0.001 px 下最大约 0.000105 px。ControlNet 一条、Mooncake 153 条填充改写实际双端摘要核对，其余原生属性保持；实际原生 PDF 图片解码样本和实例框与父成品相同。对缺原生 PDF 的五个父成品，另以字节相同 PPT 与字体影子导出留证，未改父快照。七图全图源/终态/PDF 1×、终态 2×及全部三十三组匹配 4× 实看。七项来源诊断问题关闭，完整清单仍 REVIEW；自动语义、活动文字、采样/抗锯齿和字体交付的独立问题继续开放。首次把 candidate 当作改写前路径及矩形冗余闭合命令不一致的失败保留，后继以实际 artifact-authored 框和精确源曲线恢复读回原摘要。

- MoCo 两图、CoCoNuT、GraphMAE 和 GraphMAE2 的五项 Artifact 圆头/圆角预览缺陷关闭。最终 PPT 中 7/25/265/207/138 条受影响描边的原生属性与独立源核对父成品相同，全部 101/254/801/1248/482 个对象顺序、实际 media/crop 读回；107973 个原始源控制点最大约 0.000105 px，固定 0.001 px 守卫不变。共 553 条非描边范围的已应用填充改写逐项核对真实原/终态路径摘要；不从改写证明推导像素等价。维护适配器从实际交付 PPT 解码 SVG，只替换预览中的路径，完整混合遮挡顺序恢复，交付仍为原生可编辑路径。另用字节相同 PPT 和字体作独立原生 PNG 导出；五张源/旧 Artifact/新 Artifact/原生 1×、各新 Artifact及原生 2×，以及十八组匹配 4× 实看。首次图层删除 API 不存在及遗漏原生 helper 标签参数的尝试保留。来源定义、顺序回执及最终原生文件重算，篡改/漏证据/去版本/虚报播放验证的反例被拒绝。维护测试 Python 1224、Node 62、分发 87/284 文件及已安装 62 项通过。不支持的路径、图像采样、活动文字、自动语义及 PowerPoint/WPS 保持各自限制。

- FlashAttention、LightGCN 和 ColBERT 本批关闭三项。FlashAttention 的 259 个真实活动片段、45 个短 Q/K/V/维度标记及 139 条路径全部接入独立来源门禁，七个结构仍 PASS 的反例被拒绝；123 条源描边端点/转角/斜接恢复。LightGCN 的 73 条细灰轮廓从实际 PDF 回调恢复；原清单“圆点”预期经来源核对纠正为真实 butt 端点，原预期历史保留。ColBERT 不再把源核对父版本中的多边形当原曲线，88 对真实填充/描边分别恢复三次曲线与源闭合状态；85 对完整包含于所有源裁剪和 ROI，3 对跨右裁剪边界，采用源白底及所有其他父路径/实际字形不相交的证明保留裁剪。字形证明使用实际原生 PDF 画布比例，修正私有组装器 0.75 假设；无通用遮罩准入。原 486 个叶对象/14 恒等省略号组顺序及 42 圆点保留，插入 3 个局部白底裁剪框，共 489 对象。三图全部 1217 个对象、1647 个真实字形读回；控制点固定 0.001 px 守卫、最大约 0.000105 px，ColBERT 219 次填充改写实际双端摘要核验。源/终态/PDF 1×、终态 2×、28 组匹配 4× 实看；初次漏 reading、误用偶奇填充/路径闭合/控制字段/数值 z-index/固定 PDF 比例，以及异步子进程丢失的失败均保留。四个 score 圆框已无折角；三枚蓝色节点源裁剪边界仍有细 AA 浅边，另登记 E01-CLIP-AA，不冒充像素裁剪等价。自动识别、方程语义、字体交付和跨应用验收继续开放。

- GCC 两图和近线性流算法图的三项采样描边问题关闭。六条原描边恢复为独立原生曲线路径，三图成品均无图片；其他 794 个源对象及 191 个真实活动字形保持。显式有界曲线接口以精确导数细分、细分控制包络和局部斜接支撑核算，固定 256/128/32/4096 预算不变，默认自动裁剪和组策略未放宽。三条完整源支撑包含于所有裁剪，另三条真实跨界；四个局部白底框仅在源白底及所有其他路径/实际字形不相交证明后保留源边缘。实际 Normal/alpha1/RGB 隔离组仅作本源实例的代数复合核对，未宣称通用 RGB/alpha 或像素等价。正虚线在原用户空间保留相位，输出真实 cubic 子曲线，弧位置误差最多 0.0001 目标 px。实际源/最终 PPT 控制点固定 0.001 px 守卫、最大约 0.0001043 px；69 次填充改写实际原/终态摘要核验。全部 804 个对象原生读回，源/终态/PDF 1×、终态 2×和全部十四组匹配 4× 实看，本次未发现新增可见裁剪接缝。源实际回调的虚线数组通过已安装 SWIG floats_getitem 读取，保留首次指针下标失败；空导数、全局斜接包络及预算耗尽探针均保留。公开 Python 1242、Node 62、wheel/sdist 87/287 文件及安装后 1242 项通过，冻结016含 281 文件/208个运行测试构建输入。活动文字、方程 AST、自动语义、字体交付与跨应用验收继续保留各自问题。

- Swin 两图、ZeRO、DPO、CodaMosa、Titans、SAM3D 和 NitroGen 的八项 Artifact 图片采样问题关闭。显式原生解码保留实际 RGB、同尺寸附加蒙版和浮点位置；允许完整 canonical DeviceRGB 隔离根组的政策仍为 opt-in，默认严格政策与资源预算不变。以前 Swin 资产来自原生图片实例 8× 采样，早期图库172的 SVG 标签误记在本回执纠正，原记录保留。维护预览适配器仅从最终 PPTX 媒体、EMU 图片框和绘制顺序计算，在 1×/2×/4× 分别用 MuPDF 采样，并恢复 Artifact 全部混合遮挡顺序；不读取参考图像素、不重导出瞬态场景。全部 1783 个原生对象读回，八个真实作者图片和 1775 个其他 manifest 对象保留；1763 个其他对象的原生属性相同，DPO 一次、NitroGen 十一次填充改写实际双端摘要及完整证明重放，其他样式与位置相同，控制点仍受固定 0.001 px 守卫。八图源/Artifact/独立原生 PDF 的全图 1×、完整 2× 分片及 4× 图片支持区，共 165 张原尺寸对照全部实看。有限图片区域 RGB 数值改善仅作诊断，未宣称整体像素相同、通用 RGB/alpha 界或 Office 滤波等价。MAE 的 4× 诊断未改善，继续开放；DPO 手工原生路径候选的 native-sampling-residual 也继续开放，未用作者位图候选替代其独立编辑能力声明。公开 Python 1254（8 跳过）、Node 66（0 跳过，真实 CPU 像素/蒙版/遮挡控制）、wheel/sdist 89/291 文件及安装后 1254（8 跳过）检查通过，冻结017绑定 212 个运行测试构建输入及 82 个安装/归档模块。图片内部活动文字、自动语义、ICC/组、裁剪和跨应用验收仍保留各自限制。

- TPU 和 Moon 两项 Artifact 图片采样问题关闭。v2 维护适配器从实际最终 PPT 媒体、EMU 框和原生百分比裁剪推导完整图片矩阵，再按真实浮点可见框裁剪采样；不把裁剪舍入为源图片整数像素框、不改交付图片。22 张图的原生位置与 crop 元素均和父版本相同。800 个对象完整读回，778 个其他 manifest 对象未变；616 个其他原生属性相同，162 处填充改写双端摘要及精确证明重放，控制点仍受固定 0.001 px 守卫。全部 48 张源/Artifact/独立原生 PDF 全图 1×、完整 2× 和 4× 图片支持区实看。TPU 的原问题为 2×/4× 平滑错误，这两级有限区域 RGB 诊断明显改善；1× 诊断从 2.5368 增至 3.9271，作为残差保留，不宣称各尺度均更优。Moon 三尺度诊断改善，仅作诊断，没有阈值或通用误差界。裁剪边缘可能仍在滤波中引用邻近存储样本。Python 1260（8 跳过）、真实 CPU Node 67（0 跳过）、wheel/sdist 89/291 文件、安装后 1260 检查通过，冻结018绑定 212 输入和 82 运行模块；旧八图 v1 完整定义和 PNG 字节重放相同，提交0c7b970的16项 GitHub 检查通过。内部位图文字、自动语义、普通 Office 滤波、字体交付及跨应用验收限制继续保留。

- Bao 的 D01 细边框问题关闭。源回调区分 21 次填充和随后真实零宽描边，绑定完整路径、CTM、黑色、不透明、butt cap、round join、无 dash 和真实 clip/group；21 个对象恢复源显式闭合。维护代码通过显式 `stroke_hairline` 写入启用的原生零宽线，保留填充；`libreoffice-pdf` 从未修改的最终 PDF 生成规范 1×/2×/4× 预览，三份直接 Impress PNG 单独保留。21 条最终 PDF 的真实零宽、颜色、闭合与线型独立读回；其余 186 个实际原生对象和 92 个活动文字框/内容与父版本相同。实际 PPTX 全部源控制点维持 0.001 px 守卫，最大 0.000093367454085 px；原生 PDF 导出坐标诊断残差最大 0.073738881966392 px，未包含在该守卫内。14 个字体输入字节相同，注册件除 head 时间戳/校验和外全部表一致，最终 PDF 两份真实字体程序独立绑定且无伪粗/斜/替代。11 张完整 1×/2×/4× 四方对照全部以原尺寸实看，细边线恢复；直接 PNG 黑边差异继续保留。有限区域 RGB 诊断改善，不用阈值替代实看。第一版真实构建因 Node 整数浮点 JSON 格式而未通过严格输出核验，失败与冻结019完整保留；修复仅统一整数数值写法，不四舍五入非整数系数、不放宽类型检查。冻结020与637d9fc绑定 214 输入/83 运行模块，Python 1276（8 跳过）、真实 CPU Node 67（0 跳过）、wheel/sdist 90/294 文件、安装后1276检查及该提交16项 GitHub 检查通过。SVG hairline、跨应用、字体嵌入/扩展编辑和自动语义仍有独立限制。

- CURE 和 CLIP 的两项图片预览采样问题关闭。显式 `libreoffice-pdf` 以实际最终 PDF、物理画布比例和独立有界采样回执生成 1×/2×/4× 预览，三份直接 Impress PNG 单独保留。六张原始图片的实际媒体字节、位置、裁剪、效果和顺序不变；重新生成的 relationship ID 在双端独立解析到同一媒体后才排除，其他图片属性精确比较。全部 1078 个 manifest 和实际原生绘制属性与源核对父版本一致，无填充改写或字形转换。45920 个源控制点独立读回，固定 0.001 px 守卫下最大 0.000104653161543 px；CURE 源视口字形裁剪重新核验。全部 36 张原尺寸全图 1×、完整 2× 和 4× 图片区域对照实看，源文字/虚线像素阶梯及草地/背景/叠层照片纹理恢复。CURE 图片区域三尺度 RGB 诊断从 5.412407/2.536732/7.318610 到 0/0/1.411501，CLIP 从 12.127244/9.758971/5.444924 到 0.078216/2.138745/1.487916；仅作有限诊断，4× 仍有相位/滤波残差，不宣称一般像素等价。沿用冻结020已验证的 214 输入/83 模块、Python 1276（8 跳过）、真实 CPU Node 67（0 跳过）、90/294 文件分发及安装后 1276 检查；本批未改公开代码，未重复宣称新测试。提交28b7b9f的16项 GitHub 检查通过。图片内部仍为位图，轮廓字形、活动文字、自动语义、字体交付及跨应用限制保留；读回时 relationship ID 和源元数据路径的两次失败均保留。

- SAM 2 图 1 的 D02 活动文字问题关闭。源图没有对应的文字层或字体程序；24 条标签路径按原图人工确认 Unicode、大小写、分行与空格，恢复 40 个普通活动片段、424 个字符，其中 381 个可见字形来自真实源墨迹。全部 384 次原生绘制顺序及 24 条路径的实际非零填充、颜色、无作用裁剪独立绑定；仅规范等价的填充闭合弦。五个共用 CFF 墨迹字体通过重复字形求物理基线，381 个字形的 28185 个原始原生控制点与实际保存字形在固定 0.001 px 界内，最大 0.000659704999093 px；不是拿接近的 Times 字体替代来源。43 个空格和字体度量明确为人工/合成；未使用的 Mg 度量探针来自供应字体，实际当前文本均不使用这些补充字形。早期压缩 CFF 的可选曲线末端被 LibreOffice26.2.5.2 转整数，严格读回失败；最终保留同一源控制与 advance，以完整曲线指令绕开该分支。全部 115 个使用字体/Unicode 对按该版本相对操作数 1/1024 design unit 舍入核验，424 个真实字体 handle、子集字节、f32 轮廓和原生颜色独立读取，无伪粗/斜或替代。源派生/终态原点差最大横向 0.246 px、纵向 0.508 px，单列应用排版残差。其余 291 个原生对象精确保持，包括 31 张图片的真实媒体、裁剪和位置、260 条路径；8351 个路径控制点最大 0.000101972913399 px，固定守卫不变。全图 1×、完整 2× 六片及全部 24 条标签 4×，31 张原尺寸对照实际查看。源墨迹普通字形、粗细、数值与布局保持；图片滤波、自动识别、未见字形扩展编辑、字体嵌入/交付及 PowerPoint/WPS 仍开放，共用字体交付关联 40 张图。失败版本完整保留，公开代码未改，不重复宣称新测试；前一提交bc5eb5a的16项检查通过。对应整数转换由[LibreOffice26.2.5.2上游代码](https://github.com/LibreOffice/core/blob/libreoffice-26.2.5.2/vcl/source/fontsubset/cff.cxx#L1300-L1317)支持。

## 本轮关闭项

| 图 | 问题 ID 后缀 |
| --- | --- |
| ccf-2020-01-f1-1 | R02-NATIVE-IMAGE-FRAME |
| ccf-2020-02-f02 | D01, D02 |
| ccf-2020-03-f04 | D01, D02, D04 |
| ccf-2020-04-f01 | D01, D02 |
| ccf-2020-05-f01 | R01-PREVIEW-STROKE |
| ccf-2020-05-f02 | R01-PREVIEW-STROKE |
| ccf-2020-06-f01 | D01, D02 |
| ccf-2020-07-f02 | BYOL-FLAT-COLOR-001 |
| ccf-2020-09-f02 | D01, D02, D03 |
| ccf-2020-11-f02 | D01, D02, D03 |
| ccf-2020-11-f03 | D01, D02 |
| ccf-2020-12-f02 | D03 |
| ccf-2020-12-f03 | D01 |
| ccf-2020-13-f01 | R01-PREVIEW-SAMPLING, R02-NATIVE-ALPHA-EDGE |
| ccf-2020-14-f01 | D01 |
| ccf-2020-14-f02 | SAMPLED-STROKES-001 |
| ccf-2020-14-f03 | SAMPLED-STROKES-001 |
| ccf-2020-15-f05 | D03 |
| ccf-2020-16-f02 | R02-NATIVE-IMAGE-FRAME |
| ccf-2020-16-f03 | R01-PREVIEW-JOIN |
| ccf-2020-17-f02 | D01 |
| ccf-2020-18-f01 | D01, D02, XC01 |
| ccf-2020-19-f01 | R02-NATIVE-IMAGE-RECTANGLES |
| ccf-2021-01-f01 | D01 |
| ccf-2021-02-f01 | CLIP-IMAGE-SAMPLING-001 |
| ccf-2021-03-f01 | R01-PREVIEW-IMAGE-SAMPLING |
| ccf-2021-03-f02 | R01-PREVIEW-IMAGE-SAMPLING |
| ccf-2021-03-f03 | D01 |
| ccf-2021-09-f02 | D01 |
| ccf-2021-10-f01 | D01, D02 |
| ccf-2021-11-f01 | D06 |
| ccf-2021-12-f02 | D01, D02, D03 |
| ccf-2021-12-f04 | R03-NATIVE-PDF-TILE-GRID |
| ccf-2021-13-f01 | D02 |
| ccf-2021-13-f08 | D01, D02 |
| ccf-2021-14-f04 | D05 |
| ccf-2021-16-f02 | CURE-IMAGE-SAMPLING-001 |
| ccf-2021-17-f02 | D01 |
| ccf-2021-17-f05 | D01, D02 |
| ccf-2021-18-f18 | R02-NATIVE-IMAGE-RECTANGLES |
| ccf-2022-01-f03 | D01, D02 |
| ccf-2022-02-f02 | D01, D02, D03, V3-R01 |
| ccf-2022-05-f01 | R02-NATIVE-IMAGE-FRAME |
| ccf-2022-07-f01 | D01, D02, D03 |
| ccf-2022-10-f02 | R01-PREVIEW-STROKE-STYLE |
| ccf-2022-12-f01 | D01, D02 |
| ccf-2022-12-f04 | D01 |
| ccf-2022-13-f01 | D01, D02 |
| ccf-2022-14-f01 | SAMPLED-STROKES-001 |
| ccf-2022-14-f02 | D01 |
| ccf-2023-01-f01 | D01, D02 |
| ccf-2023-03-f01 | D01 |
| ccf-2023-04-f01 | R01-PREVIEW-SAMPLING |
| ccf-2023-05-f02 | D01, D02 |
| ccf-2023-05-f04 | D01 |
| ccf-2023-06-f01 | D01, D02 |
| ccf-2023-08-f02 | D03 |
| ccf-2023-13-f01 | R01-PREVIEW-SAMPLING |
| ccf-2023-13-f07 | D05 |
| ccf-2023-14-f01 | R01-PREVIEW-SAMPLING, R02-NATIVE-ALPHA-EDGE |
| ccf-2023-14-funnumbered-p5 | R02-NATIVE-IMAGE-FRAME |
| ccf-2023-15-f02 | R01-PREVIEW-STROKE |
| ccf-2024-02-f01 | R02-NATIVE-IMAGE-FRAME |
| ccf-2024-03-f01 | D01, D02 |
| ccf-2024-03-f02 | D01, D02 |
| ccf-2024-03-f12 | D01 |
| ccf-2024-06-f02 | D01, D02 |
| ccf-2024-07-f02 | D01 |
| ccf-2024-08-f02 | D01, D02 |
| ccf-2025-01-f01 | D02 |
| ccf-2025-01-f02 | D01, D02, D03 |
| ccf-2025-01-f03 | D01 |
| ccf-2025-01-f07 | D01, D02 |
| ccf-2025-01-f08 | D01, D02 |
| ccf-2025-03-f02 | D01, D02 |
| ccf-2025-05-f13 | D01 |
| ccf-2025-06-f02 | D05 |
| ccf-2025-06-f05 | R01-PREVIEW-SAMPLING |
| ccf-2025-07-f04 | D04, R01-PREVIEW-SAMPLING |
| ccf-2026-01-f02 | R01-PREVIEW-SAMPLING |
| ccf-2026-01-f05 | D01, D02 |
| ccf-2026-03-f02 | D05 |
| ccf-2026-05-f01 | D01, D02 |
| ccf-2026-06-f01 | R01-PREVIEW-SAMPLING |

## 验证与证据

冻结源码快照 007 含 248 个输入文件。完整来源环境 1135 项 Python 检查通过，8 项可选环境检查跳过；Node 55 项中 54 通过、1 项可选控制跳过。wheel 80 文件、源码包 254 文件载荷检查通过；在仓库外安装该 wheel 后再次执行相同 1135 项检查，所有加载的 figure_rebuild 模块都来自安装目录，8 项跳过。GitHub CI 另按推送后的准确 head 核验。

后续快照 008/009 分别含 252/254 个输入文件。DDPM 裁剪后完整 Python 检查为 1148 项，Matte 支持后为 1158 项，均通过、各 8 项可选检查跳过；新增 13 项凸裁剪和 10 项 Matte 控制。Matte 测试用独立原生整页绘制核对同网格结果，黑/白 Matte、alpha、ROI、重复实例、未烧入其他绘制、Decode/重采样/CMYK 拒绝及 incomplete replay 均覆盖。以上整图关闭只对应逐项台账中的实际证据。

快照 010 的 188 个运行时、测试和构建输入与完整测试通过的 009 逐字节相同。更新后的 wheel 含 81 文件、源码包含 260 文件，载荷核验通过；wheel 的 74 个运行时文件与快照相同。在仓库外安装该 wheel 后，1158 项检查再次通过、8 项可选检查跳过，实际加载模块全部来自安装目录。首次私有测试启动器在运行测试前发生语法错误，原失败记录保留，后继启动器未改变安装包或测试。安装包 SHA-256 为 `e2d9f8955283c524e7821a97eefb884018c1a56940754f08b2e24b63edbd02ac`。

快照 011/012 的运行时、测试与构建输入为 190 个。逐字符间距及审阅绑定修复后，Python 共 1166 项：1158 通过、8 跳过；Node 共 58 项：57 通过、1 跳过。Node 输入在两快照之间逐字节未变，因此保留完整通过记录。wheel 含 82 文件、源码包 262 文件，75 个 wheel 运行时文件与快照相同；仓库外安装 wheel 后再次执行 1166 项 Python 检查，结果相同，加载模块全部来自安装目录。

本批来源修复继续使用冻结 012，190 个输入与已验证提交 `2dab218` 逐字节相同；共享分支后续的原创创作实现没有用于这些重建。SAM3D 后继台账校验核对 1323 个唯一带摘要的关闭/新增发现引用及 83 个摘要引用，当前分支运行时差异单列为本批未使用。
DeepSpeed-Inference 后继再核对 1389 个唯一带摘要的关闭/新增发现引用及 87 个摘要引用，77 项关闭和 76 项原清单余项与外部台账一致。

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
| `reports/remaining-153-001/LEDGER-RESULT-016.json` | `1e20e2df5e1a59de618ad5112a4f3d18202b1ab4b9c87f4b4c14bb23a1f65afa` |
| `reports/remaining-153-001/ARTWORK-REVIEW-RESULT-016.json` | `323e069a95e002378828c2bb5302be3d9970003e87c50dc7eaaf95ccd1eb821c` |
| `reports/remaining-153-001/LEDGER-RESULT-017.json` | `b1374b64554123e433bfe3d928877f14b7f5db16e2ef4b62afc40b99c98e104f` |
| `reports/remaining-153-001/SAM-REVIEW-RESULT-017.json` | `64bb68fea13b42eda4f14e4aa7897daf968c9d97c823957c409c8c4d0eb4ed46` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-008.json` | `e36cd5983aeb3b610ddd2814eeb2ec6e5acfd578ebc1c0a9bea6d388094b383d` |
| `reports/remaining-153-001/LEDGER-RESULT-018.json` | `a1548863c85af891120fb593ee01f8e6b4e4ae6b961396afd2e4037bd510716e` |
| `reports/remaining-153-001/SAM-REVIEW-RESULT-018.json` | `c8daaa9cd5e6153f96045f1976640ef132e89137b2f460323426e4f4ac5579b4` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-009.json` | `cc20b37a9a8d974a5c471385066b98aa4bc0c9e19273163444b0f4f02dc4a2d9` |
| `reports/remaining-153-001/SAM-WHITE-OPAQUE-UNION-PROOF-045.json` | `d15f6d30aad58e762c422e31cb9c729543a6ef6c9961fd2825a1bb23b2f41607` |
| `reports/remaining-153-001/LEDGER-RESULT-019.json` | `15bf99deff1657cf9f8259ad04c727dca494f2a2f2a82e2adaf06d6462ec5df5` |
| `reports/remaining-153-001/MAXFLOW-REVIEW-RESULT-019.json` | `1f0e99c4a677d8d186fb9b27d103f2b503ef2fe7f70541fd794cfbe3b70e7fcd` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-010.json` | `d7da451daf00ad67d771370df53b8f0d0bb90334bece008a17def5ccbcefacb5` |
| `reports/remaining-153-001/MAXFLOW-PROCESS-ATTEMPTS-019.json` | `e20d229824e143cb34ebd91cde2e603d12b9ed4eef20351e01eb274a2faf5465` |
| `reports/remaining-153-001/LEDGER-RESULT-020.json` | `13171e55a34e4e34f7326d73a9843e8988b5b2a895b3cde8ef427b3bd44ac62c` |
| `reports/remaining-153-001/INSTRUCTGPT-REVIEW-RESULT-020.json` | `dd2844da7d4d93cd950d4533b0040942627ca74195cf19ef36b34f1b017c9fb1` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-011.json` | `3652f7b023a476f0d50593a277e5a3e0a68a63e4b5bd786f015ad36321cc583b` |
| `reports/remaining-153-001/INSTRUCTGPT-PROCESS-ATTEMPTS-020.json` | `594cc4c87514eb668c67a21a3c7c3018d705c679523898cbcc07c2e838c637eb` |
| `reports/remaining-153-001/LEDGER-RESULT-021.json` | `364e8763239f856e1a7555cd11cc8c8a9693a9791db0544c9316cef5b3c5244e` |
| `reports/remaining-153-001/PHOTO-REVIEW-RESULT-027.json` | `d063343c73d334363224b02348a1e831ec3efe41131a8e668f1e1e57e31f3a9d` |
| `reports/remaining-153-001/PHOTO-PROCESS-ATTEMPTS-027.json` | `815e67508d58130350072fa2645ba873014c2cecc700785a2661fd8c12fb9759` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-012.json` | `c64cc99388c154cb4c34b4590b0f81b90800aca198f16274747a317dccdd9b88` |
| `reports/remaining-153-001/LEDGER-RESULT-022.json` | `eec940b0f71255d01cf400e6947af0cbd753fd51271fc7dea159a9763afbc097` |
| `reports/remaining-153-001/SAM3D-REVIEW-RESULT-052.json` | `b701a9d6c2bc23630d183785e49ccadabbab5f354c182ac0f32a73396ee75b0f` |
| `reports/remaining-153-001/SAM3D-PROCESS-ATTEMPTS-052.json` | `e3e70bae0674508a03fd0ee4f01aed8242343e5355e3400142e11faaf6f5af87` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-013.json` | `0e67ff67544dfb80efd66c8cae5b0851c3684cf91f5eb36caeb99f391eaae790` |
| `reports/remaining-153-001/LEDGER-RESULT-023.json` | `27294dc973e46cdaded05d2cf5a2da4bc15b35a48fac1d796567328091edef2f` |
| `reports/remaining-153-001/DEEPSPEED-REVIEW-RESULT-045.json` | `407a4324755638a657ca4b3a90e87c76e8b53e64726dc63814f0cb2649419253` |
| `reports/remaining-153-001/DEEPSPEED-PROCESS-ATTEMPTS-045.json` | `23e070afacb9963d8da540c1c561ee320a90192b6d560e6ce94ef05e19d83b5f` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-014.json` | `a230142361204b9fcc9aee4f9f8321d93b7705916f8bfaf43211c616f8890912` |
| `reports/remaining-153-001/LEDGER-RESULT-024.json` | `6dbdcfdcb31af09d502271b04f907784c3d5834503a7bcab4c732970f0b9bc87` |
| `reports/remaining-153-001/OPAQUE-RECTANGLE-REVIEW-RESULT-058.json` | `13619b9b5f07d91e12285d653b45f4465c0c3152a232558603e0ccb8db6d0b6c` |
| `reports/remaining-153-001/OPAQUE-RECTANGLE-SOURCE-NATIVE-PROOF-057v2.json` | `f61637903b26507cbf2d0d46313cc00690688bc18e7d2d5c1086b08a532908b1` |
| `reports/remaining-153-001/OPAQUE-RECTANGLE-PROCESS-ATTEMPTS-058.json` | `54e83e188d421f7cf6d191508dbe9f8613166977ce704ca4d1576d1d39ee54f1` |
| `reports/remaining-153-001/public-image-alpha-checks-058/checks.json` | `48a95909b46a61c1a6f8ae470fff25ce1bc181c09b41342f7a106d51380621d7` |

一致性复核 024：1512 份唯一已绑定证据和 92 条既有总结引用全部通过；来源构建的 190 份运行时/测试输入与已验证的 `2dab218` 冻结字节一致。新裁切 helper 由安装 wheel 独立核验，未将当前工作区运行时误当作源构建运行时。

| 证据 | SHA256 |
| --- | --- |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-024.json` | `d8752700b25e90dbd63f1b049e089bd501955400c98fa6f423d8b197e26b3785` |
| `reports/remaining-153-001/LEDGER-RESULT-025.json` | `7f122aea278011753780a41a0f64a7965a14aaab5c3b0fc0cbba53387ba55872` |
| `reports/remaining-153-001/BACKGROUND-BORDER-REVIEW-RESULT-069.json` | `4f702cc9684bd15c8d1f1a5ce094caf574b2fcd16924d04c206c2eda963e1dd5` |
| `reports/remaining-153-001/BACKGROUND-BORDER-ATTEMPTS-069.json` | `0c2d12082c4646ae6218f2c260ea793ec378416ef6ff0a687277854e4a59137a` |
| `reports/remaining-153-001/public-image-alpha-checks-067/checks.json` | `fb31a4e38b4d1d14932db455b919db5085fe439ca39b8ee439780f445b9c4af2` |
| `reports/remaining-153-001/rgba-border-pixel-proof-068v2/proof.json` | `3e225fe78f1e6679835fb16c207d25d94ef871e0ba164d4a3700c193123ee38f` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-025.json` | `fc1ab9368e7597f6ac9b0d039d0cf8e5a8f4d7a32f7ab3e1a8ae92e01607d64a` |
| `reports/remaining-153-001/BACKGROUND-BORDER-RECORD-ATTEMPT-069.json` | `6f36d63b92af0c53bbf26b0ac878fb61d7a9ffc61ca36ba031716719703559ea` |
| `reports/remaining-153-001/LEDGER-RESULT-026.json` | `3c99f270e81cb765c709bb1bb9ba014dbb87bf4bd473d79a66ea75b81a97f01a` |
| `reports/remaining-153-001/BYOL-COLOUR-REVIEW-RESULT-073.json` | `2ffe3610dc02054c61bb08b5c9963fe59c7c6e864cd778c9eb2aa08061ec7f89` |
| `reports/remaining-153-001/BYOL-COLOUR-ATTEMPTS-073.json` | `0745450cc143c8216b872e2a7dea0604fabe2002b1c3d6dbc1ee48d717903d82` |
| `reports/remaining-153-001/github-checks-95a2b45-001.json` | `1cfed2063db09ea1410f01bd7030e6ad22147130ed1390b769b86797dd172fa0` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-026.json` | `b44ce61823b97e411cbdab4b20965d9b4ec9d3b590288c371aa17ffed9850a80` |
| `reports/remaining-153-001/LEDGER-RESULT-027.json` | `2d744fc46a7d5a2da27f0c5b78b63487b2a719ca2332e9951c79a40952262baa` |
| `reports/remaining-153-001/LIGHTGCN-LABEL-REVIEW-RESULT-078.json` | `92f4b89102e4d58a6b8c4d2d5183d10e90fab1ddc069db466c5ab1e7c25515e3` |
| `reports/remaining-153-001/LIGHTGCN-LABEL-ATTEMPTS-078.json` | `3aa7c035f47fa99935933b8a1170b41825af89094704afaa57d93e714954696b` |
| `reports/remaining-153-001/LIGHTGCN-CONTENT-CONTROLS-077.json` | `98cc64cd8c8af78d49eb0fe901e1bc1264511206c0c67c01b9e99eda654f0df2` |
| `reports/remaining-153-001/github-checks-2bdff8c-001.json` | `09c4851fe8ebc11c409a39a3974771937ee8cf11aa1abd003d18fd45697b7636` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-027.json` | `d927d9066a5cb8e027895087bfdcb2c37c5a2520b32f9d9fa246bccbff0828a6` |
| `reports/remaining-153-001/LEDGER-RESULT-028.json` | `4184158ed57a813564251a9f27036a8a7edff686171e6e2ed2e89af3ffdd71cb` |
| `reports/remaining-153-001/SOURCE-INVENTORY-REVIEW-RESULT-090.json` | `4135fad7d2fbf010e3bc7ccfc60c535c5bdca8ca4dabd599b468f8a32e1422d4` |
| `reports/remaining-153-001/SOURCE-INVENTORY-ATTEMPT-083.json` | `57f7902cce0ebabd1e812e3a9e75c32359b4d6791ad7ac529670c396ef70a817` |
| `reports/remaining-153-001/SOURCE-INVENTORY-READBACK-ATTEMPT-085.json` | `58982040a6ea59c1834017d59726bcba9419c6018910fa1a6b21f8ffa2b31559` |
| `reports/remaining-153-001/FREEZE-013.json` | `d8246a419191ce93ea778d294ec8759ea7cb999028317f6cd0460cc35c507874` |
| `reports/remaining-153-001/FREEZE-014.json` | `54dc92c2944e0e0e9b7c90ad73ff161e0824476560bb8ce03e79fca7dbbf122b` |
| `reports/remaining-153-001/public-source-inventory-checks-079/report.json` | `aedf8b45fa20cd36301a7f1550390bfb77c8b054474e964369167afeaa298c9a` |
| `reports/remaining-153-001/public-source-inventory-checks-084/report.json` | `e3d987fa22664dbcc69f39af87732b7efdfa4815938d06d256276189c7165355` |
| `reports/remaining-153-001/packages-source-inventory-080/proof.json` | `581ea3aebd40d70be858d0871cc8812c928e6593dae2634671407f4d829c8b53` |
| `reports/remaining-153-001/packages-source-inventory-084/proof.json` | `07d141b3d21c3b34c1099dcb1ad7b73340673cb5b8780d7737a850fee027a749` |
| `reports/remaining-153-001/github-checks-481a70a-001.json` | `2896b962c8794b12574c9673904a3e6fd62c4ebc394e261c3dca8f4213bc9c46` |
| `reports/remaining-153-001/github-checks-78fbafa-001.json` | `1dca11740ffb10f978a22bd9a76f13444488c0459f1fc94053c8d4efeb8c9c54` |

| `reports/remaining-153-001/AUDIT-CONSISTENCY-028.json` | `ccaa4ed204e91f024bb870fee9ffac5ee6bb722d80d9537db01b7d587e2806cd` |
| `reports/remaining-153-001/github-checks-c60fe9d-001.json` | `00322a932117aba01f5e138f358a9f5233bdc6ae6d86acc0fd5d0736e047cbbf` |
| `reports/remaining-153-001/LEDGER-RESULT-029.json` | `912384202d857a94d151e290d8e70e3ade1fec7727208ddab4d4e87c04beb9d1` |
| `reports/remaining-153-001/MATH-INVENTORY-REVIEW-RESULT-099.json` | `fd9ce578151d2ad4266bf805ee603000c696f4dde07dfe09f851e56c2fddd6b3` |
| `reports/remaining-153-001/MATH-INVENTORY-PREVIEW-EQUIVALENCE-098.json` | `d921d4eae8d8464db23ce8c0e0546b46b33d4ce0d0fb3b7af68b259865fc283d` |
| `reports/remaining-153-001/github-checks-ca023cc-001.json` | `05dada59db4b3c0dc7d1c7e5b55358bd9486e3ad041406f2f197e4bceadb8c8c` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-029.json` | `c80e783120b397c853d02a165b62b3ff686cc9ba132f60afe4db2bf0095bf6e5` |
| `reports/remaining-153-001/LEDGER-RESULT-030.json` | `bd9790162e6def9f5a9af73826b92e93ebf5d78e8d6816d995ccc4e4c5df8346` |
| `reports/remaining-153-001/DIAGNOSTIC-INVENTORY-REVIEW-RESULT-109.json` | `baa77218575df54216762a97682a48db49d8dfae27a1ee5f666787c0c8907a42` |
| `reports/remaining-153-001/github-checks-30dccc3-001.json` | `851d8aaf55463a96cbacfd37cbf2bd4b6fcad0f7f09d726fcdf12648e4285f25` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-030.json` | `e57af4fd36eaab2b8b660dcb156f00768b507661a28cd0c62b86d5d893aaccaf` |

- 原生线端/连接预览适配器已完成维护实现及来源重算：Python 1224 项（8 跳过），Node 62 项（61 通过、1 跳过；含实际像素和前景遮挡验证），wheel/sdist 分发及已安装 62 项测试通过。五张实际受影响图后续已完成新构建及视觉复核，详见上面的五项预览描边关闭记录。
| `reports/remaining-153-001/public-stroke-preview-checks-114/python.json` | `51e4a12f795194b52c0e78b64c8409668f1120fa278678b65c6d2b70440817ed` |
| `reports/remaining-153-001/public-stroke-preview-checks-114/node.json` | `37c7e31856bccf0b9c3a1ffc0794febd685c0f617c0e0665461f023a70133075` |
| `reports/remaining-153-001/packages-stroke-preview-115/proof.json` | `cef7f9a74333b5b1dff26a9fc6110cb23ed2c4eb4c6ddfbd27163ad8ce86c4b5` |
| `reports/remaining-153-001/github-checks-6fa282d-001.json` | `db9fb516cba68b1e571c74251ac7ce6e0325ceba85a88cb32e8bcb2e55d15e55` |
| `reports/remaining-153-001/LEDGER-RESULT-031.json` | `d0fdb72ea6af04c58aa58c88dd0734d63de8e441eb51738dc701c278553ff8e3` |
| `reports/remaining-153-001/ARTIFACT-STROKE-REVIEW-RESULT-120.json` | `04a7fafaf3e086255c5ecb104358b46aa1c4c6ec8fc109fef2c4125e178472c6` |
| `reports/remaining-153-001/FREEZE-015.json` | `5c887d77cb550e048506785b1a36432ed444bc5c370a7115adf9d6f1d69b976b` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-031.json` | `4b7553ed95bf53f455f61b8e4e36c529ac85318ef3745baca44c309756e05033` |
| `reports/remaining-153-001/github-checks-99a6971-001.json` | `6a22d3bc82678cab1191106f38d659f0ad0939db2c50dc42911dcbac7e953de9` |
| `reports/remaining-153-001/LEDGER-RESULT-032.json` | `35d2185b980824c35b31ec95b262d362a882c1fb32772f7c4207ea44b8a1cdf4` |
| `reports/remaining-153-001/SOURCE-BRUSH-CURVE-REVIEW-RESULT-146.json` | `bcfd24503ae8de0ab21c545dbe14e71f93b35e091ccad81393adb1d29f851517` |
| `reports/remaining-153-001/COLBERT-NATIVE-CURVE-PROBE-138.json` | `404708e2d6ff84c4dcc5f008ac9929d8e8db338bdc37bb2ed963751b4f99097a` |
| `reports/remaining-153-001/COLBERT-NATIVE-CURVE-BUILD-141.json` | `6c02d4d2032dcce06d57cc3f6d79d707359d3b57d1128a6b18a605b46250b968` |
| `reports/remaining-153-001/github-checks-fae0f04-001.json` | `e08f1c5dab3d058f0e2a41858205e2ede24bef3cf2514984dba27a181a82e378` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-032.json` | `62755ae2388dc2cd7530db5619b71b8d53291f2176de3644e6afd273f89ef44f` |
| `reports/remaining-153-001/SKILL-DISTILLATION-146.json` | `13768d9c42341704773e6920b85ab9ab24678d5bb15bac0f5a51ec317021cba9` |
| `reports/remaining-153-001/LEDGER-RESULT-033.json` | `d39bd696442c845a76cc4e5bf20eb4f3f21efacb86f68e8397a5936d2813976d` |
| `reports/remaining-153-001/SAMPLED-NATIVE-STROKE-REVIEW-RESULT-167.json` | `aa1d3daa804e978565ae8be7623a01bbb4c9c342dc6452e0852d7ca6e49d885a` |
| `reports/remaining-153-001/FREEZE-016.json` | `fd3e169490338b4022123d46cc16973e545d943cb917deffc59cbedebdad21da` |
| `reports/remaining-153-001/PUBLIC-CURVE-SUPPORT-CHECKS-164.json` | `bda42ecbfc1e2deb48f9a770ab32410a7694f6af0b77af389dee8d46e30e39c1` |
| `reports/remaining-153-001/SAMPLED-NATIVE-STROKE-BUILD-165.json` | `5f07bfb17f00c324d89df3e8b968beb80a73f016e59a2d38f1793e7d33ba93b2` |
| `reports/remaining-153-001/SAMPLED-NATIVE-STROKE-READBACK-166.json` | `ec582f715d2c36825b5d8dd956173ee18662c4e11876a75430257e0c046e41b2` |
| `reports/remaining-153-001/github-checks-9b0b913-001.json` | `682bd73c548a1314000d72319d7ced563d204492658eb141efd36aa1cc88e354` |
| `reports/remaining-153-001/SAMPLED-STROKE-DOCUMENTATION-CORRECTION-168.json` | `e5bdc1bb8f216af4d55fba06a28ef9369627749936a641e96a4d379bc1518bb6` |
| `reports/remaining-153-001/SKILL-DISTILLATION-167.json` | `845c2d402bce49042553656d7b3b3aa35e7da00f9dafbfd8b20bc4cae66c907f` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-033.json` | `bbb35e7d581760fc8f8eb11bef857eb2f17c6dafa7b231c6b522c1566ac8bc13` |
| `reports/remaining-153-001/AUDIT-SUMMARY-REFS-033b.json` | `8f6ab2c099932af9c97ba32500bbc3496e66c6538aade1c1136430955689e2a7` |
| `reports/remaining-153-001/LEDGER-RESULT-034.json` | `4864ea3eee16f5150f67b734866da830ac8de149c8e84504db1af823b3f60e9a` |
| `reports/remaining-153-001/INTRINSIC-IMAGE-PREVIEW-REVIEW-RESULT-195.json` | `56ee235db362aa50dd5158213cafcda8c3a0a30a246e665855c94b647e671378` |
| `reports/remaining-153-001/FREEZE-017.json` | `e9196f41f4eb8572cf2b15d1fc97e486bd08ad04bfcbbedf82242f481710d67a` |
| `reports/remaining-153-001/PUBLIC-IMAGE-PREVIEW-CHECKS-189.json` | `89d05521ebcf6e5812c7dcbba3d0ddda21bd8c90032dfa93f2fd95eb431b2792` |
| `reports/remaining-153-001/INTRINSIC-IMAGE-SURVEY-190.json` | `9beaff457c0a3a2c5e215836294da762a6b74c043744918ea7e9a9270c93105b` |
| `reports/remaining-153-001/INTRINSIC-IMAGE-PREVIEW-BUILDS-191.json` | `ea494cd52b9a4cc7b1e5341ad464664f1b055f9101c71ae25854533c7a20cca7` |
| `reports/remaining-153-001/INTRINSIC-IMAGE-PREVIEW-READBACK-193.json` | `ef6bd283a95998cedb0cb918865c8b5d9b33f027cf69de6c7fa98dfe9a821c63` |
| `reports/remaining-153-001/INTRINSIC-IMAGE-PREVIEW-MEASUREMENTS-194.json` | `b0a89eef226867c3d67c666ed5859c997691c0fd7967bf61d43c41619ad10403` |
| `reports/remaining-153-001/github-checks-924cd94-001.json` | `ce59b8f1e7ae9ac244348678bbb9a027de1314c5c0a3868f2b3c8592126478f5` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-034.json` | `87515a5e08c35f9b594c1883f91484b849c1682ede21e32843fd3c1813ef3b59` |
| `reports/remaining-153-001/tools/check_audit_consistency034.py` | `2babe3592b6de8842856c67feb6cbdafbdd12de2c48a068870129ff2fd845cbc` |
| `reports/remaining-153-001/DISTILLATION-195.json` | `1f9e4696616fc331c4ec28b3317d155a6e9eab7e027177fc7bd7b102b8602174` |
| `reports/remaining-153-001/LEDGER-RESULT-035.json` | `fcf6351ba472e6b0fdcf32e1627eb110d8f7379a207b9347e2d303d42dfb189e` |
| `reports/remaining-153-001/NATIVE-PICTURE-CROP-PREVIEW-REVIEW-RESULT-206.json` | `753bf15c6a5fa7ee351ad6c7ec63a1df895acde09d9441462cec76cc7cf5563d` |
| `reports/remaining-153-001/FREEZE-018.json` | `a2b38bedfa4e3d4bf4651e840f98afec130ee3f9feb4f851dec4bb72c4af1298` |
| `reports/remaining-153-001/PUBLIC-PICTURE-CROP-CHECKS-202.json` | `576ff23959572f5ccae90936886fa2ec628e89141c987217a5386f848d9f9976` |
| `reports/remaining-153-001/NATIVE-PICTURE-CROP-PREVIEW-BUILDS-203.json` | `8198d5982f1935a016bd4b586d337c4a29d1c1630198f79fac85ec4b16b073bb` |
| `reports/remaining-153-001/NATIVE-PICTURE-CROP-PREVIEW-READBACK-204.json` | `9df9a89e7717c733d2821e8430fe6a22523886eb78c4b9ba60ce7467edaf3849` |
| `reports/remaining-153-001/NATIVE-PICTURE-CROP-PREVIEW-MEASUREMENTS-205.json` | `77b9f657c7dfce7d230f46dfc6e29e05536df70c89324526c2237b442bfe9508` |
| `reports/remaining-153-001/github-checks-0c7b970-001.json` | `6b59b5adb4a4ad389ce3715143f591386a329c386ffd68a1998e601b8c8d23aa` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-035.json` | `e8c710162805b07acd1d50b27f708e74ceab0def16004b76623c080ed3b7a8c3` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-035b.json` | `009ebf5ae833a1d985c62f028872f82e7c59b21d8125039173f7ab8cdb27e43c` |
| `reports/remaining-153-001/LEDGER-RESULT-036.json` | `d62e644bc2a96a5fcbf7f234b1237631233f16c802ee831f427128713659a5fa` |
| `reports/remaining-153-001/BAO-NATIVE-HAIRLINE-REVIEW-RESULT-230.json` | `8d80a1d5081876da983b250103aa6f7191a4f0768296c9c05a3ca26e14d3445a` |
| `reports/remaining-153-001/FREEZE-019.json` | `3a116f4f2ee1cda354f5c9338c72c97b2cfaf9e86a668719bfbe7a5f7ded4a51` |
| `reports/remaining-153-001/FREEZE-020.json` | `58aa596f922905a2832c29e8abbd6d6c39b7287a48535c8f9414983426500790` |
| `reports/remaining-153-001/PUBLIC-NATIVE-PDF-HAIRLINE-CHECKS-215.json` | `80a30dc5b018ee1bdde43d03ae82cef42b4d01e506dcb6d2fc6ab38455209fa7` |
| `reports/remaining-153-001/PUBLIC-NATIVE-PDF-JSON-CHECKS-224.json` | `ba4b598d9488e454ccba446871fb89142c72c9c7cbf274d4ed361fd44aef441e` |
| `reports/remaining-153-001/BAO-NATIVE-HAIRLINE-BUILD-225.json` | `9ce09ad2010a74b2d6a21a7ff46cf0fc5cd36e55bfb29689410fc03c936964fa` |
| `reports/remaining-153-001/BAO-NATIVE-HAIRLINE-READBACK-228.json` | `83c54fd939f219ba6b37032765013126a76698336873f519cfbb4c57f5d949cd` |
| `reports/remaining-153-001/BAO-NATIVE-HAIRLINE-MEASUREMENTS-229.json` | `f7884c9306f9442fbed509eb1ece4cec76e65a69b96b2d3543d6c42db753dc1e` |
| `reports/remaining-153-001/github-checks-feee493-001.json` | `58516fd9f5d24d211677b8b5b7882296abd09aca8d584cfd64e3efc36799f29b` |
| `reports/remaining-153-001/github-checks-637d9fc-001.json` | `b50659137b8e10078fe4fa78474499e0711aaa0a243d07f32c6b156c62c762f5` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-036.json` | `6f6994be14eae936fe69c8a5485ca948977d52cdaf3468d9ebe62f60bc7105f8` |
| `reports/remaining-153-001/DISTILLATION-231.json` | `fd500627893b70d8457b7ffaa746f8c0d9bb67757000b90441da01213b281055` |
| `reports/remaining-153-001/LEDGER-RESULT-037.json` | `3a2f8570b35d77dbaa2fa6f18c614d27e28363b92ea1875cf1fb7731ac61c968` |
| `reports/remaining-153-001/NATIVE-PHOTO-PDF-PREVIEW-REVIEW-RESULT-238.json` | `36e93b9b8c38fe4c24191230da22e06cd1bec68889a7446b0aa33bd706337805` |
| `reports/remaining-153-001/NATIVE-PHOTO-PDF-PREVIEW-BUILDS-232.json` | `b8df75051fefefb80c910c2ef5b3e479689844c761f33719c365a66b2d923ea1` |
| `reports/remaining-153-001/NATIVE-PHOTO-PDF-PREVIEW-READBACK-235.json` | `738ebb08ba1289c2ab97bb7c70ef1ff5d64a39c275cd485a5e8057d887f7f3c4` |
| `reports/remaining-153-001/actual-view-names-236.json` | `4cfc98f5c4d8fb6f8e41dd4c8d89e65bde47b15cad27dc8b2555e232ef0a8d6a` |
| `reports/remaining-153-001/github-checks-28b7b9f-002.json` | `efb23ea24fa8d887b89ff7e8c6999946593b6d4fbe2922992821fed046f559e3` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-037.json` | `4019d70248a0647707fb8a9c6e6195ad98ea350a7a1c54254feeed8e80fd5e2a` |
| `reports/remaining-153-001/DISTILLATION-239.json` | `0adda1d96306663401eb9c6be2401ffb4bd7ded85c5941f520aaa58b87044b03` |
| `reports/remaining-153-001/LEDGER-RESULT-038.json` | `ec92f6871ea500fd0bc4c154fff516fd4a9770a7ca9cd8851d63efc6cca8ea7b` |
| `reports/remaining-153-001/SAM1-LIVE-TEXT-REVIEW-RESULT-274.json` | `bf76e3f686a672f513cbf84038509603ebef35a9f5b0e2a2925c5b3fc3709de3` |
| `reports/remaining-153-001/SAM1-ALL-SOURCE-CONTROL-PROOF-272.json` | `452bc11d63de04ca8c18758782d1bf601811b91f6527101606fbea349b79bcd4` |
| `reports/remaining-153-001/SAM1-NATIVE-INK-FONT-READBACK-267.json` | `e3746413ecf002815a57d0b851d9e2a55e44b1e115b4f5cba37db6f63bc8a652` |
| `reports/remaining-153-001/SAM1-LIVE-SCENE-READBACK-270.json` | `e849c6170018c7906530523112562a0eae983033a8bab16b3924ee89b72f627d` |
| `reports/remaining-153-001/SAM1-RETAINED-FAILURES-273.json` | `7a0c4a0df58b818d03e1de5cf605ea1bc012356943abc867f78b3a688a54a911` |
| `reports/remaining-153-001/actual-view-names-sam1-271.json` | `e4915590b7d55197e965509e120019a850327233676c2555b66e3b1a64f8c32b` |
| `reports/remaining-153-001/SOURCE-FONT-DELIVERY-LIMITS-015.json` | `615b40a1c57ccebb14bc8d16b1270e588bee5c93b80144f97961a0ac52ccfee0` |
| `reports/remaining-153-001/github-checks-bc5eb5a-001.json` | `4274ade7204d0a6784a5539a196868aa9ad917ef6287c6a0d54e84c6490b04ea` |
| `reports/remaining-153-001/AUDIT-CONSISTENCY-038.json` | `bc8afe3eac6bafcda23dc4bd6e51152325c7ef9603b1fdec5acd28e7374bbbc3` |
| `reports/remaining-153-001/DISTILLATION-275.json` | `33fd029d3e01f3ac4af2587c644f6563a84a8917715cc497131cb44a7f421291` |
