# 工作项：阶段 1B 只读扫描器

Status: completed

Completed: 2026-09-02

## 问题

阶段 1A 只扫描固定的 10 个文件，并以少量短名称规则校准 CST、scan-result 和评估协议。该 profile 无法发现完整生产 Rust 源码，也没有 import/alias、receiver 或有界数据流解析，不能作为 Zed workspace 的只读扫描器。

## 目标

在不改变 `scan-result-v1`、不写入输入 checkout 的前提下，实现确定性的生产 Rust 文件发现、候选符号解析、第一批 typed builtin rules 和函数内保守数据流，使固定 corpus 与 corpus 外 occurrence 都能进入可解释审计。

## 范围

- 包含：
  - `crates/*/src/**/*.rs` 文件发现及路径排除规则；
  - `cfg`、`use`、import alias 和候选符号解析；
  - GPUI component、Prompt、Notification 第一批 typed builtin rules；
  - `.child()` receiver 约束与函数内反向 provenance；
  - workspace scan、snapshot、evaluation 与安装态 CLI 证据。
- 影响资产：`src/zed_i18n_kit/`、`tests/`、`scripts/`、开发与测试文档。

## 非目标

- 不冻结阶段 1C 的 precision/coverage 阈值或自动确认规则；
- 不引入外部 rule DSL、rust-analyzer/HIR 或跨函数调用图；
- 不建立 persistent inventory、Message ID、目录、runtime 或 Overlay；
- 不改写 `local/zed` 或任何输入源码。

## 已确认事实与假设

- 已确认事实：固定 checkout 下 `crates/` 约有 1,866 个 Rust 文件，其中生产发现边界约为 `crates/*/src/**/*.rs`。
- 已确认事实：阶段 1A profile、16 个 CST fixture 和 56 occurrence 必须继续作为协议回归，不随 workspace discovery 漂移。
- 已确认事实：corpus manifest 只覆盖抽样文件，workspace scan 的额外 snapshot 不能因未出现在 corpus manifest 中自动判为无效。
- 待验证假设：Tree-sitter 对生产发现范围的 parse error 数量可明确报告，扫描时间和结果体积可接受。

## 验收条件

- [x] discovery 稳定包含生产 `src/**/*.rs`，排除测试、examples、benches、fixtures、component preview、生成路径和越界符号链接；
- [x] 默认 CLI `scan` 使用 workspace discovery，阶段 1A 检查显式使用固定 profile；
- [x] workspace scan 可以对 corpus 覆盖路径评分，corpus 外 snapshot 不被误判为 manifest 错误；
- [x] import/alias/candidate symbol 解析有正例、易混淆反例和稳定证据；
- [x] `.child()` 仅在 receiver 具有 GPUI element 证据时作为 sink；
- [x] 未支持表达式保守输出 `review_required` 或明确跳过，不伪装为 confirmed；
- [x] 聚焦测试、真实 workspace scan、阶段 1A 回归和完整门禁通过，`local/zed` 保持 clean。

## 实施步骤

1. 建立 discovery policy、确定性路径结果和 snapshot 集成。
2. 提取 Rust module/import/alias 候选符号环境。
3. 将内置 sink 拆为首批 typed domain rules，并增加 receiver/value-path 约束。
4. 扩展函数内反向追踪与保守降级。
5. 运行 workspace/corpus/unlabeled 审计，收敛文档和证据。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `uv run python -m pytest -q tests/test_discovery.py tests/test_rust_symbols.py tests/test_scanner.py tests/test_evaluation.py` | passed，26 tests | discovery、作用域化符号、receiver、数据流、snapshot 与 corpus 外范围 | 合成 fixture 不证明完整 workspace |
| `uv run python scripts/check_scan_evaluation_contract.py --zed local/zed` | passed；16 fixtures / 56 occurrences | 阶段 1A 协议回归保持稳定 | 只覆盖固定 profile |
| 两次 workspace `scan`、`cmp` 与 `evaluate` | passed；1,642 files / 1,880 occurrences / 1,476,642 bytes，重复结果一致；242 evaluated / 58 unmatched / 0 ambiguous / 1,736 unlabeled | 真实 discovery、确定性、规模和错误报告 | observational，不是阶段 1C 门禁；存在 1 个 failed parse probe |
| workspace observational metrics | recorded；precision 22/22，coverage 0/0 undefined，recall 144/202，unsafe 0/57，leakage 1/40 | 固定 corpus 上的当前规则表现 | 样本均未独立复核，不构成通过阈值 |
| 从 sdist 构建 wheel、安装到全新 Python 3.13 venv 后执行 `zed-i18n-kit scan` 并与开发态 `cmp` | passed；1,880 occurrences，结果逐字节一致 | wheel 包含语义模块/schema，安装入口与开发态一致 | 依赖安装需要 PyPI，不证明其他平台 |
| `uv run python scripts/check.py` | passed；Ruff、ty、54 tests | 完整 Python 门禁 | 不证明 Zed build/UI |

## 风险与阻塞

- 风险：workspace 扫描可能暴露大量 grammar error、短名称误命中和未标注 occurrence；这些结果必须分类，不能靠扩大排除范围隐藏。
- 风险：路径或 `cfg` 排除过宽会造成系统性漏报；每条排除规则需要正反 fixture。
- 已知限制：Tree-sitter 不解析任意宏 token tree 内部的 Rust 调用；跨文件重导出、复杂解构、闭包参数和完整 trait receiver 需要 HIR 或后续有证据的扩展。当前实现只将可见通配符和父模块重导出作为审核候选，不声称完成 Rust 名称解析。
- 阻塞条件：生产文件无法稳定发现、scan-result 体积不可控，或 parse error 与候选 sink 广泛相交。
- 恢复方式：保留阶段 1A fixed profile，缩小为可验证 domain/crate 切片并记录缺口，不回退到文本 grep 或旧 patch。

## 相关决策

- ADR：[0003 扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
- ADR：[0005 阶段 1 先闭合扫描评估协议](../../decisions/0005-phase-1-evaluation-loop-first.md)
- ADR：[0006 Tree-sitter Rust CST 后端](../../decisions/0006-tree-sitter-rust-cst-backend.md)
