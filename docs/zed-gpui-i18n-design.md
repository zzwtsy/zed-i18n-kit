# Zed/GPUI 国际化迁移与临时构建工具链设计

## 1. 文档状态

- 状态：经 Zed 上游调研、阶段 0/0.5 实施与评估协议复盘修订
- 调查对象：`local/zed`
- 调查基线：Zed commit `2551721adb5b`（2026-09-01）
- 工具仓库：`zed-i18n-kit`
- 本文范围：架构与实施边界，不包含代码实现
- 上游调研：[Zed 上游国际化调研](research/zed-upstream-i18n.md)
- 阶段 0 调研：[Zed 阶段 0 UI 文本金标语料调研](research/zed-phase-0-golden-corpus.md)
- 评估契约：[ADR 0003：扫描器评估单元与文本槽位](decisions/0003-scanner-evaluation-contract.md)
- 阶段 1 顺序：[ADR 0005：阶段 1 先闭合扫描评估协议](decisions/0005-phase-1-evaluation-loop-first.md)
- 交付约束：不长期维护 Zed fork；所有 Zed 源码修改均由本项目针对指定 commit 临时生成

## 2. 目标

本项目是一个针对原版 Zed 源码进行国际化分析、版本差异对账和临时构建改写的工具链。它不扫描所有 Rust 字符串并机械替换，也不长期维护修改后的 Zed fork：

1. 从 Rust 源码中识别最终进入可见 UI、辅助功能、Toast、Prompt、菜单等界面的产品文本。
2. 区分产品文本与 Element ID、日志、路径、协议内容、用户输入等不可翻译字符串。
3. 将字面量、格式化文本、条件分支、复数和局部变量传播统一建模。
4. 永久保存稳定的 Message ID、翻译目录、人工审核决定和语义迁移计划。
5. 通过开发期 GPUI text trace 和伪语言运行，发现静态规则的覆盖盲区。
6. 在 Zed 上游持续演进时，对账新增、删除、移动、文案变化和规则失效。
7. 在独立临时工作目录中注入运行时和 UI 改写，验证后构建本地化 Zed。
8. 可选地按 crate 或 UI surface 导出小型、可独立评审的上游 patch，但不以其长期可重放为前提。

整体流程为：

```text
原版 Zed tag/commit
  ↓
无损语法解析
  ↓
GPUI/UI sink 识别 + 局部数据流分析
  ↓
UI 文本候选 IR
  ↓
规则判定 / 人工确认 / 版本对账
  ↓
持久语义资产：Message ID、目录、迁移计划
  ↓
在独立临时工作目录生成源码 Overlay
  ├──────────────┐
  ↓              ↓
编译与 UI 验证    GPUI 开发期 instrument/trace
  └───────┬──────┘
          ↓
静态/运行时 reconcile + 可选 patch 导出
```

## 3. 调查结论

### 3.1 当前项目状态

`zed-i18n-kit` 当前已完成阶段 0 和阶段 0.5：针对固定 Zed commit 建立了唯一的 corpus v2，共 266 条版本化 UI 文本参考样本，并实现严格运行时校验、源码摘要、精确 byte span 和小型内存评估器。Tree-sitter 扫描器、持久化 scan-result 协议、persistent inventory、运行时和 Overlay 尚未实现。`local/zed` 是一个独立且完整的 Zed Rust workspace，并通过当前仓库的 `.gitignore` 排除。

阶段 0.5 证明了 occurrence、sink、所有权和源码作用域必须共同参与判断，并解决了 subject kind、多文本槽位、源码快照和基础指标建模。但实施复盘也确认，canonical CST span、nullable origin constraint、corpus 外发现、自动确认覆盖率和独立复核门禁仍需由真实 Tree-sitter 原型闭合。后续不推翻总体架构，而是先完成阶段 1A 协议闭环，再扩大扫描规则。

这意味着系统应划分为持久资产、外部输入和派生工作区三个边界：

| 边界 | 位置 | 职责与生命周期 |
| --- | --- | --- |
| 持久资产 | `zed-i18n-kit` | 工具、规则、迁移计划、翻译目录、运行时模板、版本 inventory 和 CI 检查 |
| 上游输入 | 原版 Zed checkout，例如 `local/zed` | 提供精确 commit 的待分析源码；默认不由工具原地修改 |
| 派生工作区 | 独立临时目录或 Git worktree | 针对当前 commit 注入运行时和源码 Overlay，完成编译、trace 和 UI 验证后可删除 |

`local/zed` 应保持外部 checkout 的身份。`zed-i18n-kit` 保存的是可移植的 Rust runtime template 和语义 adapter，而不是修改后的 Zed 源码快照。

### 3.2 AST 不等于语义

GPUI 的 `ParentElement::child` 是接受任意 `IntoElement` 的泛型方法。仅仅发现 `.child("...")`，不能证明调用目标一定是 GPUI，也不能判断字符串是否属于产品 UI。

同时，Zed 中存在多类间接数据流：

- `Label::new("Delete")` 等直接文本参数；
- `Button::new(id, "Cancel")` 等非首参数文本；
- `div().child("...")` 等由 receiver 类型决定语义的调用；
- 先在 `match` 或 `if` 中生成文本，再通过局部变量传给组件；
- `format!`、字符串拼接和 `push_str` 构成的动态消息；
- Toast、Prompt、表单校验、辅助功能标签等非普通 Label 界面；
- 由 Rust Action 标识符动态生成的命令面板名称。

