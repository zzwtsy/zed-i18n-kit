# 工作项：阶段 0 Zed UI 文本金标语料

Status: completed

## 问题

阶段 1 的只读扫描器需要可重复、可量化的评估输入。当前设计只规定“从代表性 crate 手工标注 200～300 个样本”，尚未固定样本版本、标签语义、源码锚点、覆盖配额和机器校验，因此无法可靠计算 precision、recall 或定位规则退化。

## 目标

针对 Zed commit `2551721adb5b5187bc27cfae0fbe47f0ed4c5397` 建立 250 条人工审核样本。每条样本能够回溯到源码，明确 UI sink、表达式特征、文本所有权、源码作用域与期望决策，并可由统一命令验证格式、锚点和覆盖配额。

## 范围

- 包含：生产 UI、Toast、Prompt、辅助功能文本、局部数据流、格式化表达式、动态外部内容及 ID/日志/路径反例。
- 包含：用于验证默认排除规则的 tests、GPUI examples 和 component preview 样本。
- 影响资产：`corpus/`、`schemas/`、金标模型与校验器、测试、阶段 0 调研与使用文档。

## 非目标

- 不实现 Tree-sitter 或阶段 1 扫描器。
- 不把金标 schema 当作未来 inventory schema。
- 不修改 `local/zed`，不验证运行时实际可见性。
- 不为样本分配 Message ID 或生成翻译目录。

## 已确认事实与假设

- 已确认事实：`local/zed` 基线 commit 为 `2551721adb5b5187bc27cfae0fbe47f0ed4c5397`，调查时工作树干净。
- 已确认事实：代表 crate 中相关表达式有数千处，不能按命中数量简单比例抽样。
- 已确认事实：tests、examples 和 component preview 中存在与生产代码相同的 UI API，需要将源码作用域作为独立标签。
- 已确认事实：静态源码只能标注预期语义，不能单独证明文本在运行时可见。
- 待验证假设：250 条分层样本足以暴露阶段 1 第一批 rule packs 的主要误报和漏报模式。

## 验收条件

- [x] 固定且校验 Zed 完整 commit，输入 checkout 保持只读。
- [x] 金标集恰好包含 250 个唯一样本，并通过严格运行时 schema 校验。
- [x] 每条样本的源码路径、行号和 anchor 可在固定 commit 中验证。
- [x] `confirmed`、`review_required`、`excluded` 均有代表样本。
- [x] 覆盖设计文档要求的直接字面量、builder、局部变量、`match`、`format!`、动态内容、ID/日志/路径反例、Toast、Prompt、ARIA、测试与 preview。
- [x] 统计结果满足 manifest 中声明的最小覆盖配额。
- [x] 聚焦测试和 `uv run python scripts/check.py` 通过。
- [x] 调研方法、标签边界、已知限制和验证证据进入仓库文档。

## 实施步骤

1. 固化金标 schema、manifest、标签词汇和配额。
2. 实现不依赖第三方库的严格 JSONL 解析与源码锚点校验。
3. 从代表性 Zed 文件收集并逐条审核 250 个样本。
4. 增加 schema、配额、锚点和确定性测试。
5. 运行完整门禁，记录证据并将工作项移入 `completed/`。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `git -C local/zed rev-parse HEAD` | passed | 调查基线为固定 commit | 不证明未来 checkout 未变化 |
| `git -C local/zed status --short` | passed | 调查期间外部输入干净 | 不证明运行时 UI 覆盖 |
| `uv run pytest -q tests/test_golden_corpus.py` | passed，4 tests | 金标解析、摘要、防漂移和 checkout anchor 失败保护 | 不证明运行时 UI 可见性 |
| `uv run python scripts/check_golden_corpus.py --zed local/zed` | passed，250 samples | SHA、配额、固定 commit、33 个文件的路径/行号/anchor | 不证明宏展开或运行时可见性 |
| `gpt-5.6-luna`、reasoning effort `max` 只读复核 | passed，findings 已修正 | 逐条检查 250 条的 sink、scope、decision 和覆盖偏斜 | 仍是 AI 辅助审核，不是 maintainer 双人审核 |
| `uv run python scripts/check.py` | passed，7 tests | Ruff format、Ruff lint、ty 与仓库测试 | 不证明 Zed build 或 UI 可见性 |
| `git diff --check` | passed | 当前变更没有空白错误 | 不证明语义正确性 |

## 风险与阻塞

- 风险：人工标签可能存在分歧；通过显式 rationale 和后续双人复核降低风险。
- 风险：行号在 Zed 升级后失效；本语料固定 commit，新版本建立新快照或迁移，不静默重用。
- 风险：大文件样本过度代表；通过 crate、sink、特征和作用域配额限制偏斜。
- 阻塞条件：固定 commit 不可用或样本无法唯一定位。
- 恢复方式：恢复精确 checkout，或显式建立新 corpus version 并重新审核。

## 相关决策

- ADR：[0002 阶段 0 金标语料格式](../../decisions/0002-phase-0-golden-corpus-format.md)
