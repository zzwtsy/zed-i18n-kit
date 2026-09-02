# 测试策略

## 1. 目标

测试必须证明声明的行为，而不仅是提高覆盖率。每项证据都要说明适用范围；绿色单元测试不能替代真实 Zed build、runtime trace 或 UI smoke。

## 2. 证据层级

| 层级 | 证明内容 | 不能单独证明 |
| --- | --- | --- |
| 静态检查 | 格式、lint、类型和结构约束 | 运行时行为 |
| 单元测试 | 纯函数、模型和局部边界 | 模块集成与真实源码兼容 |
| Golden/fixture | 对固定 Rust 输入的解析、分类和输出 | 未收录的 Zed 结构 |
| 集成测试 | 多模块协议、文件 I/O 和 CLI 生命周期 | 真实完整 Zed workspace |
| Zed build | 当前 commit 能编译并通过目标测试 | UI 可见性和布局 |
| Runtime/UI | 文本实际渲染、覆盖、交互和布局 | 未执行的其他路径 |

最终交付使用达到风险所需的最低充分证据，不默认运行所有昂贵检查。

## 3. 基础门禁

所有 Python 代码和工程配置变更运行：

```bash
uv run python scripts/check.py
```

仅文档修改至少运行：

```bash
git diff --check
```

并检查新增或修改的相对链接。基础 CI 当前在 Ubuntu 上运行统一门禁；跨平台矩阵在路径处理和 Overlay 能力进入实现时再增加。

## 4. 领域测试要求

### 4.1 Parser

- Rust 字面量、宏、方法调用、`let`、`if` 和 `match` fixtures；
- 不完整源码的错误恢复；
- UTF-8 字符之前和之中的 byte range；
- 注释、raw string、转义、多行输入和同一行多个候选；
- 内嵌 `#[cfg(test)]`、component preview 和生成边界的作用域识别；
- 解析失败必须显式报告，不能返回空 inventory 冒充成功。

### 4.2 Domain rule pack

每条自动确认规则至少包含：

- 明确正例；
- 名称相似但不是 UI 文本的反例；
- 动态值或所有权不明的 review 例；
- 受支持版本能力探测；
- 命中规则和证据的稳定输出。

一个 sink 存在多个文本槽位时，每个槽位分别测试。至少覆盖：

- Button 的 identity 与 visible label；
- Prompt 的 message、optional detail 和 answers 集合；
- Toast message 与后续 action label；
- value path 不匹配时只禁用受影响槽位，不误用相邻参数。

金标集分别统计：

- auto-confirm precision：预测为 `confirmed` 的样本中，金标允许自动确认的比例；
- auto-confirm coverage：`review_state=independently_reviewed`、`expected_presence=candidate` 且 `expected_disposition=confirmed` 的样本中，被预测为 `confirmed` 的比例；
- candidate recall：应发现的样本中，被输出为 `confirmed` 或 `review_required` 的比例；
- unsafe promotion rate：金标要求审核但被预测为 `confirmed` 的比例；
- exclusion leakage：金标应排除但被输出为候选的比例；
- unmatched sample：应发现但无法按精确 primary/provenance span 对齐的样本数量；
- ambiguous sample：被多个 occurrence 命中的样本数量；
- unlabeled occurrence：扫描结果中没有 corpus 对应项的发现数量，仅进入独立审计，不计入 corpus precision。

指标必须固定样本版本、Zed commit、扫描配置和计算脚本。auto-confirm precision 与 coverage 必须同时设门禁；任一分母为零时结果是 undefined，不能视为通过。`single_review` 样本只提供 observational 指标，阻塞门禁只消费满足分层覆盖要求的 `independently_reviewed` 子集，并显式报告样本量。`disputed` 样本不用于证明自动确认安全性。

corpus 阈值是回归门禁，不得表述为完整 Zed workspace 的统计准确率。规则冻结后，从未参与调优的新路径和高风险结构中抽样，独立审计误报与漏报；corpus 回归、未标注 occurrence 审计和独立抽样结果分别报告。

评估输入必须满足以下要求：

- 相关 Zed 路径工作树干净，或 scan snapshot 记录并验证文件内容摘要；
- `scan-result-v1` 记录 Zed commit、工具版本、rule pack 版本、配置 hash、capability probe、扫描范围和相关文件 SHA-256；
- primary span 是文本槽位取值的最小完整 Rust 表达式；anchor 和 enclosing call 只用于审计；
- expression origin 只能通过结构化 provenance 匹配；非空 sink/slot 是严格约束，`null` 表示该维度不参与匹配；
- 重复匹配、span 失效、provenance 缺失和 snapshot 漂移产生明确 ambiguous、unmatched 或 invalid 结果；
- corpus 外 occurrence 进入 unlabeled 审计队列，不按英文文本模糊关联，也不因缺少金标自动判错；
- 同一输入、工具、规则和配置重复扫描产生 byte-for-byte 相同的序列化结果；
- corpus schema 与 Python 运行时模型存在自动漂移检查。

