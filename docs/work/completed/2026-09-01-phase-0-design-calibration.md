# 工作项：阶段 0 后设计校准

Status: completed

## 问题

阶段 0 已建立 250 条固定 Zed commit 的 UI 文本参考样本。复盘发现，原设计尚未定义扫描结果与语料之间的精确匹配和评分协议；简单的单参数 sink 模型也不足以表达 Prompt、Toast 等包含多个文本位置的 API。如果直接进入扫描器实现，precision 和 recall 会缺少稳定含义，rule pack 还可能过早固化错误边界。

## 目标

根据阶段 0 的实际证据修订项目权威文档，明确阶段 0.5、评估单元、文本槽位、分类处置、指标适用范围以及阶段 1 与阶段 2 的产物边界。

## 范围

- 新增扫描器评估契约 ADR。
- 修订总体架构、实施阶段和验收标准。
- 同步测试策略、README 与阶段 0 调研的后续结论。
- 记录本次文档校准的验证证据。

## 非目标

- 不修改金标语料 v1、schema、manifest 或校验器。
- 不决定 rule pack 持久文件的最终语法。
- 不实现 Tree-sitter、扫描器、评分器或语料 v2 迁移。
- 不修改 `local/zed`。

## 验收条件

- [x] 文档明确语料 v1 保持冻结，结构变化通过新 schema version 或显式迁移完成。
- [x] 文档定义扫描结果的评估单元、匹配原则和核心指标。
- [x] sink 设计支持一个 API 的多个及嵌套文本槽位。
- [x] 阶段 0.5 成为阶段 1 的前置步骤，阶段 1 扫描结果与阶段 2 持久 inventory 明确分离。
- [x] 测试策略包含 corpus 回归指标、独立抽样审计、checkout 精确性和 schema 漂移检查。
- [x] README 与调研文档反映阶段 0 已完成及下一步边界。
- [x] `git diff --check` 和文档相对链接检查通过。

## 关键决定

- corpus v1 保持冻结，阶段 0.5 通过新 schema version 或显式迁移扩展评估语义。
- rule pack 使用多个 text slot/value path 表达复杂 API，不把整个 Prompt 或 Toast 调用视为一个候选。
- `expected_presence`、`expected_disposition` 和 confidence/evidence 分层；MVP 不公开与 `review_required` 重复的 `probable` 状态。
- 阶段 1 产物命名为 `scan-result`，持久 inventory 从阶段 2 开始建立兼容承诺。
- corpus 指标只作为固定回归门禁，完整 Zed 效果由规则冻结后的独立抽样审计补充。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `git diff --check` | passed | 本次 Markdown 修改没有空白错误 | 不证明语义决定正确 |
| 本地 Python 相对链接检查，覆盖本次修改的 7 个 Markdown 文件 | passed，21 links | 所有本地相对链接目标存在 | 不检查远程 URL 和标题 anchor |
| `rg -n "稳定 JSONL inventory\|probable\|sink_argument\|argument =\|阶段 0.5\|scan-result\|ADR 0003\|0003-scanner" README.md docs` | passed | 旧术语已被定位，保留的 `probable` 和 `sink_argument` 只用于迁移说明 | 不替代人工语义复核 |
| `git -C local/zed status --short` | passed，clean | 文档更新未修改外部 Zed checkout | 不证明未来 Overlay 行为 |

## 未运行检查

未运行 `uv run python scripts/check.py`。本次只修改 Markdown，没有 Python、schema、语料或生成物变化；按照测试策略，文档 diff 和相对链接检查是最低充分证据。

## 相关决策

- [ADR 0002：阶段 0 金标语料格式](../../decisions/0002-phase-0-golden-corpus-format.md)
- [ADR 0003：扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
