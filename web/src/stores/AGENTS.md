# AGENTS.md

本文件约束 `web/src/stores/`。

## Role

`stores/` 是 Zustand 状态层，负责 Auth、Apps、Editor、Run、Settings 等跨组件状态和副作用入口。

## Rules

- Store 是业务状态边界；组件不应绕过 store 重复实现同一跨页面流程。
- `useEditorStore` 的 `app.graph` 是编辑器 canonical graph；节点、边、viewport、保存、undo/redo、布局美化和 Prompt Assistant 生成态都要保持一致。
- 文本输入保存可跳过画布 undo/redo 历史，但必须避免过期 future 覆盖新文本。
- `useEditorStore.load` 可能被 Editor 和 App View 快速连续调用，必须忽略过期响应，避免旧 app 覆盖当前路由。
- Prompt Assistant 生成态按 node id 保存在 `promptAssistantGenerations`；切换节点不应自动取消或丢失生成态。
- `useRunStore` 同时服务 Preview、App View 和 Mobile Run；修改运行流程必须兼容三处。
- `useRunStore.status` 包含前端 `idle` / `starting` 和后端 run status；`idle`、`starting` 没有可取消 run。
- `useRunStore.start`、`rerunFrom` 在创建 run 前执行 workflow lint；error 阻断，warning 不阻断。
- `useRunStore.runGraph` 使用后端返回的 run 快照；历史回放、waiting resume 和 continue 不应重新读取实时 App graph。rerun-from 返回新 run 快照：cut 前来自冻结 checkpoint，cut 及下游来自当前 App graph。
- `useRunStore.cancel` 在取消 API 返回后主动拉取 run 快照，并在 run 已切换时忽略旧响应。
- `useAppStore` 统一加载我的应用、模板、市场和最近运行；模板导入、市场克隆、创建、重命名、删除都从这里更新列表。
- `useSettingsStore` 维护 Settings、Agent 初始化状态、MCP/Skills/Tools 库存；不要在组件中复制全局设置状态。
- Store 不吞掉错误；UI 需要可展示的 loading/error 状态。

## Verification

- 运行 `cd web && npm run typecheck`。
- Store 行为改动手动检查相关页面主流程，尤其运行恢复、取消、rerun、编辑保存、首页列表刷新和 Settings 刷新。
