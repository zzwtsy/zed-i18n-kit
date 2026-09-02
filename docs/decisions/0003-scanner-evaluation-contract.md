# ADR 0003：扫描器评估单元与文本槽位

Status: accepted

## 问题

阶段 0 金标语料 v1 固定了源码 anchor、sink、所有权和期望决策，但尚未定义扫描结果如何与样本一一对应。实际样本既有 sink 调用，也有 `let`、`if` 和 `match` 的表达式来源；未来扫描器则会输出主要 byte range 和数据流 provenance。仅按路径、行号或英文文本匹配会把位置差异误计为语义错误，也无法稳定计算 precision 和 recall。

同时，单个整数 `sink_argument` 不能完整表达真实 API。`Window::prompt` 的 message、detail 和 answers 都可能包含产品文本；`Toast::new` 同时存在 identity 和 message；builder 还可能通过后续方法增加 action label。rule pack 和金标必须使用同一种“文本槽位”概念，但不能因此让金标复制扫描器 inventory schema。

## 决定

### 1. 冻结 v1，在阶段 0.5 建立评估契约

`corpus/zed-ui-text/v1` 继续作为已完成阶段 0 的可审计快照，不静默增加字段或改变标签含义。阶段 1 开始前建立新的 corpus schema version 或显式迁移，并至少表达：

- `subject_kind`：样本评估的是 sink slot、expression origin 还是 scope exclusion；
- 精确 source span：固定 commit 下的 UTF-8 byte range，并保留 anchor 供人工审查；
- sink symbol 和 text slot：符号身份与文本位置分开保存；
- `expected_presence`：使用 `candidate` 或 `not_candidate` 表示扫描器是否应发现该候选；
- `expected_disposition`：`confirmed`、`review_required` 或 `excluded`；
- review state：区分单次审核、独立复核和存在分歧的样本。

具体字段名和序列化细节在阶段 0.5 工作项中确定，但上述语义边界属于兼容性约束。

### 2. 以文本槽位建模 sink

一个 sink 可以声明零个或多个文本槽位。槽位使用可解释的 value path 指向参数或嵌套值，例如：

```text
ui::Button::new
  arg[0]       identity
  arg[1]       visible_text

gpui::Window::prompt
  arg[1]       prompt_message
  arg[2].Some  prompt_detail
  arg[3][*]    prompt_action
```

value path 是概念模型，不在本 ADR 中冻结 TOML 或 JSON 语法。每个文本槽位独立产生 occurrence、证据和处置结果；同一调用中的 ID、消息、详情和按钮不得合并成一个不可区分的候选。

### 3. 分离语义存在、工作流处置和分析证据

- `expected_presence` 用于发现率评估；
- `expected_disposition` 用于自动确认安全性和人工审核队列评估；
- confidence/evidence 描述扫描器为什么得出结论，不创建第四种持久处置状态。

MVP 对外只输出 `confirmed`、`review_required` 和 `excluded`。内部若使用 `probable` 证据等级，序列化时必须映射为 `review_required`，避免两个都需要人工审核的状态长期并存。

`candidate` 只能与 `confirmed` 或 `review_required` 组合，`not_candidate` 只能与 `excluded` 组合；违反该不变量的样本必须由 schema 或运行时校验拒绝。

### 4. 使用精确位置和 provenance 匹配

评估器优先以 Zed commit、source path 和精确 byte range 匹配样本。对于 expression origin 样本，允许匹配 occurrence 的结构化 provenance range；不得按英文文本、模糊行号或最相似上下文自动配对。

评估输入必须对应 manifest 指定 commit，并满足以下二选一条件：

- checkout 在相关路径上无本地修改；
- 扫描快照记录并验证相关文件内容摘要。

匹配不唯一、provenance 缺失或 span 失效时计入明确的 unmatched/invalid 类别，不静默选择候选。

### 5. 固定指标含义

核心指标定义为：

- auto-confirm precision：预测为 `confirmed` 的样本中，金标允许自动确认的比例；
- candidate recall：`expected_presence=candidate` 的样本中，被预测为 `confirmed` 或 `review_required` 的比例；
- unsafe promotion rate：金标要求审核但被预测为 `confirmed` 的比例；
- exclusion leakage：金标应排除但被预测为候选的比例；
- unmatched count：无法按精确位置或 provenance 对齐的样本数。

设计文档中的 99% precision 和 95% recall 只是固定 corpus 上的回归门禁，不能表述为完整 Zed workspace 的统计准确率。扫描器规则冻结后，还要从未用于规则调优的新路径和高风险结构中抽样，独立审计误报与漏报；审计结果与 corpus 回归指标分别报告。

### 6. 分离扫描结果与持久 inventory

阶段 1 输出确定性、版本化的 `scan-result`，用于评估和人工检查，但不承诺 Message ID 或跨版本审核状态。阶段 2 才把审核决定、稳定 Message ID 和版本对账状态写入持久 inventory。

## 备选方案

- 继续使用 `(path, line, anchor)` 对齐：当前样本可读，但无法稳定处理多行表达式、同一行多个候选和 origin/sink 分离。
- 按英文文本匹配：实现简单，但会混淆同文异义、重复标签、ID 和日志，违背 Message 与 Occurrence 分离原则。
- 每个 API 只配置一个文本参数：适用于 Label/Button 的简单案例，但会漏掉 Prompt detail、actions 和 builder 后续文本。
- 将 `probable` 保留为第四个公开处置状态：无法提供不同于 `review_required` 的工作流行为，只会扩大状态空间。
- 直接修改 corpus v1：短期文件更少，但破坏已接受格式和阶段 0 的可审计基线。

## 后果

- 阶段 1 在实现规则前必须先完成一个小型评估器和 corpus v2/迁移工作，增加一次性成本。
- rule pack 能表达多文本参数和嵌套集合，不再把整个调用粗略标成一个候选。
- precision、recall 和 review 安全性有稳定、可复现的计算口径。
- corpus 仍与 inventory 解耦，但二者共享 occurrence、text slot 和 provenance 的语义定义。
- v1 继续可读和可验证；新的阻塞门禁只应用于明确声明的 corpus schema version。

## 验证

- 使用同一调用中同时包含 ID 和 label 的 Button 样本验证 slot 区分。
- 使用 `Window::prompt` 的 message、detail 和 answers 验证多槽位与集合路径。
- 使用局部变量、`if`、`match` 和 `format!` 样本验证 origin range 能与 occurrence provenance 对齐。
- 为每个指标提供手工可计算的小型混淆矩阵测试。
- checkout dirty、span 失效、重复匹配和 provenance 缺失必须产生失败或显式 unmatched 结果。
- corpus schema 与运行时模型之间必须存在自动漂移检查。

## 重新评估条件

- Tree-sitter 无法稳定提供所需 byte range 或 provenance，且金标证明替代定位更可靠。
- rust-analyzer/HIR 后端成为默认语义来源并提供不同的稳定身份模型。
- Zed 上游提供正式 i18n API，使文本槽位和消息身份能够由类型系统直接表达。
- 独立抽样审计证明现有 corpus 分层与真实误报、漏报模式显著偏离。

## 后续关系

[ADR 0004](0004-direct-v2-cutover.md) 取消了本 ADR 中“继续读取和验证 v1”的兼容后果；评估单元、文本槽位、匹配和指标决定继续有效。
