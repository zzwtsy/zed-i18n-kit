# 工作项：项目级推理过程泄漏清理 Skill

Status: completed

## 问题

仓库缺少一套专门识别和清理持久文档、Python 注释与 docstring 中“作者会话视角”文字的流程，例如不可解析的临时决策编号、PR/评审叙事、版本变化流水账、控制流复述和无主计划残留。DeepSeek Harness 的 `dsh-trim-cot-leakage` 提供了成熟分类与案例，但原版依赖其专用 Agent Notes、双语文档和检查命令，不能在本项目中直接完整执行。

## 目标

在 `.agents/skills/` 中建立一个可自动发现、自包含且符合本仓库授权边界的 `trim-cot-leakage` skill。它保留上游的核心判断方法和防止过度删除规则，同时将范围、文档所有权、Python 注释规则、排除目录与验证命令适配到本项目。

## 范围

- 包含：项目级 skill 入口、用于语义校准的精简示例、适配后的 recall probes、Codex UI 元数据和上游 MIT 许可说明。
- 包含：记录固定上游 commit、原始文件路径和适配边界。
- 影响资产：`.agents/skills/trim-cot-leakage/`、本工作项。

## 非目标

- 不对现有源码、文档或金标语料执行实际泄漏审计或清理。
- 不移植 DeepSeek Harness 的 Agent Notes、双语文档体系、`dsh-prose-standard`、`dsh-doc` 或专用 gate。
- 不修改 `local/zed`、公共 API、持久 schema、CLI 或运行时行为。
- 不 push、tag 或发布；Git commit 不属于实现步骤，只有用户另行明确授权后执行。

## 已确认事实与假设

- 已确认事实：本项目通过 `.agents/skills/` 提供项目级 skills，现有 skills 均作为普通仓库文件跟踪。
- 已确认事实：上游 `dsh-trim-cot-leakage` 在 commit `4e84901e6471b79ec0338099867ebb4606d12bb5` 下包含 `SKILL.md`、`references/examples.md` 和 `references/recall-batteries.md`。
- 已确认事实：上游仓库使用 MIT License，原版 skill 还引用本项目不存在的 `dsh-prose-standard`、`dsh-doc`、Agent Notes 和专用 gate。
- 已验证事实：完整命题保留规则已直接纳入入口，Python 注释任务只路由到仓库已有的 `python-code-comments`；skill 本地链接检查没有发现缺失依赖。

## 验收条件

- [x] `trim-cot-leakage` 通过 skill 结构校验，名称、描述和 UI 元数据一致。
- [x] skill 内所有本地 Markdown 链接均可解析，不依赖未引入的 DeepSeek Harness 文件。
- [x] audit 请求保持只读，只有明确 fix/trim 请求才授权编辑，并且要求显式范围。
- [x] recall probes 排除 `local/zed`、skill 自身和受控派生数据，不把模式命中当作语义结论。
- [x] 上游来源、固定 commit、修改说明和 MIT 许可保存在 skill 目录内。
- [x] `git diff --check` 与使用任务专属可写缓存的 `uv run python scripts/check.py` 通过。

## 实施步骤

1. 从固定上游 commit 获取完整 skill 目录，核对内容、依赖和许可证。
2. 初始化项目级 `trim-cot-leakage` skill，并写入适配后的入口、示例、recall probes 和来源许可。
3. 运行 skill 结构校验、链接检查、diff 检查和完整项目门禁。
4. 记录验证证据，将工作项转为 `completed` 并移动到 `docs/work/completed/`。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/trim-cot-leakage` | passed | frontmatter、命名和 scaffold 完整性 | 不证明规则判断质量 |
| `agents/openai.yaml` YAML、描述长度、默认 prompt 和隐式调用策略检查 | passed，描述 29 字符 | UI 元数据可解析且与 skill 名称一致 | 不证明宿主 UI 已在当前会话热加载 |
| 基于 `tests/test_documentation_links.py` 解析器的 skill 本地链接检查 | passed，7 links | skill 内相对链接目标存在 | 不校验远程 URL 的长期可用性 |
| `uv run pytest -q tests/test_documentation_links.py` | passed，1 test | 仓库文档和 completed 工作项的相对链接有效 | 该既有测试不扫描 `.agents/`，由上一项补充 |
| `git diff --check` 和逐文件 `git diff --no-index --check` | passed，6 untracked files | 已跟踪 diff 与本次未跟踪文件没有空白错误 | 不证明 skill 行为质量 |
| `uv run python scripts/check.py` | blocked | 默认 uv 缓存位于只读文件系统，未进入项目检查 | 不代表 Ruff、ty 或 pytest 失败 |
| `env UV_CACHE_DIR="${TMPDIR:-/tmp}/zed-i18n-kit-uv-cache" uv run python scripts/check.py` | passed，7 tests | Ruff format、Ruff lint、ty 与仓库测试 | 不证明真实审校任务中的 precision 或 recall |

## 风险与阻塞

- 风险：模式 probes 可能误报或漏报；要求校准正反例、逐条语义判断和无模式阅读降低风险。
- 风险：适配时可能削弱原版命题保留要求；通过保留反例与过度纠正案例进行复核。
- 阻塞条件：无。
- 恢复方式：后续上游变化按新的固定 commit 重新调查和适配，不能用 `master` 漂移覆盖当前版本。

## 相关决策

- ADR：无；本次只增加特定审校流程，不改变产品架构、公共格式或兼容性承诺。
