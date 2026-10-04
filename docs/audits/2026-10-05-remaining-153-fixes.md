# 原有 153 项的继续修复记录

2026-10-05，基于请求开始时冻结的 153 项开放问题。当前状态以 [逐项台账](2026-10-04-detail-resolutions.json) 为准。

本轮完成 31 张图的来源修复及实际成品复查，关闭 38 项原问题：35 项字体、字距、基线或数学字形问题，以及 RAG 渐变横条白缝、DDPM 多余斜线、DUSt3R 点云/相机偏移。原 153 项中仍有 115 项未关闭；另记录 1 项共用字体交付限制，共 116 项开放。原始 296 项现在 232 项关闭、64 项开放；78 项后续发现中 26 项解决、52 项开放。所有模型关闭项的用户验收仍为 pending。

## 实现与复核

- 附加字体 family 可只提供实际使用的 italic/bold/boldItalic 等角色；默认 family 仍要求 regular/bold。每个实际使用角色的字节摘要和 Unicode 覆盖都核验，不补造未用字体角色。
- 旋转的源基线锚点用于计算原生文字框中心。非对称 inset 和 baseline offset 围绕真实源锚点旋转，正负 90°、任意角和 180°均有控制；原来的零旋转数值保持。SVG 主稿和最终 OOXML 审查使用同一含义，并独立检查原生 frame/inset/rotation。
- 新增只读 used-SFNT-glyph 比较接口，核对显式源 GID 到 Unicode 的轮廓、advance 和 side bearing；限定字节、字形、轮廓操作及复合字形深度。源 GID 由独立原生绘制上下文绑定，名称或相邻 bbox 不作为身份。CFF 与 OTTO、glyf 与 TrueType 文件头必须一致。字体导入成功不替代字形证明。
- 来源中的部分子集采用显式作业本地别名。重新编码/转换保留原程序和派生回执，使用的实际轮廓及 advance 核对，未宣称整个 family 或 hinting 等价。四份 CFF 包装件的错误 TrueType 文件头修正为 OTTO，失败及后继保留。
- RAG 源绘制 13 是 type-2 Pattern 203 中的 N=1 DeviceRGB 线性渐变。加载完整 Pattern 核对真实 native pointer，保留 Pattern Matrix、绘制 CTM、圆角三次曲线裁剪和顺序。原 9 块量化颜色轮廓替换为一条连续原生渐变路径。对颜色和 stop 编码的完整分段线性域给出精确有理数误差证明，固定上限为 1/255 RGB 分量；这不涵盖最终几何量化或渲染器差异。
- DDPM 源绘制 3 的两个实际字形绑定到嵌入字体程序、GID 1/34、真实字形矩阵和完整五层原生裁剪。新增显式凸直线裁剪接口，以精确有理数只求直线交点，保留同侧三次曲线控制点；曲线包络跨边界、非凸/重复绕向裁剪或预算耗尽均拒绝。独立 minus 字形被证明填充交集为空，箭头头部只截去长杆，最终保持可编辑曲线路径。私有早期探针曾重复应用已展开的根变换，其无效结果及纠正记录保留；生产接口的根坐标约定有独立真实 PDF 对照测试，未放宽自动 `outline_paths` 的拒绝策略。
- DUSt3R 原问题来自把完整位图缩放进已裁剪的 bbox。源图片实例 0 / 原生绘制 1 现在保留完整矩阵、实际 Matte 蒙版和原始裁剪回调，点云尺度和红色相机顶点一并恢复。新增显式原生 Matte 采样仅允许无 Decode 改变的 8 位 DeviceRGB 图片和同尺寸 8 位附加蒙版，不二次合并已解码 alpha；默认拒绝保留，其他图片/文字/路径不转发，干净 replay cookie 另行核验。采样步长为 8×，没有 RGB/alpha 误差界。原生相机和点云 4× 对照实看确认对齐，所有独立矢量坐标轴及标签未变。
- 最终 PPT 的字体、字符、源基线、frame/inset、曲线、图片字节和全部对象顺序均读回。曲线和绕向证明从原始 authoring 中间产物按完整生产前置条件重放，不重新处理终态。各图实看源全图 1×、最终原生 PPT 全图 1×/2×及匹配像素步长的 4× 局部；两批主要字体修复共实看 206 页 4× 对照。RAG 另实看源/旧/新渐变局部，白缝消失。

