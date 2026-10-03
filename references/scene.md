# 参考图识别清单 v1

`manifest.json` 的坐标是参考图的像素坐标，原点左上；不是页面的 0–1 内容协议。实际 validator 在 `src/figure_rebuild/validate.py`。`review` 写入内容摘要，修改清单需增加 revision 并重新审阅。

```json
{
  "schema_version": 1,
  "id": "my-figure",
  "revision": 1,
  "source": {
    "path": "sources/original.png",
    "sha256": "填写 prepare 生成的原图哈希",
    "width": 800,
    "height": 500,
    "kind": "research_original",
    "uri": "原论文或素材链接"
  },
  "canvas": {"width": 800, "height": 500, "background": "#FFFFFF"},
  "recognition": {
    "provider": "calling_host",
    "status": "needs_review",
    "notes": "来源、字体适配及近似范围",
    "unresolved": []
  },
  "objects": [
    {
      "id": "module-shape", "group_id": "module-group", "kind": "path",
      "z_index": 1,
      "commands": [
        {"moveTo": {"x": 100, "y": 100}},
        {"lineTo": {"x": 260, "y": 100}},
        {"lineTo": {"x": 260, "y": 180}},
        {"lineTo": {"x": 100, "y": 180}},
        {"close": {}}
      ],
      "style": {"fill": "#EBEBEB", "stroke": "#333333", "stroke_width": 2}
    },
    {
      "id": "module-label", "group_id": "module-group", "kind": "text",
      "z_index": 2, "text": "可见原文", "font_size": 24,
      "box": {"x": 100, "y": 100, "width": 160, "height": 80},
      "alignment": "center", "vertical_alignment": "middle", "wrap": "none",
      "style": {"fill": "#000000"}
    }
  ]
}
```

对象与样式：

- `path`：`moveTo/lineTo/cubicTo/close`，支持同一对象中的多个子路径；`cubicTo:{x1,y1,x2,y2,x,y}` 使用原画布的两个控制点和终点。最终 PPT 保留真正的 `a:cubicBezTo`，SVG 保留 `C`；Artifact Tool 中间体采样不作为最终几何。非零环绕孔洞保留。坐标是整个参考画布坐标。箭头头部也用稳定路径对象表示。SVG 自动导入目前仍采样，误差默认 0.35 px。
- `text`：原文、`font_size` 为像素，`font_family/bold/italic/rotation` 可选；family 必须在 runtime 显式配置，缺失字形或未配置字体报错，不回退。使用 `box`，或未旋转 SVG 文本的基线 `anchor:{x,y}`。`wrap` 用 `none` 或 `square`；不以空格模拟居中，不为排版插入硬换行。
- `image`：`path/sha256/editable:false/box`。路径相对 job 且不得逃逸；`crop:{left,top,right,bottom}` 为 0–1 裁剪比例，保留原图字节。`fit:"contain"` 为缺省值，裁后内容等比居中；显式 `fit:"stretch"` 将裁后内容准确映射整个框，适用于已核实的源图非等比变换。裁剪比例始终相对于完整原素材；stretch 不会推断或修复缺失的源裁切。
- `style`：实心六位 hex 或 `none`，非负 `stroke_width`，可选 `opacity`。路径可显式指定 `stroke_linecap:butt/round/square`、`stroke_linejoin:miter/round/bevel`；`stroke_miterlimit` 为正比值且需要显式 miter，省略时 miter 上限为 4。不声明这些字段时保持已有描边行为。图片的效果不由此 style 修改。
- 路径可用 `style.fill_gradient` 表达连续线性渐变，例如 `{"type":"linear","angle":90,"stops":[{"offset":0,"color":"#E4CA07"},{"offset":1,"color":"#FEFEF8"}]}`。此时 `fill` 省略或为 `none`。角度为物理画布中顺时针 `[0,360)` 度（0 向右、90 向下），按原生 1/60000 度量化。渐变覆盖原生路径框沿此方向的完整投影；路径框包括 cubic 控制点，不是曲线实际极值框。Python `linear_gradient.native_path_frame(commands)` 和 `gradient_axis(frame, angle)` 可取相同框和轴端点，SVG 也使用此轴。
- 渐变支持 2–64 个色标，从 0 到 1 严格递增，每个可附 `opacity`，再乘全局 `style.opacity`。色标位置按原生 1/100000 精度量化；量化后重合的色标拒绝。原生路径和颜色/透明度均核验，不用色带或位图代替；径向和独立全局坐标渐变尚不支持。Artifact Tool 2.8.59 对不同色标透明度的插值有已确认差异，`render-audit.json` 会标记需应用复查；原生 XML 正确不等于预览颜色正确。
- `group_id` 记录逻辑组。只有在 z 顺序中连续且有多个成员的组会生成原生 PPT 组；不连续组保留逻辑映射，以免分组改变遮挡关系。

