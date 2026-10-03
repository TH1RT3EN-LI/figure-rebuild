# 贡献指南

修改前先阅读 [架构](docs/architecture.md) 和相关 [场景协议](references/scene.md)。提交应说明触发问题的输入、修改后的行为、验证结果及仍需外部环境确认的部分。

## 准备源码环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m figure_rebuild --help
```

按修改范围安装可选依赖，并使用同一个 Python 环境运行测试：

```bash
.venv/bin/python -m pip install -e '.[vision,source,dev]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_*.mjs
```

Python 测试覆盖清单、审阅、素材、几何、PPT 包操作、安装和配置等便携逻辑。Node 测试覆盖曲线、文字计算和独立 Python 运行时调用。修改涉及某个可选模块时，须运行相应依赖环境下的测试；核心环境中的可选测试跳过不能视为该功能通过。

PPT 构建集成另需外部 Artifact Tool、Presentations 检查器和合法提供的字体。先运行 `doctor`，再用自备输入构建 PPT 并检查实际预览、编辑性和保留性报告。CI 核心检查通过时，报告核心检查结果；只有实际完成的导出、预览或 Office 播放验证才可列为已验证。

## 提供最小可分发复现

优先使用自行绘制的 SVG、合成图和最少对象的清单。复现应附以下信息：

- 输入尺寸、来源分类与必要的哈希；有权分发的输入和最小清单。
- 原本应保留的文字、孔洞、绘制顺序或连接关系，以及实际失败表现。
- CLI 命令、Python / Node 版本、所安装的可选依赖和完整错误信息。
- 涉及局部修改时的稳定对象 ID、基础 revision、内容摘要与影响范围。
- 涉及导出时的必要预览和审计片段；分享前移除个人路径与敏感内容。

不能分发原图时，用合成素材复现同一问题，保留坐标与关系特征。论文、字体、模板和用户素材的访问权限不自动包含再分发权限。

## 保留审计与交付约束

修改清单内容后递增版本并重新审阅，不直接篡改审阅摘要或已有构建快照。保留原始素材字节、来源记录、稳定 ID、公式源码与依赖哈希。变更审阅、素材冻结、PPT 合并或发布逻辑时，验证相应的失败路径和保留性要求。

新增行为、格式限制或依赖时同步更新 [使用指南](docs/usage.md)、相关 `references/` 说明及 [CHANGELOG](CHANGELOG.md)。只声明已验证的支持范围；保留可复核的来源和第三方许可，不能将依赖或素材统一声明为本项目原创。

修改包结构或入口后，还需检查实际分发文件：

```bash
.venv/bin/python -m build
.venv/bin/python scripts/check_distribution.py --wheel dist/*.whl --sdist dist/*.tar.gz
```

CI 会把 wheel 安装到新环境，并在仓库外运行测试，检查命令入口和随包分发的后端资源。源码包也必须保留技能入口、文档和测试素材许可，排除本地配置与任务输出。

## 排除本地与用户素材

任务目录、构建输出、运行时配置、字体、密钥和未获分发许可的用户输入不进入 Git。将本地任务放在 `.local/` 或仓库外，提交前检查 `git status` 和暂存 diff；`.gitignore` 无法代替素材权利与内容检查。

自建测试素材应随测试提交；第三方测试定义须附来源、修改说明及适用许可。公开展示素材应明确来源和保留的第三方内容，参见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
