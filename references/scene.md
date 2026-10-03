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
- `image`：`path/sha256/editable:false/box`。路径相对 job 且不得逃逸；`crop:{left,top,right,bottom}` 为 0–1 裁剪比例，保留原图字节。框内等比放置；不支持任意自由变形。
- `style`：实心六位 hex 或 `none`，非负 `stroke_width`，可选 `opacity`。图片的效果不由此 style 修改。
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