因此，语法树只负责保真定位和结构识别；真正的语义来自 UI sink 解析、导入/符号解析和字符串数据流分析。

### 3.3 不存在可直接复用的应用级 i18n 设施

当前调查未发现 Zed 应用层的通用翻译目录或文本格式化 API。Zed 的 `assets` crate 当前也没有嵌入 `locales/**/*`。

国际化工作不能只做静态提取，还需要在临时构建树中注入运行时资源加载、locale 协商、消息格式化和回退策略。在上游提供正式能力前，这些修改由 adapter 针对指定 Zed commit 生成；不建立长期 fork。

### 3.4 纯字面量提取无法覆盖重要界面

命令面板当前通过 `humanize_action_name(action.name())` 将 Rust Action 名称转换成英文显示文本。这里没有待提取的英文 UI 字面量。

类似的派生型界面必须成为独立的语义域。例如：

- Action 注册和命令面板名称；
- 设置 schema 中的标题与描述；
- 产品产生的错误类型最终显示到通知界面的消息。

第一阶段可以明确限定在 Rust/GPUI 产品文本，但不能把该结果表述为“覆盖全部 Zed UI 文本”。

### 3.5 上游证据改变了交付策略

Zed 的通用 i18n 主 Issue 仍处于开放状态，完整 Fluent 方案和大批量翻译 PR 均未合并。公开 review 反复暴露的约束是：数百文件、数万行的变更不可有效评审，翻译同步和人工审核超出当前维护带宽，运行时还必须先证明 binary size 与 frame time 成本。

另一方面，locale-aware 时间格式等小型前置改造已经合并。这说明问题不是“是否需要国际化”，而是如何把基础能力和迁移拆成可独立证明价值的增量。

因此，本项目默认迁移单位必须是 crate 或 UI surface；所有批量改写能力都要服从 dry-run、变更上限、benchmark 和人工评审边界。生成的 patch 只是当前 commit 的可审查派生产物，长期保存的是语义迁移计划。详细证据见[上游调研](research/zed-upstream-i18n.md)。

### 3.6 国际化不是单一字符串问题

本文后续所称的 i18n 工具核心是 Localization，但项目边界必须显式区分：

| 领域 | 处理方式 |
| --- | --- |
| Localization | 消息提取、选择、复数、语序和翻译资源，属于核心范围 |
| Formatting | 日期、数字、单位等 locale 格式，提供独立运行时 adapter |
| Collation | locale-aware 排序与比较，作为独立能力跟踪 |
| RTL/Layout | 通过 pseudolocale、目标语言和真实 UI 验证 |
| Input/IME | 作为独立交互测试域，不由文本扫描器解决 |
| Extensions | 需要消息所有权和 locale 协议，不能自动接管第三方文本 |

## 4. 总体架构

建议采用静态分析与开发期运行时验证相互校正的渐进式架构：

```text
┌───────────────────────────────────────────────┐
│ Discovery：文件发现、作用域和排除规则              │
└──────────────────────┬────────────────────────┘
                       ↓
┌───────────────────────────────────────────────┐
│ Parsing：Tree-sitter CST、源码 byte range      │
└──────────────────────┬────────────────────────┘
                       ↓
┌───────────────────────────────────────────────┐
│ Semantics：符号候选、UI sinks、有界数据流         │
└──────────────────────┬────────────────────────┘
                       ↓
┌───────────────────────────────────────────────┐
│ Analysis Result：Occurrence IR、证据与处置建议   │
└──────────────────────┬────────────────────────┘
                       ↓
┌───────────────────────────────────────────────┐
│ Persistent State：目录、审核决定、语义迁移计划    │
└──────────────────────┬────────────────────────┘
                       ↓
┌───────────────────────────────────────────────┐
│ Materialize：针对指定 commit 生成临时 Overlay   │
└──────────────────────┬────────────────────────┘
             ┌─────────┴──────────┐
             ↓                    ↓
┌──────────────────────┐  ┌──────────────────────┐
│ Build / UI smoke     │  │ GPUI instrument/trace│
└──────────┬───────────┘  └──────────┬───────────┘
           └─────────────┬───────────┘
                         ↓ reconcile / export
┌───────────────────────────────────────────────┐
│ Validation：可重生成、目录、Rust、运行时与版本对账  │
└───────────────────────────────────────────────┘
```

静态引擎决定文本所有权、消息身份和语义改写。运行时 trace 只能证明某段文本实际到达 UI，并帮助发现静态漏报；它不能仅凭观察到一个字符串就决定该字符串可翻译。原版输入 checkout 默认保持不变，所有可执行修改只物化到独立派生工作区。

### 4.1 Discovery

文件发现层负责确定分析边界，而不是由解析器猜测：

- 默认包含 `crates/*/src/**/*.rs`；
- 默认排除 `tests`、`*_tests.rs`、examples、component preview 和生成文件；
- 支持按 crate、路径、`cfg` 和规则 profile 缩小范围；
- 记录 Zed commit，保证扫描结果可追溯；
- 后续支持基于 Git diff 的增量扫描。

### 4.2 Parsing

第一阶段使用官方 Python Tree-sitter binding：

