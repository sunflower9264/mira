# AGENTS.md

本文件约束 `backend/`。这里是 Mira 的 FastAPI 后端，负责认证、Apps/Versions、全局 Settings 与 Tools、Uploads、工作流运行、SSE、NL compile、Prompt Assistant，以及唯一的 Codex Docker runtime。修改 wire shape 时必须同步 `web/src/types.ts`、`web/src/lib/api.ts` 和 `web/src/lib/ws.ts`。

## 目录与入口

- `app/main.py`：FastAPI 装配、lifespan、异常处理和磁盘监控；启动时初始化数据库并同步 prompts、runtime config、Skills、gallery、未完成会话和 workspace GC。
- `app/api/`：HTTP/SSE router 与认证依赖；复杂业务不放在 router。
- `app/models/`、`app/schemas/`：SQLAlchemy 数据结构与 Pydantic wire shape。
- `app/services/`：权限、graph、Apps、Runs、RunAgent、Artifacts、Tools、NL compile 和 Prompt Assistant 等业务规则。
- `app/runtime/`：Codex App Server JSON-RPC adapter 与 Docker sandbox；生产 runtime 只有 Codex。
- `runtime/`：Agent 镜像、受管浏览器入口和批量截图工具。
- `scripts/`：开发启动、环境/管理员初始化、runtime 镜像检查、维护脚本和真实 smoke。
- `migrations/`、`seeds/`、`tests/`：Alembic、gallery/prompt 源数据和 pytest。

推荐先读 `README.md`、`app/AGENTS.md`、目标子目录的 `AGENTS.md`，再读对应 router/schema/service；运行链路优先读 `app/services/runs.py`、`run_orchestrator.py`、`run_agent.py`、`node_handlers.py` 和 `app/runtime/`。

## 启动与环境边界

- 开发入口是 `cd backend && uv sync && uv run python scripts/dev.py`。它依次运行 `init_env.py`、`ensure_runtimes.py`、`init_admin.py`，再以 reload 模式启动 `app.main:app` 于 `0.0.0.0:8000`。
- `ensure_runtimes.py` 根据 `runtime/Dockerfile`、Playwright config 和两个 runtime helper 的摘要检查/重建 `RUNTIME_SANDBOX_IMAGE`。Docker 或构建失败当前只告警并继续启动；真实 Agent 调用会在 Settings/status 或执行时报告镜像不可用。
- `backend/data/`、`backend/logs/`、`backend/runtime/homes/`、`backend/runtime/workspaces/` 是本地运行产物，不作为源码维护。路径统一经 `app/services/runtime_paths.py` 计算。
- Codex config/auth 正文加密存库；shared/scoped fake HOME 是派生物，可被重写。不要读取宿主机 Codex 登录态，也不要恢复宿主机直跑 Codex。
- Agent 容器启用 Docker init，只挂载当前 branch workspace、scoped HOME、必要输入和 Skill 依赖；不挂载宿主 HOME、仓库根、Docker socket、`.env` 或其他用户 workspace。Office artifact 深检在后端宿主机的独立受限进程中执行，不打入 Agent 镜像。
- Run 可额外把冻结 Wiki snapshot 只读挂载到 `/mnt/wiki`。Wiki 不复制进 branch workspace/checkpoint；NL compile 与 Prompt Assistant 不挂载 Wiki。

## Runtime、Skill 与浏览器

- `runtime/Dockerfile` 当前基于 Python 3.12、Node.js 22，固定 `@openai/codex` 0.147.0 与 `@playwright/cli` 0.1.18。Chromium 来自镜像构建时的 Debian 包，受管入口固定绑定 `/usr/bin/chromium`，不要把其发行版版本误写成源码级 pin。
- 交互式浏览器操作先运行 `mira-browser doctor`，随后始终使用 `mira-browser open/snapshot/click/...`。入口拒绝替换 browser/config/profile/CDP/headed 模式，也禁止把 `npx`、运行期 `npm install` 或 Playwright 浏览器下载当作补依赖方案。
- `/opt/mira/capture_screenshots.py` 是独立的路由批量截图工具：`--project-dir` 必须位于当前 workspace，项目必须已有 `node_modules`；工具不安装依赖，按声明执行 `db:init`、`db:seed`，HTTP `<400` 后才调用 `/usr/bin/chromium`。只有截图数达标且无失败才生成仅含 PNG、manifest、日志的 ZIP；失败返回非零并删除旧 ZIP。
- Skill ZIP 必须且只能有一个 `SKILL.md`，不得携带托管 `.deps/`。同目录可声明 `requirements.lock` 或 `requirements.txt`（lock 优先）；只允许包索引形式的纯 Python requirements，并以 binary wheel 构建为内容寻址依赖层。层与 runtime 镜像身份绑定、运行时只读挂载到 Skill 根目录 `.deps/`，Skill 代码自行加入 `sys.path`；系统包、二进制和动态库仍属于 runtime 镜像。
- Skill 上传时构建依赖，启动时会对当前镜像重新协调缓存；依赖失败会禁用 Skill 与 planning。App run 使用启动时写入 graph 的允许 Tool 快照，再与当前仍启用的 MCP/Skills 求交集；planning 还要求 `planning_enabled=true`。

