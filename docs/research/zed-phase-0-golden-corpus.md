# Zed 阶段 0 UI 文本金标语料调研

## 1. 调研信息

- 日期：2026-09-01
- Zed commit：`2551721adb5b5187bc27cfae0fbe47f0ed4c5397`
- 调查输入：`local/zed`
- 方法：主 Agent 直接检索源码并统计，`gpt-5.6-luna`（reasoning effort `max`）独立核对代表性 UI surface；最终语料由 AI 辅助逐 occurrence 标注并通过源码锚点校验。
- 边界：本调查是静态源码证据，不证明文本在运行时实际显示，也不等同于母语者或 Zed maintainer 的双人审核。

## 2. 调查目的

阶段 0 要建立能够评估未来 AST 规则的真值集，而不是收集一份英文字符串清单。要回答的问题是：给定某个固定 Zed commit，哪些源码 occurrence 是产品拥有的 UI 文本，哪些需要进一步追踪，哪些必须排除，以及判断依赖哪些证据。

## 3. 候选空间

对代表性 crate 的只读统计显示：

| 结构 | 近似命中数 |
| --- | ---: |
| `.child(...)` | 5738 |
| `Label::new(...)` | 1239 |
| `.label(...)` | 104 |
| `.tooltip(...)` | 527 |
| `Tooltip::text(...)` | 242 |
| `.aria_label(...)` | 69 |
| `.prompt(...)` | 77 |
| `Toast::new(...)` | 73 |
| `format!(...)` | 5080 |

这些数字只是词法近似，不能直接相加，也不能视为待翻译文本数量。单个调用可能同时包含 Element ID 和显示标签；`format!` 大量用于日志、路径和协议；`.child(...)` 只有在 receiver 可解析为 GPUI element 时才构成可靠 sink。

在本次重点候选路径中，相关表达式按源码作用域近似分布为：production 4919、GPUI examples 483、tests 55、component preview 45。若按命中数量比例随机抽样，大型 `agent_ui` 和 `git_ui` 文件会压倒 Prompt、ARIA、Toast 和小型局部数据流案例。

## 4. 分层策略

金标使用四个正交维度约束抽样：

1. sink：visible text、Tooltip、ARIA、Toast、Prompt、none/unknown；
2. 表达式：直接字面量、builder 参数、`child`、局部变量、`if`、`match`、`format!` 和动态外部值；
3. 所有权：product、mixed、user、protocol、developer、identity、unknown；
4. 源码作用域：production、test、example、component preview。

最终 250 条样本分布：

| 维度 | 分布 |
| --- | --- |
| decision | `confirmed=140`、`review_required=50`、`excluded=60` |
| scope | `production=220`、`test=10`、`example=10`、`component_preview=10` |
| 重点 sink | visible text 101、Tooltip 29、ARIA 18、Toast 10、Prompt 5 |
| 重点表达式 | direct literal 151、builder 150、local 37、`format!` 15、`match` 8、`if` 8 |
| 生产反例 | Element ID 10、日志/诊断 10、路径/URL/命令 10 |

语料覆盖 33 个源码文件，涉及 `go_to_line`、`project_panel`、`settings_ui`、`git_ui`、`agent_ui`、`workspace`、`notifications` 和 `ui`。manifest 还对 `elicitation`、task Toast、workspace notification、`StatusToast`、Button 组件和 component preview 设置逐文件下限，防止总 crate 数掩盖关键文件缺失；`editor` tests 和 `gpui` examples 用于验证 discovery 排除边界。

## 5. 标签边界

### `confirmed`

需要同时具备足够强的 sink 和文本所有权证据。例如 `Label::new` 的产品字面量、`Button::new` 的第二参数、`Tooltip::text` 字面量和产品编写的 ARIA 标签。

### `review_required`

表示候选应被扫描器发现，但不能直接进入自动改写。包括泛型 `.child(...)`、局部变量传播、条件分支、`format!`、动态 Toast/Prompt 以及所有权混合的 placeholder。

### `excluded`

包括 Element ID、key context、日志、开发诊断、路径、URL、命令，以及默认扫描范围外的 test、example 和 component preview。作用域排除不表示其中的英文“不是可见文字”，而是表示它不属于生产翻译 inventory。

## 6. 当前持久资产与使用

