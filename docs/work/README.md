# 工作项规范

工作项是非平凡任务的可执行契约，记录问题、范围、验收和真实验证结果。它不是每日进度日志，也不保存 Agent 推理过程。

## 目录与命名

```text
docs/work/
├── active/
├── completed/
├── cancelled/
└── _template.md
```

目录随第一个对应工作项创建，不使用空占位文件。文件名使用：

```text
YYYY-MM-DD-short-slug.md
```

## 状态

- `proposed`：范围或验收尚未确认；
- `active`：当前正在实施；
- `blocked`：已尽安全检查但依赖外部决定或状态；
- `completed`：所有验收条件都有证据；
- `cancelled`：明确停止且记录原因；
- `superseded`：由另一个工作项替代。

状态与目录必须一致。`blocked` 可以暂留 `active/`，但要写清阻塞条件和恢复方式。

## WIP 约束

默认只允许一个代码写入型工作项处于 `active`。只读调查和独立 review 可以并行，但不能让多个 Agent 在同一工作树修改重叠文件。

如果确实需要并行实现，必须使用独立工作树、互不重叠的文件所有权和独立验收条件。

## 完成条件

工作项只有在以下条件全部满足后才能移动到 `completed/`：

- 范围内实现和文档已经交付；
- 每项验收条件都有 passed 证据，或明确移出范围；
- 失败和未运行检查已经记录；
- 相关 ADR、schema 和 fixtures 已同步；
- 没有依赖聊天上下文才能理解的遗留决定。

使用[_template.md](_template.md)创建工作项。
