# AGENTS.md

本文件约束 `web/src/pages/mobile/` 手机端路由页面。

## Role

`pages/mobile/` 是 Mira 手机端执行入口，覆盖桌面核心能力并针对窄屏重新布局：

- `MobileAuth.tsx`：手机端登录。
- `MobileHome.tsx`：我的应用、最近运行和应用市场。
- `MobileRun.tsx`：应用运行、历史、运行设置、等待输入和结果查看。
- `MobileEditor.tsx`：工作流画布、步骤、预览、控制台和版本入口。
- `MobileSettings.tsx`：管理员全局设置。
- `/m/wiki`：Wiki 来源、阅读、维护和版本管理。

手机端不绕过权限；编辑器、Wiki 和 Settings 复用桌面 API 与业务契约，窄屏提供独立布局。

## Architecture

- 路由在 `web/src/routes.tsx`，路径包括 `/m`、`/m/login`、`/m/apps/:id/run`、`/m/apps/:id/editor`、`/m/workspaces/:id`、`/m/wiki` 和 `/m/settings`。
- 桌面/手机切换使用 `web/src/lib/mobile.ts` 的 viewport media query 逻辑，不使用 UA 判断。
- 数据契约来自 `web/src/types.ts`；HTTP/SSE 调用通过 `web/src/lib/api.ts` 和 `web/src/lib/ws.ts`。
- 鉴权复用 `useAuthStore`，应用列表复用 `useAppStore`，运行流程复用 `useRunStore`。
- `MobileRun` 不使用 `useEditorStore`，不加载 React Flow，不参与编辑器自动保存或 selection 状态；`MobileEditor` 则复用 `useEditorStore`、Canvas 和 PreviewPanel，通过底部页签切换画布、步骤、预览和控制台。

## Rules

- `MobileHome` 展示我的应用、最近运行和市场；模板点击先导入为当前用户草稿，普通市场应用直接进入只读运行页，只有 `can_clone=true` 才显示克隆入口。
- `MobileRun` 直接通过 `api.getApp(id)` 加载应用；运行、恢复、回放、继续、取消和 waiting resume 都交给 `useRunStore`。
- 启动前展示 workflow lint；error 禁用运行，warning 只提示。`can_view_source=false` 的市场应用不显示节点数量、运行设置或源码细节。
- 只要 graph 包含唯一 `user_input` 节点，启动前就要求填写并随 run inputs 提交，不要求该节点是入口。
- 历史记录中的未结束 run 进入 live/recover 流程，终态 run 才回放。
- 页面高度跟随 visual viewport，内容区单独滚动；结果、过程、文件使用同层页签，文件页签仅在存在正式 artifact 时出现。运行设置和新运行收进顶部菜单，等待输入复用面板内停止入口。
- 最终结果展示 output HTML 和 run artifacts；文件下载只使用后端返回的 `download_url`。页面复用同一份 `useRunArtifacts` 状态渲染文件列表并通过 `HtmlOutputPreview` 传给 `HtmlOutputFrame`，只允许同名 `verified` artifact 绑定 HTML 中的下载或图片预览占位。
- owner 应用的运行设置可写回 LLM 节点统一 model 和 `graph.tools.disabled_tool_ids`；不编辑节点 prompt、结构、分支、output contract 或 reasoning_effort。
- `can_edit=false` 的市场只读应用不显示运行设置。
- 手机端输入控件字号要避免 iOS 聚焦自动放大；画布保留 React Flow 双指平移/缩放能力，页面缩放 guard 不得拦截画布手势。
- `MobileEditor` 修改 graph、prompt、模型、工具与分支必须沿用桌面 store 的校验、保存和 undo/redo；发布和打开版本历史前先保存，非 owner 跳回只读运行页。
- `/m/wiki` 复用 Wiki 文件与维护 API，手机用知识文档、原始来源、维护与版本切换，并支持上传、下载、重命名、删除、重试和版本恢复。
- `/m/settings` 复用 SettingsDialog 的全部分类，普通用户不得访问全局管理设置；管理员 Codex 初始化仍通过公共 gate。
- waiting/decision_request 的选项、补充文本、附件、摘要态和停止入口要与桌面 Preview 语义一致。

## Verification

- 运行 `cd web && npm run typecheck` 和必要时 `cd web && npm run build`。
- 手动检查 `/m`、`/m/login`、`/m/apps/:id/run`、`/m/apps/:id/editor`、`/m/wiki`、`/m/settings`：登录、列表、最近运行、市场、启动输入、运行中停止、waiting resume、历史回放、interrupted continue、运行设置、artifacts、结果页、工作流编辑/发布/版本、Wiki 管理与设置权限。
