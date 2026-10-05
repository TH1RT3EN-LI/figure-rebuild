# 从方法说明创作学术架构图

原创架构图是 Figure Rebuild 的主要创作场景之一。用户提供方法说明、代码或明确的系统结构时，由调用方模型设计表达；`create` 将明确的图结构编译为原生对象。无需参考图。原图复建继续使用 `prepare`。

## 先确定这张图需要解释什么

从用户材料提取输入、输出、模块、表示、操作、训练目标和有向关系。先写一句主结论，再列节点与边；每条关系说明依据。保留用户原始说明与明确的假设，关键机制缺失时只询问影响结构的内容。配色、留白和短标签可以自主决定，科研依赖、损失项和张量尺寸不能借模板补造。

读者应能先辨认主流程，再定位贡献模块，最后看局部细节。概览优先展示模块职责与依赖；层数、具体算子和张量尺寸放到局部展开或另一幅图。方法图、系统部署图、训练流程与消融对照的阅读任务不同，不能全压进一条流水线。

## 可复用的表达规则

以下是有条件的创作起点。代码支持范围和实际示例列在表内，不能将设计建议当作经过用户确认的普遍审美规律。修复经验来源见[共性根因](../docs/audits/2026-10-03-detail-audit.md)；它支持文字、关系、裁切与输出复核的约束，不证明某种新布局一定更美观。

| 规则 ID | 适用任务与安排 | 条件与反例 | 可执行起点 |
| --- | --- | --- | --- |
| `academic-pipeline-v1` | 串行处理按阶段排列，输入、变换、输出有稳定阅读方向 | 同时发生的分支不能为了整齐改成串行；过宽时换纵向或分面 | `creation-pipeline.json`，`LR/TB` |
| `academic-parallel-fusion-v1` | 等地位的分支在相邻泳道，共享阶段对齐，汇合点居中 | 共享权重、跨分支通信与先后依赖必须另有明确关系；不能用对称外观暗示不存在的对称性 | `creation-parallel-fusion.json` |
| `academic-training-feedback-v1` | 前向路径保留主轴，目标和参数更新在辅助区域，线型与图注解释关系类型 | 更新边不代表推理输入；损失输入、梯度、控制和时序反馈分别声明 | `creation-training-feedback.json` |
| `academic-visual-roles-v1` | 模块、输入输出、表示与目标保持一致样式；真正贡献模块可强调，层叠卡片表达张量 | 层叠卡片不自动意味着具体维度或多层网络；颜色不能是唯一的语义编码 | `kind`、`emphasis`、`tensor` |
| `academic-density-v1` | 先短标签、后辅助说明，模块宽度统一、实测文字、关系走留白区域 | 文本过密时拆图或缩短说明，不能无限缩字号；面板套面板需重新检查阅读顺序 | 字体实测、节点重叠与连线避障检查 |
| `academic-final-review-v1` | 对照说明核全部节点/边，再看真实 PPT 的整图和局部，检查最终论文尺寸 | 编译成功、几何检查通过和用户认可分别记录；实际打印缩放可能使字太小 | 审阅、构建、实际预览与原生读回 |

更复杂的重复网络块、局部放大面板、架构对照、带图像的视觉方法图、冻结模块和共享权重标记，使用[场景协议](scene.md)自主编排。当前 `create` 的网格布局只提供平面组和矩形模块，不自动推断这些机制。

## 学术视觉规范

- 明确的主流程、有限的强调色、统一线宽与字号层级。默认白底、浅色模块、深色文字；强调服务于研究内容。
- 在最终排版尺寸检查字与线。横向长图缩到单栏后通常过密，宜改用纵向、双栏或局部展开。源画布字号不等于论文中的字号。
- 同一种关系保持同一表达方式。虚线/颜色均须由图注解释；箭头穿过无关模块、连接方向不清与交叉处含义不明需要调整布局。
- 数学表达保持用户定义；普通变量字符串不冒充 LaTeX 排版。需要正式公式时，使用既有 `formula` 和带源码的公式资产流程。
- 常规文字保持活动文本，模块、张量层与线保留原生几何。照片和热图明确作为图片。修改复杂结构后重新生成与检查。

