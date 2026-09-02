# ADR 0005：阶段 1 先闭合扫描评估协议

Status: accepted

## 问题

阶段 0.5 已建立 corpus v2 和可执行评估器，但扫描器尚未接入真实 Rust CST。实施复盘发现，当前数据和评估实现仍不足以直接冻结完整阶段 1：

- 部分 `sink_slot` anchor 指向调用 wrapper 或变量使用位置，尚未由统一 CST 节点规则定义 primary span；
- 大部分 `expression_origin` 没有完整 sink/slot 信息，而当前评估器把 nullable 字段作为严格相等值；
- corpus 是分层样本，不是扫描范围的穷举标注，额外合法 occurrence 不能等同于扫描错误；
- 只要求 auto-confirm precision、candidate recall 和 unsafe promotion，允许扫描器把全部候选降为 `review_required` 而不产生自动确认价值；
- 266 条现有样本均为 `single_review`，不足以单独支撑规则冻结时的硬统计门禁；
- 当前 `ScanResult` 是内存模型，没有持久 schema、确定性序列化和足以证明源码快照身份的元数据。

这些问题不否定 corpus、Occurrence 与 persistent inventory 分层，也不要求重新设计整个工具链；它们说明扫描协议必须先由真实 CST 原型闭环，再扩大规则覆盖。

## 决定

### 1. 将阶段 1 拆成三个可独立验收的子阶段

- 阶段 1A：使用 10～20 个代表性真实样本闭合 Rust 源码、CST、Occurrence、持久 scan-result 和评分报告；
- 阶段 1B：在已验证协议上实现 discovery、作用域、第一批 typed builtin rules 和函数内有界数据流；
- 阶段 1C：完成独立复核、corpus 门禁和未标注发现审计，再冻结可自动确认的规则。

阶段 1A 不以规则覆盖率为目标。若代表样本不足以固定协议，应扩大原型样本，而不是同时铺开完整扫描器。

### 2. 固定 span 不变量，由原型决定具体 CST 映射

每个 occurrence 至少区分：

- `primary_span`：文本槽位实际取值对应的最小完整 Rust 表达式，不包含外层 sink 调用、参数分隔符或无关 wrapper；
- `provenance_span`：primary 表达式经局部变量、分支、格式化或拼接追踪到的结构化来源表达式；
- 审计上下文：用于人工阅读的 enclosing call、行列或 anchor，不参与精确身份匹配。

value path 可以穿过 `Option`、集合或组件 wrapper，但不能因此把整个 sink 调用作为 primary span。具体 Tree-sitter node 类型和 wrapper 归一化规则必须由阶段 1A fixture 验证后写入实现与测试。

### 3. nullable sink/slot 表示可选匹配约束

对 `expression_origin` 样本：

- 非空 `sink_symbol` 或 `text_slot` 是必须相等的约束；
- `null` 表示该维度不参与匹配，不表示 occurrence 对应字段必须为空；
- `text_slot` 非空时仍要求 `sink_symbol` 非空；
- 多个 occurrence 在现有约束下命中同一样本时产生 `ambiguous_sample`，不得自动选择。

`sink_slot` 样本继续要求完整 sink 和 text slot，并以 primary span 精确匹配。

### 4. 区分金标失败与 corpus 外发现

评估报告至少区分：

- `unmatched_sample`：应发现的 candidate 样本没有 occurrence；
- `ambiguous_sample`：一个样本对应多个 occurrence；
- `unlabeled_occurrence`：扫描器发现但 corpus 未穷举标注的 occurrence；
- `invalid_result`：schema、snapshot、span 或其他协议不变量无效。

四类结果都必须可审计，但只有 `unmatched_sample`、`ambiguous_sample` 和 `invalid_result` 能直接作为 corpus 协议阻塞项。`unlabeled_occurrence` 进入独立审计队列，不计入 corpus precision，也不伪装成已确认正确。

### 5. 精度与自动确认覆盖同时设门禁

