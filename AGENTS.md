# AGENTS.md

本文件约束整个 Mira 仓库。新会话先读本文件，再读目标目录最近的 `AGENTS.md`。除非用户明确要求，解释、计划和总结使用中文；代码、命令、日志、错误信息和标识符保持原文。

## 项目定位

Mira 是参考 Google Opal 产品思路构建的可视化 AI App 平台，使用节点图、Codex Agent runtime、运行预览和中段交互来创建、执行和分享 mini AI app。Mira 不是 Google 官方项目，也与 Google 无官方关联。

这是 React/FastAPI 全栈单体仓库：

- `web/`：React 18、Vite、TypeScript、Zustand、React Flow，负责 Home、Editor、Preview、App View、Mobile 和 Settings UI。
- `backend/`：FastAPI、SQLAlchemy、SQLite/Alembic，负责鉴权、Apps、Settings、Uploads、NL compile、Prompt Assistant、Run/SSE/Trace、Artifacts 和 Codex runtime 隔离。
- `docs/`：部署说明、架构决策、截图和草稿；正式事实优先看 README、ADR 和代码，不把 `docs/drafts/` 当产品契约。
- `deploy/`：部署副本和部署数据，不作为源码维护，除非用户明确要求部署或同步部署库。
- `backend/data/`、`backend/logs/`、`backend/runtime/homes/`、`backend/runtime/workspaces/`、`web/dist/`：本地或部署运行产物，不作为源码维护。

## 推荐阅读顺序

1. `README.md`：项目功能、开发入口和运行时能力。
2. `CONTEXT.md` 与 `docs/adr/0001-run-agent-session-tree.md`：RunAgent、thread tree、共享 workspace 和 checkpoint rerun 的领域事实与决策。
3. `web/AGENTS.md` 或 `backend/AGENTS.md`：目标子系统规则。
4. `web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts` 与 `backend/app/schemas/`、`backend/app/api/`：前后端 wire contract。
5. `backend/app/services/` 与 `backend/app/runtime/`：业务编排、强输出契约和 Docker sandbox。

## 启动与环境边界

- Linux/macOS/WSL2 开发启动：`sh start.sh`。脚本会停止 `8000`、`5173` 端口的旧监听进程并启动前后端，日志写入 `backend/logs/` 和 `web/logs/`。
- Windows 原生后端不受支持；`start.bat` 只提示转到 WSL2。Docker Desktop 必须开启 WSL integration。
- 后端：`cd backend && uv sync && uv run python scripts/dev.py`。`dev.py` 初始化 `.env`、检查或构建 runtime 镜像、upsert 管理员并启动 uvicorn；镜像构建失败不会阻断后端启动，实际错误由 Settings runtime status 暴露。
- 前端：`cd web && npm ci && npm run dev -- --host 0.0.0.0`。
- Codex App Server 只允许在 `backend/runtime/Dockerfile` 构建的 Docker Linux sandbox 中运行；不得恢复宿主机直跑 Codex。容器启用 Docker init，用于回收 Codex、Chromium 和项目开发服务器的后代进程。
- Office artifact 深检在后端宿主机通过隔离 helper 调用 LibreOffice、`pdfinfo` 和 `pdftotext`，不在 Agent runtime 内执行；隔离 helper 或工具缺失时 fail closed。

## 核心架构不变量

