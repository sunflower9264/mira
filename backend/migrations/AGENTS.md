# AGENTS.md

本文件约束 `backend/migrations/`。

## Role

`migrations/` 是 Alembic 迁移目录，负责数据库结构演进。

## Rules

- ORM model 结构变化必须配套 migration；不要只改 model。
- Migration 要能从空库升级到 head，不依赖本机临时数据、运行时文件或开发数据库状态。
- 当前默认数据库是 SQLite，DDL 和约束写法要兼容 SQLite。
- 不把 seed、业务流程、权限修复或大规模数据清理塞进 migration，除非结构变更必须迁移已有数据。
- 新 migration 文件命名和 `revision` / `down_revision` 要保持线性链路清晰。
- 修改历史 migration 前确认是否已经被本地/共享数据库使用；通常应新增 migration 而不是改旧 migration。

## Verification

- 运行 `cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`。
- 相关 model/schema/service/API 测试必须通过。
