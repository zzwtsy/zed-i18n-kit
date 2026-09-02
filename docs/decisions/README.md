# 架构决策记录

本目录保存会长期影响实现、兼容性或工程流程的 Architecture Decision Record（ADR）。ADR 记录已经做出的决定、真实备选项、代价和重新评估条件，不保存 Agent 的思维过程。

## 何时需要 ADR

- 新增或改变持久化格式、schema 或 Message ID 策略；
- 选择 parser、catalog、runtime 或 Overlay 的核心技术；
- 建立跨模块所有权和兼容性承诺；
- 改变测试、CI、发布或 AI 开发治理；
- 推翻一个仍可能被重新提出的重要方案。

局部实现、机械重构和一次性调查不需要 ADR。

## 文件与状态

文件名使用四位递增编号：

```text
0001-ai-first-engineering-governance.md
0002-tree-sitter-parser-backend.md
```

状态取值：

- `proposed`：待用户确认，不能作为当前实现依据；
- `accepted`：当前有效决定；
- `rejected`：明确拒绝且保留理由有长期价值；
- `superseded by ADR-NNNN`：已经由新决定替代。

不要重新编号。替代旧决定时新增 ADR，并在两份记录中互相链接。

## 格式

使用[_template.md](_template.md)。所有 ADR 至少包含问题、决定或提案、备选方案、后果、验证和重新评估条件。
