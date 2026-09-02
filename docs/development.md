# 开发指南

## 1. 环境

- Python：`pyproject.toml` 声明的最低版本，目前为 3.13
- 包管理器：uv
- 外部源码：`local/zed`，不纳入本仓库版本控制

初始化：

```bash
uv sync --locked --all-groups
```

运行只读 scan/evaluate CLI：

```bash
uv run zed-i18n-kit corpus-check --zed local/zed
uv run zed-i18n-kit scan --zed local/zed --output /tmp/zed-scan.json
uv run zed-i18n-kit evaluate \
  --corpus corpus/zed-ui-text/v2 \
  --scan-result /tmp/zed-scan.json \
  --zed local/zed \
  --output /tmp/zed-evaluation.json
```

`scan` 默认发现 `crates/*/src/**/*.rs` 下的生产 Rust 文件，排除测试、examples、benches、fixtures、component preview 和生成路径，并且只读取输入 checkout。扫描器使用作用域化 `use`/alias 候选解析和 typed builtin rules；显式唯一解析可以自动分类，条件导入、通配符、父模块重导出或未知 receiver 保守进入审核，普通对象的同名 `.child()` 不会作为 GPUI sink。`evaluate` 会重新核对 commit 和扫描范围内每个文件的 SHA-256；corpus 未覆盖的 workspace occurrence 进入 unlabeled 审计，不会伪装成金标错误。评估报告标记为 `prototype-observational-v1`，其中 `observational_metrics` 与 `independently_reviewed_metrics` 明确分离，不能直接作为阶段 1C 自动确认门禁。

## 2. 统一质量门禁

```bash
uv run python scripts/check.py
```

该命令顺序执行：

1. `ruff format --check .`
2. `ruff check .`
3. `ty check`
4. `python -m pytest -q`

检查器在第一个失败处停止并返回原始非零退出码。每项最长运行 600 秒，避免 Agent 或 CI 无限等待。

聚焦命令：

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run python -m pytest -q tests/test_check_script.py
```

阶段 0.5 corpus 命令：

```bash
# 校验唯一的 v2 corpus、固定 commit、相关源码摘要和 byte span
uv run python scripts/check_golden_corpus.py --zed local/zed
```

阶段 1A 真实源码闭环：

```bash
# 校验 16 个 CST fixture、重复扫描、scan-result round trip、snapshot 和评估
uv run python scripts/check_scan_evaluation_contract.py --zed local/zed
```

该检查允许 capability probe 如实报告与目标节点不相交的 grammar parse error；命中 error subtree 的调用始终跳过。当前 `tree-sitter-rust 0.24.2` 对 `editor.rs` 的 `dyn 'static + Fn` 留有已记录解析缺口，详见 [ADR 0006](decisions/0006-tree-sitter-rust-cst-backend.md)。

`corpus/zed-ui-text/v2/samples.jsonl` 是人工审核的唯一语料事实来源，manifest 固定其 SHA-256、覆盖配额和相关 Rust 文件摘要。修改样本或 Zed commit 时必须在同一工作项中更新 manifest，并通过 checkout 校验；项目不读取或迁移 v1。

需要自动格式化时显式运行：

```bash
uv run ruff format .
```

不要把自动修复与无关功能修改混在同一 review 切片中。

## 3. 源码与派生产物

| 类型 | 示例 | 是否持久保存 |
| --- | --- | --- |
| 源码 | `src/`、`rules/`、`runtime-template/` | 是 |
| 规格与决策 | `docs/work/`、`docs/decisions/` | 是 |
| 受控数据 | `src/zed_i18n_kit/schemas/`、审核后的 inventory/catalog | 是 |
| 外部输入 | `local/zed` | 否，由其上游 Git 管理 |
| 派生工作区 | `.worktrees/` 或仓库外临时目录 | 否 |
| 构建与报告 | `artifacts/`、coverage、trace | 默认否 |

持久生成物必须有唯一生成入口，并支持 check 模式验证漂移。派生文件不要同时充当人工编辑源。

## 4. 目录创建原则

设计文档中的目录是演进方向，不要求一次性建立空骨架。只有第一个真实模块、schema、rule pack 或 fixture 落地时才创建对应目录，并在同一修改中加入最小验证。

新增模块前先确认：

- 是否存在明确职责和调用方；
- 是否需要独立测试或版本生命周期；
- 是否会产生新的公共或持久边界；
- 是否只是为了预测未来需求。

## 5. 依赖管理

- 优先使用标准库和现有依赖。
- 新依赖必须对应当前工作项的真实需求，并说明替代方案、维护状态和许可证。
- 修改依赖后运行 `uv lock`，提交 `pyproject.toml` 与 `uv.lock` 的一致变化。
- CI 使用 `uv sync --locked --all-groups`，lock 漂移必须失败，不能在 CI 中隐式更新。

## 6. Zed checkout

`local/zed` 用于调查和集成验证：

- 扫描前记录 `git rev-parse HEAD`；
- 默认拒绝写入；
- 不在其中创建提交、分支或长期翻译目录；
- 需要验证改写时创建不同路径的派生工作区；
- 删除派生工作区后，应能从同一 commit 和持久资产重新生成。

如果未来导出上游 patch，它只是当前 Zed commit 的审查产物，不作为下一版本迁移输入。

## 7. Git 边界

- 修改前后运行 `git status --short`；
- 保留不属于当前任务的 staged、unstaged 和 untracked 内容；
- 不使用 `reset --hard`、宽范围 `restore` 或未经确认的清理命令；
- 未经用户明确授权不 commit、push、tag、rebase、stash 或发布；
- 提交前检查 `git diff --check`，并确认生成器没有留下额外 diff。

## 8. 文档

- 当前事实只保留一个权威位置，其他文档使用链接。
- 调研记录日期、来源与不确定性。
- ADR 保存决定、备选项、代价和重新评估条件，不保存推理流水账。
- 实现变化同时更新受影响的 README、工作项、schema 说明和测试证据。
