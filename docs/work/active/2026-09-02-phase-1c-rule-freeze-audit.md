# 工作项：阶段 1C 规则冻结与独立审计

Status: active

## 问题

阶段 1B 已能对完整生产 Rust workspace 输出确定性 scan-result，但 266 条 corpus 样本全部仍是 `single_review`。当前评估只有 observational metrics，缺少防标签泄漏的独立审核输入、审核结果校验、分层覆盖检查和规则冻结门禁；1,736 条 corpus 外 occurrence 也尚未形成可复现的审计样本。

## 目标

建立可审计且 fail-closed 的阶段 1C 闭环：独立审核者在看不到现有标签和扫描预测的输入上复核样本，机器校验审核结果与 corpus/checkout 身份，对满足分层覆盖的 independently reviewed 子集执行冻结门禁，并单独记录未标注 occurrence 审计结论。

## 范围

- 包含：
  - 确定性 blind review bundle 与严格 review result 协议；
  - independently reviewed 分层覆盖和 precision、coverage、recall、安全门禁；
  - corpus 外 occurrence 的确定性分层抽样与审计分类；
  - 首批 `zed-builtin-v1` 规则的 tested commit、capability 和冻结状态；
  - CLI、schema、正反测试和真实 workspace 证据。
- 影响资产：`src/zed_i18n_kit/`、`src/zed_i18n_kit/schemas/`、`rules/`、`tests/`、`docs/`。

## 非目标

- 不伪造或自动推断独立审核结论；
- 不建立阶段 2 persistent inventory、Message ID 或版本对账；
- 不实现 Overlay、runtime、目录或源码 rewrite；
- 不因 parse probe 失败而修改或预处理 `local/zed`；
- 不在独立证据不足时引入 rust-analyzer/HIR sidecar。

## 已确认事实与假设

- 已确认事实：corpus v2 有 266 条样本，`review_state` 全部为 `single_review`。
- 已确认事实：当前 workspace scan 为 1,642 files / 1,880 occurrences；评估为 242 evaluated / 58 unmatched / 0 ambiguous / 1,736 unlabeled。
- 已确认事实：observational candidate recall 为 144/202（71.29%），不满足设计中的 95% 质量目标。
- 已确认事实：`tree-sitter-rust 0.24.2` 在 14 个生产文件产生 26 个 error node；当前只有属性 token 中的 `allow(...)` 调用被跳过，没有与首批 UI sink 相交。
- 待验证假设：盲审结果能够以足够样本量覆盖 disposition、ownership、subject kind、feature 和首批 rule，不需要变更 corpus v2 样本字段。

## 验收条件

- [x] blind review bundle 不包含现有 expected label、review state、rationale 或扫描预测，并绑定完整 Zed commit 与 corpus sample SHA-256；
- [x] review result 严格拒绝未知字段、重复/未知 sample ID、身份漂移、非法 presence/disposition 组合和空审核理由；
- [x] independently reviewed 门禁报告样本量、分母、分层覆盖和每项失败原因；零分母、样本不足、unmatched、ambiguous 或 unsafe promotion 均不能通过；
- [x] unlabeled audit 从未参与调优的路径优先生成确定性分层样本，并区分合法发现、误报和 corpus gap；
- [x] `zed-builtin-v1` 冻结元数据绑定精确 tested commit、tool version、config hash、rule IDs 和 capability probes；
- [x] 真实 baseline 在没有独立审核结果时明确失败关闭，不被表述为冻结成功；
- [x] 聚焦测试、阶段 1A/1B 回归、完整门禁和安装态 CLI 通过，`local/zed` 保持 clean。

## 实施步骤

1. 冻结 blind review 与 review result 的持久协议和身份不变量。
2. 实现 review bundle 生成、严格读取和独立结论对账。
3. 实现分层覆盖、规则冻结 policy 和 fail-closed gate。
4. 实现 unlabeled occurrence 分层抽样与审计报告。
5. 生成真实 baseline 审核包，完成独立复核后按 findings 修正规则。
6. 运行真实 workspace、wheel 和完整门禁，记录证据并决定 Tree-sitter 后端是否需要重新评估。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `uv run python scripts/check.py` | baseline passed，54 tests | 阶段 1C 修改前仓库基线 | 不证明新协议 |
| `uv run zed-i18n-kit corpus-check --zed local/zed` | baseline passed，266 samples | corpus 与固定 checkout 身份一致 | 全部样本仍是 single review |
| workspace `scan` 与 `evaluate` | baseline recorded，1,880 occurrences；0 independently reviewed | 当前规则和评估输入 | observational，不构成冻结证据 |
| `review-export` 两次生成并 `cmp` | passed，266 samples，SHA-256 `881f3f8b...458f6` | byte-for-byte 确定性；字段扫描未发现旧标签、rationale、review state 或 prediction | 尚无独立审核结论 |
| `audit-export` 两次生成并 `cmp` | passed，100 samples，SHA-256 `283cfd17...0e8e3` | byte-for-byte 确定性；100 条均来自 corpus 未覆盖路径，覆盖 60 个路径 | 尚无 audit result |
| `freeze-check --zed local/zed`（无 review result） | expected failed，exit 1，`reviewing`，0 reviewed，37 failures | 完整 snapshot 校验后仍对证据不足 fail-closed | 不是规则冻结成功 |
| `uv build --out-dir /tmp/...` 与 wheel CLI `--help` | passed | wheel 包含 rule policy、四个 schema 和 8 个 CLI 命令 | 仅 smoke，不替代真实审核 |
| `uv run python scripts/check.py` | passed，84 tests | Ruff format、Ruff lint、ty 与完整 pytest | 不包含外部独立审核 |
| `scripts/check_golden_corpus.py` 与 `scripts/check_scan_evaluation_contract.py` | passed | 266 条 corpus、16 个 CST fixture 和 56 条阶段 1A occurrence 回归 | parse probe 仍按 ADR 0006 如实 failed |

## 风险与阻塞

- 风险：审核输入泄漏现有标签或预测会让“独立”结论失真；bundle 必须通过负面测试固定字段边界。
- 风险：小样本直接套用 99% 阈值会制造虚假统计确定性；gate 必须同时要求最小分母和分层覆盖。
- 风险：按当前错误定向抽样会污染独立审计；unlabeled 抽样策略必须在修规则前冻结并确定性复现。
- 阻塞条件：没有独立审核者提供满足 schema 的结论，或审核分歧未裁决；此时基础设施可以完成，但规则不得标记 frozen。
- 恢复方式：保留 active 状态和生成的 blind bundle，由新的只读审核上下文完成结论后继续，不降级为复用现有标签。

## 相关决策

- ADR：[0003 扫描器评估单元与文本槽位](../../decisions/0003-scanner-evaluation-contract.md)
- ADR：[0005 阶段 1 先闭合扫描评估协议](../../decisions/0005-phase-1-evaluation-loop-first.md)
- ADR：[0007 阶段 1C 独立审核与规则冻结协议](../../decisions/0007-phase-1c-independent-review-gate.md)
