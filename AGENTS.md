# AGENTS.md

本文件约束整个 Mira 仓库。除非用户明确要求，解释、计划和总结默认使用中文；代码、命令、日志、错误信息和标识符保持原文。

## Project Positioning

Mira 是一个参考 Google Opal 思路的可视化 AI app 搭建与运行项目。用户通过节点图、自然语言编辑、Agent runtime、运行预览和中段交互快速构建 mini AI app。

本仓库是全栈单体项目：

- `web/`：React + Vite + TypeScript 前端，负责 Home、Editor、Preview、App View、Mobile Run 和 Settings UI。
- `backend/`：FastAPI 后端，负责鉴权、Apps/Versions、Settings/Skills/MCP/Instructions/Prompt Templates、Uploads、Run 编排、SSE、NL compile、Prompt Assistant 和 Codex runtime 隔离。
- `docs/`：预留文档目录；当前不要引用不存在的正式文档路径。
- `deploy/`、`backend/data/`、`backend/logs/`、`backend/runtime/homes/`、`backend/runtime/workspaces/`：部署副本或本地运行产物，不作为源码维护。

## Must-Read Files

- `README.md`：项目定位、功能、启动方式和基础使用。
- `web/AGENTS.md`、`backend/AGENTS.md`：前后端专项规则。
- `web/src/types.ts`：前端核心类型和 wire shape。
- `web/src/lib/api.ts`、`web/src/lib/ws.ts`：HTTP/SSE 客户端契约。
- `backend/app/schemas/`、`backend/app/api/`：后端请求/响应 schema 和 router。
- `backend/app/services/`、`backend/app/runtime/`：业务规则、run 编排和 Agent sandbox。

## Runtime And Startup

- 根目录开发启动：Linux/macOS/WSL2 执行 `sh start.sh`；Windows 后端必须在 WSL2 Linux shell 内运行。
- `start.sh` 会停止 `8000`、`5173` 端口上的已有监听进程，启动后端和前端，并绑定 `0.0.0.0`。
- 后端开发服务：`cd backend && uv sync && uv run python scripts/dev.py`。
- 前端开发服务：`cd web && npm ci && npm run dev -- --host 0.0.0.0`。
- Codex App Server 只允许在 `backend/runtime/Dockerfile` 构建的 Docker Linux sandbox 容器内执行，容器启用 Docker init 回收 Codex、Chromium 和开发服务器的后代进程；不要恢复宿主机直跑 Codex 的路径。启用 artifact 的 `validate_office_documents` 时，由后端宿主机的 LibreOffice、`pdfinfo` 和 `pdftotext` 做真实打开、页数与页面文字边界验证，不把它们打入 Agent runtime。
- sandbox 内置 `/opt/mira/capture_screenshots.py`，并固定调用 `/usr/bin/chromium`，不接受 PATH 同名包装器；解包后的 Web 项目存在 `package-lock.json` 时使用 `npm ci`，否则使用 `npm install`，并在启动前依次执行项目已声明的 `db:init`、`db:seed` 脚本。每次截图使用可自动清理的临时 Chromium profile。每个 route 在 Chromium 前必须通过最终状态 `<400` 的 HTTP 检查；`--min-screenshots` 默认 1。只有全部检查通过且截图数达标时才生成 ZIP；截图不足、HTTP 错误或其他 capture failure 时 manifest `ok=false`、CLI 非零退出，并删除同路径旧 ZIP，避免失败结果被 artifact contract 当成有效 ZIP。成功截图逐张记录 SHA-256。manifest/log 脱敏 runtime 绝对路径但保留 URL；截图 ZIP 解包拒绝 traversal、链接、特殊文件、规范化后重名成员和损坏压缩流，并与 TAR 一样限制 10,000 个成员和 1 GiB 总展开量。
- runtime 同时提供固定版本的 `@playwright/cli` 和 `/usr/bin/chromium`，统一入口为 `mira-browser`。交互式浏览器取证必须先运行 `mira-browser doctor`，随后直接使用该入口；运行期禁止 `npx`、`npm install` 或 Playwright 浏览器下载。`mira-browser` 会拒绝改变浏览器、配置、持久化 profile、CDP 或 headed 模式的参数。它与 `/opt/mira/capture_screenshots.py` 是两个独立工具：前者用于交互操作，后者用于稳定路由批量截图。
- `scripts/dev.py` 会初始化 `.env`、检查或构建 runtime 镜像、运行 `scripts/init_admin.py` 并启动 uvicorn。共享或远程部署前必须修改默认 admin 密码。

## Architecture Rules

