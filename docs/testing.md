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
- 注释、raw string、转义和多行输入；
- 解析失败必须显式报告，不能返回空 inventory 冒充成功。

### 4.2 Domain rule pack

每条自动确认规则至少包含：

- 明确正例；
- 名称相似但不是 UI 文本的反例；
- 动态值或所有权不明的 review 例；
- 受支持版本能力探测；
- 命中规则和证据的稳定输出。

金标集分别统计：

- 自动确认 precision；
- 总候选 recall；
- excluded 与 review_required 的混淆情况。

指标必须固定样本版本和计算脚本，不能只报告经过挑选的成功案例。

### 4.3 Inventory 与持久 schema

- schema 正反 fixtures；
- 未知字段、缺失字段和非法状态；
- 旧版本读取或明确拒绝策略；
- Message ID 与 Occurrence 身份分离；
- 序列化结果确定性和隐私字段检查。

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
- 生成后 clean-worktree 检查。

### 扫描器阶段

- golden corpus；
- schema 校验；
- precision/recall 阈值；
- 安装 wheel 后的 CLI smoke。

### Overlay 阶段

- pinned Zed commit 集成；
- Linux/macOS/Windows 路径和换行矩阵；
- 派生 workspace Rust 检查；
- 性能基线和 runtime/UI 专用 lane。

昂贵或依赖网络、真实 API、GUI 的验证使用独立 lane，并明确 required、observational 或 manual，不能把跳过写成通过。
