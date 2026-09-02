# 工作项：建立 AI 工程化基础

Status: completed

## 问题

项目已经形成 Zed/GPUI 国际化架构设计，但仓库仍是 Python 骨架，缺少跨 Agent 会话稳定的开发契约、任务范围、长期决策记录、统一验证入口和 CI。

## 目标

建立一个不过度建设、可以立即执行的 AI 开发最小闭环，使后续 Agent 能从仓库确定项目边界、任务生命周期和基础质量门禁。

## 范围

- 包含：根 `AGENTS.md`、README、AI workflow、开发与测试规范；
- 包含：工作项与 ADR 模板，以及本次治理 ADR；
- 包含：标准库实现的统一检查入口、聚焦测试、基础 CI 和 PR 模板；
- 包含：将 AI 工程化调研沉淀到 `docs/research/`。

## 非目标

- 不实现 Zed 扫描器、rule pack、schema 或 Overlay；
- 不修改 `local/zed`；
- 不安装 Spec Kit 或新增运行时依赖；
- 不创建大量项目 skills、Agent persona 或 GitHub Issue 自动化。

## 已确认事实与假设

- 已确认事实：项目使用 Python 3.13、uv、Ruff、ty 和 pytest；
- 已确认事实：`local/zed` 由 `.gitignore` 隔离；
- 已确认事实：开始修改前没有仓库级 `AGENTS.md`、CI 或工作项规范；
- 已验证假设：标准库检查器能够在本地统一运行现有门禁，CI 调用同一入口。

## 验收条件

- [x] 新 Agent 可以从仓库文档确定目标、硬边界、权威来源和验证命令；
- [x] 非平凡任务与长期决策分别有可复用模板；
- [x] `uv run python scripts/check.py` 依次运行格式、lint、类型和测试并通过；
- [x] CI 使用锁定依赖并调用相同检查入口；
- [x] 所有新增相对 Markdown 链接有效；
- [x] `local/zed` 和现有 staged 内容未被修改或重新暂存。

## 实施步骤

1. 建立根契约、README 和项目边界。
2. 建立 AI workflow、开发、测试、ADR 和工作项规范。
3. 增加统一检查入口、测试、CI 和 PR 模板。
4. 运行完整门禁与文档/Git 检查，记录结果并归档工作项。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `uv run python scripts/check.py` | passed | Ruff format、Ruff lint、ty 和 3 个 pytest 通过 | 未证明 GitHub runner 实际执行 |
| `tests/test_documentation_links.py`（由完整门禁执行） | passed | README、AGENTS、docs 和 PR 模板的相对链接存在 | 不请求外部 URL |
| `git diff --check && git diff --cached --check` | passed | staged 基线和本轮未暂存增量均无 whitespace 错误 | 不证明内容正确 |
| `git -C local/zed status --short` | passed，无输出 | 调研结束时外部 checkout 干净 | 仅证明最终状态 |
| `git -C local/zed rev-parse HEAD` | passed | Zed 基线为 `2551721adb5b5187bc27cfae0fbe47f0ed4c5397` | 不证明其他 commit 兼容性 |

## 风险与阻塞

- 剩余风险：GitHub Actions 配置尚未在真实 runner 上执行，首次 push 后需要观察 CI。
- 剩余风险：治理文档可能随实现增长发生漂移，后续应优先用测试或 gate 替换机械规则。
- 阻塞条件：无。
- 恢复方式：不适用。

## 相关决策

- [ADR 0001：采用轻量、仓库内生的 AI 开发治理](../../decisions/0001-ai-first-engineering-governance.md)
