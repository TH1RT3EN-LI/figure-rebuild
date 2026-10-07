# WPS PDF 数学字符映射修复

2026-10-07。本轮在已合并的 `main` 后继续修复共享字体问题，原 Linux 040 交付、原 WPS 导出与失败实验保留。

## 已修复的范围

`ccf-2020-14-f03` 的实际 WPS PDF 中，数学字体对象 14 的 `ToUnicode` 对象 15 把 CID 2–10 的九个非 BMP 字符写成 `U+FFFD`。原生 PPTX 的文字正确，PDF 中实际绘制的字体程序与字形也正确，因此修复只需要恢复 PDF 的文字映射。

新增 [`repair-pdf-unicode`](../../references/pdf-unicode-repair.md) 命令：从调用方提供、SHA256 绑定的候选字体，用精确归一化轮廓及水平 metrics 唯一恢复 Unicode，再增量更新 `ToUnicode`。非 BMP 字符使用真实 UTF-16BE 代理对。它不依据预期文字、相邻字符或字体名称推断语义。

| 实际文件 | 原 PDF SHA256 | 恢复的实际绘制记录 |
| --- | --- | ---: |
| 修改前 WPS 导出 | `d91d26a7f39d56c76b3124a29e8c0e42ba3e6189dcc67fa8f0c051e6ce20d538` | 19 |
| 修改后 WPS 导出 | `f8746e3f1e551796987de1dd7fac03e151e4d85a4ead15a09762902a6805fc5b` | 18 |

两份导出的实际 `FontFile2` 程序 SHA256 均为 `643b819e7a9e6cf13bd4a974db6bbfb15d66a5d0ff7f9206e88ca399770382b0`。独立原生 handle 读取确认程序字节一致，编码、CID/GID 与实际绘制逐项绑定。候选 Cambria TTC face 1 的 SHA256 为 `84e70ccc1664482f4a960442c7a166c91a1b2cf98ff88c33cb73f79403f66d7b`；它的归一化轮廓及 metrics 精确匹配，但原候选的 hint instructions 不等同导出子集，不能据此宣称原完整字体身份或 hinting 已恢复。

修复结果保留原 PDF 的全部字节作为前缀，仅追加对象 15 的新版本及必要的增量 cross-reference/trailer。其余 23 个 xref 对象、字体、图片及绘制流保持；实际 GID、坐标、矩阵和绘制标志保持。每份 PDF 的 95 个实际绘制记录只发生上述 Unicode 改变。公共 API 在写出前校验 1 倍 RGBA；独立 MuPDF 和 Poppler 复核 1、2、4 倍结果，全部 RGBA 字节相等，两种文字提取器的非 BMP 数量与实际保存的 PPTX XML 一致。

## 拒绝范围与保留的失败

支持域为静态 TrueType、Type0/Identity-H、CIDFontType2/Identity CID-to-GID、一块两字节 Identity-UCS `bfchar`。NULL、缺失映射、任意编码、CFF、变量字体、共享 `ToUnicode`、字典 `UseCMap` 继承、`ActualText`、marked content、Form XObject、不可见或合成字体及歧义候选需要其他明确政策，当前拒绝。无实际绘制绑定的损坏 CID 也拒绝。

第一次整文件保存虽然没有改变可见内容，却重排了非目标字典；严格对象保持检查将其拒绝并保留。后继采用增量写入。独审还保存了未绘制 NULL、`UseCMap` 和非零 generation 的共享引用反例，并补入明确拒绝控制。各次实验与回执另存，不改写历史失败为通过。

字体二进制、原 PDF 和本地办公应用配置不进入公开仓库。这里提供运行代码、人工构造的测试及带 SHA256 的证据引用，未取得新字体的再分发授权。

最终源码测试运行 1,417 项，无失败，8 项既有可选环境检查跳过；新增模块及命令的 25 项检查全部执行并通过。Node 检查 73 项，69 项通过、4 项既有环境检查跳过。全新仅基础依赖环境中的已安装 wheel 在仓库外跑完 1,417 项，428 项源/视觉可选检查跳过；另一个全新 source-extra wheel 环境重新执行 18 项字体 PDF 检查及真实两份 PDF 的命令、多倍像素与文字提取验证，均通过。缺少 source extras 时命令明确提示安装依赖，不输出 PDF 或回执。wheel、sdist 的公开运行代码与对应源码字节一致。

## 仍开放的共享字体问题

本轮恢复的是两份明确导出的 PDF 语义。WPS 导出器仍会写出原损坏映射，使用时需运行独立修复步骤；PPTX 没有改动。Linux WPS 的自包含字体交付、Swift 来源连字、较广的来源排版、完整字库、hinting、未知字符、Windows/macOS 和用户验收仍需各自的证据。总登记状态保持 379 项、378 项已关闭、1 项共享字体主要问题开放。

来源连字探针、排版候选和 WPS 自嵌入测试与这个确定的 PDF 修复分别记录，未将候选提升为正式原生交付；040 本地字体依赖保持。

本轮实际公共 API 回放与冻结输入：`reports/font-repair-next-20261007/unicode/public-api-005/`；独立实际 PDF、文字提取和多尺寸像素复核：`reports/font-repair-next-20261007/independent-unicode/`。这些路径相对既有外部审计根目录，不包含于 Git。
