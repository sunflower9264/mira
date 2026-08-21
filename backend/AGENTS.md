# AGENTS.md

本文件约束 `backend/`。除非用户明确要求，解释、计划和总结默认使用中文；代码、命令、日志、错误信息和标识符保持原文。

## Scope

`backend/` 是 Mira 的 FastAPI 后端，负责鉴权、Apps/Versions、Settings/Skills/MCP/Instructions/Prompt Templates、Uploads、Run 编排、SSE、NL compile、Prompt Assistant、ask_user 和 Claude/Codex Docker sandbox runtime。

前端契约来自 `web/src/types.ts`、`web/src/lib/api.ts`、`web/src/lib/ws.ts`；后端修改 wire shape 时必须同步前端。

## Structure

- `app/main.py`：FastAPI app、lifespan、CORS、异常处理、router 注册、启动 seed 和 interrupted 标记。
- `app/api/`：HTTP/SSE routers 和认证依赖。
- `app/models/`：SQLAlchemy ORM models。
- `app/schemas/`：Pydantic 请求/响应 schema。
- `app/services/`：业务逻辑、权限、Settings、Prompt Templates、Runs、Artifacts、NL compile、Prompt Assistant、runtime config。
- `app/runtime/`：AgentRuntime 抽象、Claude/Codex adapter、ask_user bridge、Docker sandbox runner。
- `migrations/`：Alembic migrations。
- `scripts/`：开发启动、环境初始化、用户维护和 runtime smoke。
- `seeds/`：gallery、默认 agent 元数据和 prompt template seeds。
- `tests/`：pytest 集成测试和 test-only MockRuntime。

## Runtime And Data Rules

- API 错误统一返回 `{detail: string}`；未处理异常由 `app/main.py` 包装成带 `request_id` 的 500。
- 普通用户接口使用 `Depends(get_current_user)`；管理员写接口使用 `Depends(get_current_admin)`。
- 用户业务数据包括 Apps、Versions、Runs、Steps、Step Logs、Uploads 和每用户 runtime workspace，必须按当前登录用户隔离。
- 全局数据包括 Settings、Skills、MCP、Agent config、supported models、Instructions 和 Prompt Templates；写操作必须 admin-only。
- Claude/Codex CLI 只能通过 `app/runtime/sandbox.py` 在 Docker Linux sandbox 容器内执行；容器必须启用 Docker init 回收 Agent、Chromium 和开发服务器的后代进程。不要恢复宿主机 `subprocess.Popen` 直跑 Agent CLI。Office artifact 深检是后端宿主机上的独立确定性校验，不在 Agent runtime 内执行。
- sandbox 内置截图工具 `/opt/mira/capture_screenshots.py`；目标项目有 `package-lock.json` 时必须执行 `npm ci`，否则执行 `npm install`，并在启动前依次执行项目已声明的 `db:init`、`db:seed` 脚本。工具固定调用 `/usr/bin/chromium`，不接受 PATH 同名包装器；Chromium 的 HOME、XDG 目录和 `--user-data-dir` 使用截图调用结束即清理的临时目录。每个 route 在 Chromium 前必须通过最终状态 `<400` 的 HTTP 检查；`--min-screenshots` 默认 1。只有 manifest `ok=true` 时才生成 ZIP；截图不足、HTTP 错误或其他 capture failure 时 CLI 非零退出且删除同路径旧 ZIP，诊断只保留在 out-dir 的 manifest/log 中，避免失败结果被 artifact contract 接受。成功截图逐张记录 SHA-256。manifest/log 只保留相对路径或占位路径并保持 URL 原样；ZIP 解包额外拒绝规范化后重名成员和损坏压缩流，ZIP/TAR 都拒绝 traversal、链接与特殊文件，并限制 10,000 个成员和 1 GiB 总展开量。
- Runtime 文件路径必须通过 `app/services/runtime_paths.py` 计算；不要在业务 service 中手写 runtime/data 根路径。
- `backend/data/`、`backend/logs/`、`backend/runtime/homes/`、`backend/runtime/workspaces/` 是本地运行产物，不作为源码维护。
- Agent config、Codex auth 等配置正文加密存 DB；fake HOME 文件是派生物，可被重写。
- 后端启动会 seed prompt templates、runtime config、global skills、gallery；旧 run 的 `pending/running` 会标记为 `interrupted`，NL compile 的 `planning/waiting_for_user/applying` 会标记为 `interrupted`，Prompt Assistant 只把 `running` 标记为 `interrupted`，保留 `waiting_for_user` 供 active/resume 恢复。

