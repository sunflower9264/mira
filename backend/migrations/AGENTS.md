# AGENTS.md

本文件约束 `backend/migrations/`。该目录保存 Mira 的 Alembic 迁移链，当前唯一 head 为 `0028_default_workspace_git_hosts`。

## 目录职责

- `env.py`：读取 `app.config` 的数据库 URL，加载 `Base.metadata`，支持 async online migration 和 offline SQL。
- `script.py.mako`：新 revision 模板。
- `versions/0001_baseline.py`：空库基线。
- `versions/0002` 至 `0021`：管理员、Prompt Templates、运行排序/恢复/快照、市场权限、NL compile、Prompt Assistant 与 RunAgent workspace/checkpoint 演进。
- `versions/0022_remove_legacy_runtime_version.py`：移除旧 runtime version 字段。
- `versions/0023_codex_only_control_plane.py`：迁移到单一 Codex 控制面与 supported models。
- `versions/0024_remove_legacy_ask_user.py`：移除旧节点提问配置和遗留 pending 状态。
- `versions/0025_skill_dependency_layers.py`：增加 Skill 根目录与 Python 依赖层状态字段。
- `versions/0026_user_wiki.py`：增加用户 Wiki、source/revision/operation、Run snapshot 与第三方授权。
- `versions/0027_workspaces.py`：增加持久 Workspace、Session、Turn、Git 配置和 WorkflowProposal。
- `versions/0028_default_workspace_git_hosts.py`：为尚未配置白名单的 Settings 回填常见 Git 主机。

历史 migration 为从旧库升级所必需；其中出现的旧表或字段不是当前架构入口，不要据此恢复生产 model/service。

## 迁移规则

- ORM 结构变化必须新增 revision，并以当前 head `0028_default_workspace_git_hosts` 为 `down_revision`；保持单线链路，除非明确处理分支合并。
- Migration 必须可从空库升级到 head，也能在已有 SQLite 库上顺序执行。
- SQLite 删除/修改列优先使用 `batch_alter_table`，并显式处理索引、server default、nullable 和数据回填顺序。
- 不依赖本机数据、runtime 文件、外部服务或 seed 才能完成 schema migration。
- 不把权限修复、业务流程或无关数据清理塞入 migration；仅在新结构要求时迁移已有数据。
- 已发布 revision 通常不可改写；增加新 migration 修正。历史 downgrade 只需忠实恢复该 revision 的上一结构，不代表当前功能仍受支持。

## 推荐阅读顺序

1. `alembic.ini`、`migrations/env.py`。
2. `versions/0025_skill_dependency_layers.py`，再沿 `down_revision` 追踪目标表历史。
3. 对应 `app/models/`、service 和测试。

## 验证

- 运行 `cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`。
- 新迁移应另用临时空 SQLite 库验证从 baseline 到 head；必要时再用代表性旧库验证数据回填。
- 同步运行相关 model/schema/service/API pytest。
