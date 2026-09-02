# ADR 0007：阶段 1C 独立审核与规则冻结协议

Status: accepted

## 问题

阶段 1B 的 corpus 标签、扫描规则和 observational metrics 已在同一开发过程中被反复查看和调优。直接把现有 `review_state` 改成 `independently_reviewed`，或把当前预测交给第二审核者确认，会产生标签泄漏，不能证明规则能够在未参与调优的证据上安全自动确认。

同时，单一比例不足以形成可执行门禁：零分母、小样本、风险结构缺失、unmatched/ambiguous 和大量未标注 occurrence 都可能在表面高 precision 下被掩盖。

## 决定

### 1. 独立审核使用派生的 blind bundle

工具从固定 corpus 和 Zed checkout 生成确定性 review bundle。bundle 只包含定位和阅读所需的事实：sample ID、路径、精确 span、anchor、源码上下文、scope、subject kind、sink 和 text slot；不得包含现有 expected presence/disposition、ownership、review state、rationale 或扫描预测。

bundle 绑定完整 Zed commit、corpus sample SHA-256 和 review set ID。审核结果只有在这些身份完全匹配时才能参与门禁。

### 2. 审核结果是独立输入，不自动覆写争议

review result 为每个样本记录 presence、disposition、ownership 和简短理由，并记录非空 reviewer ID。机器只验证格式、身份和语义不变量，不声称验证人的真实身份或组织独立性。

与 corpus 首次审核一致的记录可以支撑 `independently_reviewed`；不一致记录进入 disputed 队列，不自动覆盖原标签，也不参与自动确认安全性证明。缺失、重复和未知 ID 必须显式报告或拒绝。

### 3. 门禁同时验证样本、分母、分层和错误类别

规则冻结 policy 固定：

- 精确 tested Zed commit、tool/rule pack version、config hash 和 rule IDs；
- 必须通过及允许失败的 capability probes；
- independently reviewed 最小样本量和关键 strata；
- precision、coverage、recall、unsafe promotion、exclusion leakage 的阈值与最小分母；
- unmatched、ambiguous 和未裁决 disagreement 的上限。

任一比例分母为零或小于 policy 最小值时门禁失败。`single_review` 和 `disputed` 不参与阻塞指标。门禁通过只证明该固定 corpus、commit 和 rule pack 的回归表现，不外推为完整 workspace 的统计准确率。

首个 `zed-builtin-v1` policy 在独立结果产生前预注册门槛，避免看到审核结论后移动目标：至少 200 条 in-scope 一致样本；precision/coverage/recall 的最小分母分别为 100/100/150，阈值分别为 99%/50%/95%；unsafe promotion 与 exclusion leakage 的最小分母为 40/30，上限分别为 0%/1%。50% coverage 是防止“全部降级人工审核”的最低自动化价值线，不是完整 workspace 的准确率声明。disposition、ownership、subject kind、高风险 feature 和 9 条规则还必须满足 policy 文件中的明确最小覆盖；这些门槛的机器权威来源是随 wheel 发布的 `zed-builtin-v1.freeze-policy.json`。

### 4. 未标注 occurrence 单独盲审

unlabeled audit 与 corpus 回归分离。抽样在规则修正前固定，优先选择 corpus 未覆盖路径，并按 rule、disposition 和高风险证据确定性分层。审核结果分类为合法候选、规则误报、corpus gap 或无法判断；它们不回填 corpus precision 分母。

- `legitimate_candidate`：扫描发现成立，但 corpus 本来就不是 workspace 穷举清单，不要求把该项加入回归集；
- `false_positive`：该 occurrence 不应作为国际化候选，必须形成规则修复输入；
- `corpus_gap`：扫描发现成立，且其风险结构或规则覆盖应在冻结前补入 corpus；
- `indeterminate`：现有上下文不足，必须补调查，不能并入正确或错误计数。

### 5. 冻结状态 fail-closed

在 review result、分层覆盖或门禁任一项不足时，rule pack 状态只能是 `draft` 或 `reviewing`，不得标记 `frozen`。生成 bundle、报告失败和继续人工审核都是有效进展，但不能写成规则冻结成功。

## 备选方案

- 直接批量修改现有 review state：成本最低，但没有独立证据，拒绝采用。
- 审核者同时查看预测和旧 rationale：便于定位差异，但会诱导确认偏差，只适用于分歧裁决，不适用于首次独立审核。
- 仅设置 99% precision：可以通过降低自动确认覆盖逃避价值，也会在极小分母下产生误导，拒绝采用。
- 把 unlabeled occurrence 当作误报：corpus 不是穷举清单，会系统性惩罚合法新发现，拒绝采用。
- 立即接入 rust-analyzer：当前尚未有独立审计证明 Tree-sitter 缺口阻塞门禁，继续按 ADR 0005 的证据触发条件执行。

## 后果

- 新增 review bundle/result 的版本化 schema、CLI 和 gate policy。
- freeze report 自包含 corpus/tool/config/rule/probe/metric policy，并在执行时重新验证完整 scan snapshot。
- 独立审核成为外部事实输入，工具不会替审核者生成结论。
- 阶段 1C 可能以 gate failed 状态持续一段时间；这是对证据不足的正确表达。
- corpus v2 样本字段保持兼容；是否将一致结论物化为 `review_state=independently_reviewed` 由后续受控命令完成。

## 验证

- snapshot 测试证明 blind bundle 不含任何标签或预测字段。
- review result 正反 fixture 覆盖身份漂移、重复/未知 ID、非法组合和分歧。
- 手工可算的小型 reviewed 子集覆盖每个 gate 失败原因及通过案例。
- 真实 266 样本 bundle 重复生成逐字节一致。
- 当前零 independently reviewed baseline 必须失败关闭。

## 重新评估条件

- 独立审核流程由外部平台提供强身份、盲化和签名能力；
- corpus 样本结构升级，使 review provenance 成为样本内建字段；
- 规则冻结不再用于自动确认，只保留人工发现队列；
- 独立审计证明现有 corpus strata 与真实 workspace 风险显著偏离。
