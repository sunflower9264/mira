# AGENTS.md

本文件约束 `backend/app/runtime/`。

## Role

`runtime/` 定义 AgentRuntime 抽象、Codex App Server adapter 和 Docker sandbox runner，把 Mira 的 run/node 请求转换为隔离容器内的双向 JSON-RPC 调用。

## Rules

- Codex adapter 必须遵守 `base.py` 的抽象契约和 streaming chunk 语义。
- Codex App Server 只能通过 `sandbox.py` 在 Docker Linux sandbox 容器内执行；不要在 adapter 中直接使用宿主机 `subprocess.Popen`。
- 容器只挂载 scoped HOME、call workspace 和必要 uploads；不要挂载宿主 HOME、项目根、Docker socket、`.env` 或其他用户 workspace。
- 容器使用后端进程 UID/GID 运行，保证 bind mount 可写；不要硬编码镜像内固定用户。
- Fake HOME 和 scoped config/auth/MCP/Skills 文件从数据库配置派生；不要读取宿主机真实 Codex 登录状态。
- 普通 run 的 MCP/Skills 注入来自 run snapshot 允许列表与当前 Settings 启用状态的交集；NL compile、Prompt Assistant 和运行期提问规划走 read-only 路径，还必须过滤 `planning_enabled=true`。
- 交互式浏览器取证使用 runtime 固定入口 `mira-browser`，其 Playwright CLI 版本和 Chromium 路径由镜像固定为 `/usr/bin/chromium`；必须先执行 `mira-browser doctor`，禁止通过 `npx`、`npm install` 或浏览器下载补依赖。`mira-browser` 与 `/opt/mira/capture_screenshots.py` 是独立工具。
- 不要把 MCP token/header 放入 CLI argv。
- sandbox stdout 必须增量解码 UTF-8，禁止按 Docker log frame 单独 `errors=ignore`，以免三字节汉字被拆开。
- App Server 必须按 `initialize`、`initialized`、thread start/resume/fork、`turn/start` 的顺序驱动，并处理 notification 与 server request；`turn/completed` 后主动停止短生命周期容器。
- 运行期提问使用 `collaborationMode=plan` 和原生 `item/tool/requestUserInput`。runtime 负责归一化问题、等待 Mira 回答并以原 request id 写回结果；不在 prompt 中指定工具名，也不增加第二条传输通道。
- 普通执行 turn 必须显式使用可写 sandbox policy；planning turn 使用 read-only policy，避免同 thread 的上一次模式泄漏到下一 turn。
- Adapter 不做 UI 模型兜底；model、reasoning_effort 和 runtime policy 由 service 层准备并传入。
- LLM thread lineage 由 RunAgent 决定；adapter 必须支持线性 `thread/resume` 与原生 `thread/fork`，并行 branch 绝不能继续写同一物理 thread。

## Verification

- Adapter/parser/sandbox 改动运行 App Server 协议、runtime config、sandbox 和原生用户提问相关测试。
- 真实 Codex 行为需要 Docker runtime 镜像和有效凭据后，用 `scripts/smoke_runtime.py` 或 Settings status 验证。
