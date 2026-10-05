# 原有 153 项的继续修复记录

2026-10-05，基于请求开始时冻结的 153 项开放问题。当前状态以 [逐项台账](2026-10-04-detail-resolutions.json) 为准。

本轮完成 45 张图的来源修复及实际成品复查，关闭 74 项原问题：49 项字体、字距、基线、数学字形或活动文字问题，以及 25 项渐变、箭头、图片、图注、边框、括号或省略号问题。原 153 项中仍有 79 项未关闭；本轮另发现字体交付限制、SAM 2 白色路径接缝及 InstructGPT 圆形边框独立编辑三项开放问题，共 82 项开放。原始 296 项现在 267 项关闭、29 项开放；81 项后续发现中 28 项解决、53 项开放。新增发现中的白色填充透明缺口另已解决，不计入原 153 项的 74 项关闭数。所有模型关闭项的用户验收仍为 pending。

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

源字体别名依赖、扩展 Unicode 编辑范围及未嵌入字体记录为一项共用开放限制，关联 37 张已复核的图。PowerPoint/WPS 重开和播放未验收。普通文字显示修复不关闭自动公式、连接语义或作者轮廓的活动文字问题；同图中的箭头、照片、遮挡和诊断覆盖问题按原 ID 保留。


- Maximum Flow 图 2 将 128 个活动片段、273 个字符绑定到 19 个实际嵌入的 Computer Modern/Latin Modern Type1 程序，并从原生矩阵区分 43 个横纵 em 变体。恢复真实粗体/斜体、纵向 em、独立下标基线及 ASCII 物理字距；UPM16000 私有别名仍分别限定 1/1024 原设计单位控制点与 1/32 advance 编码界，不宣称原轮廓、hinting 或像素相等。两处原清单中的箭头/成员符号保留正确 Unicode，但源 ToUnicode 实际错误返回 `!`/`2`，人工字形绑定与原始元数据分开记录。全部 116 个使用字体/Unicode 子集对及 273 个实际原生 font handle/GID 逐项核验；位置最大观察差为全图横向 0.905 px、纵向 0.511 px，主体数学标签为 0.156/0.464 px。外部后端单次字体策略仍上限 16；以四个连续绘制顺序的受限构建件和公开原生插入接口组装整图，逐件字体检查、所有 344 个最终对象的次序、变换、字形及 cubic/winding/stroke 回执分别重放，216 条其他路径逐字节保留。整页包/布局检查与实际字形核对通过，但未声称整页 43 字体族策略检查或单次整图构建通过。重复登记的同角色字体只在 SFNT head 保存时间/校验值上有差异，其余表及 head 字段相同后选择一个登记角色。曾误载入旧 helper 的组装/审阅失败及所有后继保留；最终实际加载的所有模块均绑定冻结 012。原图 1×、最终原生 1×/2×与十张同网格 4× 数学、编号和三行图注局部实际查看后关闭 D01。原有轮廓字和自动公式层级未升级；来源字体跨应用交付继续开放。

- Segment Anything 图 1 的五个真实图片实例恢复全部 CTM、裁剪、alpha 和绘制顺序，后两层马图的左边及顶部细条重新出现；Vision Mamba 图 2 的输入图恢复源近景裁剪，九块 patch 均由不同的原生裁剪实例采样，保留非空间排序的真实绘制顺序及来源的间隔，消除重复完整缩略图和重复位置；SAM3D 图 5 的七个图片实例保留真实圆角 clip，恢复输入照片、黑底蒙版和 Stage3 两张照片圆角。三图使用未放宽的严格原生政策，显式 8× 采样不升级为像素/滤波等价。全部其他文字、路径及字体 profile 字节不变；101/134 个活动对象的 664/565 个实际原生字形轮廓、advance、矩阵与此前源绑定成品在 f32 下完全相同，原关闭证据摘要逐项核验。三图完整对象、媒体和绘制顺序读回；源全图 1×、最终原生 1×/2×及全部十个匹配 4× 局部实看后关闭三个图片问题。SAM3D 的所有普通标签仍为路径，其 D02 保持开放。

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
| ccf-2020-17-f02 | D01 |
| ccf-2020-18-f01 | D01, D02, XC01 |
| ccf-2021-01-f01 | D01 |
| ccf-2021-03-f03 | D01 |
| ccf-2021-09-f02 | D01 |
| ccf-2021-10-f01 | D01, D02 |
| ccf-2021-12-f02 | D01, D02, D03 |
| ccf-2021-13-f01 | D02 |
| ccf-2021-13-f08 | D01, D02 |
| ccf-2021-17-f02 | D01 |
| ccf-2021-17-f05 | D02 |
| ccf-2022-01-f03 | D01 |
| ccf-2022-02-f02 | D01, D02, D03, V3-R01 |
| ccf-2022-07-f01 | D01, D02 |
| ccf-2022-12-f04 | D01 |
| ccf-2022-13-f01 | D01, D02 |
| ccf-2022-14-f02 | D01 |
| ccf-2023-01-f01 | D01, D02 |
| ccf-2023-03-f01 | D01 |
| ccf-2023-05-f02 | D01, D02 |
| ccf-2023-05-f04 | D01 |
| ccf-2023-06-f01 | D01, D02 |
| ccf-2024-03-f01 | D01, D02 |
| ccf-2024-03-f02 | D01 |
| ccf-2024-03-f12 | D01 |
| ccf-2024-06-f02 | D01, D02 |
| ccf-2024-07-f02 | D01 |
| ccf-2024-08-f02 | D01, D02 |
| ccf-2025-01-f02 | D01, D02, D03 |
| ccf-2025-01-f03 | D01 |
| ccf-2025-01-f07 | D01, D02 |
| ccf-2025-01-f08 | D02 |
| ccf-2025-03-f02 | D01, D02 |
| ccf-2025-05-f13 | D01 |
| ccf-2026-01-f05 | D01 |
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
