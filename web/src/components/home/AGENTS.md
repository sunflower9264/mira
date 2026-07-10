# AGENTS.md

本文件约束 `web/src/components/home/`。

## Role

`home/` 存放桌面首页应用卡片、模板、应用市场、最近运行/使用和应用列表相关组件。

## Rules

- Home 数据流通过 `useAppStore` 和 `lib/api.ts`；组件不要直接分散请求。
- “我的应用”只管理当前用户 owner 应用；重命名、删除、创建和克隆后要更新 store 中对应列表。
- 模板来自 `GET /api/apps?gallery=true`，普通市场应用来自 `GET /api/apps?market=true`，最近运行来自 `GET /api/apps/recent-runs`；不要硬编码卡片数据。
- 桌面 Home 的“我的应用”和“最近使用”是同组 tab；应用市场只在“我的应用”tab 下展示。
- 模板卡片点击先导入为当前用户草稿；普通市场应用进入只读 App 页面，只有 `can_clone=true` 才显示克隆入口。
- 最近运行卡片展示当前用户运行过且仍可见的应用，不显示克隆菜单。
- App cover 通过后端 cover API 和 upload id 展示；按 16:9、`object-cover`、居中裁剪，不拼外部 URL 或 data URL。
- 保持卡片菜单、确认弹窗、loading/error/empty 状态清晰，不把市场权限判断写死在前端。

## Verification

- 运行 `cd web && npm run typecheck`。
- 手动检查我的应用、最近使用 tab、模板导入、市场只读打开、市场克隆、重命名、删除、封面和空状态。
