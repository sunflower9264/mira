# AGENTS.md

本文件约束 `backend/app/models/`。

## Role

`models/` 定义 SQLAlchemy ORM models，是数据库结构在源码中的表达。

## Rules

- ORM 结构变化必须新增 Alembic migration；不要只改 model。
- 用户业务表必须保留 owner/user 维度，并在 service 查询层按当前用户隔离。
- 全局配置表使用管理员/全局 owner 语义，不混入用户私有业务数据。
- 不在 model 中实现业务流程、权限判断、runtime 调用或序列化脱敏；只保留字段、关系和轻量默认值。
- JSON 文本字段的读写解析放 service/helper；model 不承担复杂校验。
- 新字段要同步 Pydantic schema、serializer、service、测试、seed/fixture 和必要的前端类型。
- 删除字段或表前确认迁移、历史数据、测试 fixture 和前端兼容路径。

## Verification

- 结构改动运行 `cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`。
- 运行覆盖新增/变更字段读写路径的 pytest。
