# 原创学术架构图：规则与实际执行验证

用户明确将创作能力设为首要目标。本次新增从方法说明对应的显式图结构直接生成架构图的 `create` 入口，将原创模式放在技能与文档的主要流程。参考图复建继续提供内容保真、字体与成品复核经验；新方法的科研事实来自用户材料。

## 已形成的能力

调用方模型从说明或代码提取输入输出、模块职责、有向依赖与贡献重点，选择阶段、泳道和阅读方向。`academic-grid-v1` 实测字体，生成活动文字、模块、张量卡片、平面组与避开模块内部的正交路径，保存原始规格、SVG、场景及布局回执。它不包含自然语言解析器；复杂局部展开、公式与图片使用既有场景协议。

创作规格与实际对象绑定；构建冻结规格和回执。修改需新规格、新 job，旧版本保留。科学含义、实际导出视觉和用户接受分别记录。完整协议见[学术创作指南](../../references/academic-creation.md)。

## 三个实际原创示例

这三个示例是自行定义的合成架构，不对应论文、实验性能或独立真实研究任务。每个示例实际执行 `create → review → build`，检查原始 SVG 的渲染稿、实际 PPT 1×/2× 整图，并独立读回最终 PPT 的对象、全部活动文字与有向箭头尖端。

| 示例及规格 | 可编辑成品 | 节点 / 有向边 | 原生对象 / 活动文字 / 路径 | 图片对象 |
| --- | --- | --- | --- | --- |
| [串行处理](../assets/creation-pipeline.json) | [PPT](../assets/creation-pipeline.pptx) · [预览](../assets/creation-pipeline.png) | 5 / 4 | 25 / 12 / 13 | 0 |
| [并行融合](../assets/creation-parallel-fusion.json) | [PPT](../assets/creation-parallel-fusion.pptx) · [预览](../assets/creation-parallel-fusion.png) | 9 / 8 | 48 / 18 / 30 | 0 |
| [训练反馈](../assets/creation-training-feedback.json) | [PPT](../assets/creation-training-feedback.pptx) · [预览](../assets/creation-training-feedback.png) | 7 / 7 | 36 / 12 / 24 | 0 |

三份成品全部保留声明文字；19 个有向箭头尖端与声明目标的原生坐标相差不超过 1 EMU。实际活动文字测量均为 PASS，三个布局的独立边交叉警告均为 0。这些结果支持指定规格的执行与表达，不证明任意新方法科研正确或一次即可通过审美验收。

初版与修改版都已保存。融合例初版分支间距过大，修改为相邻泳道、汇合点居中；训练例初版目标线使用预测模块的输入侧，修改为下方目标区域与明确的底部/顶部端口。串行例更正了与实际编码器强调不一致的图注。修改后重新导出和实际检查。

最终三次构建的 `output-review.json` 均绑定确切源文件、PPT、交付文件与实际预览，记录模型检查 `no_observed_issue`；用户接受仍为 `pending`，目标办公应用仍为 `not_verified`。

## 六条可调用的知识

外部资料库 `indexes/knowledge.json` 已登记六条不可覆盖的 revision，当前均为 revision 1。前三级规则的 `tested` 只覆盖表中的合成图；后三条是有条件的 `candidate`，尚未获得真实任务与用户接受证据。

| 知识 ID | 本次状态 | 可迁移内容 |
| --- | --- | --- |
| `academic-pipeline-v1` | tested | 串行阶段、统一阅读轴与端口方向 |
| `academic-parallel-fusion-v1` | tested | 相邻泳道、共享阶段对齐与居中汇合 |
| `academic-training-feedback-v1` | tested | 前向计算、目标输入与更新关系分层 |
| `academic-visual-roles-v1` | candidate | 视觉角色一致、有限强调与图例 |
| `academic-density-v1` | candidate | 真实字体测量、短标签、间距和最终尺寸 |
| `academic-final-review-v1` | candidate | 说明、结构、实际导出与接受分别核对 |

每条知识保留条件、反例、来源组和评价引用。初版与修改版属于同一来源组；未统计全图库模式频次，频率字段保持未知；没有迁移示例的科学事实，也没有将模型审阅计为用户确认。

外部评估批次为 `academic-creation-evaluation-20261005`，三个 figure ID 为 `academic-original-{pipeline,parallel-fusion,training-feedback}`。各自保留 `initial-001` 和 `revised-002`；后者的第二次完整 capture 包含最终正式审阅记录。资料库完整性检查通过，不代表科学或审美验收。

## 测试与安装验证

- Python 3.12.14：1179 项，1171 通过、8 个可选测试跳过。新增 13 项覆盖文字与有向图保留、模块避障、纵向布局、歧义边类型、过密标签、组边界、生成 ID 冲突、旧 job 保护及规格/对象失效检查。
- Node：58 项，57 通过、1 个可选跳过；本次未改 Node 后端。
- 构建 wheel 与源码包，分发检查通过，包含作者入口、创作指南及公开规格，排除本地字体、配置与任务目录。
- 将 wheel 安装到仓库外独立目录，确认 `authoring` 模块来自该目录；仓库外测试同为 1179 项、8 个可选跳过，命令帮助与 `create/review` 成功。
- 安装版首轮 `build` 未提供独立运行时配置而拒绝导出。保留失败记录，补充明确的运行时配置后实际导出成功；其 1× 预览与已检查的串行公开示例逐字节相同。没有重新运行已通过的测试。
- 仓库技能与本机 `figure-build` 技能均已更新原创入口，技能结构校验通过。

完整执行脚本、stdout/stderr、原始及修改 job、原生读回结果、正式审阅与安装验证保存在外部评估工作目录和不可覆盖 captures。公开规范、合成输入、PPT 与预览保留在仓库。

## 仍需实务验证的范围

正式研究方法应由材料确定依赖、损失项、张量尺寸与共享机制。几何和活动文字检查无法验证科研正确性；当前源内容/公式连接诊断明确为未提供独立语义记录，不能升级为语义识别通过。

尚未完成真实方法的用户视觉接受、最终论文栏宽检查或 PowerPoint/WPS 重开与编辑测试。大幅 2× 图按显示尺寸检查，不声称逐像素检查所有细节。复杂路径可以编辑，但拖动模块后不会自动重布线。重复块、局部展开、架构对照和公式图片组合仍需调用方按场景协议设计与检查。

公开 PPT SHA256：

| 文件 | SHA256 |
| --- | --- |
| `creation-pipeline.pptx` | `79bb246110d4491597ad3dc09d2f156e77320ec3051339261a15062ec99416ba` |
| `creation-parallel-fusion.pptx` | `f04ad46e48239ab682fc8a901f2ec6ba1b8c533bb6c08bb69b64c4bda2f76716` |
| `creation-training-feedback.pptx` | `de8215e133a755eb95ed616b3ecd63c2341591ebd4fea04a3cc1900aea7423b9` |
