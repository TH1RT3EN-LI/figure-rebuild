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

018 的真实 WPS PDF 已显示独立字母组件；这仍是与源连字不同的明确近似。它尚未完成自己的文字修改、保存和重开验证，也尚缺完整冻结的生成程序。Probe 主动以 `ReadOnly=True` 打开，所以只读标志不能作为安装字体的原生稿不能编辑的证据。新的 donor 许可只覆盖新增组件，不能自动重新许可保留的源子集。字体二进制不随仓库分发。

[Swift 字形与后继许可证据复核](/home/th1rt3en/dev/forge/figure-build-data/work/detail-audit-20261003/reports/font-followup-root-20261007/independent-glyph-review/INDEPENDENT-GLYPH-REVIEW.md)

还有两类应用限制需要单列：一份 `ccf-2020-14-f03` 的 WPS 导出 PDF 丢失数学字符的 Unicode 映射，修改前/后分别有 19/18 个记录；有限字形墨迹匹配通过，原生 PPTX 的 Unicode 文字保存正确，PDF 语义未恢复。字体名、有限字形检查及文字顺序检查也未证明 WPS 版式正确，字号、字距、基线与图注裁边的专用候选仍在实际导出复核中。

Windows PowerPoint 与 macOS 尚未验收。原完整字体、hinting、未知字符和无需安装字体的自包含 WPS 交付未证明。当前仍为一个共享字体主要问题开放；本轮只记录已实际验证的子问题恢复，不将未完成候选作为最终交付。
