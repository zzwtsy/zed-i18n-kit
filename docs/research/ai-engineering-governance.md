# AI 驱动项目工程化调研

## 1. 文档状态

- 调查日期：2026-09-01
- 调查目标：识别 AI 主导开发项目如何管理 Agent 指令、规格、决策、测试、PR 证据和 CI，并筛选适合 `zed-i18n-kit` 的做法
- 调查对象：DeepSeek Harness、OpenAI Codex、OpenHands、GitHub Spec Kit，以及本地 Zed checkout 的 Agent 规则
- 关联决定：[ADR 0001：采用轻量、仓库内生的 AI 开发治理](../decisions/0001-ai-first-engineering-governance.md)

本文记录调查时点的公开仓库事实和本项目推断。外部默认分支会继续变化；链接固定到本次调查的 commit，不能把任何单个项目的做法视为普遍最佳实践。

## 2. 当前项目基线

调查时，`zed-i18n-kit` 已具备：

- Python 3.13 与 uv lock；
- Ruff、ty、pytest；
- 三个 Python 实践相关 skills；
- Zed/GPUI 架构设计和上游国际化调研；
- 通过 `.gitignore` 隔离的 `local/zed` 外部 checkout。

主要缺口是：

- 没有仓库级 `AGENTS.md`；
- README 为空，CLI 仍是占位实现；
- 没有 CI 和统一本地门禁；
- 没有任务范围、验收与终态的持久记录；
- 没有长期技术决策生命周期；
- 没有 golden corpus、schema、生成物漂移或真实 Zed 集成证据规范。

## 3. DeepSeek Harness