- Workflow 节点类型只有 `user_input`、`asset`、`generate`、`condition`、`output`。最多一个 `user_input` 和一个 `output`；可执行图必须有且只有一个可达的终点 `output`，`output` 不能出边。
- `execution_edges` 只表达执行顺序，不是字段绑定；condition 出边用 `branch_key`。输入/素材不能作为连线终点，所有节点和 condition 分支都必须能到达 output。
- App 不保存 runtime 选择，执行固定使用 Codex。App 通过 `graph.tools.disabled_tool_ids` 排除 Tools，Run 创建时把允许项冻结到 `graph._runtime_tools.allowed_tool_ids`。
- 一次 Application Run 对应一个逻辑 RunAgent。线性节点复用同一 Codex thread 和 branch workspace；`user_input` / `asset` 写入 `.mira/run-context/`，附件复制到 `inputs/`。只有真实 fan-out 才通过 checkpoint、`thread/fork` 和独立物化的可写 workspace 分支；checkpoint 使用 manifest 与不可变内容对象，同一 Run 内相同文件内容只保存一次，不依赖宿主 reflink。fan-in 由协调 Agent 合并，后端验证 receipt 后才清理源分支。不得恢复 `/mnt/results`、每节点 workspace/thread 或手工 sidecar 通道。
- Run 创建时冻结 `graph_json`；执行、waiting resume、恢复、历史回放和序列化使用 Run 快照。checkpoint rerun 创建新 Run，冻结 cut 前 workspace、thread lineage 和 step；旧 Run 只读。
- 每个用户有一个长期 Wiki。普通 Run 冻结当前 Wiki revision 并只读挂载到 `/mnt/wiki`；Wiki 不进入 branch workspace/checkpoint，输出与 Artifact 不自动写回。第三方 App 授权绑定用户、App 与 graph SHA-256。
- 每个 Workspace 是 owner-only 的持久项目目录，拥有多个 Codex thread 和一个常驻 Docker runtime；同一 Workspace 的 turn 必须串行。Workspace Wiki 使用 working copy 和三方合并发布，Workflow graph 只能经带 base SHA 的 Proposal、lint、只读预览和人工确认修改；Workspace 内执行 App 仍使用正式 Run 链路。
- Run 按直接前置依赖调度，ready 节点可并发。Run 只有唯一 output Step 为 `success`、其他 Step 为 `success` / `skipped` / `checkpoint_reused`，并且所有正式 Artifact 复验通过时才能成功。
- `failure_kind` 只使用 `runtime`、`contract`、`routing`、`integrity`、`internal`。业务验收不通过应作为正常业务输出，不伪装成执行异常。
- Run 事件持久化到数据库；SSE 的进程内 hub 只负责广播、取消和等待信号。后端启动把未完成运行标为 `interrupted`，并清理没有数据库记录的普通 Run workspace。

## 输出、文件与完整性

- `generate.output_contract` 支持 `json`、`html`、`artifact`；自由文本不设契约。JSON 使用 strict object `json_schema`；HTML 通过 `{"html":"..."}` wrapper 校验后原样保存；artifact 只接受当前运行 workspace 内的文件路径和声明的 `artifact_kind`。
- `output` 是 HTML-only 最终展示节点。JSON Schema 是内部校验实现，不向普通用户显示字段大纲、Schema 或字段引用控件。
- workspace 文件可以供同一 Run 后续推理，但 Files、Trace 和下载 API 只承认成功 artifact contract Step 的正式 manifest，不扫描 workspace，也不新增旁路 artifact 表。
- Artifact manifest 必须包含 holder、origin、可选 reused_from、相对路径、大小、SHA-256、kind 和版本。下载响应只返回 hash-bound 签名链接、`sha256` 与 `integrity`，不得返回 runtime 绝对路径；`modified` 产物不可下载。
- 文本和 OOXML 成员拒绝 `U+FFFD`；ZIP/TAR/OOXML 等容器必须真实有效并拒绝 traversal、链接、特殊文件和解包炸弹。启用 `validate_office_documents` 时必须真实打开 Office 文件并验证页面边界。

## Runtime、Skills 与浏览器

- Settings、Skills、MCP、Codex config、supported models、Instructions 和 Prompt Templates 是全局数据，写操作必须 admin-only；Apps、Versions、Runs、Steps、Uploads 和 runtime workspace 按当前登录用户隔离。
- Workspace Git host 白名单是 admin-only 全局设置；每个 Workspace 的私有 HTTPS token 加密保存且不得挂载进 Agent 容器、写入命令参数或日志。Pull 必须 ff-only，Push 必须先经用户确认。
- MCP/Skills 在普通运行中按 App 允许列表注入；只有 `planning_enabled=true` 的 Tool 可进入 NL compile、Prompt Assistant 和运行期 Plan 的 read-only 阶段。
- Skill 可在 `SKILL.md` 同目录声明 `requirements.lock` 或 `requirements.txt`，前者优先。纯 Python wheel 依赖由隔离 builder 构建和缓存，在运行时只读挂载为 Skill 根目录 `.deps/`；Skill 必须自行加入 `sys.path`。系统包、二进制和动态库进入 runtime 镜像。依赖为 `pending` 或 `failed` 时不能启用 Skill。
- `/opt/mira/capture_screenshots.py` 只接受当前 workspace 内的 `--project-dir`，只复用已有 `node_modules`，缺失时失败且不安装依赖；按项目声明执行 `db:init`、`db:seed`，逐路由先做 HTTP `<400` 检查。只有截图数达标且全部成功时才生成只含 PNG、manifest 和日志的 ZIP。
- 交互浏览器统一使用镜像内的 `mira-browser`：`@playwright/cli` 版本由 Dockerfile 固定，Chromium 来自镜像构建时的 Debian 包但可执行路径固定为 `/usr/bin/chromium`。先执行 `mira-browser doctor`；运行期不得用 `npx`、`npm install` 或浏览器下载补依赖，也不得覆盖浏览器、config、profile、CDP 或 headed 策略。

