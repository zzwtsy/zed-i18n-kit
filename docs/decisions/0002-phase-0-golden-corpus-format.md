# ADR 0002：阶段 0 金标语料格式

Status: superseded by ADR-0004

## 问题

阶段 1 扫描器需要稳定的评估真值，但 Zed UI 文本的判断同时依赖源码位置、sink、表达式结构、文本所有权和文件作用域。若只保存字符串或未来扫描器的 inventory 输出，无法表达同文异义、动态值边界和反例，也会让评估数据与被测实现耦合。

## 决定

阶段 0 使用版本化目录中的 JSONL 保存人工审核样本，并使用单独的 JSON manifest 固定：

- schema version；
- Zed 完整 commit；
- 样本数量与覆盖配额；
- 样本文件的确定性摘要。

每条样本描述一个源码 occurrence，包含稳定的语料内 ID、相对 Zed 路径、行号、精确 source anchor、源码作用域、候选 sink、sink kind、表达式特征、文本所有权、期望决策和审核理由。

期望决策只使用：

- `confirmed`：产品拥有且已确认需要国际化的 UI 文本；
- `review_required`：已到达或可能到达 UI，但所有权、传播或表达式需要人工判断；
- `excluded`：已确认不是生产环境中应由工具国际化的产品文本。

JSONL 是人工审核事实来源，不复制未来 inventory schema。校验器在外部输入边界进行严格运行时验证，不用静态类型注解代替数据校验。

## 备选方案

- 仅保存字符串列表：无法区分相同英文在 ID、日志、用户内容和 UI 中的不同语义。
- 保存完整 Rust 文件快照：可离线复现，但会复制大量上游源码并增加许可证、体积和升级维护成本。
- 直接使用扫描器 inventory：会让金标真值受被测扫描器的数据模型和漏报影响。
- CSV：便于表格编辑，但不适合多值特征、可选 sink 和后续兼容字段。
- 使用行号而不保存 anchor：实现简单，但不能发现错行、重复或 checkout 漂移。

## 后果

- 金标可审计、可 diff，并能独立于扫描器演进。
- 测试需要精确 Zed checkout 才能验证源码 anchor；没有 checkout 时只能验证 schema 和配额。
- 固定 commit 的行号不会跨版本复用；Zed 升级需要显式新建或迁移 corpus version。
- JSONL 的任何兼容变更必须提升 schema version 或提供明确迁移。
- source anchor 只保存必要表达式，不复制完整上游文件。

## 验证

- 严格解析每个 JSONL 对象，拒绝未知字段、非法枚举、空 anchor 和越界行号。
- 验证样本 ID、源码定位和 `(path, line, anchor)` 唯一。
- 在 manifest 指定 commit 上验证每个 anchor。
- 统计 decision、scope、sink kind、feature、ownership 和 crate 分布，并执行覆盖下限。
- 校验规范化 JSONL 的 SHA-256，防止未更新 manifest 的隐式漂移。

## 重新评估条件

- 标注工具需要保留评论线程、多人审核或字段级 provenance。
- 金标规模增长到 JSONL 人工维护明显不可靠。
- 阶段 1 证明现有字段不能计算目标 precision/recall 或表达关键语义。
- 上游正式 i18n API 提供更稳定的消息身份和所有权边界。

## 后续关系

阶段 0 实施后触发了“现有字段不能完整表达评估单元”的重新评估条件。阶段 0.5 的精确 span、provenance 匹配、文本槽位和指标定义由 [ADR 0003](0003-scanner-evaluation-contract.md) 约束；项目随后根据 [ADR 0004](0004-direct-v2-cutover.md) 删除 v1 并直接切换到 v2。
