# zed-i18n-kit

`zed-i18n-kit` 是一个针对原版 Zed 源码进行国际化语义分析、版本差异对账和临时构建改写的工具链。

项目不会长期维护 Zed fork。它永久保存 UI 文本语义、Message ID、翻译目录、审核决定和迁移规则，并针对指定 Zed commit 在独立派生工作区生成可删除、可重新生成的源码 Overlay。

## 当前状态

项目已经完成扫描器实施前的阶段 0.5 评估协议校准：当前唯一支持的 corpus schema 是 v2，共 266 条样本，并提供 UTF-8 byte span、文本槽位、源码摘要、exact span/provenance 匹配和固定指标计算。旧 v1 格式已删除，不提供兼容读取或迁移入口。CLI 仍是占位实现，尚不能扫描或改写 Zed。

当前下一步是阶段 1A：先用 10～20 个代表性真实样本闭合 Tree-sitter CST、canonical span、持久 `scan-result-v1` 和评分报告，再扩展只读扫描规则。该阶段不生成翻译目录，也不修改源码。

## 核心边界

- `local/zed` 是外部只读输入，不属于本项目源码。
- 修改后的 Zed 只存在于显式派生工作区。
- patch 是当前 commit 的可选审查产物，不是持久迁移状态。
- runtime trace 只用于开发期覆盖验证，不能仅凭观察到字符串就决定翻译所有权。

## 开发环境

要求 Python 3.13 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --locked --all-groups
uv run python scripts/check.py
uv run python scripts/check_golden_corpus.py --zed local/zed
```

聚焦命令和环境说明见[开发指南](docs/development.md)。

## 项目文档

- [架构设计](docs/zed-gpui-i18n-design.md)
- [AI 开发工作流](docs/ai-workflow.md)
- [开发指南](docs/development.md)
- [测试策略](docs/testing.md)
- [长期决策记录](docs/decisions/README.md)
- [工作项规范](docs/work/README.md)
- [Zed 上游国际化调研](docs/research/zed-upstream-i18n.md)
- [阶段 0 金标语料调研](docs/research/zed-phase-0-golden-corpus.md)
- [扫描器评估契约 ADR](docs/decisions/0003-scanner-evaluation-contract.md)
- [直接切换 v2 ADR](docs/decisions/0004-direct-v2-cutover.md)
- [阶段 1 评估闭环优先 ADR](docs/decisions/0005-phase-1-evaluation-loop-first.md)
- [AI 工程化调研](docs/research/ai-engineering-governance.md)

## 目录方向

```text
src/zed_i18n_kit/   Python 分析与生成工具
rules/              Zed/GPUI domain rule packs
schemas/            Inventory、trace 与迁移格式
runtime-template/   注入派生工作区的最小 Rust runtime
adapters/           针对 Zed commit 的接入规则
tests/              单元、金标与集成测试
docs/               架构、工作流、决策和调研
local/zed/           外部 Zed checkout，不纳入版本控制
```

这些目录会随对应能力落地逐步创建，不为尚未实现的模块提前放置空文件。

## AI 开发

仓库以 `AGENTS.md` 作为 Agent 开发契约。非平凡任务使用 `docs/work/active/` 中的工作项约束范围和验收条件；架构、持久格式和兼容性决策记录在 `docs/decisions/`。

AI 的聊天上下文不是项目状态。只有进入仓库并通过检查的代码、规格、决策和验证证据才是可持续资产。
