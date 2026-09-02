# 工作项：阶段 1C-C 最终冻结与交付证据

Status: blocked

Blocked by: [阶段 1C-B 真实审核与 scanner/corpus 调整](2026-09-02-phase-1c-b-evidence-calibration.md) 的 external ownership leakage 尚未归零；本工作项不得提前执行。

## 问题

即使 1C-B 产生了真实审核结果，仍需在同一 Zed commit、corpus SHA、scan config、tool/rule-pack version 和最终 audit set 上重跑双证据门禁，才能区分 `reviewing` 与真正 `frozen`，并形成可交付的版本闭环。

## 目标

以 1C-B 产出的最终 corpus review、workspace holdout audit、scan snapshot 和 policy 运行 `freeze-check`，保存自包含报告与失败/通过证据；只有两套证据完整、身份一致、样本/分层/指标和 policy 阈值全部满足时，才允许记录规则 frozen 或进入后续发布决策。

## 范围

- 完成 1C-B 未执行的全部 266 条 corpus blind review、争议重审和 100 条 workspace holdout audit，且审核者独立性、分层覆盖与身份均有记录；
- 核验最终 `phase-1c-unlabeled-final-r3` audit set 与 policy、bundle、result、scan-result 的一致性；
- 运行真实 workspace、corpus、wheel/安装态和完整本地门禁，记录命令、输出摘要、限制和 `local/zed` clean 证据；
- 生成最终 freeze report、版本闭环记录和必要 ADR/开发文档更新；
- 明确失败时保持 `reviewing`，将 findings 退回 1C-B，不绕过 gate。

## 非目标与禁止范围

- 1C-B 未完成或任一独立证据缺失时不得运行最终冻结，不得把 `reviewing` 改写成 `frozen`；
- 不在本工作项内临时修改 scanner、corpus、schema 或阈值来让 gate 通过；新发现必须返回 1C-B，协议变化返回 1C-A；
- 不新增阶段 2 inventory、Message ID、目录、runtime、Overlay 或源码 rewrite；不修改 `local/zed`；
- 不 push、tag、发布或创建 Release，除非另有独立授权。

## 串行验收条件

- [ ] 1C-B 已完成且所有真实审核输入、分歧裁决和 scanner/corpus findings 有可审计证据；
- [ ] 最终 policy、audit bundle/result、review result、scan-result 和 corpus 身份完全一致，audit set 为 `phase-1c-unlabeled-final-r3` 或经明确决策记录的替代版本；
- [ ] `freeze-check` 输出 `freeze_status=frozen`、返回 0，报告同时包含两套证据的样本、reviewer、分类计数、结果对账、指标和 policy；
- [ ] 若 gate 失败，报告准确保持 `reviewing` 并记录可恢复原因；不得通过删样本、放宽阈值或复用旧标签规避；
- [ ] 真实 workspace/wheel/完整门禁证据与限制完整记录，`local/zed` clean；
- [ ] 发布、tag 或 Release 仍需单独授权，本工作项不自动执行。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| 1C-B 完成记录 | blocked | 本工作项启动条件 | 当前不能进行最终冻结 |
| `freeze-check` 使用 r2 scan、但未提供 review/audit result | expected failed；exit 1，`freeze_status=reviewing`，`reviewed=0`、`audited=0`、47 failures；报告身份绑定当前 Zed commit、corpus SHA=`b0ec8dad5dacc601fcd1e38d959b557248d13bacedac1b204690549d26f2ac41` 和 config hash=`5afb6728f7e5496f2b7f87da3601d5ca2cc6db94f34a0f307b8900d340018c92` | 证明无独立证据时门禁 fail-closed，不能把 reviewing 改写为 frozen | 不是最终冻结；仍缺失真实独立 review/audit |
| r2 独立 review/audit | failed and superseded；corpus 分歧和 holdout mismatch 已回流 1C-B | 证明独立审核门禁确实阻止错误冻结 | r2 身份和样本不得作为最终 r3 证据复用 |
| workspace scan/evaluate、wheel smoke、`scripts/check.py` | passed；workspace scan 2698 occurrences / SHA=`1d81f60b75763a42dea76b390f3ad090ed3708b1dcf7c864b43ed5fadfa072f`，evaluate 0 unmatched/ambiguous、precision 1.0、recall 1.0、unsafe 0、leakage 0；wheel 与源码 policy 字节一致；`scripts/check.py` 149 tests | 记录最终身份下的交付回归 | evaluate 仍是 observational；未提供独立 review/audit result，不能通过 freeze |
| `git -C local/zed status --short` | passed；无输出 | 外部 checkout 无修改 | 不替代功能证据 |

## 风险与恢复

- 风险：最终报告若遗漏身份、分母、分类计数或限制，会造成不可复核的伪冻结。
- 阻塞条件：1C-B 未完成、双证据不完整/不一致、gate 失败或发布授权缺失。
- 恢复方式：保持 blocked/reviewing，按报告把问题退回 1C-B；只有重新生成一致证据后再重跑，不修改 `local/zed`。

## 相关决策

- ADR：[0007 阶段 1C 独立审核与规则冻结协议](../../decisions/0007-phase-1c-independent-review-gate.md)
