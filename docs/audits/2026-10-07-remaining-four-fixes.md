# 4 项剩余问题的继续修复

2026-10-07。基于已合并的 `main`（`080c210`），本批统一提交使用 `codex/remaining-four-release`。模型复核与用户验收分开记录，用户验收仍为 pending。

三项图形细节已通过模型复核。字体主要问题完成嵌入部分并继续开放。原 296 项全部关闭；后续 83 项为 82 关闭、1 开放。

| 登记项 | 本次结果 | 状态 |
|---|---|---|
| SAM 2 白色箭头接缝 | 八组白色组件改成八条连续可编辑轮廓，消除箭头头部及杆端细缝；修正合并轮廓的来源标识 | 模型复核关闭 |
| InstructGPT 头像边框 | 两个头像素材移除绑定的源圆框绘制，按实际圆形裁剪恢复独立可编辑边框；修正两个边框的来源标识 | 模型复核关闭 |
| ColBERT 蓝色节点裁剪白边 | 从原始 PDF 的局部曲线展开和裁剪三条边框，再变换一次；白边消失且保留圆角 | 模型复核关闭 |
| 字体交付，48 张 | 296 个实际使用角色完整嵌入，保留原字节、权限和当前字符集；编辑权限、字符集扩展和跨应用交付仍有限制 | 主要问题开放，嵌入部分完成 |

## 原生图形复核与后继

SAM 2 的八条白色轮廓来自显式作业级 Skia union。42 个其他顶层对象、图片及文字的原生字节和顺序保留；查看来源和最终全图 1 倍、完整覆盖的 2 倍分块及全部 10 个箭头/控件的匹配 4 倍局部。新轮廓的 `source_id` 对应实际合并对象，原组件集合记录在外部回执中，不再沿用单个组件的标识。

InstructGPT 的两个头像仅抑制独立绑定的原始边框绘制 62/106，保留真实裁剪、组、Multiply 与蒙版回调。两条边框按真实圆形裁剪恢复为独立原生填充路径，紧随各自头像绘制；71 段活动图注和其他原对象不变。保留未裁剪圆框偏粗的失败尝试。修正来源标识后的真实直接 PNG 在 1/2/4 倍与已审查后继逐像素相同。

ColBERT 的首个去白边候选在大数值全局 EMU 坐标中展开描边，造成圆角变成斜角，现已明确撤回其作为最终成品的资格并保留失败记录。后继从原始 PDF 的局部控制点、真实描边宽度、butt cap 与 miter limit 8 出发，先在局部展开/裁剪，再应用源 CTM 一次、EMU 舍入一次。486 个最终叶对象中 483 个未受影响对象字节不变，34 段文字、14 组圆点和原蓝色填充曲线保留。实际 1/2/4 倍预览确认三处白边消失、圆角保留。

三项均为有限、显式的原生后处理。浮点布尔运算和描边展开不构成精确源轮廓、一般拓扑或所有倍率像素等价证明；公共准入、源控制误差界和资源预算未放宽。恢复的头像边框及 ColBERT 边框是可独立移动、改色和编辑节点的填充路径，其粗细通过路径几何修改。后继 Linux WPS 的实际保存及 1/2/4 倍输出已复查三项修复，13 个目标保持原有控制点 guard；PowerPoint/macOS 与用户验收仍为 pending。

## 字体嵌入与操作边界

新增 `embed-fonts` CLI，按最终活动文字的明确 family 与样式角色引用 EOT 字体部件，只嵌入实际使用的已登记字体，保持完整 SFNT 原字节、权限和已有字符集。[使用及格式依据](../../references/fonts.md#embed-the-registered-fonts-in-an-existing-figure)。

独立边界复核修正两个缺陷，并新增五个回归控制：同一字体文件的有效 family 别名共享一个字体部件和内容类型声明；递归检查实际可达的 layout/master，拒绝未纳入字体策略的继承活动文字或字段。空白模板仍可保留。原包的字体部件/内容类型碰撞、受限或冲突权限、变动哈希、缺字、变量字体、继承字体及已嵌入稿均拒绝。

统一交付为 49 张图（原 48 张字体图，加 ColBERT），300 个使用角色；其中 237 个角色仅允许预览/打印嵌入，42 个文件至少使用一个此类角色。另保留 49 份未嵌入原生编辑版本及其既有字体依赖。新 49 包逐部件验证：除字体登记所需的三个包级 XML 部件外，原始幻灯片、媒体、布局等字节全部不变。

前一批 49 份包在只有一个无关 DejaVu 启动字体、无源字体的全新 LibreOffice 26.2.5.2 环境中实际导入/导出，观察到全部嵌入角色且无替代字体。该结果绑定前一批包及相同登记字体字节；本批改变三张图的局部几何/来源标识，重新绑定实际包和嵌入回执，不把前一批的应用输出误记为本批完整重跑。

Linux WPS 的独立字体环境检查已观察到替代字体，不能宣称这些 EOT 包在 WPS 中无需安装源字体。发布时独立环境为 0/49 份通过字体名称核对，安装原登记字体后为 46/49；[发布时的证据绑定](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/WPS-STATUS-AT-RELEASE.json) 保持不变。后续采用每图独立字体别名的原生稿为 49/49，独立实际字形核对确认旧三份九个可见 Arial 替换全部恢复；最终 49 份的 300 个角色完成有限修改、保存和重开，包括 Swift 补字后继，实际 PPTX 文字独立读回通过。完整生成程序已重放，最终版字号、字距与基线校正记录应用残差，三项图形实际保存后保持。最终本地包 `linux-delivery-040` 还修正新组件版权署名及逐 face 范围，保留旧失败证据。这是本地字体依赖型编辑结果；Swift 源连字墨迹近似、导出 PDF Unicode、更广的来源排版、Windows PowerPoint/macOS 和自包含交付仍未验收。字体权限不变，主要问题继续开放。详见[Linux 后继排查](2026-10-07-linux-font-followup.md)。

## 证据与交付

发布源码的完整 Python 测试 1392 项通过（8 项原有可选测试跳过）；字体嵌入的 14 项测试全部通过。wheel/sdist 结构检查及 checkout 外安装 wheel 后实际调用 `embed-fonts`、独立字体/媒体读回通过。源码和测试输入前后摘要一致，逐项台账仅更新本批四项，原 296 项冻结记录不变。

原来源、失败候选、复核后继及逐项历史均保留。完整来源和成品位于独立审计数据目录，不随此仓库重新分发；下列本机链接供当前协作验收使用。

[统一交付目录与 SHA256](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/delivery-index.json) · [整合结果与限制](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/INTEGRATION-RESULT.json)

[SAM 独立复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/independent-sam2-seam-20261007/README.md) · [头像独立复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/independent-instructgpt-border-20261007/RESULT.json) · [ColBERT 候选与圆角后继复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/independent-colbert-clip-20261007/REPORT.md)

[SAM 原生编辑版本](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/editing-native/ccf-2025-01-f02.pptx) · [InstructGPT](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/editing-native/ccf-2022-02-f02.pptx) · [ColBERT](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/remaining-four-release-20261007/editing-native/ccf-2020-11-f02.pptx)
