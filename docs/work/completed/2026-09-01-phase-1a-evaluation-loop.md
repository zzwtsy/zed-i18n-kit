# 工作项：阶段 1A 扫描评估协议闭环

Status: completed

## 问题

阶段 0.5 已建立 corpus v2 和内存评估器，但真实 Rust CST 尚未验证 canonical primary/provenance span，`ScanResult` 也没有持久 schema、严格解析、确定性序列化和源码快照身份。当前评估器还把 nullable origin constraint 当作严格空值、把 corpus 外 occurrence 计入 unmatched，并缺少 auto-confirm coverage。这些缺口会让阶段 1B 在扩大扫描规则时反复改变协议。

## 目标

以固定 Zed commit 的 16 条高风险样本完成 Rust 源码、Tree-sitter CST、最小 typed prototype scanner、`scan-result-v1`、评估报告和 CLI 的端到端闭环，使后续扫描器可以在稳定协议上扩展。

## 范围

- 包含：Tree-sitter Rust 依赖与解析边界、canonical span/provenance 原型、`scan-result-v1` schema/模型/I/O/快照校验、评估语义修正、最小 `scan`/`evaluate`/`corpus-check` CLI、聚焦与真实 Zed 验证。
- 包含：若真实 CST 证明风险样本 span 违反 ADR 0005，可在不改变字段结构的前提下校准 corpus v2 数据与 manifest 摘要。
- 影响资产：`pyproject.toml`、`uv.lock`、`src/zed_i18n_kit/`、包内 schema、`scripts/`、`tests/`、`corpus/zed-ui-text/v2/` 和相关开发文档。

## 非目标

- 不实现完整 workspace discovery、通用 import/alias/type resolution 或跨函数调用图。
- 不冻结复杂外部 rule DSL；原型只使用少量强类型内置 sink 规则。
- 不把阶段 1A 的 observational 指标宣称为规则冻结门禁，不把 266 条 `single_review` 样本升级为独立复核。
- 不引入 rust-analyzer，不生成翻译目录，不改写 `local/zed` 或其他 Zed 源码。

## 已确认事实与假设

- 已确认事实：项目最低 Python 版本是 3.13，当前没有运行时依赖，CLI 仍是占位实现。
- 已确认事实：`local/zed` 位于 corpus manifest 指定 commit `2551721adb5b5187bc27cfae0fbe47f0ed4c5397` 且工作树 clean。
- 已确认事实：风险样本 `0251`～`0266` 覆盖 `.child()`、Prompt 多槽位、`format!`/`concat!`、局部变量、helper、错误链、协议内容、多行 raw string 和内嵌 `cfg(test)`。
- 已确认事实：当前 266 条样本均为 `single_review`，auto-confirm coverage 的硬门禁分母为零。
- 假设：官方 `tree-sitter` Python binding 与 `tree-sitter-rust` 可以为这些结构提供稳定 UTF-8 byte range；若不成立，停止扩大 scanner 并记录阻塞证据。

## 验收条件

- [x] `tree-sitter` 与 `tree-sitter-rust` 作为锁定运行时依赖接入，并记录选择原因、许可证和替代边界。
- [x] 16 条真实风险样本均能定位到可解释的 CST node；sink value 使用最小完整表达式作为 primary span，origin 使用结构化 provenance，scope fixture 不被伪装成扫描候选。
- [x] `scan-result-v1` 具有 JSON schema、强类型运行时模型、严格未知/缺失字段拒绝、确定性序列化和 schema drift 检查。
- [x] scan snapshot 记录并验证 Zed commit、工具/rule/config 版本、capability probe、扫描范围和相关文件 SHA-256；同 HEAD 内容漂移会拒绝评分。
- [x] expression-origin 的 null sink/slot 作为不参与匹配的约束；重复匹配报告 ambiguous；corpus 外结果报告 unlabeled occurrence，不计入 unmatched sample。
- [x] 指标新增 auto-confirm coverage；零分母保持 undefined；`single_review` 只产生 observational 结果，不能形成已通过的自动确认门禁。
- [x] `scan` 和 `evaluate` 可从固定 Zed checkout 生成持久结果与报告；同一输入重复扫描得到 byte-for-byte 相同输出。
- [x] 聚焦测试、真实 Zed 阶段 1A 检查、corpus 校验和完整本地门禁通过，`local/zed` 保持 clean。

## 实施步骤

1. 接入解析依赖，使用最小 probe 确认 Rust grammar API 和风险样本 CST 结构。
2. 固定 parser/value-path/provenance 的领域模型与 fixture 断言。
3. 建立 `scan-result-v1` schema、严格 JSON 解析、确定性 I/O 和 snapshot 防护。
4. 按 ADR 0005 修正评估匹配、分类和指标。
5. 实现只覆盖阶段 1A scope 的 typed prototype scanner 与 CLI 闭环。
6. 运行负面控制、真实 Zed 校验、完整门禁和只读复核，收敛后完成工作项。

