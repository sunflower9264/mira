# AGENTS.md

本文件约束 `web/src/components/editor/`。

## Role

`editor/` 是桌面可视化工作流编辑器组件层，负责 React Flow 画布、节点工具栏、自然语言编辑入口、节点/边交互和布局美化入口。

## Key Components

- `Canvas.tsx`：React Flow 画布、本地选择/拖拽/连线交互。
- `NodeToolbar.tsx`：新增节点和画布工具入口。
- `NlInputBar.tsx`：自然语言编排、ask_user 方案追问、方案确认/应用。
- `nodes/`：各节点视图。

## Rules

- `useEditorStore` 持有 canonical graph；Canvas 本地 state 只服务 React Flow 交互，必须与 store graph 保持同步。
- 画布最多允许一个 `user_input` 和一个 `output`；新增、拖拽和 NL compile apply 后都要遵守该规则。
- `output` 是终点节点，不能作为 edge source；condition edge 必须保持 `branch_key` 与分支 key 对齐。
- 画布“美化布局”调用后端 `POST /api/graph-layout/beautify`，只写回节点 `position`；前端不要维护第二套布局算法。
- NL compile 调用后端两阶段流程：先恢复/生成 planned 会话并展示确认，确认后调用 apply，只有 apply 返回 `new_graph` 才写入画布。
- NL compile waiting 使用 `DecisionPromptPanel` 风格：选项来自后端，补充文本/附件按题目保存，最后一题统一提交，提交后保留可停止状态。
- 确认弹窗默认展示目标摘要和 graph changes；长文本必须在滚动区域内完整可读。
- Undo/redo 只管理画布结构和节点位置等 graph 操作；文本输入框内 `Ctrl/Cmd+Z`、redo 交给浏览器原生行为。
- 连线和节点删除走 React Flow selection/delete 事件；节点选择写入 store，连线选择保持 Canvas 局部状态。
- 不在 Canvas 中直接实现保存或 API 细节；通过 store/API 封装。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查新增节点、拖拽、连线、删除、多选、undo/redo、NL compile planned/waiting/apply、布局美化和 Step 面板联动。
