# AGENTS.md

本文件约束 `backend/app/runtime/`。该目录是 Mira 与 Codex App Server 之间唯一的生产 adapter：service 给出 prompt、thread、policy、Tools 和 workspace，runtime 将其转换为短生命周期 Docker 容器内的双向 JSON-RPC。

## 关键文件与调用链

- `base.py`：`AgentRuntime` 协议、stream chunk、runtime status、decision request/result 和 `execute` / `plan` policy。
- `factory.py`：生产固定返回 `CodexRuntime`；override 仅供测试使用，不增加第二套生产 runtime。
- `codex_runtime.py`：准备 scoped fake HOME 与 Skill mounts，驱动 App Server，转换通知/工具结果，处理原生 `requestUserInput`。
- `sandbox.py`：创建、附着、取消并清理 Docker 容器；负责 host/container 路径映射和增量 UTF-8 解码。
- 镜像与浏览器 helper 位于 `backend/runtime/`，路径计算位于 `app/services/runtime_paths.py`，Tool 选择与 Skill 安装位于 `app/services/tools.py`、`skills_install.py`。

调用顺序固定为 `initialize` → `initialized` → `thread/start|resume|fork` → `turn/start`；`turn/completed` 后主动结束容器。adapter 必须保留 `base.py` 的 chunk 语义，不能把 Codex 协议细节泄漏到 router。

## Sandbox 边界

- Codex App Server 只能经 `DockerSandboxRunner` 运行；禁止宿主机 `subprocess.Popen`、读取宿主 Codex 登录态或引入其他生产 runtime fallback。
- 容器使用后端进程 UID/GID（无该能力的平台才用镜像用户），启用 Docker init，drop all capabilities、`no-new-privileges`，并应用配置的 memory/CPU/pids/network。
- 只挂载当前 branch workspace 到 `/workspace`、scoped HOME 到 `/home/mira`、必要上传到只读 `/mnt/inputs`、Run 冻结 Wiki 到只读 `/mnt/wiki`，以及选中 Skill 的只读依赖层。不要挂载宿主 HOME、仓库根、Docker socket、`.env`、共享 runtime 根或其他用户 workspace。
- config/auth 从数据库派生的 shared HOME 复制到 scoped HOME；MCP URL/header 写入 scoped `config.toml`，不得通过 argv 或日志暴露 token/header。敏感环境变量不透传。
- stdout/stderr 使用跨 Docker frame 的增量 UTF-8 decoder；不要对单个 frame 使用 `errors=ignore`。容器/宿主路径通过 `RuntimePathMap` 改写，不向模型或前端制造第二套路径协议。

## Thread、Plan 与 Tools

- 线性 branch 使用 `thread/resume`；只有 RunAgent 标记的真实分支才 `thread/fork`。runtime 不自行决定 lineage，也不得让并行 branch 继续写同一 thread。
- planning thread/turn 使用 `collaborationMode=plan`；外层 Docker 将 branch workspace 只读挂载，App Server 使用 `externalSandbox` 并声明 restricted network，避免在受限容器中再次启动 Linux `bwrap`。execute 继续使用可写 workspace 与 `dangerFullAccess`。每次请求都显式传 policy，避免继承上一 turn。
- 原生 `item/tool/requestUserInput` 只在提供 callback 时接受；runtime 要求非空问题列表、每题 2-3 个选项且拒绝 secret，等待 Mira 回答后以原 JSON-RPC id 返回。不要用 prompt 工具名或另加传输通道。
- 普通 Run 的 MCP/Skills 来自 Run graph 允许列表快照与当前启用状态的交集；planning 额外只保留 `planning_enabled=true`。Skill 解压到 scoped HOME 的 `.agents/skills/<id>`。
- Skill 同目录的 `requirements.lock` 优先于 `requirements.txt`。依赖层由 service 隔离构建、按 runtime 镜像内容寻址并在挂载前复验，运行时只读出现在 Skill 根目录 `.deps/`；Skill 自行加入 `sys.path`，系统工具仍由镜像提供。

## 镜像与浏览器事实

- `backend/runtime/Dockerfile` 当前固定 Python 3.12、Node.js 22、`@openai/codex` 0.147.0、`@playwright/cli` 0.1.18。Chromium 是镜像构建时安装的 Debian 包；受管路径固定为 `/usr/bin/chromium`，不是源码中锁定的 Debian 版本号。
- 交互取证先 `mira-browser doctor`，再统一使用该入口。它固定 Chromium/headless/isolated config，拒绝 workspace Playwright config、替代 browser/config/profile/CDP/headed 参数及安装类命令；不要用 `npx`、运行期 `npm install` 或浏览器下载绕过。
- `/opt/mira/capture_screenshots.py` 与 `mira-browser` 独立：前者只处理 workspace 内已有 `node_modules` 的项目、可选 `db:init`/`db:seed`、路由 HTTP 预检和批量截图；失败不保留旧 ZIP，成功 ZIP 仅含 PNG、manifest、日志。

## 修改与验证

- adapter/parser 改动优先运行 `tests/test_runtime_parsers.py`、`tests/test_runtime_sandbox.py`、`tests/test_runtime_config.py` 和用户提问相关测试。
- Skill mount/依赖改动运行 `tests/test_runtime_skill_mounts.py`、`tests/test_skill_dependencies.py`、`tests/test_skill_lifecycle.py`。
- 浏览器 helper 改动运行 `tests/test_mira_browser.py`、`tests/test_capture_screenshots.py`；镜像定义变化还要确认 `scripts/ensure_runtimes.py` 的 digest 文件清单。
- 真实 Codex 验证需要 Docker 镜像和有效数据库凭据，使用 `scripts/smoke_runtime.py` 或 Settings status；默认 pytest 不依赖真实 Codex 或网络。
