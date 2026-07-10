# AGENTS.md

本文件约束 `backend/app/api/`。

## Role

`api/` 是 FastAPI router 层，负责 HTTP/SSE 路由、认证依赖、请求 schema 接收、响应 schema 返回和 HTTP 错误转换。

## Rules

- 普通用户接口使用 `get_current_user`；管理员接口使用 `get_current_admin`。
- Router 不实现复杂业务流程；权限细节、持久化、graph 校验、run 编排、脱敏和 artifact 处理放到 `services/`。
- API 返回结构必须与 `backend/app/schemas/`、`web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts` 对齐。
- Apps API 的编辑、删除、发布、版本、NL compile、Graph Layout 和 Prompt Assistant 必须 owner-only；`system_gallery` 源应用只读，只能先克隆。
- 可见应用读取、封面、lint、run 创建和当前用户 run 列表可以按可见性开放；clone 仍必须检查 `can_clone`。
- `run_only` 市场应用对非 owner 的 App/Run/SSE/Trace/artifact 响应必须使用现有 serializer/service helper 或 runs router 的 `_protected_event_transform()` 脱敏；不要新增绕过这些集中逻辑的临时字段拼接。
- Runs API 只提供运行创建、继续、取消、resume、历史、SSE、trace、rerun-from 和 artifacts；run 归属与安全处理放 service。
- SSE 事件契约保持稳定；新增或修改事件类型时同步 `web/src/lib/ws.ts`、`web/src/types.ts` 和 run store。
- NL compile router 负责会话归属校验和 service 调用；`POST /api/nlcompile` 只出方案，apply 才返回 graph，cancel/resume/refine/apply 都要校验当前用户。
- Prompt Assistant router 负责 app/会话归属校验和 service 调用；generate/resume/cancel/active 不在 router 中直接跑 runtime。
- Graph Layout router 只接收请求和校验 app 归属；只允许 service 合并节点位置。
- Upload、cover、artifact 下载必须校验当前用户对资源或 run/app 的权限。

## Verification

- API 契约改动运行相关 pytest，并同步前端类型/API。
- 至少运行 `cd backend && uv run python -m compileall app scripts`。
