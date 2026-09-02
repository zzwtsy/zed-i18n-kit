# 工作项：阶段 0.5 评估协议与风险样本校准

Status: completed

## 问题

阶段 0 的 250 条 v1 样本能够在固定 Zed commit 上稳定定位，但其单行 anchor、合并后的 sink 参数和单一 decision 不能表达扫描结果的精确匹配与评分。若直接实施扫描器，Prompt 多文本槽位、expression provenance 和排除项会得到含义不稳定的 precision/recall。

## 目标

建立唯一的 corpus v2、真实高风险样本和小型评估器，使阶段 1 能用 exact span/provenance 对齐扫描结果，并按固定口径计算安全性与发现率；项目未发布，因此不保留 v1 兼容层。

## 范围

- 包含：v2 schema 与强类型运行时模型、初始样本的一次性转换、相关源码摘要校验、风险样本、scan-result 匹配和指标计算。
- 影响资产：`schemas/`、`corpus/zed-ui-text/v2/`、`src/zed_i18n_kit/`、`scripts/`、`tests/` 和阶段说明文档。

## 非目标

- 不实现 Tree-sitter 扫描器、rule pack、持久 inventory、Message ID 或源码改写。
- 不修改 `local/zed`，不把 corpus 指标表述为整个 Zed workspace 的统计准确率。
- 不为阶段 1 尚未产出的完整 scan-result 文件格式提供长期兼容承诺。

## 已确认事实与假设

- 已确认事实：v1 的 250/250 条样本可由文件、行号和 anchor 唯一转换为文件级 UTF-8 byte range。
- 已确认事实：当前 v1 没有 concatenation、user 或 protocol 样本，`.child()` 仅有一条，Prompt 样本没有按文本槽位拆分。
- 已确认事实：固定 Zed commit 中存在 Prompt 多槽位、`concat!`、`push_str`、跨函数 helper、内嵌 `#[cfg(test)]`、错误链以及 user/protocol 来源的真实样本。
- 已验证事实：以相关文件 SHA-256 固定源码快照，可以不依赖整个 checkout clean，并会拒绝评估路径内容漂移。

## 验收条件

- [x] v1 corpus、schema、运行时模型和兼容测试已删除。
- [x] v2 严格表达 subject kind、UTF-8 byte span、text slot、expected presence/disposition 和 review state。
- [x] 250 条初始样本均已转换为精确 byte span，并与 16 条风险样本共同进入唯一 v2 corpus。
- [x] v2 包含设计文档列出的高风险正例、反例或 review-required 样本。
- [x] checkout 校验拒绝 commit、相关文件摘要、span 或 anchor 漂移。
- [x] exact primary span 和 provenance span 可匹配；重复匹配与缺失 provenance 显式计入 unmatched。
- [x] 五个核心指标都有手工可计算的混淆矩阵测试。
- [x] schema 与运行时模型具有自动漂移检查。
- [x] 完整本地门禁通过，`local/zed` 保持 clean。

## 实施步骤

1. 固化 v2 schema、运行时不变量和一次性转换规则。
2. 转换 250 条初始样本并补充真实风险样本。
3. 实现 scan-result exact span/provenance 匹配和指标计算。
4. 补充失败路径、手算矩阵和 schema drift 测试。
5. 同步权威文档，运行完整门禁并完成阶段审视。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| v1 anchor 唯一性检查 | passed，250/250 | 固定 commit 上所有 v1 anchor 可确定性迁移为 byte span | 不证明 span 语义粒度正确 |
| `uv run python scripts/check_golden_corpus.py --zed local/zed` | passed，266 samples / 40 files | v2 schema、不变量、覆盖配额、commit、相关文件摘要和 anchor span 有效 | 不执行 Rust AST 分析或 UI runtime |
| `uv run pytest -q tests/test_golden_corpus.py tests/test_evaluation.py` | passed，11 tests | 唯一 v2 模型、拒绝 v1、schema drift、checkout/span 防护、exact provenance、正确缺席、重复匹配和手算指标 | 使用小型 scan-result fixture，不是实际扫描器输出 |
| `uv run python scripts/check.py` | passed，14 tests | Ruff format/lint、ty 和完整 pytest 门禁 | 不证明完整 Zed workspace 的统计准确率 |
| `git diff --check` | passed | 代码、schema、corpus 和文档无空白错误 | 不证明语义标签正确 |
| `git -C local/zed status --short` | passed，clean | 外部 checkout 未被修改 | 不证明 corpus 标签正确 |

## 风险与阻塞

- 风险：一次性转换后的窄 anchor span 只保留初始标注意图，不能替代新增风险样本对复杂数据流的校准。
- 风险：评分器先于真实扫描器实现，阶段 1 可能暴露需要新增字段的情况；v2 因此只承诺评估语义，不承诺持久 inventory。
- 阻塞条件：固定 Zed checkout 不在 manifest commit，或相关源码与记录摘要不一致。
- 恢复方式：恢复指定 commit 的只读 checkout，或显式建立新的 corpus version。

## 相关决策

- ADR：[0002 阶段 0 金标语料格式](../../decisions/0002-phase-0-golden-corpus-format.md)
- ADR：[0003 扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
- ADR：[0004 直接切换 corpus v2](../../decisions/0004-direct-v2-cutover.md)
