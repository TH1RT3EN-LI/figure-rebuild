# 细节保真与输出审阅

先从原图建立证据，再填写对象。文本尺寸合格只说明所测文字能放入框内，不能证明转录、字形、连线和内容正确。生成一次成功的判断必须来自从源素材开始的新任务；复用修好的清单只算渲染回归。

## 识别顺序

1. 在相同源坐标中分区，逐字记录代码、负号、下标、标点和有意拼错的字符串。不要从论文知识、上下文或常见 API 名称补成“合理”的文本。源 PDF 的 Unicode 也可能与实际字形不同，数学子集字体需要对照字形。
2. 逐条记录连接的起点、终点、箭头方向、分叉、交叉与是否真正相接。热图单元、节点编号、端口和图标不能由通用模板替代；规律不能覆盖实际可见的例外。
3. 分别核对线帽、孔洞、浅色细线、虚线、遮挡、圆角、透明度和图片范围。同一素材的多次绘制可能有不同裁切和变换。组透明度不能无条件分摊给每个子对象。
4. 不确定项持续保留，解决时记录依据。辅助脚本不得仅因生成结束就清空 `recognition.unresolved`。源主体是位图时，不声称恢复了作者原生路径；描绘轮廓与可编辑文本框应分别披露。

## 已确认内容的约束

清单可增加 `source_evidence`。预期文字必须来自原图独立转录，不能复制当前输出再把自洽当作正确。区域使用源画布坐标：

```json
{
  "source_evidence": {
    "schema_version": 1,
    "source_sha256": "与 source.sha256 相同的原图 SHA256",
    "literals": [{
      "id": "code-reading", "status": "confirmed",
      "source_region": {"x": 10, "y": 20, "width": 150, "height": 30},
      "object_ids": ["code-label"], "text": "pos_min = -3"
    }],
    "connections": [{
      "id": "flow-reading", "status": "confirmed",
      "source_region": {"x": 20, "y": 50, "width": 220, "height": 40},
      "object_id": "flow",
      "from": {"id": "module-a", "site": "right"},
      "to": {"id": "module-b", "site": "left"},
      "arrow": {"start": "none", "end": "triangle"}
    }]
  }
}
```

文字约束按给定对象顺序逐字连接，不做空白、拼写或 Unicode 归一化，只针对实际可见的文本对象。连接约束检查解析后的连接器、实际命令与端点记录。普通手绘路径的语义仍需目视核对。未确定的条目用 `status:"unresolved"`，不能通过删除 `recognition.unresolved` 绕过。

`validate` 与构建会阻断已声明约束的失配或未解决项，构建保存 `source-content-audit.json`。没有约束时明确报告 `NOT_PROVIDED`；`PASS` 只覆盖所声明的约束。重复文本诊断需要目视确认，不自动删除可能有意叠加的内容。Python API `audit_source_content(scene, previous_manifest=...)` 可检查旧未知项是否有明确解决记录；未传历史时不会声称检查过历史删除。

## 审查实际输出

先冻结待检查文件的绑定：

```bash
figure-rebuild review-output --run JOB/build/run-001 --template JOB/review-draft.json
```

亲自打开模板列出的原图、实际 1x/2x PPT 预览和必要局部；补填 `model_review` 中的检查人、方法、检查范围、问题与限制。`inspected` 每项写明 `artifact`、原绑定的 `sha256` 与 `regions`。`performed` 只有实际检查后才为 `true`。状态为 `no_observed_issue`、`issues_found` 或 `needs_further_review`，并不意味着用户接受。

```bash
figure-rebuild review-output --run JOB/build/run-001 --record JOB/review-draft.json
figure-rebuild review-output --run JOB/build/run-001 --verify JOB/build/run-001/output-review.json --require-no-observed-issues
```

记录时不会刷新旧哈希：源图、清单、解析场景、PPT 或预览发生变化会拒绝套用旧审查。开放问题可以保存，严格核验选项会阻断尚未解决的问题。后续审阅使用新文件名，保留旧记录。用户验收与原生 PowerPoint/WPS 验证独立记录；构建预览不证明原生应用播放或编辑通过。

对照诊断同时检查整张源画布的网格和对象局部。`coverage` 明确记录分析缩放、预算和遗漏，网格覆盖不等于每处细节已经人工确认；未经配准的原始误差和实际内容核对始终保留。