## 验证证据

| 命令或操作 | 结果与证明 | 限制 |
| --- | --- | --- |
| `uv run python -m pytest -q tests/test_cli.py tests/test_rust_cst.py tests/test_scan_result.py tests/test_evaluation.py tests/test_scanner.py` | passed，32 tests；CLI 写保护、包资源、parser、协议、指标、命名后的报告契约和端到端负面控制 | 不证明完整 workspace 覆盖率 |
| `uv run python scripts/check_scan_evaluation_contract.py --zed local/zed` | passed；16 条 CST fixture、56 条 occurrence、重复输出、round trip、snapshot 和风险样本对齐 | 只覆盖固定 commit 与 4 类原型规则 |
| `uv run python scripts/check_golden_corpus.py --zed local/zed` | passed；266 samples / 40 files，corpus/span/hash 与固定 checkout 对齐 | 不证明 scanner 语义正确 |
| `uv run zed-i18n-kit scan ...`、`evaluate ...`、`corpus-check ...` | passed；生成合法持久 scan-result 与 observational report | 产物写入 `/tmp`，未作为仓库资产提交 |
| `uv build --wheel --out-dir /tmp/zed-i18n-fix-dist` 与解包 wheel 的 `corpus-check` | passed；wheel 携带两个 schema，安装布局不依赖仓库根目录 | 未验证其他平台 wheel 安装 |
| `uv run pytest -q` | passed，41 tests；pytest console 与 module 入口等价 | 不包含真实 Zed checkout 检查 |
| `uv run python scripts/check.py` | passed；Ruff format、Ruff lint、ty、41 tests | 不证明 Zed build 或 UI runtime |
| `git -C local/zed status --short` | clean；外部 checkout 未被修改 | 不证明仓库外副作用 |

## 实施结果

- 新增 `scan-result-v1` schema、严格模型、原子 I/O、源码快照和 evaluation report；`observational_metrics` 与 `independently_reviewed_metrics` 分离，当前后者为 `null`，不暗示门禁已经可用。
- 两个持久 schema 从仓库根 `schemas/` 移至 `src/zed_i18n_kit/schemas/`，作为唯一包资源随 wheel 发布；JSON `$id` 与字段契约不变，但仓库内文件路径不提供兼容别名。CLI 拒绝把 scan/evaluation 输出写入输入 Zed checkout，包括经符号链接解析后落入 checkout 的路径。
- 扫描配置、扫描执行和 CST 校准分别位于 `scan_profiles.py`、`scanner.py` 与 `cst_calibration.py`；生产 API 使用领域名称，不携带阶段编号。
- CST 扫描跳过含 parse error 后代的调用；函数规则按 Rust 路径段边界匹配；测试作用域按 `cfg` 布尔结构识别，并保留 Zed 的 `test-support` 约定。
- 新增 Rust CST 封装、10 文件 typed prototype、函数内有界 provenance 和 16 样本真实校准。`0254`、`0263` 从 `Some(&detail)` 校准为 canonical `&detail`，corpus schema 保持 v2。
- 新增 `scan`、`evaluate`、`corpus-check` CLI 与 `scripts/check_scan_evaluation_contract.py`；所有输入 checkout 操作只读。
- Tree-sitter 选择、MIT License、替代方案和重新评估条件记录于 [ADR 0006](../../decisions/0006-tree-sitter-rust-cst-backend.md)。
- 真实评估覆盖同路径 39 条样本：matched 13、candidate unmatched 21、ambiguous 0、unlabeled 44；precision `5/5`、coverage `0/0` undefined、recall `12/33`。全部 266 条样本仍为 `single_review`，结果不构成规则质量门禁。
- `tree-sitter-rust 0.24.2` 在 `editor.rs` 的合法 `dyn 'static + Fn` 处产生一个 parse error。目标调用不与错误区间相交，scanner 跳过 error subtree，并将完整解析能力记录为 failed capability probe。

## 风险与阻塞

- 风险：Tree-sitter node 对宏 token tree 的结构粒度不足，可能只能稳定提取 macro invocation 与内部字面量，而不能表达宏展开后的调用语义。
- 风险：原型内置规则只为冻结协议，不代表阶段 1B 的通用符号解析质量。
- 阻塞条件：代表样本无法获得稳定 primary/provenance span，或官方 binding 无法在受支持平台可靠加载 Rust grammar。
- 恢复方式：保留 scan-result 和评估不变量，缩小到可验证 CST 结构并记录缺口；不按英文文本、模糊行号或旧 patch 降级。

## 相关决策

- ADR：[0003 扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
- ADR：[0005 阶段 1 先闭合扫描评估协议](../../decisions/0005-phase-1-evaluation-loop-first.md)
