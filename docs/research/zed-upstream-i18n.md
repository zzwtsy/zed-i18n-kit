# Zed 上游国际化调研

## 1. 文档状态

- 调查日期：2026-09-01
- 调查范围：Zed 官方仓库的 Discussions、Issues、Pull Requests，以及两个公开社区实现
- 目的：识别上游已经验证的约束、失败模式和可接受的推进方式，为 `zed-i18n-kit` 的定位与架构提供依据
- 关联设计：[Zed/GPUI 国际化迁移与临时构建工具链设计](../zed-gpui-i18n-design.md)

本文记录的是调查时点的公开信息。Issue、Discussion 和 Pull Request 的状态可能继续变化；引用社区方案时，只把它们视为实验和工程证据，不代表 Zed 官方路线。

## 2. 证据分级

| 级别 | 含义 | 使用方式 |
| --- | --- | --- |
| 官方事实 | Zed 维护者的 Issue、Discussion、PR review、已合并代码 | 用于判断当前状态、验收偏好和上游约束 |
| 社区实验 | 未被 Zed 官方采纳的提案、fork 或外部工具 | 用于发现技术路径和失败模式，不视为官方决策 |
| 本项目推断 | 根据多项证据形成的设计判断 | 必须标明适用边界，并通过原型、benchmark 或金标语料验证 |

## 3. 执行摘要

截至调查日期，Zed 仍没有合并通用应用级国际化框架。主跟踪 Issue 仍开放，社区仍在讨论技术路线，但官方反馈已经给出清晰边界：

1. 国际化改造必须可分批评审，不能以数百文件、数万行改动一次性引入。
2. 在大面积替换 UI 文本前，需要先证明运行时开销、binary size、frame time 和维护成本可接受。
3. Zed 团队没有足够带宽长期同步和审核大量机器生成或未经母语者确认的翻译。
4. 小型、语义明确、能独立改善 locale 行为的改动更容易被接受。
5. 完整国际化不只是“提取字符串”，还包括格式化、排序、RTL、输入法、扩展内容和布局验证。

因此，`zed-i18n-kit` 不应建立并长期维护一个多语言 Zed fork。更有价值的定位是：针对原版 Zed commit 完成 UI 文本语义分析、版本差异对账和运行时覆盖验证，并在独立临时工作目录中可重复生成国际化源码 Overlay。按 crate 或 UI surface 导出的 patch 只是可选审查或上游贡献产物。

## 4. 官方跟踪入口