- `tree-sitter`
- `tree-sitter-rust`

Tree-sitter 在这里作为 CST 使用，主要职责是：

- 提供调用、方法调用、宏、字面量、`let`、`if`、`match` 等结构；
- 保留注释、空白和准确 byte range；
- 在不完整源码下提供可恢复的解析结果；
- 为后续精确文本编辑提供位置。

不能用 AST pretty-printer 重写整个文件。源码修改必须使用 byte-range edits，并按位置倒序应用。

### 4.3 Domain rule packs

规则按语义域拆成独立 rule packs，而不是把路径特判持续堆积到单个配置或扫描器中：

```text
rules/
├── gpui_components/
├── menus/
├── settings/
├── actions/
├── notifications/
└── prompts/
```

每个 pack 必须包含明确的 tested commits、capability requirements/probes、sink、文本所有权边界、evaluator、rewrite 策略、正反 fixtures 和已知限制。不得把任意两个 Git commit 之间声明为连续兼容区间。一个 sink 可以声明多个文本槽位；槽位通过可解释的 value path 区分普通参数、`Option` 内部值和集合元素。以下只是概念表示，不冻结最终 rule pack 文件语法：

```toml
[[sinks]]
symbol = "ui::Label::new"
slots = [{ path = "arg[0]", kind = "visible_text" }]

[[sinks]]
symbol = "ui::Button::new"
slots = [
  { path = "arg[0]", kind = "identity", disposition = "excluded" },
  { path = "arg[1]", kind = "visible_text" },
]

[[sinks]]
symbol = "gpui::Window::prompt"
slots = [
  { path = "arg[1]", kind = "prompt_message" },
  { path = "arg[2].Some", kind = "prompt_detail" },
  { path = "arg[3][*]", kind = "prompt_action" },
]
```

每个槽位独立产生 occurrence、证据和处置结果。同一调用中的 Element ID、可见标签、详情和按钮不能合并成一个候选。Tooltip、ARIA、Toast action 等 builder 方法继续作为各自符号的文本槽位建模。

规则不能只依赖方法短名称，应尽量解析：

- `use` 导入和别名；
- 完整或候选符号路径；
- 构造器的参数位置；
- builder receiver 的起点；
- 已知 wrapper/helper 的传播关系。

`.child()` 只有在 receiver 可追溯到 `div()`、`h_flex()`、`v_flex()` 等 GPUI element，或者有更强类型证据时，才能自动判定为 UI sink。

当某个 domain 超出声明的支持版本时，扫描器应报告兼容性风险，而不是静默沿用可能已失效的规则。

### 4.4 可选 rust-analyzer 后端

Tree-sitter 和规则系统无法可靠解决以下问题时，可以增加一个 Rust sidecar：

- trait method 的实际解析结果；
- receiver 的完整类型；
- 跨模块重导出和复杂别名；
- 跨函数调用关系；
- 宏展开后的调用结构。

该 sidecar 可以基于 rust-analyzer/HIR，但不应成为 MVP 的硬依赖。只有在金标数据证明基础方案的误报或漏报无法接受时再引入，以避免过早承担大型 workspace 分析、宏、`cfg` 和版本耦合成本。

### 4.5 版本快照与能力探测

每次扫描必须记录精确 `zed_commit`、工具版本、rule pack 版本和配置 hash。不能仅记录 release 名称，也不能假设任意两个 Git commit 之间存在可靠的兼容区间。

每个 rule pack 对目标 checkout 执行 capability probe，例如确认符号是否存在、文本参数位置是否变化、GPUI trait 结构是否仍符合假设。兼容性分为：

- `tested`：该 commit 已通过对应 fixtures、金标和集成验证；
- `compatible_unverified`：能力探测通过，但尚未完成完整验证；
- `unsupported`：关键符号或结构变化，只禁用受影响的规则并报告，不让整个扫描静默产生错误结论。

升级时必须重新扫描新版 Zed，再将新旧 inventory 语义对账；不能尝试直接重放旧 patch。

## 5. 中间表示

### 5.1 Occurrence

发现阶段输出文本出现位置，而不是直接生成翻译消息：

```text
Occurrence
├── source_commit
├── source_path
├── primary_span
├── syntax_kind
├── sink_symbol
├── sink_slot
├── expression
├── enclosing_module
├── enclosing_function
├── cfg_conditions
├── provenance
├── resolution_lifecycle
├── confidence
└── structural_fingerprint
```

`primary_span` 是文本槽位实际取值对应的最小完整 Rust 表达式，不包含外层 sink 调用、参数分隔符或仅用于定位的 wrapper。`provenance` 保存经局部变量、分支、格式化或拼接追踪到的结构化来源 span。enclosing call、行列和 anchor 可以作为人工审计上下文，但不参与精确身份匹配。具体 Tree-sitter node 映射必须由阶段 1A 的真实 fixtures 固定，不能仅依据当前 corpus anchor 反推。

`resolution_lifecycle` 描述消息必须在哪个边界解析：

- `render_scoped`：只在当前 render/layout 中使用；
- `state_retained`：会存入组件或应用状态；
- `platform_owned`：会交给系统菜单、窗口标题或通知等平台 API；
- `identity_coupled`：显示文本还参与 ID、缓存或行为判断，改写前必须先解耦。

`expression` 至少支持：