源字体别名依赖、扩展 Unicode 编辑范围及未嵌入字体记录为一项共用开放限制，关联 26 张主要字体批次的图。PowerPoint/WPS 重开和播放未验收。普通文字显示修复不关闭自动公式、连接语义或作者轮廓的活动文字问题；同图中的箭头、照片、遮挡和诊断覆盖问题按原 ID 保留。

## 本轮关闭项

| 图 | 问题 ID 后缀 |
| --- | --- |
| ccf-2020-02-f02 | D01, D02 |
| ccf-2020-04-f01 | D01, D02 |
| ccf-2020-06-f01 | D01, D02 |
| ccf-2020-09-f02 | D01 |
| ccf-2020-11-f02 | D01 |
| ccf-2020-11-f03 | D02 |
| ccf-2020-12-f03 | D01 |
| ccf-2020-14-f01 | D01 |
| ccf-2021-01-f01 | D01 |
| ccf-2021-03-f03 | D01 |
| ccf-2021-09-f02 | D01 |
| ccf-2021-13-f01 | D02 |
| ccf-2021-13-f08 | D02 |
| ccf-2021-17-f02 | D01 |
| ccf-2021-17-f05 | D02 |
| ccf-2022-01-f03 | D01 |
| ccf-2022-02-f02 | D03 |
| ccf-2022-07-f01 | D01, D02 |
| ccf-2022-12-f04 | D01 |
| ccf-2023-01-f01 | D01 |
| ccf-2023-03-f01 | D01 |
| ccf-2023-06-f01 | D01 |
| ccf-2024-03-f01 | D01, D02 |
| ccf-2024-03-f12 | D01 |
| ccf-2024-06-f02 | D02 |
| ccf-2024-07-f02 | D01 |
| ccf-2024-08-f02 | D01, D02 |
| ccf-2025-01-f08 | D02 |
| ccf-2025-03-f02 | D02 |
| ccf-2025-05-f13 | D01 |
| ccf-2026-05-f01 | D01, D02 |

## 验证与证据

冻结源码快照 007 含 248 个输入文件。完整来源环境 1135 项 Python 检查通过，8 项可选环境检查跳过；Node 55 项中 54 通过、1 项可选控制跳过。wheel 80 文件、源码包 254 文件载荷检查通过；在仓库外安装该 wheel 后再次执行相同 1135 项检查，所有加载的 figure_rebuild 模块都来自安装目录，8 项跳过。GitHub CI 另按推送后的准确 head 核验。

后续快照 008/009 分别含 252/254 个输入文件。DDPM 裁剪后完整 Python 检查为 1148 项，Matte 支持后为 1158 项，均通过、各 8 项可选检查跳过；新增 13 项凸裁剪和 10 项 Matte 控制。Matte 测试用独立原生整页绘制核对同网格结果，黑/白 Matte、alpha、ROI、重复实例、未烧入其他绘制、Decode/重采样/CMYK 拒绝及 incomplete replay 均覆盖。以上整图关闭只对应逐项台账中的实际证据。

快照 010 的 188 个运行时、测试和构建输入与完整测试通过的 009 逐字节相同。更新后的 wheel 含 81 文件、源码包含 260 文件，载荷核验通过；wheel 的 74 个运行时文件与快照相同。在仓库外安装该 wheel 后，1158 项检查再次通过、8 项可选检查跳过，实际加载模块全部来自安装目录。首次私有测试启动器在运行测试前发生语法错误，原失败记录保留，后继启动器未改变安装包或测试。安装包 SHA-256 为 `e2d9f8955283c524e7821a97eefb884018c1a56940754f08b2e24b63edbd02ac`。

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
