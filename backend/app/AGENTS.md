# AGENTS.md

本文件约束 `backend/app/`，即 FastAPI 后端应用源码。

## Role

`app/` 包含后端运行时代码：应用装配、配置、数据库、API routers、ORM models、Pydantic schemas、业务 services、runtime adapters 和日志。

## Boundaries

- `main.py` 只放 FastAPI 装配、lifespan、middleware、异常处理、router 注册和进程级后台任务。
- `config.py` 只定义环境配置；业务代码不要手写环境变量解析。
- `db.py` 维护 SQLAlchemy engine/session/base；不要在 service 中创建第二套 engine。
- `api/` 只做鉴权、schema 接收、HTTP 错误转换和 service 调用；复杂业务流程放 `services/`。
- `models/` 只表达数据库结构；业务流程和权限判断放 service。
- `schemas/` 定义 API wire shape；不要直接把 ORM model 暴露给前端。
- `runtime/` 封装 provider adapter、Docker sandbox 和 ask_user bridge；API/service 不应散落 provider CLI 细节。
- `services/runtime_paths.py` 是 runtime/data 文件路径事实来源。

## Rules

- 用户资源归属从当前登录用户推导，不接受外部传入 `user_id` 作为 owner。
- 全局设置类写操作必须走 admin 依赖。
- Run 执行、恢复、序列化和 rerun-from 使用 `Run.graph_json` 快照。
- DB 持久化状态优先于内存状态；`RunHub` 和 Prompt Assistant/NL compile 内存 session 只用于当前进程快速衔接。
- 新增跨层能力时同步考虑 model、migration、schema、service、API、前端类型和测试。

## Verification

- 后端源码改动至少运行 `cd backend && uv run python -m compileall app scripts`。
- 行为改动按影响范围运行相关 pytest；跨契约改动同时运行前端 typecheck。