| 入口 | 调查时状态 | 观察 | 对本项目的含义 |
| --- | --- | --- | --- |
| [Issue #7409](https://github.com/zed-industries/zed/issues/7409) | Open，`area:internationalization` | 长期主跟踪入口，证明需求存在，但通用框架尚未落地 | 不能假设已有稳定官方 API；适配层必须可替换 |
| [Discussion #59416](https://github.com/zed-industries/zed/discussions/59416) | 当前社区讨论入口 | 汇集用户与贡献者对国际化的诉求和方案讨论 | 作为持续观察入口，不把讨论意见当作已接受设计 |
| [Discussion #43592](https://github.com/zed-industries/zed/discussions/43592) | 社区技术提案 | 提供了具体技术方向，但没有形成官方架构决定 | 可借鉴问题拆解，不能作为兼容承诺 |
| [Issue #48865](https://github.com/zed-industries/zed/issues/48865) | locale-aware collation 需求 | 文本翻译以外，列表排序也受 locale 影响 | 工具边界必须明确区分 Localization 与 Collation |

## 5. Pull Request 证据

### 5.1 大规模方案为何未被接受

| PR | 规模与结果 | 主要信号 | 设计启示 |
| --- | --- | --- | --- |
| [#62027](https://github.com/zed-industries/zed/pull/62027) | Fluent + 简体中文完整方案；约 390 个文件、约 23k 行；关闭 | 维护者认为 benchmark 方向有希望，但整体不可有效评审；建议先做核心、压力测试、binary size/frame time，再缩小范围推进 | 先验证最小运行时；迁移按 UI surface 拆分；自动化工具默认产出小 patch |
| [#52157](https://github.com/zed-industries/zed/pull/52157) | 约 35,547 行新增；关闭 | 大量翻译存在低效、不一致和缺少充分人工审核的问题 | 翻译质量与母语审核不属于静态提取器能够自动保证的范围；CI 不应强制所有 locale 同步更新 |
| [#54185](https://github.com/zed-industries/zed/pull/54185) | 关闭 | 维护者明确指出当前没有维护多语言同步的带宽 | 本项目不能把上游团队持续维护翻译目录作为隐含前提 |
| [#60633](https://github.com/zed-industries/zed/pull/60633) | 空 `i18n` crate；关闭 | 没有可独立评估的功能价值，且方案未先讨论对齐 | crate 本身不是进展；垂直切片必须同时证明 API、资源、UI 与性能 |

这些 PR 不能推出“Zed 拒绝国际化”。更准确的结论是：上游尚未接受维护成本和评审风险过高的整体落地方式。

### 5.2 已合并的小型前置改造

| PR | 规模与结果 | 证明了什么 |
| --- | --- | --- |
| [#34115](https://github.com/zed-industries/zed/pull/34115) | 5 个文件、89 行新增；已合并 | 小型、语义明确、与 locale 能力直接相关的改造可以独立评审和合并 |
| [#7994](https://github.com/zed-industries/zed/pull/7994) | locale-aware 时间格式；已合并 | 国际化可以先从独立格式化能力演进，不必等待完整文本翻译框架 |

这支持以“基础能力 → benchmark → 单个 UI surface → 更多 domain”的顺序推进。

## 6. 社区实现

### 6.1 LI-NA/zed-i18n

[LI-NA/zed-i18n](https://github.com/LI-NA/zed-i18n) 与本项目最接近，已经实现：

- 基于 Tree-sitter 的 Rust 文本提取；
- `accepted` / `needs_review` manifest；
- Zed 版本差异跟踪；
- placeholder 校验；
- 多语言资源与构建流水线。

它证明 Tree-sitter、人工审核状态和版本 diff 是可行的工程路径。同时，其提取脚本已经增长到数千行并包含大量路径特判，说明把全部 Zed 语义塞入单个扫描器会快速形成不可维护的规则堆积。

本项目应借鉴其经过实践验证的机制，但将规则拆成具有版本范围、fixtures 和独立 evaluator 的 domain rule packs。还需注意其许可证边界：源码与派生翻译、构建产物不一定采用相同许可证，不能未经核对直接复制目录数据或翻译资源。

### 6.2 axxion/Zed-L10n

[axxion/Zed-L10n](https://github.com/axxion/Zed-L10n) 在 GPUI layout 路径统一拦截文本，能够观察大量实际渲染内容。这条路线揭示了静态扫描看不到的 wrapper、动态组合和派生文本，适合用于覆盖率验证。

但把所有进入 layout 的字符串当作可翻译产品文本，会遇到根本边界：

- 无法可靠区分产品文本、用户内容、文件名、协议返回和插件内容；
- 以英文原文作为 key，难以稳定处理文案修改和同文异义；
- 复数、gender、整句重排和 placeholder 语义不足；
- `format!`、`thiserror` 及 byte-offset 相关处理容易产生边界问题。

因此，本项目只借鉴“运行时观测”能力：开发期 instrument/trace、伪语言和静态—运行时 reconcile。运行时 hook 不作为生产翻译架构，也不直接决定哪些文本应该翻译。

### 6.3 能力对比

| 维度 | LI-NA/zed-i18n | axxion/Zed-L10n | zed-i18n-kit 的目标 |
| --- | --- | --- | --- |
| 主要入口 | 静态 Tree-sitter | GPUI 运行时 layout | 静态分析 + 开发期运行时验证 |
| 语义边界 | manifest 与路径规则 | 运行时字符串拦截 | sink、数据流、生命周期与人工决策 |
| 核心产物 | 翻译与构建流水线 | 运行时替换 | inventory、迁移计划、版本报告、临时 Overlay |
| key 策略 | 依实现而定 | 英文源文本 | 显式、稳定、生成后冻结的 Message ID |
| 上游适配 | 社区发行导向 | fork/runtime patch | 针对精确 commit 重新生成，patch 仅为可选输出 |
| 修改后源码 | 社区维护 | fork 中维护 | 独立派生工作区，用后可删除 |

## 7. 对架构的直接影响

### 7.1 静态与运行时双引擎

```text
静态 Tree-sitter 扫描 ──→ 语义化 Occurrence inventory ────┐
                                                        ├─→ reconcile → 漏报/误报/未知来源
GPUI 开发期 instrument ──→ runtime text trace ───────────┘
```

- 静态引擎负责所有权判断、稳定定位、目录生成和源码改写。
- 运行时引擎负责证明文本实际到达 UI，并发现静态规则的覆盖盲区。
- `reconcile` 只生成证据和待审查项，不因运行时出现某字符串就自动翻译。

### 7.2 晚解析文本模型

简单的 `tr!(cx, ...) -> SharedString` 会在组件树较早阶段丢失消息身份、参数和 locale 变化能力。更合理的运行时边界是 `LocalizedText` 或 `LocalizedString`：尽量保留 Message ID 与参数，直到 render/layout 边界再解析。

需要为 sink 标记解析生命周期：

- `render_scoped`：只在当前 render/layout 中消费，可晚解析；
- `state_retained`：进入组件或应用状态，需要定义 locale 变化后的失效方式；
- `platform_owned`：传给操作系统菜单、窗口标题或通知，需要在平台边界解析；
- `identity_coupled`：文本同时参与 ID、缓存或行为判断，必须先解耦身份与显示文本。

`Into<SharedString>` 可能触发过早解析，不能仅凭类型转换看似方便就把它作为默认 API。

### 7.3 Domain rule packs

规则应按语义域组织，而不是集中在单一 `zed.toml` 或不断增长的路径特判脚本中：

```text
rules/
├── gpui_components/
├── menus/
├── settings/
├── actions/
├── notifications/
└── prompts/
```

每个 pack 至少声明：支持的 Zed commit/version 范围、sink、翻译边界、局部 evaluator、rewrite 策略、正反 fixtures 和已知限制。

### 7.4 可再生成的迁移单位

工具应按 crate 或 UI surface 保存语义迁移计划，并针对精确 Zed commit 在派生工作区重新生成变更。dry-run diff、性能影响和覆盖证据用于审查，但旧 diff 不作为新版输入。建议次序为：

1. 只读 inventory 与金标语料；
2. 开发期 trace/pseudolocale；
3. 最小晚解析运行时与 benchmark；
4. `go_to_line` 垂直切片；
5. `project_panel` 复杂语言语义；
6. Action/Command Palette 等派生型文本；
7. 根据证据逐域扩大，而不是全仓一次性替换。

### 7.5 不维护 Zed fork

本项目采用以下生命周期边界：

- 原版 Zed checkout 是只读输入，记录精确 commit；
- `zed-i18n-kit` 永久保存 Message ID、翻译目录、人工审核决定、rule packs、语义迁移计划和最小 runtime template；
- 工具在显式指定的临时目录或 Git worktree 中注入 runtime、资源和 UI 改写；
- 派生工作区通过编译与 UI 验证后可以删除，并应能从相同输入重复生成；
- Zed 升级后重新扫描并对账 inventory，不直接重放旧 patch；
- 小型 patch 仍可按需导出给人审查或贡献上游，但不构成项目的持久状态。

### 7.6 CI 责任边界

应硬失败的错误：

- Fluent 或其他目录语法损坏；
- placeholder 集合或类型不一致；
- 重复 Message ID；
- 源码引用不存在的消息；
- 目录引用和结构完整性错误。

默认只报告、不阻塞普通源码变更的状态：

- 某个非源语言 locale 缺少新消息；
- 静态或运行时覆盖率下降；
- 翻译仍待母语者审核；
- 某个 domain pack 超出其声明的支持版本。

这避免把“每次英文改动必须同步所有语言”变成上游不可承担的维护承诺。

## 8. 明确不应混为一谈的领域

| 领域 | 核心问题 | 与文本提取器的关系 |
| --- | --- | --- |
| Localization | 消息选择、复数、语序、翻译资源 | 本项目核心 |
| Formatting | 日期、时间、数字、单位的 locale 格式 | 需要运行时 adapter，但不是字符串扫描 |
| Collation | locale-aware 排序与比较 | 独立能力，不能由翻译目录解决 |
| RTL/Layout | 镜像、方向、文本扩张和组件几何 | 通过伪语言与真实 UI 验证覆盖 |
| Input/IME | 输入法、组合输入、快捷键和编辑行为 | 独立测试域 |
| Extensions | 第三方拥有的消息与 locale 协议 | 需要单独契约，不能自动接管其文本 |

## 9. 尚未得到证明的事项

以下仍是待验证假设，不能因为社区已有原型就视为完成：

- Fluent 是否是 Zed 最终会接受的资源格式；
- 晚解析 `LocalizedText` 对 frame time、内存和 binary size 的真实影响；
- 仅用 Tree-sitter、导入解析与函数内数据流能否达到目标 precision/recall；
- GPUI runtime trace 能覆盖多少文本，又会引入多少用户内容噪声；
- 上游愿意接受的具体 API、crate 边界和贡献节奏。

这些问题应通过最小原型、benchmark、金标语料和小型上游讨论逐项关闭，而不是在工具设计中假定答案。
