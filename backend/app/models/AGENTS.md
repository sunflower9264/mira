# AGENTS.md

本文件约束 `backend/app/models/`。这里是 SQLAlchemy ORM 的当前结构表达；数据库演进由 `backend/migrations/` 负责。

## 当前模型

- `user.py`：`users`，包含登录身份和 `is_admin`。
- `app.py`、`app_version.py`：应用、版本、发布/市场可见性、gallery 克隆来源和 graph JSON。
- `run.py`：`runs`、`steps`、`step_logs`、`run_events`，以及 RunAgent branch、workspace checkpoint、operation 持久化状态。
- `nlcompile_session.py`：NL compile 计划、历史、waiting request 和 apply 状态。
- `prompt_assistant_generation.py`：Prompt Assistant 生成、历史、waiting request 和结果状态。
- `codex_config.py`：唯一 Codex config/auth 密文及用户修改状态。
- `settings.py`：全局 supported models、Skill/MCP 配置和 Workspace Git 主机白名单 JSON。
- `skill.py`：Skill 归档、根目录、启用/规划开关和 Python 依赖层状态。
- `prompt_template.py`：Prompt Template 内容、变量和更新时间。
- `wiki.py`：用户单例 Wiki、原始来源、不可变 revision、后台 operation、Run Wiki snapshot 与第三方授权。
- `__init__.py`：集中导出，供 `db.py:create_all()` 和 services 使用。

## 持久化边界

- 用户业务数据通过 `owner_id` / `user_id` 隔离；隔离判断仍必须在 service 查询层执行。
- Apps、Runs、Steps 使用 JSON 文本保存 graph、input、output 或上下文；解析、校验、脱敏放 service/helper。
- `Run.graph_json` 是运行快照；Step 的 `ordering` 是 API/SSE 稳定顺序，不可改用 UUID 字典序。
- RunAgent branch/checkpoint/operation 是恢复、fork/fan-in 和 rerun-from 的持久化事实，不是临时缓存。
- Codex config/auth 正文只保存加密内容；runtime HOME 文件是可重建派生物。
- 当前没有多 provider 配置表或节点级提问开关；不要重新引入。Wiki 不是节点类型，也不进入 Graph。

## 结构变更规则

- 每个 ORM 结构变化都新增线性 Alembic migration；当前 head 为 `0028_default_workspace_git_hosts`。
- Model 不实现权限、业务流程、runtime 调用、JSON 校验或响应序列化。
- 新字段同步 migration、Pydantic schema、serializer/service、测试、必要的 seed/fixture 和前端类型。
- 外键删除语义、索引和 nullable/default 必须与 migration 一致，尤其注意 SQLite batch alter。
- 不因历史 migration 中存在已删除结构，就在当前 model 中恢复兼容字段。

## 推荐阅读顺序

1. `__init__.py` 和目标 model。
2. `backend/migrations/versions/0025_skill_dependency_layers.py` 向前追踪相关迁移。
3. 使用该 model 的 `app/services/` 与 schema/serializer。

## 验证

- 运行 `cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`。
- 运行覆盖新增/变更字段读写、权限隔离和序列化的 pytest。
