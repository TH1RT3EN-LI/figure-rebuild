# 原有 153 项的继续修复记录

2026-10-05，基于请求开始时冻结的 153 项开放问题。当前状态以 [逐项台账](2026-10-04-detail-resolutions.json) 为准。

本轮完成 31 张图的来源修复及实际成品复查，关闭 36 项原问题：35 项字体、字距、基线或数学字形问题，1 项 RAG 渐变横条白缝。原 153 项中仍有 117 项未关闭；另记录 1 项共用字体交付限制，共 118 项开放。原始 296 项现在 230 项关闭、66 项开放；78 项后续发现中 26 项解决、52 项开放。所有模型关闭项的用户验收仍为 pending。

## 实现与复核

- 附加字体 family 可只提供实际使用的 italic/bold/boldItalic 等角色；默认 family 仍要求 regular/bold。每个实际使用角色的字节摘要和 Unicode 覆盖都核验，不补造未用字体角色。
- 旋转的源基线锚点用于计算原生文字框中心。非对称 inset 和 baseline offset 围绕真实源锚点旋转，正负 90°、任意角和 180°均有控制；原来的零旋转数值保持。SVG 主稿和最终 OOXML 审查使用同一含义，并独立检查原生 frame/inset/rotation。
- 新增只读 used-SFNT-glyph 比较接口，核对显式源 GID 到 Unicode 的轮廓、advance 和 side bearing；限定字节、字形、轮廓操作及复合字形深度。源 GID 由独立原生绘制上下文绑定，名称或相邻 bbox 不作为身份。CFF 与 OTTO、glyf 与 TrueType 文件头必须一致。字体导入成功不替代字形证明。
- 来源中的部分子集采用显式作业本地别名。重新编码/转换保留原程序和派生回执，使用的实际轮廓及 advance 核对，未宣称整个 family 或 hinting 等价。四份 CFF 包装件的错误 TrueType 文件头修正为 OTTO，失败及后继保留。
- RAG 源绘制 13 是 type-2 Pattern 203 中的 N=1 DeviceRGB 线性渐变。加载完整 Pattern 核对真实 native pointer，保留 Pattern Matrix、绘制 CTM、圆角三次曲线裁剪和顺序。原 9 块量化颜色轮廓替换为一条连续原生渐变路径。对颜色和 stop 编码的完整分段线性域给出精确有理数误差证明，固定上限为 1/255 RGB 分量；这不涵盖最终几何量化或渲染器差异。
- 最终 PPT 的字体、字符、源基线、frame/inset、曲线、图片字节和全部对象顺序均读回。曲线和绕向证明从原始 authoring 中间产物按完整生产前置条件重放，不重新处理终态。各图实看源全图 1×、最终原生 PPT 全图 1×/2×及匹配像素步长的 4× 局部；两批主要字体修复共实看 206 页 4× 对照。RAG 另实看源/旧/新渐变局部，白缝消失。

源字体别名依赖、扩展 Unicode 编辑范围及未嵌入字体记录为一项共用开放限制，关联 26 张主要字体批次的图。PowerPoint/WPS 重开和播放未验收。普通文字显示修复不关闭自动公式、连接语义或作者轮廓的活动文字问题；同图中的箭头、照片、遮挡和诊断覆盖问题按原 ID 保留。

## 本轮关闭项

| 图 | 问题 ID 后缀 |
| --- | --- |
| ccf-2020-02-f02 | D02 |
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
| ccf-2024-08-f02 | D02 |
| ccf-2025-01-f08 | D02 |
| ccf-2025-03-f02 | D02 |
| ccf-2025-05-f13 | D01 |
| ccf-2026-05-f01 | D01, D02 |

## 验证与证据

冻结源码快照 007 含 248 个输入文件。完整来源环境 1135 项 Python 检查通过，8 项可选环境检查跳过；Node 55 项中 54 通过、1 项可选控制跳过。wheel 80 文件、源码包 254 文件载荷检查通过；在仓库外安装该 wheel 后再次执行相同 1135 项检查，所有加载的 figure_rebuild 模块都来自安装目录，8 项跳过。GitHub CI 另按推送后的准确 head 核验。

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
