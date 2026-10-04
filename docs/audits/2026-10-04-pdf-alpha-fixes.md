# PDF 透明图片边缘的受控派生与复核

新增可选的 [binary-alpha-white-matte-v1](../../references/pdf-binary-alpha.md) PDF 派生件。FlashAttention-2、CodaMosa 无编号 AST、ZeRO 2020 和 MAE 的对应额外灰框，在派生 PDF 的整图 1x/2x 与图片边缘 4x 检查中未再观察到；另一套 Poppler 渲染器的关键 4x 边缘检查也确认了这一结果。原始 LibreOffice PDF 保留，其问题仍开放。没有把这项有条件改善计成原始导出问题关闭。

原冻结清单仍为 190/296 项关闭、106 项开放。后续发现新增 1 项 ZeRO-Infinity 原始 PDF 的 CPU 分块细线，累计 72 项中 26 项解决、46 项开放，合计 **152 项开放**。用户验收保持 pending，活动文字、自动语义和原生应用播放限制未关闭。逐项状态及新旧证据见[台账](2026-10-04-detail-resolutions.json)。

## 保留源与原始导出

10 张图都从冻结的完整清单和原素材走正常 CLI 重新构建，使用 LibreOffice 26.2.5.2 的独立 PNG/PDF 导出。清单字节保持不变，最终 PPT 的完整对象顺序、图片媒体、框、裁剪和路径几何均直接读回；两张较早候选的曲线填充处理从原始中间 PPT 独立重放。构建没有移动图片框、扩张遮盖、补白条或修改原媒体来掩盖细线。

派生仅适用于同尺寸的直接 8-bit DeviceRGB 图片和 DeviceGray 二值 soft mask。alpha=1 的 RGB 不变，alpha=0 的 RGB 精确预混为白色并添加 `Matte [1 1 1]`；原始蒙版流和 Decode 保留。根据原始 PDF 独立重建每个样本，并核验其余全部 PDF 对象、字体、内容流和编码字节。共有 **230 个二值蒙版变换、18 个蒙版保留**；保留原因是非二值或常量 alpha，没有舍入半透明样本。

这是离散预乘 RGBA 样本的等价核验，不提供 RGB/alpha 滤波误差界或任意倍率外观保证。修改只出现在 `native-preview/derived-pdf/binary-alpha-white-matte.pdf`；原始 `native-preview/pdf/reconstruction.pdf` 继续绑定为 `native_pdf`，直接 PNG 继续用来审阅最终 PPT。派生件与回执独立绑定到配置、渲染审计、delivery 和 output review，重绑被篡改文件的外层摘要仍不能通过逐样本重放。

## 逐图观察

全部 10 张图实看源/最终 PNG 的整图 1x 比较、29 个无缩放的 2x 四联片，以及每张图明确记录的 4x 图片边缘和内部裁片。Fawkes 与 ZeRO-Infinity 的 4x 只覆盖指定关键实例，没有声称检查每个图片实例。四张无对应灰框的派生图另用 Poppler 检查关键 4x 边缘。

| 图 | 变换 / 保留蒙版 | 派生 PDF 的观察 |
| --- | ---: | --- |
| Entangled Watermarks | 8 / 0 | 大部分外框减轻；数字栈和 DNN 的上、左、下边仍有局部细线，4x 明显。 |
| FlashAttention-2 | 1 / 0 | 原新增上/下边灰线未再观察到；源橙色虚线、文字和模块保留。 |
| CodaMosa 无编号 AST | 1 / 0 | 原新增外侧灰矩形未再观察到；蓝色虚线和源阴影保留。 |
| GPT-3 | 0 / 1 | 非二值蒙版保留；派生 PDF 是原始文件的逐字节副本，外框仍在。 |
| Fawkes | 52 / 1 | 大部分外框减轻；深色括号内部的竖向分块接缝仍在。 |
| ZeRO 2020 | 1 / 0 | 原新增外侧灰矩形未再观察到；源蓝色网格与图注保留。 |
| CodaMosa Figure 1 | 1 / 0 | 多处边缘改善；2x 仍见彩色 mutant 框下方跨空白的细底线。 |
| MAE | 10 / 0 | 输入/目标照片网格的新增外灰框未再观察到；既有照片细节采样问题仍开放。 |
| CoCoNuT | 9 / 15 | 彩色网络框改善；灰色网络缩略图的部分外框仍在。 |
| ZeRO-Infinity 控制 | 147 / 1 | 派生件的 CPU 分块灰网格未再观察到；原始 PDF 的该问题新增并保持开放。 |

10 份普通 output review 记录和复核均成功绑定；严格无问题门禁均因上述保留的原始 PDF 问题返回 1。不能用普通审阅成功宣称无问题或用户验收。

## 失败、预算与验证

同一 PNG 的独立 PDF 控制复现了灰框，反转 soft-mask 字节并同步反转 Decode 没有消除它。初始仅含 Artifact 的冻结运行时配置导致 10 个预检失败；后继绑定实际原生 profile。16MP 单图预算下 7 张成功、3 张在分配前受控拒绝；后继单图上限为 32MP，总解码工作预算仍为 256MB，计入 RGB 原始/可变/不可变副本后 10 张构建成功。文件、对象及 Flate 展开均有独立边界；这些限制不声称约束外部原生解析器的全部内存。

小流实验证明 MuPDF 可能删除显式 `DecodeParms null`；维护实现保留该字段。PDF 保存可能内联间接 Length，验证比较真实编码长度而不要求引用形式相同。原始文件、失败候选、检查器错误和后继版本全部保留，未放宽无关对象的核验。

最终可执行代码冻结树为 `38845995f76db2916fac795e2c98f49f27c3be82`，完整 Python 检查 1,096 项，1,088 通过、8 个可选环境项跳过；Node 53 项通过，包括实际 Skia 控制。后续改动仅为文档和台账，源码及测试字节另行与该冻结副本核对。发行包、仓库外 core 安装和最终提交 CI 的原始结果保存在下述外部报告中。

外部证据根为 `figure-build-data/work/detail-audit-20261003`。`reports/native-binary-alpha-maintained-001` 保存代码快照、完整检查、10 张构建、实际 PPT 读回、双渲染器对比、正式审阅和台账前后版本；`reports/native-soft-mask-decode-001` 保存诊断原型与反例；`source-regeneration/native-binary-alpha-maintained-001/002/003` 保存失败、预算拒绝及完整成功成品。论文源素材和完整输出没有加入源码仓库。