## 创作规格 v1

`brief` 保存原始创作要求，`nodes/edges` 是调用方依据要求设计的结构；工具没有自然语言解析器。`stage/lane` 是逻辑布局位置，工具计算物理坐标。

```json
{
  "schema_version": 1,
  "id": "my-method",
  "brief": "输入经编码器变成输出表示；强调编码器。不增加其他模块或依赖。",
  "direction": "LR",
  "nodes": [
    {"id": "input", "label": "Input", "kind": "input", "stage": 0},
    {"id": "encoder", "label": "Encoder", "detail": "Feature extraction", "stage": 1, "emphasis": true},
    {"id": "features", "label": "Features", "kind": "tensor", "stage": 2}
  ],
  "edges": [
    {"id": "encode", "from": "input", "to": "encoder"},
    {"id": "represent", "from": "encoder", "to": "features"}
  ]
}
```

字段：

- `id` 为字母起始，最多 48 个字母、数字、`_`、`-`。`schema_version` 为整数 1。
- `brief` 必填；可选 `title/caption` 必须非空。标题只支持单行，过长时拒绝，不截字。
- `direction` 为 `LR` 或 `TB`。最多 48 个节点、96 条边、12 个互不重叠的平面组。
- 节点 `id/label/stage` 必填；`stage` 是 0–12 整数，`lane` 默认为 0，可为 0–8 的半整数。列/泳道安排由调用方选定；实际节点仍不得重叠。
- `kind` 为 `input/module/tensor/operator/output/loss`，默认为 `module`；可选 `detail/emphasis`。`operator` 是短标签模块，不自动推断加法或拼接语义。
- 边 `id/from/to` 必填；可选 `kind` 为 `flow/skip/feedback/training`，默认为 `flow`。`training/feedback` 使用不同颜色的虚线；`skip` 是实线旁路。工具保留声明方向，不推断研究意义。自环不在此规格内。
- 边可选 `label`；没有明确的无重叠位置时拒绝。`groups` 每项为 `id/label/members`，不能包含未声明成员或无关模块。

当前避障器寻找正交路径，避开所有模块内部；共享目标的边可以汇合。独立边交叉写入 `authoring-audit.json`，必须实际复查。复杂路线是可编辑路径，移动节点不会自动重新布线；在创作规格中调整阶段/泳道后创建新 job。

## 完整执行

```bash
figure-rebuild create --spec /path/to/creation.json --job /path/to/new-job
figure-rebuild review --manifest /path/to/new-job/manifest.json --note '已核对方法说明、节点、方向与布局'
figure-rebuild build --manifest /path/to/new-job/manifest.json
```

`create` 可用 `--font-profile` 指定已有的常规/粗体字体，无需 Node/PPT 后端即可生成原始 SVG 和清单；未指定时使用配置好的字体。实际 `build` 仍需要导出运行时。

输出保存原规格字节、主 SVG、清单和布局回执。来源分类为 `generated_diagram`，不冒充原论文素材；默认审阅和用户接受均待完成。规格、布局与实际对象绑定，构建冻结这些字节。创作修改应保存新规格并创建新 job，保留先前版本；不能改了成品清单却沿用旧布局证明。

真实 PPT 预览是验收对象；SVG 是检查结构的辅助稿。原创图的像素对照只比较设计稿与导出，不评价科研正确性。审查逐条核对用户说明，记录仍未知的科学机制、交叉关系、最终缩放、字体依赖及目标 Office 应用的未验证范围。

三个原创示例规格在 `docs/assets/creation-*.json`，均为合成架构，不对应真实论文或性能结论。对应真实 PPT、修改理由、知识状态及安装验证见[本次创作验证记录](../docs/audits/2026-10-05-academic-creation.md)。它们用于验证创作管线，不能算作对独立真实研究任务的审美验收。
