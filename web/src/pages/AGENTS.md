# AGENTS.md

本文件约束 `web/src/pages/`。

## Role

`pages/` 存放路由级页面：登录、首页、Wiki 管理、编辑器、应用运行视图和手机端入口。

## Rules

- 页面负责组合路由参数、store、组件和页面级状态；可复用 UI 下沉到 `components/`。
- 复杂 API 流程优先通过 store 或 `lib/api.ts` helper，不在页面中复制请求逻辑。
- Auth 页面要保持 token/user 持久化和跳转逻辑与 `useAuthStore`、`routes.tsx` 一致。
- Editor 和 App View 都可能使用 `useEditorStore.app`；消费前必须确认 app id 与当前 route id 匹配，避免旧 app 状态触发错误跳转或只读 UI。
- App View 的 `/market/apps/:id` 或 `can_edit=false` 必须保持只读，不显示编辑、发布、版本管理等 owner-only 入口。
- 桌面 Editor 以左侧对话为主，右侧切换工作流、预览、步骤与控制台；工作区可展开，切换保留对话草稿和任务状态。
- Editor 页面快捷键必须跳过 `input`、`textarea`、`contentEditable`，让文本框使用浏览器原生 undo/redo。
- Mobile 页面覆盖桌面核心入口：登录、应用列表、市场、运行、历史、结果、owner 运行设置、编辑器、Workspace、Wiki 与管理员 Settings；页面继续复用现有 stores/API，并保持权限限制。
- Wiki 管理在 `/wiki` 与 `/m/wiki` 提供同一后端能力；Mobile Run 仍必须执行第三方 App Wiki 授权。
- Mobile Run 不使用 Editor store，保持独立运行页与编辑器的自动保存和 selection 状态隔离。
- App View 与 Mobile Run 的 HTML 结果都通过共享 `HtmlOutputFrame` 渲染；内嵌 artifact 下载/预览只能绑定当前 Run artifacts API 返回的受验证产物。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查受影响路由、登录态跳转、只读市场应用、Editor/App View route id 切换和手机路径跳转。