## 产品与数据规则

- `system_gallery` seed 应用是只读源模板；`gallery=true` 获取模板，普通 `market=true` 不包含源模板。模板修改先克隆为当前用户应用。
- 发布应用支持私有、公开可克隆和公开仅运行。`run_only` 对非 owner 只能运行；App、Run、SSE 和 artifact 响应走集中脱敏，Step Trace 返回 403，不得泄漏 graph、prompt、内部日志或来源节点标题。
- NL compile 是 `plan → confirm/apply` 的持久化两阶段流程；plan/refine/resume/cancel 以 `nlcompile_sessions` 为事实来源，附件保存当前用户 upload 引用并在挂载前重新校验。
- Prompt Assistant 使用 `/api/prompt-assistant` 和 `prompt_assistant_generations` 持久化生成、waiting、resume 与 cancel；不要恢复 `prompt_helper` 命名。
- 用户提问只走 Codex Plan 的原生 `item/tool/requestUserInput` JSON-RPC request，由 runtime 归一化为 Mira `DecisionRequest` 并把回答写回同一 request。Graph 不保存提问开关，也不增加 prompt/MCP 约定的第二条提问通道。
- 页面 Prompt Assistant 使用独立 `prompt_assistant` 模板和 Codex 配置文件的默认模型/推理强度，最多进行一轮 1–3 个问题的原生提问；NL compile apply 使用独立 `nlcompile_prompt_refiner` 模板，不向用户提问。两者生成的用户可见提示词使用中文业务语言，不暴露内部字段和工作流协议。

## 修改规则

- 动手前明确目标、假设和验证方式；只改与任务直接相关的代码，不顺手重构、改名、格式化或清理既有死代码。
- 优先最简单的现有结构；不添加 speculative features、预留扩展点或一次性逻辑框架。
- 前端界面禁止添加解释内部实现、重复控件含义或没有操作价值的辅助提示；仅保留完成任务所必需的状态、错误、安全确认、空状态和无障碍文本。
- Wire shape 变化必须同步前端类型/API/WS 与后端 schema/router/service/test。ORM 变化必须新增 Alembic migration。
- 修改节点类型、Run/SSE、Settings、Tools、runtime 边界或启动方式时，同步相关 README、ADR 和最近的 `AGENTS.md`。
- 修改 seed 后必须同步开发数据库和部署数据库，或在交付说明中明确未同步原因。Prompt Template seed 的变量必须与 `backend/app/services/prompts.py` 调用一致。
- 不修改 `.serena/`、部署副本、数据库、日志、缓存和 runtime 产物，除非用户明确授权。

## 验证

- 禁止默认运行全量测试；只运行与本次功能或改动直接相关的最小测试集。只有用户明确要求全量测试时，才执行全量测试套件。
- 前端类型：`cd web && npm run typecheck`
- 前端构建：`cd web && npm run build`
- 后端相关测试：`cd backend && uv sync && uv run pytest -q <相关测试文件或 -k 表达式>`
- 后端编译：`cd backend && uv run python -m compileall app scripts`
- 迁移：`cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`
- 文档与空白错误：`git diff --check`

按变更风险选择最小充分验证；不要用无关的大范围修改换取测试通过。项目正式名称统一写作 Mira，路径示例使用相对路径，不引用不存在的文档。