### 4.3 Inventory 与持久 schema

- schema 正反 fixtures；
- 未知字段、缺失字段和非法状态；
- 旧版本读取或明确拒绝策略；
- Message ID 与 Occurrence 身份分离；
- 序列化结果确定性和隐私字段检查。
- 阶段 1 `scan-result` 必须携带可验证 snapshot 和规则证据，但不携带已冻结 Message ID 或跨版本审核状态；阶段 2 persistent inventory 才建立这些承诺。

### 4.4 Rewrite

- byte-range edits 正确且不重叠；
- UTF-8 byte offset 与换行保持；
- 源文件 hash 和 AST fingerprint 失败保护；
- 第二次运行零变化；
- 低置信候选不被修改；
- 原版输入 checkout 内容和 Git 状态保持不变。

每个重要保护都应有负面控制：移除或绕过保护时，测试必须能够失败。

### 4.5 版本对账

使用至少两个真实、精确的 Zed commit fixtures 验证：

- `unchanged`
- `moved`
- `source_changed`
- `added`
- `removed`
- `sink_changed`
- `ambiguous`

一次消失只产生 `possibly_obsolete`；不唯一匹配必须进入人工审核。

### 4.6 Runtime template 与 Overlay

- 派生工作区和输入 checkout 路径不同；
- 同一输入、规则、迁移计划和目录生成等价文件树；
- `cargo fmt --check`、目标 crate `cargo check` 与相关测试；
- runtime 变化记录 binary size、内存和代表性 frame time；
- pseudolocale 检查漏翻、文本扩张、截断和辅助功能标签；
- runtime trace 不持久化用户、协议或第三方原始正文。

## 5. 测试设计

- 测试名称描述行为和条件，不写“works”或“correct”。
- 优先比较完整领域对象或稳定序列化输出。
- fixtures 记录来源 commit 和最小化理由。
- 不依赖固定本机路径、端口、时区或未声明环境变量。
- 不使用无界 sleep 等待异步状态；等待可观察条件并设置总超时。
- flaky 测试先分类根因，不通过盲目重试或放宽断言掩盖。
- 修改已有行为时同步修改或删除已过时测试，并在工作项说明原因。

## 6. CI 演进

### 当前阶段

- Ruff format；
- Ruff lint；
- ty；
- pytest；
- corpus v2 schema 与运行时模型漂移检查；
- 生成后 clean-worktree 检查；
- 在固定 Zed checkout 可用时执行 corpus 源码摘要/span 校验。
- `scan-result-v1` 严格 schema、确定性 I/O、snapshot 与 UTF-8 span 负面控制；
- 生产 Rust discovery 的包含、排除、越界符号链接和确定性回归；
- 合成 Rust CST/typed scanner fixtures，包括作用域化 import/alias、`cfg(test)`、通配符降级、相似名称反例、receiver、Tooltip 直接/动态/同名 impostor、`documentation_aside` 命令选项上下文和函数内 provenance；
- 在固定 Zed checkout 可用时执行 `scripts/check_scan_evaluation_contract.py` 的 16 样本闭环。
- blind review/audit bundle 的字段隐私、确定性、UTF-8 context 与身份绑定；
- review/audit result 的未知字段、重复/未知 ID、非法状态、身份漂移和不完整结果负面控制；
- rule freeze policy 的最小分母、分层、identity、capability、unsafe promotion 和零独立审核 fail-closed 控制。

阶段 1C-A 的 holdout audit 使用 `expected_disposition`（`confirmed`、`review_required`、`excluded`、`indeterminate`）和独立的 `corpus_gap` 布尔字段；`corpus_gap` 只标记现有 corpus 未覆盖的新语义类别、风险结构或标签体系缺口，不因一个合法 occurrence 未被逐条收录而置位。bundle 与 result 都必须绑定 `audit_set_id`、Zed commit、corpus SHA-256、scan config、tool version 和 rule-pack version。bundle 不得包含 scanner disposition、rule ID、旧标签或预测。`audit-check` 的 structural completeness（未知/重复/missing occurrence ID）与 gate acceptability 分离：只有完整且无 indeterminate、corpus gap、unsafe promotion 或 candidate/excluded mismatch 才返回 0。`freeze-check` 必须同时消费 corpus review 与 workspace holdout audit；两者身份先核验，再按 occurrence ID 对账。

### 扫描器阶段

- 精确 checkout、canonical primary span、provenance 与 snapshot 匹配；
- auto-confirm precision、coverage、candidate recall、unsafe promotion、exclusion leakage 和 unmatched/ambiguous 门禁；
- unlabeled occurrence 审计报告；
- 独立抽样审计报告；
- 安装 wheel 后的 CLI smoke。

### Overlay 阶段

- pinned Zed commit 集成；
- Linux/macOS/Windows 路径和换行矩阵；
- 派生 workspace Rust 检查；
- 性能基线和 runtime/UI 专用 lane。

昂贵或依赖网络、真实 API、GUI 的验证使用独立 lane，并明确 required、observational 或 manual，不能把跳过写成通过。
