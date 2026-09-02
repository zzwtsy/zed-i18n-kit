# 工作项：直接切换 corpus v2

Status: completed

## 问题

阶段 0.5 当前同时保留 v1/v2 数据、两套 Python 模型、双版本校验分支和一次性迁移脚本。项目尚未发布，也没有外部兼容承诺；继续维护双版本只会扩大阶段 1 的实现与测试边界。

## 目标

将 v2 设为唯一 corpus 和运行时契约，删除 v1 资产及兼容代码，使后续扫描器只面对一种领域模型。

## 范围

- 包含：删除 v1 corpus/schema/model/tests，收敛唯一 `golden` API，简化校验入口，同步 ADR 和当前文档。
- 影响资产：`corpus/`、`schemas/`、`src/zed_i18n_kit/`、`scripts/`、`tests/` 和相关文档。

## 非目标

- 不改变 v2 字段语义、266 条样本标签或评估指标。
- 不实现扫描器、inventory 或 Overlay。

## 已确认事实与假设

- 已确认事实：项目尚未发布，v1 没有外部调用方或兼容承诺。
- 已确认事实：v2 已覆盖全部 250 条基线样本和 16 条风险样本。
- 已确认事实：当前阶段 0.5 改动已在 Git index 中，实施必须保留用户暂存意图。

## 验收条件

- [x] 仓库不存在 v1 corpus、schema、运行时解析器或兼容测试。
- [x] 唯一 `golden` API 严格解析并校验 schema version 2。
- [x] corpus 校验命令不再包含版本分派。
- [x] 266 条样本、源码摘要、span 和评估指标测试保持通过。
- [x] 当前文档明确项目只支持 v2。
- [x] 完整本地门禁通过，`local/zed` 保持 clean。

## 实施步骤

1. 记录破坏性切换决定并删除 v1 资产。
2. 将 v2 类型和函数收敛为无版本后缀的唯一 API。
3. 简化脚本、测试和文档。
4. 运行完整门禁并检查 Git index/working tree 边界。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `uv run pytest -q tests/test_golden_corpus.py tests/test_evaluation.py` | passed，11 tests | 唯一 v2 模型、拒绝 schema v1、span/hash 和评分边界 | 使用 fixture，不是实际扫描器 |
| `uv run python scripts/check_golden_corpus.py --zed local/zed` | passed，266 samples / 40 files | 唯一 corpus 与固定 Zed checkout 完整对齐 | 不执行 Rust AST 或 UI runtime |
| `uv run python scripts/check.py` | passed，14 tests | Ruff format/lint、ty 和完整 pytest 门禁 | 不证明未来扫描器行为 |
| v1 代码与资产残留搜索 | passed，0 matches | 当前源码、脚本、测试、schema 和 corpus 无兼容实现 | 历史 ADR/完成工作项仍保留事实记录 |
| `git diff --check` | passed | 修改无空白错误 | 不证明领域语义正确 |
| `git -C local/zed status --short` | passed，clean | 外部 checkout 未被修改 | 不证明运行时 UI 覆盖 |

## 风险与阻塞

- 风险：任何尚未发现的 v1 本地消费者会立即失效；当前仓库和未发布状态证明该风险可接受。
- 阻塞条件：发现已发布包、外部数据或 CI 仍依赖 v1。
- 恢复方式：停止切换并重新定义明确的迁移窗口；不在主代码中保留隐式双版本。

## 相关决策

- ADR：[0004 直接切换 corpus v2](../../decisions/0004-direct-v2-cutover.md)
