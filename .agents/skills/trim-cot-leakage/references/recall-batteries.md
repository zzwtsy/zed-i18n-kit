# Recall probes

这些命令用于发现候选，不定义泄漏。每个命中都要结合语境判断，而且模式天然会漏报；运行后还必须无模式阅读范围内文字最密集、契约最重要的部分。

## 使用规则

- 将命令中的 `path/to/scope` 替换为用户明确指定的文件或目录，不默认扫描整个仓库。
- 使用 `--hidden --glob '!.git/**'`，让项目级 `.agents/` 可被审计。
- 排除规则放在所有 include globs 之后，避免后续 include 重新纳入排除目录。
- 默认排除 `.venv/`、`local/zed/`、`.worktrees/`、`artifacts/`、`corpus/`、fixtures、snapshots 和本 skill；明确任务需要检查某类受控数据时，先确认其所有者和生成方式。
- 每组模式先验证能命中一个已知正例，并拒绝一个近似反例。零命中只有在校准成功后才有意义。
- 不对命中结果做批量替换；先分类为 `keep`、`restate`、`delete` 或 `defer`。

下面命令中的通用排除尾部为：

```sh
--glob '!.git/**' \
--glob '!.venv/**' \
--glob '!local/zed/**' \
--glob '!.worktrees/**' \
--glob '!artifacts/**' \
--glob '!corpus/**' \
--glob '!tests/fixtures/**' \
--glob '!tests/golden/**' \
--glob '!**/__snapshots__/**' \
--glob '!.agents/skills/trim-cot-leakage/**'
```

## 英文候选

逐条执行，并在末尾追加通用排除尾部：

```sh
rg -n --hidden '\(decision [0-9]|\(audit [A-Z][0-9]|design §|plan §|design ledger|\bP-I\b|\bW[0-9]\b|\bT[0-9]\b' path/to/scope
rg -n --hidden -i '\bthis PR\b|\bthis branch\b|\bthis stack\b|\blater PRs?\b|\bprevious commits?\b|\bthis commit\b' path/to/scope
rg -n --hidden -i '\bused to\b|\bno longer\b|\bpreviously\b|\bthe old\b|\bwas renamed\b|\bwas moved\b' path/to/scope
rg -n --hidden -i '\bv1\b|this cut|\bcut [0-9]|\bfor now\b|roadmap' path/to/scope
rg -n --hidden -i 'rejected in review|review round|reviewer|as of v[0-9]' path/to/scope
rg -n --hidden -i 'probably |should be enough|should suffice|it simply|is safe —|is safe --' path/to/scope
rg -n --hidden '§[0-9]' path/to/scope
```

## 中文候选

逐条执行，并在末尾追加通用排除尾部：

```sh
rg -n --hidden '决策[[:space:]]*[A-Z0-9]+|审计项|设计稿?第[0-9]+|计划第[0-9]+|阶段[[:space:]]*[A-Z0-9]+' path/to/scope
rg -n --hidden '本次[[:space:]]*(PR|提交|改动)|上一?轮[[:space:]]*(评审|review)|前一个[[:space:]]*commit|后续[[:space:]]*PR' path/to/scope
rg -n --hidden '以前|曾经|旧版|老的|不再|现已|现在改为|本版|本次切片' path/to/scope
rg -n --hidden 'reviewer|评审者|评审确认|评审认为|评审要求|这是正确的|这是安全的' path/to/scope
rg -n --hidden '暂时应该|目前应该|应该够用|大概可以|暂时没问题|以后再|后续再说' path/to/scope
rg -n --hidden '私有|草稿|TODO[：:]?[[:space:]]*(fix|处理|以后)' path/to/scope
```

## 常见误报

- `v1` 是 schema、protocol、corpus 或路径的稳定标识符。
- “旧 inventory/新 inventory”描述同一次运行中的对象生命周期。
- completed 工作项、ADR、postmortem 正在记录其文体允许的历史证据。
- 开发流程文档在一般意义上讨论 PR、review 和 commit。
- `§N` 指向当前 checkout 中存在的章节，或 RFC 等固定外部标准。
- “今天”用于自然时间或已记录命令输出，而不是表示仓库版本。
- issue 编号、真实 `TODO` 退出条件和精确文件路径可以由当前 checkout 解析。
- 本 skill 的示例会故意包含泄漏措辞，因此默认排除自身。