- 前后端契约以 `web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts`、`backend/app/schemas/` 和 `backend/app/api/` 为准；改 wire shape 必须两端同步。
- Workflow 节点类型包括 `user_input`、`asset`、`generate`、`condition`、`output`。每个 workflow 最多一个 `user_input` 和一个 `output`；`output` 是唯一终点节点，不能出边。
- Graph 使用 `execution_edges` 表达节点执行顺序；condition 出边用 `branch_key` 表达分支。普通执行线不表示字段绑定，已有传递路径时不应再添加冗余直连线。
- App 不保存 runtime 选择；运行固定使用 Codex。App 级 Tools 排除项保存在 `graph.tools.disabled_tool_ids`；运行创建时写入 `graph._runtime_tools.allowed_tool_ids` 快照。
- `asset` 节点契约：`text.content`、`url.urls[]`、`file.uploads[]`、`drawing.upload`。文件和画板上传引用跨用户克隆时必须复制到目标用户。
- `generate.output_contract` 支持 `json`、`html`、`artifact`；自由文本不设置契约。JSON 必须提供 strict object `json_schema`；HTML 只通过 `{"html":"..."}` wrapper 校验并原样保存；artifact 必须提供 `artifact_kind` 且只接受运行工作区内文件 `path`，其中 `zip` kind 只接受真实有效的 `.zip`。可选 `validate_office_documents=true` 只允许 `docx`、`excel`、`ppt`、`zip`、`file`，要求产物本身或 ZIP 内至少包含一个 Office 文档，且每份都能由宿主机 LibreOffice 转换为至少一页 PDF；校验最多并发 2 个、总时限 120 秒，并通过 system manager transient unit、专用无 Docker 组账号和 root-owned helper 执行。helper、ACL 或宿主机工具缺失时必须 fail-closed，不得退回只有限资源、没有权限隔离的进程。JSON、HTML、自由文本以及 artifact 的 UTF-8 文本/OOXML 成员都拒绝 `U+FFFD`；带 `.zip`、OOXML、`.tar`、`.gz` 或 `.tgz` 扩展名的文件必须是对应有效容器，归档扫描拒绝危险成员，并限制 10,000 个成员、64 MiB 文本/XML、512 MiB 压缩文件和 1 GiB 总展开量。成功的 artifact Step 保存包含 `holder`（当前 run/node/step）、`origin`（首次生产者）、可选 `reused_from`（直接复用来源）、引擎内部相对路径、大小、SHA-256、artifact kind 和版本号的 manifest；Run 标记成功前必须复验新版 manifest，文件缺失、manifest 非法或大小/hash 变化都使 Run 失败。`output` 节点保持 HTML-only 最终展示节点，并内部使用 HTML wrapper 契约。
- 一次 Application Run 对应一个逻辑 RunAgent。线性节点延续同一 Codex thread 和可写 workspace；非 Agent 的 `user_input` / `asset` 将输入写入 `.mira/run-context/`，附件复制到 `inputs/`。只有真正 fan-out 才从父 checkpoint 通过 App Server `thread/fork` 创建子 thread 和 CoW branch workspace；fan-in 必须由协调 Agent 读取完整分支快照与上下文后合并，后端严格验证 receipt 并清理已消费分支。不得恢复 `/mnt/results`、每节点 workspace 或手工 handoff/sidecar 通道。
- 运行结果区只有 `输出` 和 `文件` 两类视图，当前 Run 没有正式 artifact 时不显示 `文件` 页签；`GET /api/runs/{run_id}/artifacts` 和 Step Trace 只从成功 artifact contract Step 的声明产物组装，不扫描 workspace。artifact 响应包含 `sha256` 和 `integrity`（`verified` / `modified`）及 hash-bound 签名下载链接，不返回内部 `path`，禁止向前端泄漏 runtime 本地绝对路径。
- 每次 run 保存启动时 `graph` 快照。执行、恢复、历史回放、rerun-from 和 run 态前端视图使用 run 快照，不受后续 App graph 编辑影响。
- Run 执行按直接前置依赖驱动，ready 节点可并发执行；同一 branch 的 LLM 节点复用 Codex thread/workspace，分支隔离与合并由 RunAgent 管理。节点正式结果仍使用统一 Envelope 和强输出契约；未声明 workspace 文件可供同一运行后续推理使用，但不能进入 artifacts API 或下载视图。
- Run 只有在唯一 output Step 为 `success`、其它 Step 均为 `success` / `skipped` 且 Artifact 最终复验通过时才能成功。失败保留 `error`，并用 `failure_kind` 区分 `runtime`、`contract`、`routing`、`integrity`、`internal`；业务验收不通过应作为正常业务输出，不冒充执行异常。
- 后端启动会把未完成 run 标记为 `interrupted`，并删除数据库中已不存在的普通 Run workspace，保留 `_nlcompile` 等特殊 workspace；继续运行是节点级恢复，跳过已成功或已跳过节点，不承诺中断节点内部副作用去重。
- 从历史 run 指定节点重新执行是 checkpoint rerun：必须创建新 run，冻结来源 run 在 cut 前的 workspace、Codex thread lineage 和 step 状态，使用当前 App graph 执行 cut 及其后代；当前 Graph 新增的 cut 前节点标记 `checkpoint_reused` 且不执行，cut 前 input override 不生效。旧 run 永远只读；没有可用 pre-checkpoint 的 cut 必须拒绝。
- 桌面 condition 分支测试通过新 run snapshot 中的 `condition_branch_override` 强制分支，不修改 App graph。
- Apps、Versions、Runs、Steps、Uploads、runtime workspace 等用户业务数据必须按当前登录用户隔离；禁止用外部传入的 `user_id` 决定资源归属。
- 发布应用支持公开可克隆、公开仅运行和私有。`run_only` 市场应用对非 owner 可运行但不可克隆，且 App、Run、SSE、Trace、artifacts 响应必须脱敏 graph、prompt、内部 step 日志和来源节点标题。
- `backend/seeds/gallery.json` 同步出的内置模板归属 `system_gallery`，源应用只读；模板通过 `gallery=true` 获取，普通市场通过 `market=true` 获取且不包含 `system_gallery`。
- Settings、Skills、MCP、Codex config、支持模型、Instructions 和 Prompt Templates 是全局共享数据；写操作必须走 admin 权限。
- MCP/Skills 默认只在普通运行中按 App 允许列表注入；只有 `planning_enabled=true` 的 Tool 才能进入 NL compile、Prompt Assistant 和运行期提问规划的 read-only 阶段。
- NL compile 是持久化两阶段流程：`POST /api/nlcompile` 只生成可确认方案，`POST /api/nlcompile/{compile_id}/apply` 才返回 `new_graph`；active/refine/resume/cancel 以 `nlcompile_sessions` 为事实来源。首次请求可携带当前用户的 upload 引用，引用随会话历史持久化，并在 plan/apply Agent 调用时通过 `/mnt/inputs` 提供文件内容。
- Prompt Assistant 使用统一 `/api/prompt-assistant` 接口和 `prompt_assistant_generations` 持久化等待态；不要新增旧式 `prompt_helper` 命名或 `/api/prompt-helper` 接口。
- JSON output contract 和 strict `json_schema` 仍是内部校验契约，由 AI 根据节点任务维护；普通用户界面不展示 JSON Schema、字段大纲、字段引用或“可引用结果”入口。
- 用户提问用于 NL compile 方案阶段、Prompt Assistant 和 app run 中段交互。Codex planning turn 使用 `collaborationMode=plan`，原生 `item/tool/requestUserInput` 由 runtime 归一化为 Mira `DecisionRequest` waiting 请求并把答案回填同一 JSON-RPC request；用户提问只走这条原生协议，不通过 prompt 约定工具或增加第二条传输通道。Graph 不保存提问开关；运行期除 `output` 外的 LLM 节点统一先进入 Plan，由 Codex 自主判断是否需要用户决策。

