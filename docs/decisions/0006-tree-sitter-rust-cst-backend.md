# ADR 0006：阶段 1 使用 Tree-sitter Rust 作为 CST 后端

Status: accepted

## 问题

阶段 1A 需要在固定 Zed commit 上提供稳定的 UTF-8 byte range、调用与表达式结构，并保留宏 token tree，作为 `primary_span`、`provenance` 和后续源码改写的语法基础。项目需要选择一个能由 Python CLI 直接调用、可锁定版本且不会要求构建完整 Zed workspace 的 Rust 解析后端。

## 决定

- 使用官方 `tree-sitter` Python binding 和 `tree-sitter-rust` grammar 作为阶段 1 的 CST 后端；当前 lock 解析为 `tree-sitter 0.26.0` 与 `tree-sitter-rust 0.24.2`。
- 两个包的发布 metadata 均声明 MIT License；`pyproject.toml` 保存最低兼容版本，`uv.lock` 固定实际解析版本。
- Tree-sitter 只负责保真语法结构和 byte range。GPUI sink 身份、文本槽位、所有权和处置仍由 typed domain rules 与保守数据流分析决定。
- parser error 必须进入 capability probe；扫描器只能消费不在 error subtree 内的调用，不能用空结果掩盖解析缺口。
- 阶段 1A 的内置规则只声明固定 commit 上已验证的语法能力，不声明任意版本区间兼容，也不冻结外部 rule DSL。

## 备选方案

- `syn` sidecar：Rust AST 质量高，但需要新增 Rust 构建、进程协议和发布矩阵；对当前 16 个协议校准样本没有必要。
- rust-analyzer/HIR sidecar：能提供更强的类型和符号信息，但启动、缓存、版本耦合和资源成本显著更高，应由真实金标缺口触发，而不是阶段 1A 预先引入。
- 正则或文本搜索：实现成本低，但无法可靠表达嵌套槽位、最小完整表达式、宏与 UTF-8 byte range，因此不满足 scan-result 和 rewrite 的身份要求。

## 后果

- Python 工具新增两个运行时依赖，并需要在受支持平台验证 native wheel/grammar 加载。
- 固定 Zed commit 的 16 个风险样本均可定位到可解释的 CST node；Prompt `Some(&detail)` 的 canonical primary 被校准为内部 `&detail`。
- `tree-sitter-rust 0.24.2` 无法完整解析当前 `editor.rs` 中合法的 `dyn 'static + Fn` 类型写法。该错误位于目标调用之外，阶段 1A 将它记录为 failed error-free-parse probe，并继续扫描不相交的安全子树；这不等于完整文件语义可用。
- `format!` 内嵌 `concat!` 的内容主要表现为 macro token tree，原型只能保存 macro 与内部字面量 provenance，不能把它当作宏展开或类型解析结果。

## 验证

- 合成 fixture 验证 UTF-8 byte range、parse error 暴露、`#[cfg(test)]` 和 `mod tests` 分类。
- 固定 Zed commit 的 16 个风险样本验证 exact/smallest-containing node 类型与 canonical span。
- 同一 checkout 扫描两次必须产生 byte-for-byte 相同的 `scan-result-v1`。
- capability probe 必须记录 binding/grammar 版本和所有 parse error byte range。

## 重新评估条件

- parse error 与候选调用相交，无法通过保守跳过维持正确性；
- 独立审计证明 receiver/type、宏展开或跨函数语义缺口使 Tree-sitter 方案无法达到阶段 1C 门禁；
- Python binding 在任一受支持平台缺少可靠 wheel 或出现无法规避的稳定性问题；
- Zed 上游提供可直接消费的国际化 metadata 或正式提取接口。