调查基线：[commit `4e84901e6471`](https://github.com/deepseek-ai/deepseek-harness/commit/4e84901e6471b79ec0338099867ebb4606d12bb5)。

### 3.1 观察

[根 AGENTS.md](https://github.com/deepseek-ai/deepseek-harness/blob/4e84901e6471b79ec0338099867ebb4606d12bb5/AGENTS.md)提供：

- 仓库结构、权威文档和精确命令；
- 架构、类型、测试、文档和 Git 约束；
- “运行相关检查，不默认重复全量检查”的证据选择原则；
- path-scoped `AGENTS.md` 和按场景加载的 skills；
- 可机械验证的约束进入顶层 gate，而不是只写在提示词中。

[Agent Notes 规范](https://github.com/deepseek-ai/deepseek-harness/blob/4e84901e6471b79ec0338099867ebb4606d12bb5/.agents/notes/README.md)把设计记录分为 `proposed`、`implemented`、`rejected` 和 frozen archive，要求记录问题、决定、备选方案、后果与验证，并使用脚本检查目录、状态和格式。

Issue 模板分别定义 Research 的核心问题、证据标准和交付结论，以及 Task 的验收、交付物和测试证据。PR 模板要求关联 Issue 并记录变更与验证。仓库通过集中 gate runner 组合 lint、typecheck、unit、coverage、snapshot、artifact、consumer 和文档检查。

### 3.2 对本项目的启示

- Agent 指令应提供精确入口和边界，而不是只表达抽象价值观；
- 重要决定必须记录未采用方案，否则后续 Agent 会重复争论；
- skills 适合已经重复验证的条件式流程，不适合一次性发现；
- 机器可检查的不变量不应长期依赖 Agent 自觉。

### 3.3 不直接复制

DeepSeek Harness 是大型多包、多语言、多平台项目。双语 Agent Notes、复杂归档规则和大量专用 gate 对当前 Python 骨架成本过高。本项目采用单语轻量 ADR 和单文件工作契约，等真实复杂度增长后再扩展。

## 4. OpenAI Codex

调查基线：[commit `8971fc25a96c`](https://github.com/openai/codex/commit/8971fc25a96c5368f6e7bd8e66799a2dec476f9a)。

### 4.1 观察

[AGENTS.md](https://github.com/openai/codex/blob/8971fc25a96c5368f6e7bd8e66799a2dec476f9a/AGENTS.md)不是通用风格指南，而是具体说明：

- 必须使用的仓库命令和禁止的替代命令；
- 哪些改动触发 schema 生成、集成测试、snapshot 或 benchmark；
- 复杂改动的 review size 预算；
- 公共协议、模型上下文和生成物的特殊风险；
- 按 crate 运行聚焦测试，再根据共享面决定是否运行全量测试。

[justfile](https://github.com/openai/codex/blob/8971fc25a96c5368f6e7bd8e66799a2dec476f9a/justfile)统一本地命令，避免 Agent 选择不同工具参数。[Rust CI](https://github.com/openai/codex/blob/8971fc25a96c5368f6e7bd8e66799a2dec476f9a/.github/workflows/rust-ci.yml)根据变更路径选择 job，检查 format、依赖、benchmark smoke 和自定义 lint，并在多个阶段确认工作树干净。

### 4.2 对本项目的启示

- `scripts/check.py` 应成为本地和 CI 的同一入口；
- 规则、schema、rewrite、Overlay 和 runtime 各自需要不同证据；
- 生成器执行后 clean-worktree 是重要 gate；
- diff 规模是 review 风险信号，但不应变成机械拆分规则；
- 用户可见输出适合 snapshot/golden，且更新内容必须被人实际审查。

## 5. OpenHands

调查基线：[commit `b4428e1f8529`](https://github.com/OpenHands/OpenHands/commit/b4428e1f8529fe726039437c8e54a7e7319986eb)。

### 5.1 观察

[PR 模板](https://github.com/OpenHands/OpenHands/blob/b4428e1f8529fe726039437c8e54a7e7319986eb/.github/pull_request_template.md)显式区分 HUMAN 和 AGENT 内容，要求 AI 提供端到端运行证据，不能只报告单元测试。

[PR 描述检查器](https://github.com/OpenHands/OpenHands/blob/b4428e1f8529fe726039437c8e54a7e7319986eb/.github/scripts/check_pr_description.py)机器检查：

- Why、Summary、How to Test；
- 至少一个带 `ready-for-dev` 标签的关联 Issue；
- 前端改动的截图或视频；
- Bug fix 的复现与修复证据；
- 人类确认区域和 Agent 区域。

### 5.2 对本项目的启示

- AI 摘要不能替代人类对实际 diff 的确认；
- PR 证据应说明“证明了什么”，不仅列命令；
- 工作项在实现前必须达到可执行状态；
- 将来若引入可见 UI 或真实 Zed 运行验证，PR 应链接截图、trace 摘要或可复现步骤。

当前项目由单人主导，暂不机器强制 Issue 标签和人类正文长度，只在 PR 模板中保留 human review checklist。

## 6. GitHub Spec Kit

调查基线：[commit `0053c3a328ae`](https://github.com/github/spec-kit/commit/0053c3a328aefbfebae096657c095eb0740a444d)。

### 6.1 观察

Spec Kit 使用以下闭环：

```text
constitution → specify → plan → tasks → implement → converge
```

其模板将内容分离为：

- Constitution：项目原则、治理和质量门禁；
- Specification：用户场景、边界条件、功能要求和可量化成功标准；
- Plan：技术上下文、constitution check 和项目结构；
- Tasks：依赖、并行性、独立验收和阶段 checkpoint；
- Converge：实现与规格的差异回填为剩余任务。

### 6.2 对本项目的启示

- AI 编码前需要明确 what/why，再决定 how；
- 验收场景要能独立运行或观察；
- 完成不是任务列表清空，而是实现与规格收敛；
- 工作项应包含非目标，防止 Agent 将合理联想扩展成未授权功能。

当前阶段将 spec、plan、tasks 和 verification 合并为一个工作项文件。只有单文件无法清晰表达依赖时，才考虑引入完整 Spec Kit 或拆分资产。

## 7. Zed 项目规则

本地调查基线为 Zed commit `2551721adb5b` 的 [`AGENTS.md`](https://github.com/zed-industries/zed/blob/2551721adb5b5187bc27cfae0fbe47f0ed4c5397/AGENTS.md)。它记录 Rust/GPUI 的具体安全约束、测试计时器、实体生命周期、构建命令和 PR 规则，并对新增 Agent 规则设置高门槛：规则应该是非显然、重复出现且具体可执行的陷阱，不应该成为容易过时的架构地图。

对本项目最重要的结论是：

- 生成上游贡献 patch 时必须重新读取目标 Zed commit 的当前规则；
- 本项目不能复制一份 Zed 规则并假设永久有效；
- `local/zed` 保持只读，使上游 Agent 规则与本项目生成流程不会混成一个长期 fork 工作流。

## 8. 采用矩阵

| 做法 | 本项目决定 | 落点 |
| --- | --- | --- |
| 根 Agent 契约 | 立即采用 | `AGENTS.md` |
| Path-scoped Agent 规则 | 有真实差异时采用 | 未来 `rules/`、`runtime-template/` |
| 精确统一命令 | 立即采用 | `scripts/check.py`、CI |
| 工作项规格与终态 | 立即采用轻量版 | `docs/work/` |
| 长期决策生命周期 | 立即采用轻量 ADR | `docs/decisions/` |
| 独立只读 review | 高风险任务采用 | `docs/ai-workflow.md` |
| PR 证据与 human review | 先模板化 | `.github/pull_request_template.md` |
| Schema/生成物 gate | 能力落地时采用 | `schemas/`、generator `--check` |
| Golden/snapshot | 扫描器阶段采用 | `tests/golden/` |
| 路径感知 CI | CI 变慢后采用 | `.github/workflows/` |
| 完整 Spec Kit | 暂不采用 | 单工作项不足时重新评估 |
| 大量 Agent persona | 不采用 | 使用任务阶段，不固化角色组织 |
| 多 Agent 同工作树写入 | 禁止 | 独立 worktree 或单写入者 |

## 9. 项目专属质量重点

通用 AI 工程规范不能代替领域验证。本项目最容易被 AI 误判的部分是：

1. 把字符串搜索当作 UI 语义；
2. 把用户、协议和第三方文本误归为产品所有；
3. 用行号或英文原文充当稳定身份；
4. 让旧 patch 成为 Zed 升级路径；
5. 在输入 checkout 原地改写；
6. 用单元测试声称完整 Zed 或 GPUI 已验证；
7. 生成 inventory、catalog、schema 后未检查漂移和兼容性。

因此，未来 Agent 工作流必须围绕 golden corpus、可解释证据、版本快照、rewrite 幂等、输入不变和真实派生 workspace 验证建立门禁。

## 10. 结论

AI-first 不意味着 prompt-first。适合本项目的工程模型是：

```text
仓库规则约束行为
工作项约束当前范围
ADR 保存长期决定
测试与 CI 验证机器可判定事实
人类确认范围、风险和发布
```

治理应随真实风险增长。先建立一个能执行的最小闭环，再在重复失败提供证据时增加规则、skill 或 gate；不能为了看起来“AI 原生”而预先复制大型项目的全部流程。