## Editing Rules

- 修改前先明确目标、假设和验证方式；只改和当前任务直接相关的文件。
- 不顺手重构、格式化、改名、移动文件或清理无关代码；保持现有风格，即使它不是最佳实践。
- 不添加 speculative features、提前抽象、插件机制或用户没要求的扩展点。
- 发现无关问题时只在回复中说明，不擅自修改。
- 修改结构、启动方式、接口契约、节点类型、runtime 边界或业务行为后，同步更新相关 `README.md` / `AGENTS.md` / 正式文档。
- 开发阶段修改任何 seed 后，必须同步开发数据库和 `deploy` 数据库，或在回复中明确说明未同步的原因；Settings 保存 Prompt Template 会同步写回 prompt seed，修改 prompt seed 还必须确认变量名与 `backend/app/services/prompts.py` 调用一致。
- 不修改 `.serena/`、runtime 生成物、部署副本、日志、数据库、缓存和 ignored 目录，除非用户明确要求。

## Verification Commands

- 前端类型检查：`cd web && npm run typecheck`
- 前端构建：`cd web && npm run build`
- 后端测试：`cd backend && uv sync && uv run pytest -q`
- 后端编译：`cd backend && uv run python -m compileall app scripts`
- 迁移验证：`cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`
- 文档格式：`git diff --check`

## Documentation Rules

- 项目正式名称统一写作 Mira。
- README 可以说明 Mira 参考 Google Opal，但必须声明非 Google 官方项目、非官方关联。
- 文档路径示例优先使用相对路径；不要把本地工作区路径写成项目名。
- 不引用不存在的文档路径。