## 数据、Graph 与 Run 规则

- 普通资源查询必须从当前登录用户推导 owner；Apps、Versions、Runs、Steps、Events/Logs、Uploads 和 runtime workspace 按用户隔离。Settings、Skills、MCP、Codex config、supported models、Instructions、Prompt Templates 是全局管理数据，写操作 admin-only。
- Workflow 节点类型只有 `user_input`、`asset`、`generate`、`condition`、`output`；最多一个输入和一个输出。可执行 graph 必须恰有一个有正式上游的终点 `output`，所有节点及 condition 已连接分支都可到达它。
- `execution_edges` 只表达执行顺序，condition 出边用 `branch_key`；普通连线不承担字段绑定。Run 创建时保存可执行 graph 与 Tool 允许列表快照，执行、恢复、历史和 rerun-from 都以 `Run.graph_json` 为准。
- 一次 Run 由一个逻辑 RunAgent 管理 thread lineage、branch workspace 和 checkpoint。线性节点复用 thread/workspace；真实 fan-out 才 `thread/fork` 并复制 checkpoint，fan-in 由协调 Codex 基于完整分支证据合并并校验 receipt。
- `user_input` 接受文本、附件或两者组合，至少一项非空；它与 `asset` 都把上下文写入 `.mira/run-context/`，附件副本写入 `inputs/`；`/mnt/inputs` 只暂挂当前决策请求附件。不要恢复 `/mnt/results`、每节点独立 workspace 或手工 sidecar/handoff 通道。
- 除 `output` 外的 LLM 节点先以 `collaborationMode=plan` 判断是否缺少关键用户决策；原生 `item/tool/requestUserInput` 归一化为 Mira waiting/resume，并用同一 JSON-RPC request id 回填。执行 turn 使用可写 policy；不通过 prompt 或 MCP 另造提问协议。
- `generate.output_contract` 支持 `json`、`html`、`artifact`；未配置即自由文本。JSON 需要受支持子集内的 strict object schema；HTML 使用 `{"html":"..."}`；artifact 只接受当前 branch workspace 内的真实文件并提交到 run 级只读 artifact 目录。`output` 节点始终是 HTML-only 最终展示节点。
- 正式产物只来自成功 artifact contract Step 的版本化 Envelope，含 holder/origin/可选 reused_from、相对路径、大小、SHA-256、kind 和 manifest version。Files API 与 Trace 的 artifact 部分不扫描 workspace、不新增 artifact 表、不返回内部路径；下载链接绑定 hash，已修改文件不可下载，Run 成功前还会统一复验。
- Artifact 文本及受支持归档成员拒绝 U+FFFD、危险路径/链接/特殊文件和伪造容器。`validate_office_documents=true` 仅适用于 `docx`、`excel`、`ppt`、`zip`、`file`，并要求宿主机隔离校验器真实转换、检查页数和文字边界；基础设施缺失时 fail closed。
- ready 节点按直接前置依赖并发执行。Run 仅在唯一 output Step 成功、其他 Step 为 `success` / `skipped` / checkpoint reuse，且 artifact 复验通过后成功；引擎失败用 `runtime`、`contract`、`routing`、`integrity`、`internal` 分类，业务验收不通过仍是正常业务输出。
- rerun-from 创建新 Run，读取当前 App graph，但从来源 Run 的 cut 前 checkpoint/thread lineage/Step 结果冻结复用；没有有效 pre-checkpoint 返回 409，旧 Run 保持只读。condition 分支测试只在新 Run snapshot 写 override。
- `run_only` 市场应用对非 owner 可运行但不可克隆；App/Run/SSE/artifact 走集中脱敏，Step Trace 对受保护 viewer 返回 403。`system_gallery` 是只读 seed 源，只能克隆后编辑。

## 编辑与验证

- Router 只做鉴权、schema 接收、HTTP 映射和 service 调用。ORM 变化必须新增 migration，并同步 schema、serializer、service、前端类型和测试。
- Prompt seed 以 `seeds/prompts/` 为源码事实来源；Settings 保存会同步 seed。修改任何 seed 后同步开发与 deploy 数据库，做不到时明确说明。
- 页面 Prompt Assistant 与 NL compile 节点提示词后处理分别使用 `prompt_assistant`、`nlcompile_prompt_refiner`；前者走 Codex Plan、使用 Codex 配置默认模型与推理强度并最多提问一轮，后者在 apply 阶段禁止提问。
- 后端行为改动按范围运行 pytest，至少执行 `cd backend && uv run python -m compileall app scripts`；迁移执行 `uv run alembic upgrade head && uv run alembic current && uv run alembic check`。跨 wire shape 改动还要运行前端 typecheck，文档改动执行 `git diff --check`。
