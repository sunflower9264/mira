# AGENTS.md

本文件约束 `web/src/pages/mobile/` 手机端路由页面。

## Role

`pages/mobile/` 是 Mira 手机端执行入口，只承载轻量运行体验：

- `MobileAuth.tsx`：手机端登录。
- `MobileHome.tsx`：我的应用、最近运行和应用市场。
- `MobileRun.tsx`：应用运行、历史、运行设置、等待输入和结果查看。

这里不是编辑器，也不是 Settings 手机版。

## Architecture

- 路由在 `web/src/routes.tsx`，路径包括 `/m`、`/m/login`、`/m/apps/:id/run`。
- 桌面/手机切换使用 `web/src/lib/mobile.ts` 的 viewport media query 逻辑，不使用 UA 判断。
- 数据契约来自 `web/src/types.ts`；HTTP/SSE 调用通过 `web/src/lib/api.ts` 和 `web/src/lib/ws.ts`。
- 鉴权复用 `useAuthStore`，应用列表复用 `useAppStore`，运行流程复用 `useRunStore`。
- `MobileRun` 不使用 `useEditorStore`，不加载 React Flow，不参与桌面自动保存或 selection 状态。

## Rules

- `MobileHome` 展示我的应用、最近运行和市场；模板点击先导入为当前用户草稿，普通市场应用直接进入只读运行页，只有 `can_clone=true` 才显示克隆入口。
- `MobileRun` 直接通过 `api.getApp(id)` 加载应用；运行、恢复、回放、继续、取消和 waiting resume 都交给 `useRunStore`。
- 启动前展示 workflow lint；error 禁用运行，warning 只提示。`can_view_source=false` 的市场应用不显示节点数量、运行设置或源码细节。
- 只要 graph 包含唯一 `user_input` 节点，启动前就要求填写并随 run inputs 提交，不要求该节点是入口。
- 历史记录中的未结束 run 进入 live/recover 流程，终态 run 才回放。
- 最终结果展示 output HTML 和 run artifacts；文件下载只使用后端返回的 `download_url`。
- owner 应用的运行设置可写回 LLM 节点统一 model 和 `graph.tools.disabled_tool_ids`；不编辑节点 prompt、结构、分支、output contract 或 reasoning_effort。
- `can_edit=false` 的市场只读应用不显示运行设置。
- 手机端页面保持不可缩放体验，输入控件字号要避免 iOS 聚焦自动放大。
- waiting/decision_request 的选项、补充文本、附件、摘要态和停止入口要与桌面 Preview 语义一致。

## Verification

- 运行 `cd web && npm run typecheck` 和必要时 `cd web && npm run build`。
- 手动检查 `/m`、`/m/login`、`/m/apps/:id/run`：登录、列表、最近运行、市场、启动输入、运行中停止、waiting resume、历史回放、interrupted continue、运行设置、artifacts 和结果页。