PNG 识别时先列可见文字，再确定各图形轮廓与连线端点，最后核对所有分支和遮挡。透明像素不是黑色背景。不要借论文知识增加图中没有的连接或标签。数值图表需要更精确的作者矢量源或原数据；肉眼描线不得声称恢复了实验数据。

SVG 支持基本几何、变换、普通文字及路径 M/L/H/V/C/S/Q/T/A/Z；曲线输出为可编辑采样路径。渐变、遮罩、裁切、资源引用、evenodd 填充、旋转/倾斜 SVG 文字、非均匀描边变换和特殊端点/虚线效果等未实现；导入器拒绝这些输入。对不支持的效果，可先制作受控的混合清单，原始素材始终保留。

0.5 语义对象：

- `formula`：`audit/audit_sha256/representation`（`svg` 默认，或 `png`），`box:{x,y,width,height}` 或 `baseline_anchor:{x,y}` 二选一。可选真实 em `font_size`；最终放置和 PNG 回退采样受校验。详见 [formulas.md](formulas.md)。
- `connector`：`from/to:{id,site}`，`route` 为 `straight/elbow`，`arrow:{start,end}` 与描边。目标为已确认模块，导出原生连接器；连接器之间不连接。详见 [connections.md](connections.md)。
- 标签 `attach_to:{id,site,offset:{x,y}}`：关联模块的中心或四边；框文本按框中心，基线文本按 anchor。构建保留原生分组并检验绘制顺序。
- 文本 `line_height/baseline_offset/insets` 使用像素，四字面必须分别配置真实文件。详见 [fonts.md](fonts.md)。

原始清单接受审阅，构建输出 `resolved-scene.json` 与 `semantic-audit.json` 供检查；临时解析清单不取代源清单。

## 径向指针

仪表盘等径向部件可用 `figure_rebuild.geometry.radial_pointer_commands` 生成原生路径：

```python
from figure_rebuild.geometry import radial_pointer_commands

pivot = (60.0, 80.0)  # 从参考图确认的轴心；hub 也复用这对坐标
needle = {
    'id': 'score-gauge-needle', 'kind': 'path',
    'commands': radial_pointer_commands(pivot, (87.0, 49.0), half_width=3.5),
    'style': {'fill': '#3A444C', 'stroke': 'none', 'stroke_width': 0},
}
```

函数用轴心到尖端的方向推导两个对称底角，使底边垂直于指针轴，底边中点与轴心一致；不独立估计三个顶点。默认尖三角；原图为钝头时，可显式传 `tip_half_width=0.6`，生成末端中点为 `tip` 的共轴梯形，不把可见钝头延长为虚构尖端。坐标和宽度必须为有限数值，轴长和底部半宽为正，末端半宽非负，输出不能退化。轴心不必等于表盘弧线的几何中心。尖端、宽度、刻度数量和角度仍须来自原图，工具不自动调整示意值或均匀化刻度。编辑后重新生成路径，并检查真实导出中的 hub、尖端、刻度及遮挡关系。