- `Literal("Reset")`
- `Template("Input Requested by {requester}")`
- `Select(status, arms)`
- `Plural(count, variants)`
- `Concat(parts)`
- `ExternalValue`
- `Opaque`

### 5.2 Message

Occurrence 经规则或人工确认后才转化为 Message：

```text
Message
├── message_id
├── domain
├── source_text
├── placeholders
├── selectors
├── translator_comment
├── occurrences
└── state
```

这种分层用于区分以下状态：

1. 找到了疑似 UI 文本；
2. 已确认该文本需要翻译；
3. 已分配稳定 message ID；
4. 已写入目录；
5. 已生成当前 Zed commit 的临时源码 Overlay。

Message ID 是跨 Zed 版本的稳定语义身份；Occurrence 只是某个 commit 中的源码位置。文件路径、行号和 byte range 不能充当 Message 身份。`structural_fingerprint` 应组合 enclosing symbol、sink、参数位置、表达式结构和 domain，用于识别代码移动，但不能在匹配不唯一时跳过人工审核。

### 5.3 Scan result 与评估单元

阶段 1 输出的是确定性、版本化的 `scan-result`，不是已经冻结 Message ID 和人工决定的持久 inventory。阶段 2 才把审核状态、稳定 Message ID 和版本对账信息写入 persistent inventory。架构图中的 Analysis Result 表示一次扫描产物，不是跨版本人工事实来源。

参考语料的评估单元必须区分：

- `sink_slot`：具体调用中的一个文本槽位；
- `expression_origin`：经局部变量、`if`、`match` 或 `format!` 传播的表达式来源；
- `scope_exclusion`：因 test、example、component preview 或生成边界被排除的源码区域。

匹配优先使用 Zed commit、source path 和精确 UTF-8 byte range。`sink_slot` 必须匹配 primary span；`expression_origin` 匹配 occurrence 的结构化 provenance span。origin 样本中非空 sink/slot 是必须相等的约束，`null` 表示该维度不参与匹配；多个结果满足约束时必须报告歧义。不能按英文文本、模糊行号或最相似上下文自动配对。具体约束见 [ADR 0003](decisions/0003-scanner-evaluation-contract.md) 和 [ADR 0005](decisions/0005-phase-1-evaluation-loop-first.md)。

评估结果必须区分：应发现样本缺失的 `unmatched_sample`、一个样本多重命中的 `ambiguous_sample`、协议或快照无效的 `invalid_result`，以及 corpus 没有穷举标注的 `unlabeled_occurrence`。前三类可以阻塞 corpus 门禁；未标注 occurrence 进入独立审计队列，不直接计为误报，也不自动视为正确。

持久 `scan-result-v1` 至少记录 Zed commit、工具版本、rule pack 版本、配置 hash、capability probe、扫描范围和相关文件 SHA-256。相同 HEAD 但相关文件内容不同的结果必须拒绝评分。同一输入和配置重复扫描应产生 byte-for-byte 相同的序列化结果。

## 6. 有界数据流分析

第一版采用函数内反向追踪，不立即实现全程序分析：

1. 从已确认 UI sink 的参数开始。
2. 参数是字面量时生成直接候选。
3. 参数是 `format!` 时解析为带 placeholder 的模板。
4. 参数是局部变量时，追踪对应的 `let`、`if` 和 `match` 定义。
5. 参数由字符串拼接或 `push_str` 组成时，生成复合表达式或标记人工处理。
6. 参数来自外部对象时标记动态来源，不自动翻译。
7. 追踪超过函数边界时先标记为 `review_required`。

所有分析结果都应携带处置结果和可解释证据：

- `confirmed`：完整 sink、文本槽位和所有权证据，可进入自动流程；
- `review_required`：间接来源、复杂传播、低置信符号解析或语义边界不明确；
- `excluded`：已确认不是可翻译产品文本。

confidence 可以作为内部证据等级，但不能创建与 `review_required` 行为相同的第四种持久状态。内部 `probable` 必须对外映射为 `review_required`。

只有 `confirmed` 或已经人工接受的项允许进入源码改写。

## 7. 翻译边界

### 7.1 自动确认候选

- `Label::new("Delete")`
- `Button::new(id, "Cancel")`
- `Tooltip::text("Select Line Ending")`
- 产品编写的 `aria_label` 和 `aria_description`
- 产品编写的 Toast、Prompt 和表单校验消息

### 7.2 自动发现、人工确认

- 通过局部变量或 helper 传递的文本；
- `.child("...")`；
- `format!`、字符串拼接和 `push_str`；
- 从错误类型转化并最终显示的字符串；
- receiver 或完整符号无法可靠解析的调用。

### 7.3 默认排除

- Element ID、Action ID、配置 key；
- 文件路径、URL、SQL、正则表达式；
- 日志和开发诊断；
- 用户输入、文件名、文档正文；
- LSP、ACP、模型或插件返回的文本；
- 测试数据、examples、component preview；
- 图标字符和纯符号，例如 `›`、`⋯`。

动态内容不能仅依据其最终类型为 `SharedString` 就进行翻译。应翻译产品拥有的句法骨架，保留用户、文件、协议或第三方拥有的值作为 placeholder。

## 8. 国际化运行时

### 8.1 资源格式

