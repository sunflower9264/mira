# AGENTS.md

本文件约束 `backend/app/`，即 Mira 的 FastAPI 应用源码。上层规则见 `backend/AGENTS.md`。

## 文件夹职责

`app/` 负责 HTTP/SSE 接口、数据库模型与契约、业务服务，以及唯一的 Codex Docker runtime 适配。前端契约对应 `web/src/types.ts`、`web/src/lib/api.ts` 和 `web/src/lib/ws.ts`。

## 目录与关键文件

- `main.py`：FastAPI 装配、lifespan、CORS、异常处理、router 注册和磁盘监控。
- `config.py`：从 `.env` 读取 Pydantic Settings；业务代码不要自行解析环境变量。
- `db.py`：异步 SQLAlchemy engine、session、测试重绑定和首次建库逻辑。
- `api/`：鉴权、请求/响应 schema、HTTP/SSE 路由和错误转换。
- `models/`：当前 ORM 表结构。
- `schemas/`：Pydantic wire shape 与共享决策结构。
- `services/`：权限、持久化、Graph、Runs、Artifacts、Skills、NL compile、Prompt Assistant 等业务规则。
- `runtime/`：Codex App Server、JSON-RPC、原生 `requestUserInput` 和 Docker sandbox。
- `log.py`、`utils.py`：集中日志与通用 id/时间工具。

## 启动与数据流

1. `main.py:lifespan` 创建运行目录并确保基础表存在。
2. 启动时同步 Prompt Templates、Codex runtime config、Skill 依赖状态、gallery，并中断遗留活动任务、清理孤儿 workspace。
3. Router 解析身份与 schema 后调用 service；service 访问 ORM 或 runtime；serializer/schema 形成响应。
4. 一次 Application Run 由 `services/run_agent.py` 协调，Codex 只能经 `runtime/sandbox.py` 在 Docker 中执行。

数据库结构演进仍以 `backend/migrations/` 为准；`db.py:create_all()` 只承担首次建库和测试初始化，不替代 Alembic migration。

## 边界

- Mira 当前是 Codex-only；不要新增其它 Agent provider、宿主机直跑 Agent 或第二套提问协议。
- `main.py` 只放应用级装配；复杂业务和权限判断进入 `services/`。
- Router 不直接拼 SQL、操作 runtime workspace 或实现 graph/run 编排。
- Model 只表达持久化结构；Schema 只表达 wire shape；不要直接返回 ORM 对象。
- Runtime/data 路径统一通过 `services/runtime_paths.py` 计算。
- 用户资源归属从认证用户推导；Settings、Skills、MCP、Codex config、Instructions 和 Prompt Templates 的写操作必须 admin-only。
- Run 执行、恢复、回放和 rerun-from 使用 `Run.graph_json` 快照，不重新读取实时 App graph。

## 推荐阅读顺序

1. `main.py`、`api/__init__.py`：确认启动与路由面。
2. 对应 `api/*.py`、`schemas/*.py`：确认 HTTP 契约。
3. 对应 `services/*.py`：确认业务事实与权限边界。
4. `models/*.py`、`backend/migrations/versions/`：确认持久化结构。
5. `runtime/base.py`、`runtime/codex_runtime.py`、`runtime/sandbox.py`：仅在修改 Agent 执行时阅读。

## 本目录规则

- 跨层变更同步 model、migration、schema、service、API、前端类型和测试；不为旧运行方案新增兼容分支。
- API 错误保持 `{detail: string}`；未知 500 由 `main.py` 添加 `request_id`。
- 不在生产代码导入 `tests/runtime_mock.py`。
- 后端源码改动至少运行 `cd backend && uv run python -m compileall app scripts`；行为变更运行相关 pytest，wire shape 变更再运行前端 typecheck。
