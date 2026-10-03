# 参考图识别清单 v1

`manifest.json` 的坐标是参考图的像素坐标，原点左上；不是页面的 0–1 内容协议。实际 validator 在 `tools/figure_rebuild/validate.py`。`review` 写入内容摘要，修改清单需增加 revision 并重新审阅。自建 SVG 示例见 `examples/compound-curves/reference.svg`。

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