建议使用 Fluent，而不是简单 JSON 键值目录。Zed 已存在复数、选择、动态插值和整句重排需求：

```ftl
go-to-line-target = Go to line { $line }

project-panel-files-not-shown =
    { $count ->
        [one] 1 file not shown
       *[other] { $count } files not shown
    }
```

资源按 domain 拆分：

```text
assets/locales/
├── en-US/
│   ├── go-to-line.ftl
│   ├── project-panel.ftl
│   └── command-palette.ftl
└── zh-CN/
    ├── go-to-line.ftl
    ├── project-panel.ftl
    └── command-palette.ftl
```

### 8.2 Rust API

运行时 API 应尽量保留 Message ID、参数和 fallback 信息到 render/layout 边界，而不是在组件树入口立即转换为 `SharedString`。建议引入惰性的 `LocalizedText`（名称可在原型阶段调整）：

```rust
let help = t!(
    key = "go-to-line-help-relative",
    "Go to line {$line}",
    line = line,
);

label.child(help)
```

运行时要求：

- `t!` 返回携带稳定 Message ID、源语言 fallback 和类型化参数的 `LocalizedText`，不立即解析；
- `render_scoped` sink 尽可能在 render/layout 边界解析为 GPUI 文本；
- `state_retained` sink 必须定义 locale 变化后的重新解析或状态失效方式；
- `platform_owned` sink 在平台 API 边界显式解析；
- `identity_coupled` sink 在显示文本与身份解耦前禁止自动改写；
- locale 查找顺序为精确 locale、language、`en-US`；
- 缺失翻译回退到 `en-US`，开发模式记录一次诊断；
- placeholder 名称和集合必须与源语言一致；
- 必须立即得到字符串的边界显式接收 `&App` 或 Translator；
- MVP 可以要求语言变更后重启，实时切换作为后续功能；
- `en-US` 同样是正式资源，不能把残留源码字面量当作静默回退。

为 `LocalizedText` 提供无条件的 `Into<SharedString>` 虽然方便，但很可能导致过早解析，使 locale 切换、trace 和消息身份丢失。只有在生命周期明确的边界才能使用这种转换；是否实现该 trait 应由原型和调用点审计决定。

物化 adapter 需要在派生工作区中为 Zed Assets 增加 `locales/**/*`，使 release build 能嵌入语言资源，dev build 能从 checkout 读取；该改动不写回原版输入 checkout。

## 9. Message ID

推荐格式：

```text
<domain>-<component>-<meaning>
```

例如：

```text
go-to-line-help-relative
project-panel-remove-confirmation
agent-elicitation-status-waiting
```

约束如下：

- ID 一经分配就保持稳定，并由持久迁移状态管理；
- 文件移动和英文修改不能改变 ID；
- 相同英文默认不自动合并；
- 只有语义完全一致时才显式复用；
- 初次生成可以参考 crate、模块、函数和文本 slug，但生成后必须冻结；
- 使用结构 fingerprint 辅助识别上游移动，不使用行号充当消息身份。

英文源文本可以作为宏中的 fallback 和译者上下文，但不能作为永久 key。分析 IR 也不能绑定某一种 key 生成算法：ID 分配由 catalog adapter 完成，便于未来与上游最终采用的格式对接。

## 10. 语义迁移与临时 Overlay

### 10.1 长期保存语义，不长期保存 patch

长期资产是 Message、人工审核决定和语义迁移计划。迁移计划描述目标 domain、enclosing symbol、sink、参数、预期表达式与 rewrite adapter；它不能只保存旧文件的 diff 上下文。

patch 和修改后的 Zed 源码都是特定 commit 的派生产物，可以删除并重新生成。工具不维护长期 Zed fork，也不承诺旧 patch 能应用到新版 Zed。

### 10.2 版本升级对账

新版 Zed 必须重新扫描，并与上一个已审核 inventory 分类对账：

| 状态 | 含义 | 默认处理 |
| --- | --- | --- |
| `unchanged` | 位置和文本未变化 | 自动保留 |
| `moved` | 文件或函数移动，语义证据一致 | 自动绑定旧 Message ID |
| `source_changed` | 源文变化，语义可能相同 | 保留候选 ID，要求检查翻译与语义 |
| `added` | 新增 UI 文本 | 进入审核队列 |
| `removed` | 旧 occurrence 消失 | 标记 `possibly_obsolete`，不立即删除目录 |
| `sink_changed` | sink 或解析生命周期变化 | 重新审核改写方式 |
| `ambiguous` | 新旧 occurrence 不能唯一匹配 | 人工处理，禁止自动改写 |
| `excluded_now` | 已不属于产品 UI 文本 | 记录理由后取消迁移 |

删除采用延迟清理：消息第一次消失时只记录 `missing_since` 和 `last_seen_commit`；连续多个受支持版本不存在且排除规则失效、`cfg` 变化或扫描范围变化后，才允许人工确认删除。

### 10.3 Overlay 物化

改写器只消费已确认 Message、仍有效的 Occurrence 和通过能力探测的 adapter：

