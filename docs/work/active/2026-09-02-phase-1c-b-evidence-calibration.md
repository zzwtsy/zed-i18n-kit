# 工作项：阶段 1C-B 真实审核与 scanner/corpus 调整

Status: blocked

Blocked by: 最小 rust-analyzer/HIR 来源解析尚未实现；当前 CST scanner 无法在不使用脆弱启发式的情况下把 7 条外部 ownership fixture 与产品动态字段区分。

Depends on: [阶段 1C-A 审核协议与 fail-closed 门禁](../completed/2026-09-02-phase-1c-a-review-gate.md) 已完成；本工作项仍不得进入 1C-C。

## 问题

1C-A 已提供审核协议和门禁，但 corpus 的 266 条样本尚未全部 canonical 化，scanner 仍有表达式宏、局部数据流和跨文件 export 的可验证覆盖缺口。当前 `phase-1c-unlabeled-baseline` 仅是 provisional audit set，不能支撑最终冻结；真实独立审核留给 1C-C。

## 目标

在 1C-A 验收后，将全部 266 条 corpus 样本 canonical 化，受控增强 scanner 并记录预审核 observational 指标；独立审核发现 CST 能力边界时，按 ADR 0007 先补齐最小 HIR 来源解析，再生成新的最终 blind artifacts。

## 范围

- 将 266 条 corpus 样本的 source span、anchor 和 UTF-8 byte offset canonical 化，更新 source hash 与 manifest sample SHA；
- 受控增强表达式宏、局部 provenance、嵌套 builder 和唯一跨文件 export 解析，并为每条自动规则保留正例、易混淆反例和可解释命中证据；
- 重新生成 deterministic scan/evaluation artifacts，记录 candidate unmatched、ambiguous、exclusion leakage、precision、coverage 和 recall 等预审核 observational 指标；
- `phase-1c-corpus-final-r2` / `phase-1c-unlabeled-final-r2` evidence set 审核失败后已废弃；policy 预登记 `phase-1c-corpus-final-r3` / `phase-1c-unlabeled-final-r3` 身份，只有 HIR 修复和预审核门禁通过后才生成对应 blind bundle；
- 最小 HIR 能力只解决类型/字段来源、跨 helper/callback 返回来源和 sink/origin 分离，不扩展到阶段 2 inventory 或完整通用语义后端；
- 不填写独立 reviewer 的 review/audit result；完整审核、争议重审和 100 条 holdout audit 由 1C-C 执行；
- 影响资产必须局限于本项目 corpus、scanner/rule-pack、测试、脚本和文档。

## 非目标与禁止范围

- 在 1C-A 未验收前不得开始；不得在本工作项中填写真实独立 review/audit result，也不得复用旧标签、scanner prediction 或同一作者的非独立判断冒充审核结果；
- 不得修改 blind/result schema、freeze gate 语义或把 `maximum_*` 阈值改成掩盖 findings 的方式；协议变化必须回到新的 1C-A 工作项；
- 不得复用已废弃 evidence set 的 review/audit result，或在 HIR 修复前把 policy 预登记的候选身份标记为 `frozen`；
- 不引入阶段 2 persistent inventory、Message ID、Overlay、runtime、目录或源码 rewrite；不修改 `local/zed`。

## 串行验收条件

- [x] 1C-A 已完成代码、schema、测试和文档复核，`local/zed` 状态保持不变；
- [x] 全部 266 条 corpus 样本通过 canonical CST/span/UTF-8 校验，source hash 与 manifest sample SHA 一致；
- [x] 受控 scanner 增强覆盖目标表达式和唯一 export，未知宏/复杂跨函数传播保持 candidate 或 `review_required`，不误升 exact；
- [x] 每项 scanner/corpus 修正都有可复现 fixture、正例、易混淆反例和命中证据，且未改变 1C-A 的协议边界；
- [x] 预审核 workspace scan/evaluate 已生成并如实记录指标，deterministic scan 重复输出 byte-for-byte 一致；
- [x] `phase-1c-corpus-final-r2` 的 266 条 corpus review 与 `phase-1c-unlabeled-final-r2` 的 100 条 holdout audit 已执行并准确记录失败，不复用为最终证据；
- [x] `.child(match ...)` element 误报和纯格式控制字面量已由通用规则修复，并有正反回归测试；
- [ ] 7 条 external ownership exclusion leakage 尚未归零；最小 HIR 来源解析是当前阻塞项；
- [ ] policy 预登记身份对应的全量 review、新 100 条 holdout audit 和最终冻结尚未执行，不能声称完成。

