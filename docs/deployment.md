# Mira Deployment Notes

本文档说明 Mira 的单机部署形态和公开模板边界。它不是生产运维承诺；正式环境仍需要自行配置 HTTPS、进程守护、备份、日志轮转、监控和密钥管理。

## Architecture

一个最小部署通常包含：

- 前端：在 `web/` 执行 `npm run build`，由 nginx 提供 `web/dist` 静态文件。
- 后端：在 `backend/` 执行 `uv sync --frozen`，用 uvicorn 启动 `app.main:app`。
- 反向代理：nginx 对外监听 HTTP 端口，静态文件走 `/`，后端 API 走 `/api/`。
- 数据：默认 SQLite，`DATABASE_URL` 指向部署数据目录内的数据库文件。
- Runtime：Claude/Codex CLI 只在 Docker sandbox runtime 中执行，运行 homes、workspaces 和 CLI 缓存都属于本机运行产物。

真实部署目录、数据库、日志、备份、`.env`、runtime homes/workspaces、上传文件和构建产物都不应提交到源码仓库。

## Example Script

`deploy/example/mira-deploy.example.sh` 是脱敏模板，供阅读和改造。它默认把部署产物写到仓库根目录的 `.mira-deploy/`，该目录已被忽略。

最小试运行：

```sh
MIRA_DEPLOY_ADMIN_PASSWORD='<strong-password>' sh deploy/example/mira-deploy.example.sh deploy
```

常用环境变量：

- `MIRA_DEPLOY_ROOT`：部署产物目录，默认 `.mira-deploy/`。
- `MIRA_DEPLOY_ADMIN_USERNAME`：管理员用户名，默认 `admin`。
- `MIRA_DEPLOY_ADMIN_PASSWORD`：管理员密码，必填。
- `PUBLIC_HOST` / `PUBLIC_PORT`：nginx 对外监听地址，默认 `127.0.0.1:19090`。
- `BACKEND_PORT`：uvicorn 本机端口，默认 `19091`。

## Security

- 不要把真实 `.env`、SQLite 数据库、上传文件、runtime homes/workspaces 或日志提交到 Git。
- `JWT_SECRET` 和 `AGENT_CONFIG_SECRET` 必须随机生成，并和数据库备份一起保管。
- 共享或远程部署必须修改管理员密码，并配置 HTTPS、访问控制和备份策略。
- 如果数据库和 `AGENT_CONFIG_SECRET` 同时泄漏，已保存的 Agent 配置可能被解密。