## Business Rules

- Workflow 最多一个 `user_input` 和一个 `output`；`output` 是终点节点，不能出边。graph validation、workflow lint、Run 创建和 NL compile apply 都必须遵守。
- App cover 只保存 upload id；写入时校验 upload 归属，读取通过 `GET /api/apps/{app_id}/cover`。
- `system_gallery` seed 应用是只读源模板；`gallery=true` 返回模板，`market=true` 不包含 gallery 源应用。模板编辑必须先克隆为当前用户草稿。
- 发布应用支持 `visibility` 和 `market_access`。`run_only` 对非 owner 只能运行，App/Run/SSE/Trace/artifact 响应必须脱敏源码、prompt、内部日志和来源节点标题。
- MCP/Skills 是 Settings 管理的全局 Tools；App 通过 `graph.tools.disabled_tool_ids` 排除。Run 创建时写入 `_runtime_tools.allowed_tool_ids` 快照，执行时与当前仍启用 Tools 取交集。
- 只有 `planning_enabled=true` 的 MCP/Skill 可进入 NL compile、Prompt Assistant 和运行期 ask_user preflight 的 planning/read-only 阶段。
- `generate.output_contract` 支持 json/html/artifact；普通 generate 默认自由文本且不设置契约。只有下游需要稳定字段、当前节点明确输出 HTML 片段或需要可下载文件产物时才使用契约。JSON 必须提供 strict object `json_schema`，artifact 必须提供 `artifact_kind` 且只接受当前节点 workspace 内文件 `path`；`zip` kind 只接受真实有效的 `.zip`。可选 bool `validate_office_documents` 只用于 artifact，且只允许 `docx`、`excel`、`ppt`、`zip`、`file`；设为 `true` 时要求直接产物或 ZIP 内至少有一个 Office 文档，并由宿主机 LibreOffice、`pdfinfo` 和 `pdftotext` 验证可打开、页数大于 0 且文字边界未超出页面。深检最多并发 2 个、总时限 120 秒、响应 run cancel；必须通过 system manager transient unit、专用无 Docker 组账号和 root-owned helper 隐藏 HOME/运行时 socket、限制网络和资源。helper、ACL 或宿主机工具缺失属于不可修复基础设施错误，不得退回仅 `prlimit` 的非隔离进程，也不调用 Agent repair。JSON、HTML、自由文本和 artifact 文本/OOXML 成员都拒绝 `U+FFFD`；带 `.zip`、OOXML、`.tar`、`.gz` 或 `.tgz` 扩展名的文件必须是对应有效容器，ZIP/TAR/OOXML 扫描拒绝 traversal、链接和特殊文件，并限制 10,000 个成员、64 MiB 文本/XML、512 MiB 压缩文件与 1 GiB 总展开量。成功 Step 将 artifact 输出归一化为带 `holder`（当前 run/node/step）、`origin`（首次生产者）、可选 `reused_from`（直接复用来源）、引擎内部相对路径、大小、SHA-256、artifact kind 和版本号的 manifest；Run 写入 success 前复验全部 manifest，文件缺失、manifest 非法或大小/hash 不符必须使 Run 失败，不接受缺少版本号或来源关系的旧声明；成功终态必须使用条件更新，不能覆盖并发取消。`output` 节点保持 HTML-only，并内部使用 `{"html":"..."}` wrapper 契约。
- Run 创建时保存 graph 快照；执行、继续、waiting resume、序列化和历史回放读取 `Run.graph_json`，不要重新读取实时 App graph。
- 节点间只通过 Graph 直接入边传递统一输出 Envelope；每个节点 attempt 使用独立 workspace，`/workspace` 不得充当跨节点通道。上游声明 artifact 只读 staging 到 `/mnt/inputs`，下游不得读取固定路径、未声明文件、祖先输出或历史 Agent session。
- Rerun-from 必须创建新 run，使用当前 App graph，只复制起点前可复用成功/跳过祖先 step；仅复制正式 Envelope 中声明且完整性通过的 artifact，不扫描 `path` / `*_path` 或任意 workspace 文件。复用 condition 的 branch key 必须在当前 Graph 上重新计算冻结分支的 skipped 集合，起点位于未选分支时返回 409；condition 分支测试只写新 run snapshot override。
- Run 事件持久化到数据库；`RunHub` 只负责当前进程广播、取消和等待信号。
- Run 只有在唯一 output Step 为 `success`、其它 Step 均为 `success` / `skipped` 且 Artifact 最终复验通过时才能成功。失败保留 `error`，并用 `failure_kind` 区分 `runtime`、`contract`、`routing`、`integrity`、`internal`；业务验收不通过必须作为正常业务输出表达。
- 后端启动除标记未完成 Run 为 `interrupted` 外，还会删除数据库中已不存在的普通 Run workspace，并保留 `_nlcompile` 等特殊 workspace。
- Run Files 和 Step Trace 按需从成功 artifact contract Step 的声明产物组装，不扫描 workspace，也不新增 artifact 表。响应必须返回 `sha256` 和 `integrity`（`verified` / `modified`）以及 hash-bound 签名下载链接，不返回内部 `path`，禁止泄漏 runtime 本地绝对路径；`modified` 产物不可下载。
- NL compile 使用 `nlcompile_sessions` 持久化两阶段流程：plan 返回可确认方案，apply 才生成 graph；active/resume/refine/cancel 必须校验会话归属。
- Prompt Assistant 使用 `prompt_assistant_generations` 持久化生成和等待态；按钮调用可 ask_user，一旦 waiting 应允许后续 resume 重放。
- ask_user 请求必须包含 `context.title` 和 `context.summary`；选项标准化集中在 `decision_prompts.py`：校验 2-3 个真实选项、唯一推荐项，再追加 `以上都不是`。`ask_user_enabled` 只允许出现在 generate 节点且必须为 bool；`false` 完全跳过该节点的运行期 preflight，省略或 `true` 使用默认 preflight 判定。

## Commands

- 安装依赖：`cd backend && uv sync`
- 开发启动：`cd backend && uv run python scripts/dev.py`
- 测试：`cd backend && uv run pytest -q`
- 编译：`cd backend && uv run python -m compileall app scripts`
- 迁移验证：`cd backend && uv run alembic upgrade head && uv run alembic current && uv run alembic check`

## Editing Rules

- Router 只做鉴权、schema 接收、HTTP 错误转换和 service 调用；复杂流程放 service。
- ORM 结构变化必须新增 Alembic migration，并同步 schema、serializer、service、前端类型和测试。
- MockRuntime 只能在 `tests/` 中使用，生产代码不得依赖测试 runtime。
- 修改 graph 节点类型、Run/SSE 事件、Settings/Agent/Skill/MCP/Prompt 契约时，同步前端类型/API 客户端和相关文档。
- 开发阶段修改任何 seed 后，必须同步开发数据库和 `deploy` 数据库，或在回复中明确说明未同步的原因；修改 prompt seed 还必须确认变量名与 `app/services/prompts.py` 调用一致。
- Seed-only 改动至少运行 `git diff --check` 和相关测试。
- 后端行为改动按影响范围运行 pytest；至少运行 `uv run python -m compileall app scripts`。
