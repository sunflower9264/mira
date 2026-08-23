# AGENTS.md

本文件约束 `web/src/` 前端源码。

## Role

`src/` 包含 Mira 前端运行时代码：入口、路由、页面、组件、stores、API/SSE 客户端、hooks、样式和类型契约。

## Key Files

- `main.tsx`：React mount 入口。
- `routes.tsx`：桌面/手机路由、鉴权 gate、管理员 Codex 初始化 gate。
- `types.ts`：前端核心类型，必须与后端 schema 和 API 响应保持一致。
- `index.css`：Tailwind 和全局样式。

## Rules

- 路由级组合放 `pages/`，可复用 UI 和业务组件放 `components/`，跨页面状态和副作用入口放 `stores/`。
- 后端通信只通过 `lib/api.ts` 和 `lib/ws.ts`；auth token 读写集中在 `lib/auth.ts`。
- 不在组件或页面中复制临时 API 类型；优先从 `types.ts` 引用。
- 修改 `Graph`、`WorkflowNode`、`Run`、Settings、Prompt Assistant、NL compile、artifact 或 SSE 类型时，同步后端 schema/API、store 和相关测试。
- 运行态视图使用 run graph 快照；编辑器和 Step 面板使用当前 App graph，不要混用。
- `Run.status === "interrupted"` 是可恢复状态，不应当当作普通终态回放。
- 手机端路径只做执行体验，不引入编辑器、React Flow 或管理员设置能力。

## Verification

- 前端源码改动至少运行 `cd web && npm run typecheck`。
- 路由、运行、编辑器或手机端交互改动需要启动开发服务并手动检查对应路径。
