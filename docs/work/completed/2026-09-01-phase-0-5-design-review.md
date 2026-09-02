# 工作项：阶段 0.5 后续设计复盘

Status: completed

## 问题

阶段 0.5 已建立唯一 corpus v2 和小型评估器，但实现复盘发现，真实扫描器尚未验证 canonical CST span、expression-origin 约束、未标注 occurrence 分类、自动确认覆盖率和独立复核门禁。若直接按原阶段 1 并行铺开 discovery、rule pack 和完整 scan-result，可能在实现中反复改变评估协议。

## 目标

在不修改阶段 0.5 实现的前提下，更新后续架构和测试契约：把阶段 1 拆成协议闭环原型、只读扫描器和规则冻结审计三个子阶段，并记录扫描结果、匹配类别和指标门禁的目标语义。

## 范围

- 包含：新增阶段 0.5 设计复盘 ADR，同步总体设计、测试策略和阶段路线。
- 影响资产：`docs/decisions/`、`docs/zed-gpui-i18n-design.md`、`docs/testing.md`、`docs/work/`。

## 非目标

- 不修改 corpus v2 样本、schema、Python 评估器或 CLI。
- 不接入 Tree-sitter，不冻结未经真实 CST 原型验证的最终 JSON 字段细节。
- 不引入 rust-analyzer sidecar 或复杂外部 rule DSL。

## 已确认事实与假设

- 已确认事实：当前评估器要求 occurrence 与样本的 `sink_symbol`、`text_slot` 严格相等。
- 已确认事实：corpus 是分层参考样本，不是扫描范围内全部 occurrence 的穷举标注。
- 已确认事实：当前 scan-result 只有内存模型，尚无持久 schema、确定性序列化和文件快照摘要。
- 已确认事实：当前 266 条样本均为 `single_review`。
- 假设：10～20 个覆盖主要 CST 结构的真实样本足以先验证 span 与匹配协议；若实践否定该假设，再扩大原型样本而不是直接铺开扫描器。

## 验收条件

- [x] ADR 明确 canonical primary/provenance span、optional constraint 和未标注 occurrence 的设计方向。
- [x] 阶段路线拆为 1A 协议闭环、1B 只读扫描器、1C 规则冻结与审计。
- [x] 测试策略同时约束 auto-confirm precision 与 coverage，并规定 review state 的门禁作用。
- [x] scan-result 目标契约记录工具、规则、配置、capability probe、扫描范围和文件摘要。
- [x] 文档不把当前内存评估器表述为已经满足上述目标契约。
- [x] `git diff --check` 和相对链接检查通过。

## 实施步骤

1. 记录阶段 0.5 实施后暴露的协议缺口和阶段 1 调整决定。
2. 同步总体架构、Occurrence/scan-result 边界和实施阶段。
3. 同步指标、评估分类和独立复核门禁。
4. 检查文档一致性与链接，将工作项移入 completed。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `git diff --check` | passed | 已跟踪文档修改无空白错误 | 不覆盖未跟踪的新 ADR/工作项 |
| `rg -n '[[:blank:]]+$' docs/decisions/0005-phase-1-evaluation-loop-first.md docs/work/completed/2026-09-01-phase-0-5-design-review.md` | passed，0 matches | 两个新文件无行尾空白 | 不检查 Markdown 语义 |
| 变更文档链接清单与 `test -f` 目标检查 | passed | 新增和修改的仓库内相对链接目标存在 | 不检查外部 GitHub URL 当前可用性 |
| `rg` 检索旧 unmatched、固定阈值、连续 commit range 和阶段 1 标题 | passed | 权威设计与测试策略已使用 ADR 0005 的新语义；历史 ADR 通过后续关系保留原决定上下文 | 不验证未来实现已符合新契约 |

## 风险与阻塞

- 风险：canonical span 的最终节点类型仍需 Tree-sitter 原型验证，文档只能冻结不变量，不能虚构具体 CST 实现。
- 阻塞条件：真实 Rust CST 无法为代表样本提供稳定且可解释的 byte range。
- 恢复方式：保留 primary/provenance 语义边界，扩大原型样本或评估更强语义后端，不按英文文本或模糊行号降级。

## 相关决策

- ADR：[0003 扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
- ADR：[0005 阶段 1 先闭合扫描评估协议](../../decisions/0005-phase-1-evaluation-loop-first.md)