1. 验证输入 checkout 的精确 commit 和干净度要求。
2. 创建显式输出目录或独立 Git worktree；默认不修改输入 checkout。
3. 记录源文件 hash，生成不可重叠的 byte-range edits。
4. 注入 runtime template、资源目录和当前 UI surface 的改写。
5. 应用前验证目标 AST fingerprint 与文件 hash，拒绝过期或模糊编辑。
6. 按 byte offset 倒序应用 edits，并重新解析修改后的文件。
7. 对同一 commit、配置和目录重复物化必须得到等价文件树。
8. 根据需要从派生工作区导出统一 diff；该 diff 不是下一版本的输入。

禁止行为：

- 重新打印整份 Rust AST；
- 对低置信候选自动改写；
- 按英文文本全局替换；
- 在输入 Zed checkout 中默认原地写入；
- 为了套用旧 patch 而进行模糊上下文匹配；
- 在目录生成成功前删除源码中的英文语义；
- 把外部动态内容包进翻译调用。

## 11. 推荐项目结构

```text
src/zed_i18n_kit/
├── cli.py
├── config.py
├── discovery.py
├── parsing/
│   ├── protocol.py
│   └── tree_sitter_backend.py
├── semantics/
│   ├── model.py
│   ├── imports.py
│   ├── expressions.py
│   ├── dataflow.py
│   └── classifier.py
├── rules/
│   ├── model.py
│   ├── loader.py
│   └── compatibility.py
├── trace/
│   ├── protocol.py
│   ├── inventory.py
│   └── reconcile.py
├── versions/
│   ├── snapshot.py
│   ├── capabilities.py
│   └── diff.py
├── catalog/
│   ├── model.py
│   ├── fluent.py
│   ├── ids.py
│   └── validation.py
├── rewrite/
│   ├── edits.py
│   ├── rust.py
│   ├── overlay.py
│   └── transaction.py
├── build/
│   ├── materialize.py
│   └── verify.py
└── reports/
    ├── jsonl.py
    └── sarif.py

rules/
├── gpui_components/
├── menus/
├── settings/
├── actions/
├── notifications/
└── prompts/

migrations/                 # 可跨版本重定位的语义迁移计划
catalogs/                   # 源语言、目标语言和审核状态
runtime-template/           # 注入派生工作区的最小 Rust runtime
adapters/                   # Zed workspace、Assets 与 GPUI 接入规则
inventories/                # 可选提交的已审核版本快照

tests/
├── fixtures/rust/
├── fixtures/rules/
├── golden/
└── integration/
```

`runtime-template/` 保存可移植的 Rust 模板，不保存一份修改后的 Zed crate。物化时，adapter 才把它映射到目标 commit 的 workspace、Assets 和 GPUI 接入点。

CLI 生命周期：

```text
zed-i18n scan
zed-i18n update --previous <inventory> --zed <checkout>
zed-i18n review
zed-i18n extract
zed-i18n prepare --zed <checkout> --output <derived-worktree>
zed-i18n instrument --worktree <derived-worktree>
zed-i18n trace <trace-file>
zed-i18n reconcile <inventory> <trace-file>
zed-i18n verify <derived-worktree>
zed-i18n build <derived-worktree>
zed-i18n export-patch --worktree <derived-worktree> --scope <surface>
zed-i18n catalog check
zed-i18n check
```

这是目标生命周期，不是阶段 1 的一次性 CLI 承诺。阶段 1 只冻结只读 `scan`、scan-result `evaluate` 和 corpus check；其余命令在对应持久状态、Overlay 或 runtime 阶段再确定参数与兼容性。

其中：

- `scan` 永远只读；
- `update` 重新扫描并输出新增、删除、移动、变化和冲突，不消费旧 patch；
- `prepare` 只写入显式指定的派生工作区，拒绝把输入 checkout 与输出设为同一路径；
- `instrument` 只修改派生工作区，并且 hook 只用于开发构建；
- `trace` 规范化运行时事件，不执行翻译；
- `reconcile` 对比静态 inventory 与 runtime trace，输出漏报、未渲染项和未知所有权文本；
- `verify` 执行解析、目录、Rust 与配置检查，`build` 调用受支持的 Zed 构建流程并记录产物来源；
- `export-patch` 是可选的当前 commit 审查/上游贡献产物，不写回持久迁移状态。

## 12. 实施阶段

### 阶段 0：建立金标语料（已完成）

从代表性 crate 中手工标注 200～300 个样本，至少覆盖：

- 直接字面量；
- builder 参数位置；
- 局部变量和 `match`；
- `format!` 与 placeholder；
- 动态协议/用户内容；
- ID、日志、路径等反例；
- Toast、Prompt、ARIA；
- 测试和 component preview。

阶段 0 最初建立了 250 条参考样本；阶段 0.5 已将其一次性转换到唯一支持的 corpus v2，旧格式不再保留或读取。

### 阶段 0.5：评估协议与风险样本校准（已完成）

- 直接切换到唯一的 corpus schema v2，表达 subject kind、精确 source span、text slot、期望发现状态、期望处置和 review state；
- 定义 scan result 与 sink/origin/provenance 的精确匹配算法；
- 固定 auto-confirm precision、candidate recall、unsafe promotion、exclusion leakage 和 unmatched 的计算方式；
- 补充 `.child()` receiver、Prompt 多槽位、拼接/`push_str`、多行 raw string、跨函数 helper、内嵌 `#[cfg(test)]`、用户/协议内容和错误链样本；
- 加入 schema 与运行时模型漂移检查，并要求评估输入是相关路径干净或有内容摘要的精确 checkout。

