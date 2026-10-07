# Linux WPS 字体问题的后续排查

2026-10-07。基于统一提交 `d2c9cae` 的后继证据；原来的失败记录与成品保持不变。字体主要问题继续开放，用户验收为 pending。

## 三份可见字体替换已经修复

前一轮 `wps-native-005` 的三份失败不是空白字符误报：RT-1 的 `2/sys`、VMamba 两图的 `I/6/k` 共九个实际绘制字形被替换成 Arial，轮廓和字宽与明确登记的字体不一致。

这些稿件使用了同名但字符范围不同的字体子集。例如另一份 RT-1 子集没有 `2/s/y`，另一份 VMamba 子集没有 `I/6/k`；混合安装时办公应用可以选中另一份子集。

`linux-delivery-006` 为每张图使用独立 family 名称，并对五个角色采用已核对原有字符的完整 Type1 来源。新 WPS 名称检查为 49/49；独立实际 PDF 原生 handle、嵌入字体程序、逐点轮廓和字宽核对确认，旧三份的九个异常字形全部恢复。匹配位置的 1/2/4 倍渲染也已复查。旧失败不会改写为通过，另用 SHA256 绑定的后继回执记录恢复。

[三份旧/新独立复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-fallback-review/REPORT.md) · [后继状态回执](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-fallback-review/old-to-new-receipt.json)

## 49 份本地依赖型编辑验证

本轮独立读取 49 份 Linux 原生候选和 300 个字体角色：相对统一发布的原生稿，仅改变明确登记的字体名称，三项图形后继及其他包级数据均保留；原先 cmap 字形轮廓、水平 metrics、权限、em 和基线 metrics 保持。

WPS 逐角色修改了 300 个角色，保存并重开 49 份稿件；独立读取实际保存的 PPTX，所有文字均与预期相同。一个 RPC 对非 BMP 字符插入 NUL 的包装异常单独绑定到原 XML，未以 API 字符串代替实际文件证据。55 个角色的有限测试使用了原稿未使用、但登记字体已有的字符。

这些原生稿依赖本地安装字体，没有嵌入字体部件。字体的 `fsType` 原值未改；本地原生稿的编辑试验不等于预览/打印嵌入稿获得编辑授权，也不等于拥有完整原 family 或任意增字能力。

[49/300 独立包、字体及实际保存文字复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/native-delivery-review-001/RESULT.json)

## 尚未完成的兼容范围

Swift 的原子集有三个为空的普通字母占位，依赖源 `fl/ft` 连字。有限逐字 cmap 对照会接受这些空轮廓，因此“登记字体一致”不能证明文字完整显示。实际 WPS 输出没有使用这些源连字。历史 Google Sans v3.002 donor 候选保留为失败实验；后继 `component-recovery-probe-018` 改用许可字段及附带 OFL 已核对的 v14.000 donor，仅补 regular `f`、bold `f/t`，其他原字形、cmap、GSUB 和权限保持。

018 的真实 WPS PDF 已显示独立字母组件；这仍是与源连字不同的明确近似。早期 Probe 主动以 `ReadOnly=True` 打开，所以只读标志不能作为安装字体的原生稿不能编辑的证据。后继已经捕获完整生成程序：独立重放两份字体全部字节及 21 个 PPTX 部件与冻结的 018 一致，外层 ZIP 时间戳另列。023/025 的实际可编辑后继完成了 Swift 三个角色的修改、保存和重开，独立读取实际保存文件确认全部文字正确，导出的新 bold `f` 与登记组件轮廓和字宽一致。此前“未完成编辑 / 缺生成程序”的范围已由这些后继证据补齐。新的 donor 许可只覆盖新增组件，不能自动重新许可保留的源子集。字体二进制不随仓库分发。

[Swift 字形与后继许可证据复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-glyph-review/INDEPENDENT-GLYPH-REVIEW.md)

## 最终 Linux 有限验证

独立冻结并读取 023/024 的 49 份最终原生稿、300 字体角色，以及 025 实际保存的 49 份文件：相对统一发布原生稿，仅改变登记字体别名、字号、字距和文字框 inset，原生文字、路径、图片、变换、顺序及其他包部件保持。原有非空 cmap 字形及水平 metrics、权限和基线 metrics 保持，仅三个明确的 Swift 空占位新增组件。所有 300 角色的实际保存文字正确，55 角色使用了原稿未用但字体已包含的字符；API 的 48/49 与实际 XML 的 49/49 分别记录。

