# 工作项：阶段 1C-A 审核协议与 fail-closed 门禁

Status: completed

Completed: 2026-09-02

## 问题

阶段 1B 已能对完整生产 Rust workspace 输出确定性 `scan-result`，但 corpus 的 266 条样本仍全部是 `single_review`，workspace 还有 1,736 条 corpus 外 occurrence。已有 observational evaluation 没有防标签泄漏的独立审核输入、审核结果对账和双证据冻结门禁。

## 目标与范围

建立版本化、可复现、身份绑定且 fail-closed 的 blind review、workspace holdout audit 和 freeze gate 基础设施，包括严格 schema、确定性序列化、expected disposition/corpus gap 对账、双证据门禁、策略阈值、CLI、审计分类计数和身份漂移负面测试。

1C-A 不产生独立审核结论，不修改 scanner 规则，不把 provisional policy 当作最终规则冻结；真实 corpus review、workspace holdout audit、scanner/corpus 调整和最终冻结属于后续 1C-B/1C-C。

## 交付结果

- blind review/audit bundle 不泄漏旧标签、scanner disposition、rule ID 或预测，并绑定 commit、corpus、config、tool、rule-pack 和 audit-set 身份；
- review/audit result、occurrence/span、结构完整性、对账和 freeze gate 均 fail closed；freeze gate 的四类审计语义问题由 `maximum_*` policy 控制，发布 policy 仍为零容忍；
- freeze report 输出 review/audit 样本、reviewer、expected disposition counts、audit outcome counts、指标和详细失败原因；无 audit 证据时分类计数全为 0；
- CLI 的 audit 参数组合、`reviewing` 状态和错误返回语义已覆盖正反测试。

## 验收条件

- [x] 1C-A 基础设施、schema、CLI、测试和文档完成；
- [x] 完整门禁和聚焦测试通过，输入 checkout `local/zed` 保持 clean；
- [x] 主 Agent 已完成独立验收；staged patch SHA-256 与验收前一致；
- [ ] 真实独立 corpus review、workspace holdout audit、scanner/corpus 调整和最终冻结报告；这些不属于 1C-A，转由 1C-B/1C-C 串行完成。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| `env UV_CACHE_DIR=/tmp/zed-i18n-uv-cache uv run pytest -q tests/test_unlabeled_audit.py tests/test_freeze_gate.py tests/test_cli.py` | passed，50 tests | 协议、门禁策略、CLI 负面路径和身份漂移 | 不证明真实外部审核 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-uv-cache uv run python scripts/check.py` | passed，Ruff、ty、111 tests | 完整仓库门禁 | 不包含外部独立审核 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-uv-cache uv run ruff format --check .` 与 `git diff --check` | passed | 格式和差异空白 | 不包含外部独立审核 |
| `git diff --cached | sha256sum`、`git -C local/zed status --short` | passed；staged patch SHA-256 `129483a898027a9eecc13d63e647f75e7f506a1d022373b18cd20cd23b296e98`，`local/zed` clean | 暂存区边界和外部 checkout | 不证明 1C-B/1C-C |

## 后续依赖

- [阶段 1C-B 真实审核与 scanner/corpus 调整](../active/2026-09-02-phase-1c-b-evidence-calibration.md)
- [阶段 1C-C 最终冻结与交付证据](../active/2026-09-02-phase-1c-c-final-freeze.md)

## 相关决策

- ADR：[0007 阶段 1C 独立审核与规则冻结协议](../../decisions/0007-phase-1c-independent-review-gate.md)
