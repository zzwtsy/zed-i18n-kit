# zed-i18n-kit Agent 开发契约

## 项目目标

本项目针对原版 Zed 源码提供国际化语义分析、版本差异对账和临时源码 Overlay。永久资产属于本仓库；修改后的 Zed 源码只存在于显式派生工作区。

开始非平凡修改前，先阅读：

1. `docs/zed-gpui-i18n-design.md`
2. `docs/ai-workflow.md`
3. `docs/development.md`
4. `docs/testing.md`
5. 当前 `docs/work/active/` 工作项（如果存在）

## 权威来源

- 产品与架构边界：`docs/zed-gpui-i18n-design.md`
- AI 开发生命周期：`docs/ai-workflow.md`
- 本地命令与目录约定：`docs/development.md`
- 测试层级与证据要求：`docs/testing.md`
- 长期技术决策：`docs/decisions/`
- 当前任务范围与验收：`docs/work/active/`

聊天记录、旧 patch、临时报告和生成后的 Zed 源码都不是长期权威来源。

## 硬边界

- `local/zed` 是外部上游 checkout，默认只读。不得在其中提交、暂存或长期保存国际化改动。
- 工具生成的 Zed 修改只能写入用户显式指定且不同于输入 checkout 的派生目录。
- 不维护长期 Zed fork，不把旧 patch 当作新版 Zed 的迁移输入。
- 不提交密钥、用户内容、未经脱敏的 runtime trace、临时构建树或本机路径。
- 保留用户已有的暂存和未暂存修改；不使用破坏性 Git 命令清理工作树。

## 工作方式

- 修改前检查 `git status --short`，确认目标文件及已有改动。
- 非平凡功能、格式、兼容性或流程变更需要工作项；长期决策同时需要 ADR。
- 一个代码实施切片只解决一个可独立验收的问题。避免顺手重构和无关格式化。
- 未经用户明确要求，不提交、push、发布、创建 Release 或修改外部仓库。
- 先运行聚焦检查，再运行与变更风险匹配的完整门禁。
- 失败、阻塞和未运行的检查必须如实报告，不能用静态推断代替运行证据。

## 实现规则

- Python 最低版本以 `pyproject.toml` 为准，使用标准库和现有依赖优先。
- 公共 API、持久化 schema、Message ID、rule pack 和迁移格式的变化必须说明兼容影响。
- 每条自动语义规则至少包含正例、易混淆反例和可解释的命中证据。
- 自动 rewrite 必须验证 byte range、edit 不重叠、UTF-8、hash/fingerprint 防护与幂等性。
- 生成文件必须标注来源，并提供 `--check` 或等价漂移检查；不要手工修改生成物。
- 注释解释非显然原因、不变量和兼容约束，不复述代码。

## 验证命令

完整本地门禁：

```bash
uv run python scripts/check.py
```

按需运行聚焦命令：

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

仅文档修改至少执行 `git diff --check` 并检查相对链接。涉及规则、rewrite、版本对账、runtime template 或 Overlay 时，遵循 `docs/testing.md` 中对应证据要求。

## 交付说明

最终说明必须包含：

- 实际改变的行为和文件；
- 关键设计选择及兼容性影响；
- 运行过的精确验证命令和结果；
- 未运行的检查、原因和剩余风险；
- Git 状态中与本次任务相关的暂存/未暂存边界。