完整后继字体/原生稿生成器已经冻结。49 页预览在 1 倍 RGBA 下与对应的实际 WPS 单图导出一致。独立重算 18,590 个有限字符起点，相对已接受 LibreOffice 参考的最大观察差为 1.7579 pt、字号最大差为 0.1600 pt；Swift 起点最大差为 0.05469 pt。这些是应用输出残差，不能作为原来源几何准入容差，也不证明整图来源逐像素相同。

[最终 49/300 实际包与文字复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/final-native-review-002/RESULT.json) · [完整重放与实际 Swift 编辑复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-glyph-review/final-followup-20261007/FINAL-FOLLOWUP-027-023.md) · [最终预览与版式残差独审](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-baseline-review/final-followup-025/README.md)

独审还发现 023 两份新组件字体的版权署名引用了历史年份/主体。后继更正为实际 v14 donor 二进制的 `2025 The Google Sans Project Authors`；原始 OFL sidecar 的另一版权头保持原文，二者分别说明。旧包与失败回执保留，元信息更正不改变 glyph、metrics、权限或其余 298 角色，不将旧源子集重新许可为 OFL。

三项图形的后继及实际保存文件另行独审：SAM 2 八个 union、InstructGPT 两个原生圆框、ColBERT 三个裁边轮廓的 13 个目标保持 fill-only，实际原生控制点误差均在原有 0.001 canvas px guard 内；13 份实际引用图片字节与位置保持。1/2/4 倍结果已查看，旧字体异常的 baseline / edited 共 18 次原生程序、字形和 advance 核对通过。历史 ColBERT LO 参考含旧斜角，因此正确圆角直接绑定统一原生稿控制点，不用旧参考宣称当前来源像素等价。

[最终三项图形与旧异常字形独审](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-final-geometry-review/REPORT.md)

最终本地包为 `linux-delivery-040`：regular 的新增范围明确为 `f`，bold 为 `f/t`，已按实际 donor 更正署名。49 份原生稿与 298 份其他字体保持 023 字节，新增元信息不改变原有字形和权限。最终归档的 476 个 manifest 条目、CRC、49 原生稿及 300 字体角色均已独立核对；包内没有任务私有办公应用配置或改字测试 PPTX。它是本地审计交付，字体二进制仍不进入公开 Git。

[最终归档独立读回](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/final-archive-review-004/RESULT.json)

040/041 的锁定独审确认两份新字体的精确署名、逐 face 范围和所有其他 table 保持，300 个实际安装字体哈希匹配；新的 Swift 实际保存件 183 个文字对象全部正确，两份 PDF 的 402 个字形轮廓和度量匹配。WPS 的这些 PDF 字体子集不含 name 表；因此没有宣称版权元信息随导出 PDF 保留，相关失败假设单独保存。

[最终署名及实际 Swift 文件独审](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-glyph-review/final-followup-20261007/FINAL040-INDEPENDENT-REVIEW.md)

[Linux 交付说明与安装入口](2026-10-07-linux-font-delivery.md)另列本地字体依赖及当前包；旧预览嵌入结果与可编辑原生稿保持区分。

还有两类应用限制需要单列：一份 `ccf-2020-14-f03` 的 WPS 导出 PDF 丢失数学字符的 Unicode 映射，修改前/后分别有 19/18 个记录；有限字形墨迹匹配通过，原生 PPTX 的 Unicode 文字保存正确，PDF 语义未恢复。字体名、有限字形检查及文字顺序检查不等于版式证明；已测的字号、字距、基线及图注布局仍有上述有限残差，源画布已截断的上下文沿用原边界，实际裁边及更广的来源排版验收继续单列。

Windows PowerPoint 与 macOS 尚未验收。原完整字体、hinting、未知字符和无需安装字体的自包含 WPS 交付未证明。当前仍为一个共享字体主要问题开放；本轮只记录已实际验证的子问题恢复，不将未完成候选作为最终交付。
