# AGENTS.md

本文件约束 `backend/app/api/`。该目录是 Mira 的 FastAPI HTTP/SSE 边界，业务实现位于 `app/services/`。

## 路由分工

- `__init__.py`：在 `/api` 前缀下注册全部 router。
- `health.py`：`GET /health`。
- `auth.py`、`deps.py`：登录、当前用户和管理员依赖；系统没有公开注册 API。
- `apps.py`：Apps、Versions、发布/下架、克隆、封面和 workflow lint。
- `settings.py`：全局 Settings、Skill 上传/启停、MCP、Codex config/status、Prompt Templates 和 Instructions。
- `uploads.py`：当前用户上传文件的创建与读取。
- `graph_layout.py`：Agent 布局美化；只接收 graph 和节点尺寸，合并位置由 service 负责。
- `nlcompile.py`：持久化 plan/refine/resume/apply/cancel 两阶段流程。
- `prompt_assistant.py`：节点提示词生成、waiting resume/cancel 和 active 查询。
- `runs.py`：创建/读取/重命名/取消/继续/恢复/删除、checkpoint rerun、SSE、Trace 和 Artifacts。
- `wiki.py`：当前用户 Wiki 管理、文件预览/下载、operation、revision、lint 与 App Wiki 授权。

## 请求链路与权限

- 普通用户接口使用 `Depends(get_current_user)`；管理员写接口使用 `Depends(get_current_admin)`。
- Router 只做身份与资源入口校验、schema 接收、service 调用和 HTTP 错误转换。
- Apps/Versions 的编辑、发布、删除、NL compile、Prompt Assistant 和 Graph Layout 必须 owner-only；`system_gallery` 源应用只读。
- Settings 可供登录用户读取；Skill、MCP、Codex、Prompt Template、Instruction 的修改必须 admin-only。
- Upload、cover、Run、Trace、SSE 和 artifact 下载必须通过现有 service 校验用户权限。
- `run_only` 应用对非 owner 可运行但不可克隆或查看源码；App/Run/SSE/artifact 使用既有 serializer 和 `_protected_event_transform()` 脱敏，Step Trace 返回 403。

## 契约规则

- 请求/响应类型以 `app/schemas/` 为准，并与 `web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts` 同步。
- `POST /nlcompile` 只生成可确认方案；只有 `/nlcompile/{compile_id}/apply` 返回 `new_graph`。
- Prompt Assistant 统一使用 `/prompt-assistant` 命名；不要增加旧别名或并行接口。
- Run SSE 事件来自持久化 `run_events`，`RunHub` 只做当前进程广播；修改事件时同步前端 run store。
- Runs API 不扫描 workspace 暴露文件；文件列表和下载只接受成功 artifact contract Step 的已校验声明。
- 原生 Codex `requestUserInput` 由 runtime/service 映射为 waiting/resume；router 不实现另一套提问通道。

## 推荐阅读顺序

1. `__init__.py`、`deps.py`。
2. 目标 router 及其导入的 `app/schemas/` 类型。
3. Router 调用的 `app/services/` 函数及其测试。
4. 改 SSE 或脱敏时再读 `runs.py`、`services/run_events.py`、`services/run_serializer.py`。

## 本目录规则

- 不在 router 中实现事务编排、graph 校验、artifact 完整性、runtime 调用或文件系统操作。
- 新端点必须明确身份、资源归属、响应 schema 和前端调用方；不要仅为测试绕过集中权限逻辑。
- 至少运行 `cd backend && uv run python -m compileall app scripts` 和相关 API pytest；wire shape 变化同时运行 `cd web && npm run typecheck`。
