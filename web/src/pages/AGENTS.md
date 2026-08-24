# AGENTS.md

本文件约束 `web/src/pages/`。

## Role

`pages/` 存放路由级页面：登录、首页、编辑器、应用运行视图和手机端执行入口。

## Rules

- 页面负责组合路由参数、store、组件和页面级状态；可复用 UI 下沉到 `components/`。
- 复杂 API 流程优先通过 store 或 `lib/api.ts` helper，不在页面中复制请求逻辑。
- Auth 页面要保持 token/user 持久化和跳转逻辑与 `useAuthStore`、`routes.tsx` 一致。
- Editor 和 App View 都可能使用 `useEditorStore.app`；消费前必须确认 app id 与当前 route id 匹配，避免旧 app 状态触发错误跳转或只读 UI。
- App View 的 `/market/apps/:id` 或 `can_edit=false` 必须保持只读，不显示编辑、发布、版本管理等 owner-only 入口。
- Editor 页面快捷键必须跳过 `input`、`textarea`、`contentEditable`，让文本框使用浏览器原生 undo/redo。
- Mobile 页面只承载执行入口：登录、应用列表、最近运行、市场、运行、历史、结果和 owner 运行设置；不要加节点编辑、Prompt 编辑、管理员设置或版本管理。
- Mobile Run 不使用 Editor store，避免把 React Flow、自动保存和 selection 状态带入手机端。
- App View 与 Mobile Run 的 HTML 结果都通过共享 `HtmlOutputFrame` 渲染；内嵌 artifact 下载/预览只能绑定当前 Run artifacts API 返回的受验证产物。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查受影响路由、登录态跳转、只读市场应用、Editor/App View route id 切换和手机路径跳转。
