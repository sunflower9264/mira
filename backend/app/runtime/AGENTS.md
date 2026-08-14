# AGENTS.md

本文件约束 `backend/app/runtime/`。

## Role

`runtime/` 定义 AgentRuntime 抽象、Claude/Codex CLI adapter、内部 ask_user bridge 和 Docker sandbox runner，把 Mira 的 run/node 请求转换为隔离容器内的 streaming Agent 调用。

## Rules

- Provider adapter 必须遵守 `base.py` 的抽象契约和 streaming chunk 语义。
- Claude/Codex CLI 只能通过 `sandbox.py` 在 Docker Linux sandbox 容器内执行；不要在 adapter 中直接使用宿主机 `subprocess.Popen` 跑 CLI。
- 容器只挂载 scoped HOME、call workspace 和必要 uploads；不要挂载宿主 HOME、项目根、Docker socket、`.env` 或其他用户 workspace。
- 容器使用后端进程 UID/GID 运行，保证 bind mount 可写；不要硬编码镜像内固定用户。
- Fake HOME 和 scoped config/auth/MCP/Skills 文件从数据库配置派生；不要读取宿主机真实 Claude/Codex 登录状态。
- 普通 run 的 MCP/Skills 注入来自 run snapshot 允许列表与当前 Settings 启用状态的交集；NL compile、Prompt Assistant 和运行期 ask_user preflight 走 planning/read-only 路径，还必须过滤 `planning_enabled=true`。
- 不要把 MCP token/header 放入 CLI argv。
- Codex exec 跳过 CLI git repo trust 检查，信任边界由 Mira 的隔离 workspace 和 Docker sandbox 提供。
- Claude `stream-json` 只聚合真实 text delta/final message；不要把 partial snapshot 当正文累加。
- sandbox stdout 必须增量解码 UTF-8，禁止按 Docker log frame 单独 `errors=ignore`，以免三字节汉字被拆开。
- Codex `exec --json` stdout 若含 U+FFFD，最终文本以同 session rollout 里未损坏的最后一条 agent_message 为准。
- ask_user 通过内部 bridge/MCP 调用后端 callback；请求必须带 context/title/summary。修改协议时同步 runtime helper、run resume、SSE、decision schema 和 prompt templates。
- Adapter 不做 UI 模型兜底；model、reasoning_effort 和 runtime policy 由 service 层准备并传入。
- LLM session 复用策略由 run orchestrator 决定；adapter 不假设并行分支共享 session。

## Verification

- Adapter/parser/sandbox 改动运行 runtime parser、runtime config、sandbox、ask_user MCP 相关测试。
- 真实 CLI 行为需要 Docker runtime 镜像和有效 Agent 凭据后，用 `scripts/smoke_runtime.py` 或 Settings status 验证。
