# 细节保真与输出审阅

先从原图建立证据，再填写对象。文本尺寸合格只说明所测文字能放入框内，不能证明转录、字形、连线和内容正确。生成一次成功的判断必须来自从源素材开始的新任务；复用修好的清单只算渲染回归。

## 识别顺序

1. 在相同源坐标中分区，逐字记录代码、负号、下标、标点和有意拼错的字符串。不要从论文知识、上下文或常见 API 名称补成“合理”的文本。源 PDF 的 Unicode 也可能与实际字形不同，数学子集字体需要对照字形。
2. 逐条记录连接的起点、终点、箭头方向、分叉、交叉与是否真正相接。热图单元、节点编号、端口和图标不能由通用模板替代；规律不能覆盖实际可见的例外。
3. 分别核对线帽、孔洞、浅色细线、虚线、遮挡、圆角、透明度和图片范围。同一素材的多次绘制可能有不同裁切和变换。组透明度不能无条件分摊给每个子对象。
4. 不确定项持续保留，解决时记录依据。辅助脚本不得仅因生成结束就清空 `recognition.unresolved`。源主体是位图时，不声称恢复了作者原生路径；描绘轮廓与可编辑文本框应分别披露。

有源 PDF 时，可用[PDF 源几何与图片实例](pdf-source.md)提取有来源身份的候选路径和图片。该接口不会自动完成整图识别，也不允许静默遗漏不支持的绘制项。

首次绘制前再独立核读一次容易改变含义的局部：逐位置核对故意拼错的词、重复节点号、代码标点和数学字形，不用全局替换“纠错”。旧审查报告也可能误读或漏项，最终以绑定的原图和对应 PDF 高倍率区域为准。高倍率参考应重新渲染同一 PDF 区域，并先验证坐标与原始裁图一致；把低分辨率截图放大不能恢复作者细节。每个未处理绘制项保留源身份和原因，明确区分真正不绘制、目标区域外、可证明无影响的裁切与尚不支持的效果。

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

新版构建的 `diagnostic_coverage` 绑定本次 source-content、semantic 和 text-fit 三份回执，以及源图、清单、解析场景和最终 PPT。文字度量没有 live text 时为 `NOT_APPLICABLE`；有 live text 时也只覆盖列出的实际测量对象。字形路径与图片中的文字不因此通过文字检查。semantic 的 `RECORDED` 仅表示已记录声明的公式/连接数据，未声明时为 `NOT_PROVIDED`，两者都不是独立语义识别。审查核对回执内容、对象数和身份；旧版本没有这项证据时不补造覆盖声明。

文字回执还须包含完整测量记录，且与实际源文字、框或锚点、inset、行数、基线、行距及内容框算术一致。自动换行必须保留源字符和每个硬换行，只允许 writer 在软换行处去掉行尾空白；验证有明确的工作预算，超限拒绝。非有限数字、布尔冒充数值、重复 JSON 字段、已记录的溢出和超出画布的框均不能通过。溢出与画布检查沿用 writer 的 0.001 源像素容差，确定性布局算术没有另加容差。

上述校验只证明记录内部一致，并未重新加载字体测量字宽或推断图中文字。自洽但伪造的字宽仍可能通过，实际字体和预览证据仍须单独检查；它不能关闭自动语义覆盖或视觉问题。
