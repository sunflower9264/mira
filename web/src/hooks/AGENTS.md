# AGENTS.md

本文件约束 `web/src/hooks/`。

## Role

`hooks/` 存放跨组件复用的 React hooks。当前包含应用封面 object URL 管理、分栏拖拽等局部 DOM/状态逻辑。

## Rules

- 只抽取真实复用的状态、DOM 或资源生命周期逻辑；不要为单次使用提前抽象 hook。
- Hook 不承担业务持久化流程，除非已有多个调用方明确需要同一副作用入口。
- 返回值命名要清晰、稳定，避免调用方猜测副作用时机。
- 涉及 object URL、resize、pointer/mouse、stream、timer 等资源时必须在 effect cleanup 中释放。
- 不在 hook 中绕过 `lib/api.ts`、`lib/auth.ts` 或 store 约定。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查使用该 hook 的页面交互、卸载清理、重复打开/关闭和窄屏行为。
