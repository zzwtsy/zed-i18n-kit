# 测试策略

## 1. 目标

测试必须证明声明的行为，而不仅是提高覆盖率。每项证据都要说明适用范围；绿色单元测试不能替代真实 Zed build、runtime trace 或 UI smoke。

## 2. 证据层级

| 层级 | 证明内容 | 不能单独证明 |
| --- | --- | --- |
| 静态检查 | 格式、lint、类型和结构约束 | 运行时行为 |
| 单元测试 | 纯函数、模型和局部边界 | 模块集成与真实源码兼容 |
| Golden/fixture | 对固定 Rust 输入的解析、分类和输出 | 未收录的 Zed 结构 |
| 集成测试 | 多模块协议、文件 I/O 和 CLI 生命周期 | 真实完整 Zed workspace |
| Zed build | 当前 commit 能编译并通过目标测试 | UI 可见性和布局 |
| Runtime/UI | 文本实际渲染、覆盖、交互和布局 | 未执行的其他路径 |

最终交付使用达到风险所需的最低充分证据，不默认运行所有昂贵检查。

## 3. 基础门禁

所有 Python 代码和工程配置变更运行：

```bash
uv run python scripts/check.py
```

仅文档修改至少运行：

```bash
git diff --check
```

并检查新增或修改的相对链接。基础 CI 当前在 Ubuntu 上运行统一门禁；跨平台矩阵在路径处理和 Overlay 能力进入实现时再增加。

## 4. 领域测试要求

### 4.1 Parser

- Rust 字面量、宏、方法调用、`let`、`if` 和 `match` fixtures；
- 不完整源码的错误恢复；
- UTF-8 字符之前和之中的 byte range；
- 注释、raw string、转义、多行输入和同一行多个候选；
- 内嵌 `#[cfg(test)]`、component preview 和生成边界的作用域识别；
- 解析失败必须显式报告，不能返回空 inventory 冒充成功。

### 4.2 Domain rule pack

每条自动确认规则至少包含：

- 明确正例；
- 名称相似但不是 UI 文本的反例；
- 动态值或所有权不明的 review 例；
- 受支持版本能力探测；
- 命中规则和证据的稳定输出。

一个 sink 存在多个文本槽位时，每个槽位分别测试。至少覆盖：

- Button 的 identity 与 visible label；
- Prompt 的 message、optional detail 和 answers 集合；
- Toast message 与后续 action label；
- value path 不匹配时只禁用受影响槽位，不误用相邻参数。

金标集分别统计：

- auto-confirm precision：预测为 `confirmed` 的样本中，金标允许自动确认的比例；
- candidate recall：应发现的样本中，被输出为 `confirmed` 或 `review_required` 的比例；
- unsafe promotion rate：金标要求审核但被预测为 `confirmed` 的比例；
- exclusion leakage：金标应排除但被输出为候选的比例；
- unmatched count：无法按精确 source span 或 provenance 对齐的样本数量。

指标必须固定样本版本、Zed commit、扫描配置和计算脚本。corpus 阈值是回归门禁，不得表述为完整 Zed workspace 的统计准确率。规则冻结后，从未参与调优的新路径和高风险结构中抽样，独立审计误报与漏报；两类结果分别报告。

评估输入必须满足以下要求：

- 相关 Zed 路径工作树干净，或 scan snapshot 记录并验证文件内容摘要；
- anchor 在声明范围内唯一，评估匹配使用精确 UTF-8 byte range；
- expression origin 只能通过结构化 provenance 匹配，不能按英文文本模糊关联；
- 重复匹配、span 失效和 provenance 缺失产生明确失败或 unmatched；
- corpus schema 与 Python 运行时模型存在自动漂移检查。

### 4.3 Inventory 与持久 schema

- schema 正反 fixtures；
- 未知字段、缺失字段和非法状态；
- 旧版本读取或明确拒绝策略；
- Message ID 与 Occurrence 身份分离；
- 序列化结果确定性和隐私字段检查。
- 阶段 1 `scan-result` 不携带已冻结 Message ID 或跨版本审核状态；阶段 2 persistent inventory 才建立这些承诺。

### 4.4 Rewrite

- byte-range edits 正确且不重叠；
- UTF-8 byte offset 与换行保持；
- 源文件 hash 和 AST fingerprint 失败保护；
- 第二次运行零变化；
- 低置信候选不被修改；
- 原版输入 checkout 内容和 Git 状态保持不变。

每个重要保护都应有负面控制：移除或绕过保护时，测试必须能够失败。

### 4.5 版本对账

使用至少两个真实、精确的 Zed commit fixtures 验证：

- `unchanged`
- `moved`
- `source_changed`
- `added`
- `removed`
- `sink_changed`
- `ambiguous`

一次消失只产生 `possibly_obsolete`；不唯一匹配必须进入人工审核。

### 4.6 Runtime template 与 Overlay

- 派生工作区和输入 checkout 路径不同；
- 同一输入、规则、迁移计划和目录生成等价文件树；
- `cargo fmt --check`、目标 crate `cargo check` 与相关测试；
- runtime 变化记录 binary size、内存和代表性 frame time；
- pseudolocale 检查漏翻、文本扩张、截断和辅助功能标签；
- runtime trace 不持久化用户、协议或第三方原始正文。

## 5. 测试设计

- 测试名称描述行为和条件，不写“works”或“correct”。
- 优先比较完整领域对象或稳定序列化输出。
- fixtures 记录来源 commit 和最小化理由。
- 不依赖固定本机路径、端口、时区或未声明环境变量。
- 不使用无界 sleep 等待异步状态；等待可观察条件并设置总超时。
- flaky 测试先分类根因，不通过盲目重试或放宽断言掩盖。
- 修改已有行为时同步修改或删除已过时测试，并在工作项说明原因。

## 6. CI 演进

### 当前阶段

- Ruff format；
- Ruff lint；
- ty；
- pytest；
- corpus v2 schema 与运行时模型漂移检查；
- 生成后 clean-worktree 检查；
- 在固定 Zed checkout 可用时执行 corpus 源码摘要/span 校验。

### 扫描器阶段

- 精确 checkout、source span 与 provenance 匹配；
- auto-confirm precision、candidate recall、unsafe promotion、exclusion leakage 和 unmatched 门禁；
- 独立抽样审计报告；
- 安装 wheel 后的 CLI smoke。

### Overlay 阶段

- pinned Zed commit 集成；
- Linux/macOS/Windows 路径和换行矩阵；
- 派生 workspace Rust 检查；
- 性能基线和 runtime/UI 专用 lane。

昂贵或依赖网络、真实 API、GUI 的验证使用独立 lane，并明确 required、observational 或 manual，不能把跳过写成通过。