当前唯一的 v2 corpus 包含 266 条样本，其中 16 条用于校准高风险结构。v2 使用文件级 UTF-8 byte span 和相关 Rust 文件 SHA-256；小型评估器实现了 exact span/provenance 匹配和基础指标。v1 资产和兼容代码已按 [ADR 0004](decisions/0004-direct-v2-cutover.md) 删除。

阶段 0.5 后复盘确认，现有 span 尚未全部由 canonical CST 节点规则验证，nullable origin constraint、未配对 occurrence 和自动确认覆盖门禁也仍是原型语义。该结果只建立阶段 1 的评估地基，不代表持久 scan-result 契约已经冻结；调整决定见 [ADR 0005](decisions/0005-phase-1-evaluation-loop-first.md)。

### 阶段 1A：扫描评估协议闭环

- 接入 Tree-sitter Rust，使用 10～20 个代表性真实样本验证 CST range；
- 固定 primary span、provenance span、wrapper 归一化和 nullable origin constraint；
- 建立 `scan-result-v1` JSON schema、严格解析与确定性序列化；
- 记录 Zed/tool/rule/config/capability/scope 和相关文件摘要；
- 区分 unmatched、ambiguous、invalid 和 unlabeled occurrence；
- 增加 auto-confirm coverage 与 review-state-aware 指标；
- 不以规则覆盖率为目标，不生成目录，不改写源码。

### 阶段 1B：只读扫描器

- 完成文件发现和排除规则；
- 实现 `cfg`、import、alias 和候选符号解析；
- 以 typed builtin rules 建立第一批 domain rule packs；
- 实现函数内反向追踪和保守降级；
- 使用阶段 1A 协议输出确定性、版本化的 scan-result；
- 不生成目录，不改写源码。

### 阶段 1C：规则冻结与独立审计

- 独立复核按风险和 disposition 分层的 corpus 子集；
- 对 independently reviewed 子集执行 precision、coverage、recall 和安全门禁；
- 审计 corpus 外的 `unlabeled_occurrence`，区分合法发现、规则误报和需补金标结构；
- 固定首批允许自动确认的规则、tested commits 和 capability probes；
- 量化 Tree-sitter 在 receiver、宏、helper 和跨函数语义上的缺口，再决定是否评估 rust-analyzer sidecar。

### 阶段 2：持久 inventory 与版本对账

- 保存 `zed_commit`、工具、规则和配置版本；
- 冻结 Message ID 与人工审核决定；
- 实现 `unchanged`、`moved`、`source_changed`、`added`、`removed`、`sink_changed` 和 `ambiguous` 分类；
- 对 `removed` 使用延迟清理，不因一次扫描不到就删除目录；
- 使用两个真实 Zed commit 验证升级报告，而不是只在合成 fixtures 上测试。

### 阶段 3：目录、迁移计划与 Overlay 物化器

- Message ID 分配和 Fluent 目录同步；
- 建立独立于旧 diff 上下文的语义迁移计划；
- 实现 byte-range edits、hash 与 AST fingerprint 防护；
- `prepare` 只写入显式派生工作区，保持输入 checkout 不变；
- 验证同一输入可重复生成等价文件树；
- 可选导出当前 commit 的 dry-run diff。

### 阶段 4：最小晚解析运行时与 benchmark

- 将 `LocalizedText`、`en-US` fallback 和一种测试 locale 实现为 `runtime-template/`；
- 用 adapter 注入派生工作区，不建立 Zed fork；
- 验证 `render_scoped` 与必须立即解析的边界；
- 测量 clean build 增量、binary size、空闲内存和代表性 UI frame time；
- 在 benchmark 结论明确前不批量迁移源码。

### 阶段 5：开发期运行时覆盖验证

- 定义本地 trace schema；对用户、协议和第三方动态内容默认只记录来源类别与 fingerprint，不持久化原始正文；
- 以最小 GPUI hook 记录文本 sink、调用位置或稳定 fingerprint；
- 加入 pseudolocale，用文本扩张和标记暴露漏翻与布局问题；
- 实现 `instrument`、`trace` 和 `reconcile`；
- runtime hook 只存在于派生开发工作区，不作为生产翻译路径。

### 阶段 6：第一个垂直切片

选择 `go_to_line`：它同时包含动态格式化文本、局部变量、Tooltip 和 ARIA，能够验证主要设计，而规模仍然可控。

该阶段使用已经验证的 runtime template、`en-US`、`zh-CN` 和伪语言资源。验收产物是可从原版 Zed commit 重复生成的派生工作区；小型 patch 只作为可选审查或上游贡献输出。

### 阶段 7：复杂语言语义

选择 `project_panel` 验证：

- 单复数；
- 多条件选择；
- 文件名等动态内容；
- 分段拼接消息；
- Prompt 与 Toast。

### 阶段 8：派生型 UI 文本

单独处理 Action/Command Palette，并逐步覆盖设置 schema 等非普通 GPUI 字面量来源。

### 阶段 9：按证据增强语义后端

根据金标评估决定是否加入 rust-analyzer sidecar。不能仅因为“完整语义更先进”就提前引入。

## 13. 验收标准

### 扫描与分类