- manifest：`corpus/zed-ui-text/v2/manifest.json`
- 样本：`corpus/zed-ui-text/v2/samples.jsonl`
- schema：`src/zed_i18n_kit/schemas/golden-corpus-sample-v2.schema.json`
- 当前格式决定：[ADR 0003](../decisions/0003-scanner-evaluation-contract.md) 与 [ADR 0004](../decisions/0004-direct-v2-cutover.md)

验证固定 checkout：

```bash
uv run python scripts/check_golden_corpus.py --zed local/zed
```

校验器会检查 schema version、未知字段、枚举、样本与定位唯一性、JSONL SHA-256、覆盖配额、Zed commit、源码路径、行号和单行 anchor。它不会通过字符串内容推断标签，也不会宣称运行时覆盖。

## 7. 阶段 0 初始格式的已知限制

- 语料是按当前 commit 固定的 occurrence，不跨版本复用行号。
- 当前样本集中 `settings_ui` 和 `git_ui` 比重较高；manifest 使用最小配额防止关键小领域消失，但不声称按真实 UI 使用频率加权。
- 初始 250 条样本不包含多行 raw string、跨函数 helper、字符串累积和由错误类型生成的 UI 文本；这些边界已在当前 corpus 加入校准样本，真正的 parser/rule 识别仍由阶段 1 验证。
- 初始 tests、preview 和 example 作用域主要由路径和已知组件边界标注；当前 corpus 已加入生产文件内部 `#[cfg(test)]` 反例，但 parser 级作用域识别尚未实现。
- 静态标签需要在后续 runtime trace 和真实 UI smoke 中校正，尤其是外部协议、用户内容和错误链。

## 8. 实施后设计复盘

阶段 0 完成后的只读复盘确认，250 条样本均能在固定 checkout 中定位，当前 anchor 在各自源码行内也均唯一；但这不足以直接定义阶段 1 的 precision 和 recall。实际样本同时包含 sink 调用、局部变量定义、`if`/`match` expression origin 和作用域反例，而扫描器计划输出主要 byte range 与数据流 provenance。阶段 1 前必须先固定评估单元和精确匹配协议。

复盘还发现：

- 当前语料中 `.child()` 只有 1 条，尚不足以判断 receiver 追踪的真实效果；
- `concatenation`、`user` 和 `protocol` 标签当前没有实际样本；
- Prompt 的 message、detail 和 answers，以及 Toast 的 identity、message 和 action label，需要按多个文本槽位分别建模；
- 分层语料适合回归测试，但 `settings_ui` 和 `git_ui` 占比较高，不能据此宣称完整 Zed workspace 的统计准确率；
- corpus v1 的 `(path, line, anchor)` 能保持人工可审计性，但新的阻塞评估需要精确 source span、origin/sink 角色和 review state。

因此阶段 0.5 建立了新的评估协议并补充风险样本。项目尚未发布，最终按 [ADR 0004](../decisions/0004-direct-v2-cutover.md) 直接切换到 v2，不保留旧格式兼容层。

## 9. 阶段 0.5 实施结果

阶段 0.5 将初始样本一次性转换为当前 v2：

- 250 条初始样本在固定 commit 上全部由行内唯一 anchor 转换为文件级 UTF-8 byte span；
- 16 条风险样本覆盖 `.child()` receiver 正反例、Prompt message/detail/actions、`concat!`、`push_str`、跨行 raw string、跨函数 helper、内嵌 `#[cfg(test)]`、错误链以及 user/protocol 内容；
- Prompt 的 message、optional detail 和三个 action 使用不同 value path，不再把整个调用视为一个候选；
- v2 manifest 记录全部相关 Rust 文件 SHA-256，因此允许无关路径存在本地修改，同时会拒绝任何评估路径漂移；
- 小型评估器以 primary span 匹配 sink/scope subject，以 provenance range 匹配 expression origin，并计算 ADR 0003 固定的五个核心指标。

风险样本仍是单次 AI 辅助审核，`review_state=single_review` 如实保留这一边界。它们用于规则回归校准，不构成 Zed 全量准确率估计，也不替代规则冻结后的独立抽样审计。

持久资产：

- v2 manifest 与样本：`corpus/zed-ui-text/v2/manifest.json`、`samples.jsonl`
- v2 schema：`src/zed_i18n_kit/schemas/golden-corpus-sample-v2.schema.json`
- 完成工作项：[阶段 0.5 评估协议与风险样本校准](../work/completed/2026-09-01-phase-0-5-evaluation-contract.md)