保留 ADR 0003 的 auto-confirm precision、candidate recall、unsafe promotion 和 exclusion leakage，并增加 auto-confirm coverage：`review_state=independently_reviewed`、`expected_presence=candidate` 且 `expected_disposition=confirmed` 的样本中，被预测为 `confirmed` 的比例。

- precision 分母为零时结果是 undefined，不能视为通过；
- 自动确认规则必须同时满足 precision 和 coverage 下限；
- `single_review` 样本只产生 observational 指标；
- 阶段 1C 对 `independently_reviewed` 子集执行阻塞门禁，并单独报告样本量和分层覆盖；
- `disputed` 样本只能用于审核队列和争议分析，不能支撑自动确认规则。

具体阈值由阶段 1C 根据独立复核样本量确定；在此之前不得沿用 99%/95% 形成虚假的统计确定性。

### 6. 阶段 1 冻结持久 scan-result 协议

阶段 1A 建立 `scan-result-v1` JSON schema、严格解析和确定性序列化。每次扫描至少记录：

- Zed commit、工具版本、rule pack 版本和配置 hash；
- capability probe 结果及扫描范围；
- 参与扫描或评估的相关文件 SHA-256；
- occurrence、primary span、provenance、处置结果和可解释规则证据。

快照摘要与 corpus 不一致时拒绝评分。scan-result 仍不携带稳定 Message ID 或跨版本人工审核状态，这些承诺属于阶段 2 persistent inventory。

### 7. 先使用 typed builtin rules

阶段 1B 先用有明确 Python 类型和测试的内置规则验证领域边界，不提前冻结复杂外部 rule DSL。rule pack 声明 capability requirements 和明确的 tested commits，不声明任意两个 Git commit 之间的连续兼容区间。

只有 Tree-sitter 方案在金标和独立审计中出现不可通过保守降级解决的类型、宏或跨函数缺口时，才评估 rust-analyzer/HIR sidecar。

## 后果

- 阶段 1 增加一个小型协议原型切片，但降低后续大规模改动 span、schema 和指标的返工成本。
- 当前阶段 0.5 评估器继续作为原型地基；其严格 nullable 匹配、未配对 occurrence 和指标口径需要在阶段 1A 按本 ADR 调整。
- corpus v2 数据中不符合 canonical span 的样本需要显式校准；若字段结构不变，可以作为受审计的数据修正，不自动要求新 schema version。
- 阶段 1C 完成前，项目可以报告探索性指标，但不能声称扫描器达到自动确认质量门禁。
- Persistent inventory、Message ID、Overlay 和不维护长期 Zed fork 的边界保持不变。

## 验证

- 代表样本覆盖直接 literal、`format!`、局部变量、`if`/`match`、Prompt 嵌套槽位、`.child()` receiver、helper、拼接和内嵌 `#[cfg(test)]`；
- 同一输入和配置重复扫描产生 byte-for-byte 相同的 scan-result；
- 修改相关文件但保持相同 HEAD 时，snapshot 校验拒绝评分；
- null origin constraint 可以匹配带 sink/slot 的 occurrence，非空 constraint 仍严格过滤；
- corpus 外合法 occurrence 进入 `unlabeled_occurrence`，不增加 `unmatched_sample`；
- 全部输出 `review_required` 时 auto-confirm coverage 不通过；
- 硬门禁只消费 independently reviewed 子集，并报告零分母和样本不足。

## 重新评估条件

- Tree-sitter 无法为代表性 Rust 结构提供稳定、可解释的 primary/provenance span；
- 独立审计证明 nullable constraint 或 unlabeled occurrence 分类掩盖系统性错误；
- 自动确认业务价值不再是项目目标，只读发现和人工审核成为唯一产品形态；
- Zed 上游提供正式 i18n 类型或 metadata，使 sink、slot 和消息身份可由上游直接表达。

## 与既有决策的关系

本 ADR 细化 [ADR 0003](0003-scanner-evaluation-contract.md) 的匹配与指标语义，不推翻其 subject kind、文本槽位、处置状态和 scan-result/persistent inventory 分层。[ADR 0004](0004-direct-v2-cutover.md) 的唯一 v2 决定保持有效。
