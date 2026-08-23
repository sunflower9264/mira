# AGENTS.md

本文件约束 `backend/scripts/`。

## Role

`scripts/` 包含开发启动、环境初始化、管理员初始化、普通用户维护、Codex runtime 镜像检查和真实 runtime smoke 验证脚本。

## Rules

- 脚本应能从 `backend/` 工作目录运行；需要时自行切到 backend 根目录，避免相对路径漂移。
- `dev.py` 是后端开发启动入口：初始化 `.env`、检查/构建 Docker runtime 镜像、运行 `init_admin.py`、启动 uvicorn。
- `init_env.py` 不覆盖已有 secret；首次创建 `.env` 时填充 generated placeholders，已有 `.env` 时补齐 `CODEX_CONFIG_SECRET`。
- `init_admin.py` 从 `.env` 读取 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，校验后 upsert 固定管理员并初始化全局 settings。
- 用户提问由 Codex App Server 原生 `requestUserInput` 承载；scripts 不实现额外的提问传输服务。
- 普通用户通过 `create_user.py` 创建；不要重新暴露公开注册入口。
- destructive admin 脚本必须保留 dry-run、`--apply` 和显式确认，不默认删除数据。
- Runtime smoke 脚本可能打印模型输出；不要把真实 secret 放进 prompt、日志或期望输出。
- 启动链路、端口、Docker runtime 或 admin 初始化语义变化时，同步根 README、backend README 和相关 AGENTS。

## Verification

- 脚本改动至少运行 `cd backend && uv run python -m compileall app scripts`。
- 启动链路改动需要实际运行对应脚本，或在回复中说明未运行原因。
