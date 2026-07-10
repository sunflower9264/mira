# AGENTS.md

本文件约束 `web/src/components/mobile/` 手机端专用组件。

## Role

`components/mobile/` 存放手机端执行入口使用的轻量 UI 组件。当前核心组件是 `MobileSheet.tsx`，用于手机端历史记录、运行设置等底部 sheet。

页面级状态、API 调用、运行控制和业务流程属于 `pages/mobile/`、stores 或 `lib/`，不放在这里。

## Rules

- 组件可以封装手机端布局、sheet、遮罩、标题、滚动区域、安全区和触控友好按钮容器。
- 不直接调用 `lib/api.ts`、`useRunStore`、`useAppStore`、`useEditorStore` 或 `useAuthStore`，除非组件职责明确升级为业务容器。
- 跨桌面和手机都可复用的基础能力应放入 `components/common/`，不要在 mobile 目录重复实现。
- 手机端 sheet 使用现有 `vaul` 依赖和 Tailwind 风格；按钮尺寸、底部安全区和滚动区域要适配窄屏。
- 不在组件内写运行历史、Agent 设置、上传、SSE 或 ask_user 业务状态。

## Verification

- 运行 `cd web && npm run typecheck`。
- 修改 `MobileSheet` 时手动检查打开、关闭、遮罩点击、标题、滚动区域、底部安全区和窄屏触控表现。
