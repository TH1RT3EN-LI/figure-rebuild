# 来源组件清单

`source_inventory` 是可选的来源核对门禁。调用方先独立查看源图/PDF，保存核对过的 authoring manifest，再列出普通文字、数学符号、连接和图形组件。它不执行 OCR、公式转写或箭头方向推断；证据摘要也不能证明调用方对来源的理解正确。

在待构建 manifest 中声明相对路径和真实 SHA256：

```json
"source_inventory": {"path": "source-evidence/inventory.json", "sha256": "<inventory 文件的 SHA256>"}
```

清单使用以下结构。`source_sha256` 必须匹配 manifest 的原始来源；`reference_manifest` 是独立核对后保留的 authoring manifest，不是当前候选输出或已 materialize 的 resolved scene。`source_artifacts` 至少保留一份实际来源/核对证据。所有路径以 job 为根，文件在构建时一起冻结。

```json
{
  "schema_version": 1,
  "source_sha256": "<原始来源 PNG 的 SHA256>",
  "reference_manifest": {"path": "source-evidence/reviewed-manifest.json", "sha256": "<真实摘要>"},
  "source_artifacts": [{"path": "source-evidence/source.svg", "sha256": "<真实摘要>"}],
  "components": [
    {
      "id": "layer-label", "category": "literal",
      "source_region": {"x": 10, "y": 10, "width": 80, "height": 20},
      "object_ids": ["label"], "representation": "live_text",
      "reading_status": "verified", "reading": "Layer1", "limitations": []
    },
    {
      "id": "update-equation", "category": "math",
      "source_region": {"x": 10, "y": 40, "width": 80, "height": 30},
      "object_ids": ["equation-glyph-1", "equation-glyph-2"], "representation": "glyph_paths",
      "reading_status": "not_provided", "limitations": ["源字形轮廓已核对；公式语义及 LaTeX 未恢复。"]
    }
  ],
  "unresolved": []
}
```

区域使用来源画布坐标。类别为 `literal`、`math`、`connection`、`graphic`；表示为 `live_text`、`glyph_paths`、`mixed`、`raster`、`native_geometry`。`reading_status` 为 `verified`、`not_provided`、`unresolved`；活动文字已确认时必须提供精确 Unicode `reading`，按 `object_ids` 顺序连接比较。字形路径、混合表示和图片必须声明编辑/语义限制。

未读清的内容保留在 `unresolved`，每项包含 `id`、`category`、`source_region`、`reason`。这类内容以及 `reading_status=unresolved` 会使门禁失败；清空 manifest 的 recognition 未决项不能消除它们。

门禁比较参考 manifest 的全部对象、绘制顺序、画布和组。对象内容、几何和样式使用确定性的完整 JSON 比较，不采用局部边缘诊断的 256 对象预算，也不容许静默忽略未命名对象。未命名对象仍被比较，但覆盖范围标为 `partial`。删/增对象、移符号、改色、改文字、改变箭头或关系都会失败。

运行 `review` / `validate` / `build` 沿用普通流程。构建新增 `source-inventory-audit.json`，结果写入 semantic audit、交付诊断和输出审阅绑定；输出审阅会从冻结证据重新计算清单，并核对 resolved scene 与 authoring manifest。删除或修改清单声明会使已绑定的模型审阅失效。

`FAIL` 表示证据损坏、内容改变或来源未决；`REVIEW` 表示未命名范围、未提供读法或显式表示限制仍需查看；`PASS` 只表示调用方声明的清单和全部参考对象匹配。最终 PPT 的实际字体、图形合成、可见性和用户接受仍需各自验证，`PASS` 不升级为自动语义理解或最终应用验收。
