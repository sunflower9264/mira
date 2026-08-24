# AGENTS.md

本文件约束 `backend/scripts/`。这里是开发启动、账号维护、Runtime/Office 检查和一次性数据维护入口，不是 Web API。

## 脚本分工

- `dev.py`：开发启动总入口，依次运行 `init_env.py`、`ensure_runtimes.py`、`init_admin.py`，再以 reload 模式启动 `app.main:app`。
- `init_env.py`：从 `.env.example` 首次创建 `.env`；已有文件只补齐 `CODEX_CONFIG_SECRET`，不覆盖现有 secret。
- `ensure_runtimes.py`：按 Dockerfile、Playwright config、截图脚本和 `mira-browser` 的 digest 检查/构建 `mira-agent-runtime:latest`；Docker 不可用或构建失败会告警但不阻塞开发后端启动。
- `init_admin.py`：从 `.env` 读取管理员凭据，upsert 固定管理员并写出 Codex runtime config。
- `create_user.py`：显式创建非管理员用户；项目没有公开注册 API。
- `delete_user.py`：删除指定用户及关联 DB/运行文件，支持 dry-run 和显式 `--apply`。
- `reset_user_runtime.py`：重置指定用户 runtime 文件，支持 dry-run 和 `--apply`。
- `reset_workflow_runs.py`：迁移 0019 前的一次性 Run 历史清理，默认 dry-run，并保护仍被 graph/cover 使用的 uploads。
- `smoke_runtime.py`：按用户、app、node、prompt 直接调用当前 Codex runtime；会打印真实模型流与结果。
- `mira_office_sandbox.py`：供 root-owned helper/system manager 调用的 Office 文档隔离校验与 smoke 入口。

## 执行边界

- 脚本应从任意调用目录稳定解析到 `backend/`；涉及 `.env` 和相对 SQLite 路径时显式切换到 backend 根目录。
- `dev.py` 是推荐后端开发入口：`cd backend && uv run python scripts/dev.py`。
- Codex 只在 Docker sandbox 中运行；scripts 不新增宿主机直跑 Agent、其它 provider 或额外的用户提问服务。
- `ensure_runtimes.py` 的 digest 输入必须覆盖影响镜像运行语义的源文件；增加受管 runtime 文件时同步列表和测试。
- 删除/重置类脚本保持默认无副作用、目标摘要、显式 `--apply` 和必要确认；不要扩大目标目录。
- Runtime smoke 可能输出模型内容，不把 secret 放入 prompt、日志或断言。

## 推荐阅读顺序

1. 启动问题：`dev.py` → `init_env.py` → `ensure_runtimes.py` → `init_admin.py`。
2. Runtime 问题：`ensure_runtimes.py`、`smoke_runtime.py`、`backend/runtime/`。
3. 数据维护：先读完整目标脚本及其 service/path helper，再执行 dry-run。
4. Office 校验：`mira_office_sandbox.py` 与 `app/services/office_documents.py`。

## 验证

- 脚本改动至少运行 `cd backend && uv run python -m compileall app scripts` 和对应测试。
- 启动/runtime/维护语义变更需要实际运行安全路径或 dry-run；未运行破坏性或真实 Agent 路径时在交付中明确说明。
- 启动命令、端口、镜像或管理员初始化变化时同步 README 和相关 AGENTS。