## 验证证据

| 命令或操作 | 状态 | 证明内容 | 限制 |
| --- | --- | --- | --- |
| [1C-A 完成记录](../completed/2026-09-02-phase-1c-a-review-gate.md) | passed | review/audit 协议、freeze gate、schema、CLI 和测试已经复核 | 不等于真实 review/audit 已完成 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run python scripts/check_golden_corpus.py --zed local/zed` | passed；266 条样本通过 canonical CST/span/UTF-8 与 checkout 校验；计数为 `confirmed=147`、`review_required=55`、`excluded=64`、`candidate=202`、`not_candidate=64`；manifest sample SHA=`b0ec8dad5dacc601fcd1e38d959b557248d13bacedac1b204690549d26f2ac41` | 真实 checkout 上全部 corpus 定位、源文件摘要和覆盖配额一致 | 尚无独立 review result |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run python scripts/check_scan_evaluation_contract.py --zed local/zed`（两次） | passed；两次均验证 16 条 CST fixtures 与 64 条 deterministic occurrences，`matched=20`、`unmatched=14`、`ambiguous=0`、`unlabeled=48`；`0259` 的 not-candidate/excluded 语义通过 calibration contract；唯一允许的 `prototype-error-free-parse` probe failure 保持不变 | 真实 16 条 CST fixture、扫描确定性和候选/排除回归闭环 | 固定 1A profile 的 observational unmatched 不代表 workspace corpus 门禁；不证明独立审核结果 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run pytest -q tests/test_golden_corpus.py tests/test_scanner.py tests/test_cst_canonical.py tests/test_freeze_gate.py` | passed；72 tests | corpus 身份、canonical CST、scanner 语义修正和冻结 policy 身份回归 | 不证明真实独立审核 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run pytest -q` | passed；149 tests | 完整 Python 测试回归 | 不替代真实独立审核与 workspace contract finding |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run python scripts/check.py` | passed；Ruff format、Ruff lint、ty、pytest（149 tests）全部通过 | 完整本地代码门禁 | 不替代真实 checkout contract finding 或独立审核 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run zed-i18n-kit scan --zed local/zed --output /tmp/zed-i18n-scan-final-r2.json`（重复生成并 `cmp`） | passed；两次均 `2698` occurrences，输出逐字节一致；SHA-256=`1d81f60b75763a42dea76b390f3ad090ed3708b1dcf7c864b43ed5fadfa072f`；config hash=`5afb6728f7e5496f2b7f87da3601d5ca2cc6db94f34a0f307b8900d340018c92`；仅 `prototype-error-free-parse` failed | 固定 checkout 上 workspace scan 的确定性、最终 rule/config/probe 身份和 occurrence 数量 | parse probe 失败范围仍是 ADR 0006 已记录的目标调用外 grammar 缺口 |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run zed-i18n-kit evaluate --corpus corpus/zed-ui-text/v2 --scan-result /tmp/zed-i18n-scan-final-r2.json --zed local/zed --output /tmp/zed-i18n-evaluation-final-r2.json`（重复生成并 `cmp`） | passed；242 evaluated、0 unmatched、0 ambiguous、2497 unlabeled；precision `123/123=1.0000`、coverage `undefined (0/0)`、candidate recall `202/202=1.0000`、unsafe `0/55=0.0000`、leakage `0/40=0.0000`；两次输出 SHA-256=`a39dbfe5d7af8c70e3d46ae76945e5c9116063db3130b1846ef2e84a69136d63` | 预审核 observational 指标与 corpus 外 occurrence 分离记录 | coverage 分母为零，不能作为 independently reviewed 门禁通过；没有 independently reviewed metrics |
| `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run zed-i18n-kit review-export --corpus corpus/zed-ui-text/v2 --zed local/zed --review-set phase-1c-corpus-final-r2 --output /tmp/zed-i18n-review-bundle-final-r2.json` 与 `env UV_CACHE_DIR=/tmp/zed-i18n-kit-uv-cache uv run zed-i18n-kit audit-export --corpus corpus/zed-ui-text/v2 --scan-result /tmp/zed-i18n-scan-final-r2.json --zed local/zed --audit-set phase-1c-unlabeled-final-r2 --sample-size 100 --output /tmp/zed-i18n-audit-bundle-final-r2.json`（各重复生成并 `cmp`） | passed；review bundle 266 条，SHA-256=`b3246a829a7bbdcbaed1ffa2ef82b7f543b66cb580b289d195fbc703c224ab44`；audit bundle 100 条、覆盖 57 个 corpus 外路径，SHA-256=`248a1b72851320ce42b8e5ac7921a28c2c364626d54bee9dc1965aba75e75c08`；身份分别为 `phase-1c-corpus-final-r2` / `phase-1c-unlabeled-final-r2` | 绑定最终 corpus/commit/config/tool/rule 身份的 blind 输入具备确定性且不泄漏标签或预测 | 未填写 review/audit result，不能运行成功的 freeze gate |
| `git diff --check`、`git -C local/zed status --short` | passed；差异无空白错误，`local/zed` 无输出 | 文本格式和输入 checkout 状态 | 不证明功能或审核协议已完成 |
| 候选 blind audit bundle 生成 | passed；已生成并重复比较 `phase-1c-unlabeled-final-r2` bundle | 为 1C-C 准备不泄漏预测的审核输入 | 不包含独立审核 result |
| `uv build` 与 wheel 内容检查 | passed；生成 `zed_i18n_kit-0.1.0` sdist/wheel，wheel 包含最终 freeze policy（与源码字节一致）、6 个 schema、CLI、`cst_canonical.py`、`rust_macros.py` 和 scanner | 构建产物携带阶段 1C 所需持久资源 | 未创建隔离环境执行安装态 CLI；1C-C 仍因独立审核缺失而 blocked |
| `phase-1c-corpus-final-r2` 独立 corpus review / dispute adjudication | failed；266/266 完成，首次对账 `agreed=246`、`disputed=20`、`missing=0`；与初审隔离的裁决者复核 20 条，确认协议、身份、用户和诊断来源标签需要修订 | 首次获得不读取旧标签或 scanner prediction 的真实分歧证据 | corpus 改动使该 evidence set 身份失效，必须按 policy 身份完整生成新 review，不能合并局部结果 |
| `phase-1c-unlabeled-final-r2` 独立 holdout audit | failed；100/100 完成，`agreement=85`、`conservative_review=5`、`unsafe_promotion=0`、`corpus_gap=0`、`indeterminate=0`、candidate/excluded mismatch=10 | 证明无 unsafe promotion，同时发现 2 个 scanner 缺陷、2 条技术名词误分类和 6 个待裁决语义边界 | 该 audit set 已废弃，不得复用为最终 holdout |
| `.child(match ...)` 与格式控制修复后的聚焦验证 | passed；`pytest -q tests/test_scanner.py tests/test_golden_corpus.py tests/test_freeze_gate.py` 为 48 passed；266 条 canonical corpus 计数为 `confirmed=149`、`review_required=46`、`excluded=71`、`candidate=195`、`not_candidate=71` | 通用 element-return 与格式控制规则具备正反回归，corpus 新 SHA 为 `41525aceed25c0cf857d8e91335b5af2552f357c2d638d1103d1fb4d6c015bef` | 尚未解决 external ownership |
| policy 候选身份的预审核 scan/evaluate | failed gate；scan 为 2695 occurrences、0 unmatched、0 ambiguous；precision `123/123=100%`、candidate recall `195/195=100%`、unsafe `0/46`，但 exclusion leakage 为 `7/47=14.89%`；`check_scan_evaluation_contract.py` 首先报告 `zed-2551721-0254: exclusion fixture leaked as review_required` | 定量证明当前主要矛盾是外部来源 ownership，不是 span、recall 或 unsafe promotion | 未达到零 leakage，按 ADR 0007 停止生成最终 bundle/audit，转入最小 HIR 修复 |

## 风险与恢复

- 风险：按变量名、路径或精确字符串补 exclusion 会压低表面 leakage，却把产品动态字段静默排除，形成不可见漏报。
- 阻塞条件：当前 7 条 exclusion fixture 均被预测为 `review_required`；同一 sink occurrence 还可能同时对应 mixed sink 与 excluded origin，单一 CST disposition 无法表达。
- 恢复方式：实现 ADR 0007 限定的最小 HIR 来源解析和 sink/origin 分离，重新计算 config hash、scan 与 observational 指标；只有 leakage=0 后才按 policy 预登记身份生成 blind bundle 并启动全量独立审核。

## 下一依赖

- 完成后才能进入：[阶段 1C-C 最终冻结与交付证据](2026-09-02-phase-1c-c-final-freeze.md)