- 目标生产 Rust 文件解析成功率为 100%，失败项必须显式报告；
- auto-confirm precision 与 auto-confirm coverage 必须同时报告，任一分母为零都不能视为通过；
- candidate recall、unsafe promotion 和 exclusion leakage 必须分别报告；
- `single_review` 样本只提供 observational 指标，阻塞门禁只使用满足分层覆盖要求的 `independently_reviewed` 子集；
- 99% auto-confirm precision 和 95% candidate recall 保留为质量目标，阶段 1C 根据独立复核样本量确定首个可执行门禁，不能在样本不足时宣称达到目标；
- `review_required` 被提升为 `confirmed` 的 unsafe promotion 必须单独报告并作为阻塞指标；
- `excluded` 被输出为候选的 exclusion leakage、无法精确对齐的 unmatched/ambiguous 和无效快照必须单独报告；
- corpus 外 occurrence 作为 `unlabeled_occurrence` 单独审计，不计入 corpus precision，也不自动视为正确；
- 未支持的传播或表达式进入 review 队列，不静默丢失；
- 每个结论都能回溯到源码位置和命中的规则。

上述阈值只证明固定、分层构造 corpus 上的回归表现，不代表完整 Zed workspace 的统计准确率。规则冻结后必须从未用于调优的新路径和高风险结构中抽样，独立审计误报与漏报，并将审计结果与 corpus 指标分别报告。

### 版本对账

- 每个 inventory 能回溯到精确 Zed commit、工具版本、rule pack 和配置；
- 使用两个真实 Zed commit 验证新增、删除、移动、文案变化和歧义分类；
- 文件移动不改变已确认 Message ID；
- 匹配不唯一时进入人工审核，不自动选择最相似候选；
- 消失一次的消息只标记 `possibly_obsolete`，不立即删除目录。

### Overlay 物化

- `prepare` 要求显式且不同于输入 checkout 的输出路径；
- 物化前后输入 Zed checkout 的文件内容和 Git 状态保持不变；
- 文件 hash 或 AST fingerprint 不匹配时拒绝改写；
- 修改后重新解析成功，不改动未确认候选和范围外文件；
- 同一 Zed commit、工具版本、规则、迁移计划和目录生成等价文件树；
- `export-patch` 输出可审查 diff，但新版 Zed 不以重放该 diff 为升级路径。

### 目录

硬失败条件：

- 重复 Message ID；
- Fluent 语法或目录结构损坏；
- placeholder 集合或类型不一致；
- 源码引用不存在的消息；
- 源语言目录不完整或引用无效。

默认报告但不阻塞普通上游改动：

- 非源语言 locale 缺少消息；
- 未使用消息；
- 静态或 runtime 覆盖率下降；
- 翻译待母语者复核；
- rule pack 超出声明的支持版本。

不能要求每次英文文案改动同时更新所有语言；否则工具会把上游已明确缺乏的翻译维护带宽变成硬性合并成本。

### Zed 验证

- 通过 `cargo fmt --check`；
- 通过目标 crate 的 `cargo check` 和测试；
- `en-US`、`zh-CN`、伪语言各完成一次真实 UI smoke test；
- 检查文本截断、布局扩张、辅助功能标签和缺失翻译回退；
- 记录最小运行时引入前后的 binary size、代表性 frame time 和内存基线；
- 静态 inventory 与 runtime trace 的差异均有明确分类，不把用户内容或第三方内容误计为待翻译产品文本。
- 验证完成后删除派生工作区，再次 `prepare` 仍能得到通过相同检查的构建树。

## 14. 当前推荐决策

项目应定位为“针对原版 Zed 源码进行国际化分析、版本差异对账和临时构建改写的工具链”。项目不维护 Zed fork；永久保存国际化语义和翻译数据，临时生成对指定 Zed commit 的源码 Overlay。

推进顺序应是“金标语料 → 评估协议与风险样本校准 → 阶段 1A 最小 CST/scan-result/评分闭环 → 阶段 1B 只读扫描器 → 阶段 1C 规则冻结与独立审计 → 持久 inventory 与版本对账 → Overlay 物化器 → 最小晚解析 runtime template 与 benchmark → runtime trace/pseudolocale → `go_to_line` → `project_panel` → Action/Command Palette”，然后再按证据扩展到其他 domain。

这个顺序能够尽早验证：

- 什么才是可翻译 UI 文本；
- Tree-sitter 和规则系统是否足够准确；
- 静态扫描与运行时观测之间有哪些覆盖缺口；
- Fluent 和晚解析 Rust API 是否适合 Zed；
- 运行时对 binary size、frame time 和内存的成本是否可接受；
- 源码 Overlay 能否从干净上游 checkout 保真且可重复地生成；
- Zed 升级后新增、删除、移动和变化能否局部、可解释地处理；
- 上游同步的长期成本是否可控。

在上述闭环建立前，不应批量替换整个 Zed workspace，不应维护长期修改分支，不应把旧 patch 当作新版输入，不应把 runtime 字符串拦截当作生产翻译方案，也不应把完整 rust-analyzer/HIR 集成作为第一阶段前置条件。

当前主攻方向是阶段 1A，而不是同时扩充 rule pack、CLI 和语义后端。canonical span 必须由真实 Tree-sitter 端到端原型确定；在 snapshot、匹配分类、coverage 和独立复核门禁闭合之前，现有阶段 0.5 指标只能作为探索性证据。
