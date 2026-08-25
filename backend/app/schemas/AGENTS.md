# AGENTS.md

本文件约束 `backend/app/schemas/`。Pydantic 模型是后端 API 与前端 TypeScript 之间的 wire shape 事实来源之一。

## 文件分工

- `requests.py`：Auth、App、Version、NL compile、Graph Layout、Workflow Lint 和 Prompt Assistant 请求体。
- `runs.py`：Run/Step 状态、输入附件、创建/恢复/rerun 请求、Trace、Artifacts、waiting 和运行摘要响应。
- `decision.py`：NL compile、Prompt Assistant 和 Application Run 共用的决策 context/group/option/answer。
- `wiki.py`：Wiki 管理、operation/revision/lint 与 App 访问授权契约。
- `types.py`：App/Version、Codex config、Settings/Skills/MCP、Upload、NL compile、Lint 和 Prompt Assistant 响应。
- `__init__.py`：稳定的集中导出面；router/service 优先从此处导入公共 schema。

## 当前契约事实

- Workflow wire shape 仍主要是 `dict[str, Any]`，由 `services/graph_validation.py` 和相关 service 做语义校验。
- Run 状态为 `pending/running/waiting_for_user/interrupted/success/failed/cancelled`；Step 另有 `checkpoint_reused/skipped`。
- `FailureKind` 仅为 `runtime/contract/routing/integrity/internal`；业务验收失败不是执行异常。
- Run、NL compile 和 Prompt Assistant 的附件只接收当前用户 upload 引用；路径和元数据由 service 解析。
- NL compile 响应是 planned/completed/waiting/progress 联合类型；apply 前不能返回 `new_graph`。
- Prompt Assistant 响应是 completed/waiting/interrupted 联合类型。
- Prompt Assistant generate 请求只携带应用、graph、目标节点和用户说明；模型与推理强度不从节点或请求传入，使用 Codex 配置默认值。
- 决策请求统一使用 `context + groups + request_id`，恢复答案使用 `DecisionAnswer`；不定义另一套提问协议。
- Artifact 响应包含 hash、integrity、来源关系和签名下载 URL，不返回 runtime 内部路径。

## 契约规则

- 字段名、可选性、Literal 和嵌套结构必须与 `web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts` 一致。
- 不直接暴露 ORM model；响应通过 serializer 和 schema 控制权限脱敏。
- 校验优先放共享 schema 或 service，不在多个 router 重复实现。
- 新增必填字段时同时检查旧数据库、seed、测试 fixture、前端创建路径和 `run_only` 脱敏结果。
- 不为已经删除的多 provider 或节点级提问配置保留兼容字段。

## 推荐阅读顺序

1. 目标 router 与它声明的 response model。
2. 本目录对应 schema。
3. `app/services/serializers.py`、`run_serializer.py` 或相关 service。
4. 前端对应类型与 API helper。

## 验证

- 运行相关 API pytest 和 `cd backend && uv run python -m compileall app scripts`。
- Wire shape 变化必须运行 `cd web && npm run typecheck`；涉及 SSE 时同时检查 `web/src/lib/ws.ts` 与 run store。
