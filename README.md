# Mira

Mira 是一个参考 Google Opal 思路的可视化 AI 应用搭建与运行项目。它把自然语言编辑、节点式工作流、Agent 运行时、运行预览和中段交互组合在一起，用于快速复刻并优化无代码 AI 迷你应用构建器体验。

> Mira 不是 Google 官方项目，也不隶属于 Google。Google Opal 是 Google 的产品或实验名称；本项目仅声明参考其产品思路进行复刻和优化。

## 功能

- 可视化工作流编辑：通过 `input`、`asset`、`generate`、`condition`、`output` 等节点搭建应用；每个工作流最多一个 `input` 和一个 `output`，画布提供背景网格、AI 一键布局美化、选中后键盘删除，以及 `Ctrl/Cmd+Z` 撤销和 `Ctrl/Cmd+Y` / `Ctrl/Cmd+Shift+Z` 重做。
- 素材节点：文本素材保存单段 `content`，链接素材保存多个 `urls`，文件素材保存多个 `uploads`，画板素材保存单个 `upload`；运行时文件素材会输出包含 `path` 和签名 `download_url` 的上传元数据数组。
- 自然语言辅助编辑：用 Agent 生成或调整 graph；方案会持久化到数据库，刷新页面、离开页面或后端重启后可通过 active 会话恢复，确认弹窗支持在同一会话内补充说明并迭代方案。
- 节点提示词助手：编辑 `generate`、`condition`、`output` 节点时可调用当前应用 Agent，先判断用户输入是新目标还是对已有提示词的修改请求，再生成或最小改动 prompt；必要时会先通过 `ask_user` 追问一次，并可在生成中中止。
- `generate` 输出契约：默认使用自由文本且不设置契约；只有下游需要稳定读取字段、当前节点明确产出 HTML 片段或生成可下载文件时才使用契约。结构化 JSON 必须提供 strict object `json_schema`；HTML 通过 `{"html":"..."}` wrapper 校验并原样保存；文件产物通过 `artifact_kind` 细分且只接受运行工作区内 `path`。运行时会把契约传给支持 schema 的 Agent CLI，并在后端二次校验，失败后自动修正一次。`output` 节点仍固定生成 HTML，用于最终预览展示。
- 文件产物列表：运行完成后，Preview、App View 和手机端结果页会展示 run workspace 文件以及 artifact 输出契约声明的文件产物，并提供签名下载链接。
- 节点级模型与推理等级：桌面编辑器可为 `generate`、`condition`、`output` 节点选择模型和推理等级；未选择时推理等级默认最低。
- 工作流执行与结果查看：在编辑器、独立 App View 和只读应用市场 App 页面中执行工作流，查看输出、文件产物和历史；最终 HTML 预览由 iframe 隔离渲染，并由外层结果区统一滚动。
- 应用市场：发布应用时可选择公开可克隆、公开仅运行或仅自己可见；公开仅运行应用会进入应用市场，但其他用户不能克隆，也不能查看节点和 prompt。后端 seed 的内置模板也会在前端应用市场区展示，点击后先导入为当前用户自己的草稿。
- 最近运行/使用：桌面 Home 会在“我的应用”旁以“最近使用”tab 展示当前用户最近运行过的可见应用，应用市场只在“我的应用”tab 下展示；手机端仍提供最近运行入口，包括自己的应用和运行过的市场应用。
- 手机端执行入口：窄屏设备自动进入 `/m`，提供登录、应用列表、最近运行、应用市场、工作流执行、历史回放和结果查看，不包含编辑器与设置页。
- 手机端页面固定为不可缩放视口；输入框聚焦时不会触发浏览器自动放大。
- 运行事件流：后端通过 SSE 推送 step、log、delta、waiting、complete 等运行状态，并持久化事件用于刷新和恢复回放；刷新或从历史记录打开未结束 run 时，前端会恢复 live run 并重新订阅 SSE。
- 停止运行：Preview、App View 和手机端运行中或等待输入时都可停止当前 run；前端会在取消后主动刷新 run 快照并显示“已取消”。
- 执行 Trace 视图：桌面编辑器控制台按工作流连线依赖顺序展示步骤，可查看 `generate`、`condition`、`output` 节点的实际组合 prompt、运行参数、Agent 事件、最终输出和文件产物。
- 运行快照：每次 run 保存启动时工作流 graph；运行中继续编辑节点只影响后续 run，不会改变当前 run 或历史回放。
- 从节点重新执行：历史 run 可基于当前 App graph 创建新 run，并复用所选起点之前已成功或已跳过的前置节点结果。
- 失败节点修复运行：桌面编辑器中失败的 run 可直接聚焦失败节点、调整本次重跑输入，并基于当前 App graph 创建新的修复 run；旧 run 保持只读。
- 条件分支测试：桌面编辑器 Console 可从 `condition` 节点强制指定某个分支创建测试 run，用于验证该分支下游路径；历史记录会展示命中分支、未走分支和判断理由。
- 中段交互：运行中可通过 `ask_user` 等机制暂停并等待用户补充输入。
- 运行恢复：刷新页面或从历史记录打开 pending、running、waiting run 时会回到实时运行态；后端重启后会把未完成 run 标记为 interrupted，前端可选择继续运行或重新运行。
- Agent 配置：管理员可维护 Claude/Codex 配置、支持模型、全局 Tool 库存（MCP/Skills）、Instructions 和 Prompt Templates；Skills 支持上传 zip 后预览包内 `SKILL.md`；App 默认可使用所有已启用 Tools，Preview、App View 和手机端运行设置可在 App 级取消个别 Tool，运行时按 run 快照注入。管理员可把 MCP/Skill 显式标记为规划可用，使其进入 NL compile、Prompt Assistant 和运行期 preflight 的只读规划阶段。
- 用户隔离：Apps、Versions、Runs、Uploads 和 runtime workspace 按登录用户隔离；其他用户的公开已发布应用只能按发布权限只读运行或克隆，不能编辑源应用。
- 应用封面：编辑应用时选择的封面图通过 Uploads 保存；封面只保存 upload id，不支持外部 URL 或 data URL；前端统一按 16:9 居中裁剪展示。

## 功能截图

以下截图来自 `deploy/mira-server` 的本机部署实例。

### 首页与应用市场

![首页与应用市场](docs/screenshots/home-library-market.png)

### 可视化工作流编辑器

![可视化工作流编辑器](docs/screenshots/workflow-editor.png)

### 独立应用运行页

![独立应用运行页](docs/screenshots/app-run-view.png)

### 历史运行结果回放

![历史运行结果回放](docs/screenshots/run-preview-trace.png)

### Settings 中的 Skills 与规划可用状态

![Settings 中的 Skills 与规划可用状态](docs/screenshots/settings-runtime-tools.png)

### 手机端运行入口

![手机端运行入口](docs/screenshots/mobile-run.png)

## 技术栈

- 前端：React 18、Vite 5、TypeScript、Tailwind CSS、React Router、Zustand、React Flow。
- 后端：FastAPI、SQLAlchemy async、Pydantic v2、SQLite、Alembic、uvicorn。
- 运行时：Claude/Codex adapter 在 Docker Linux sandbox 容器内执行，使用隔离的 fake HOME 和 run 级 workspace。

## 目录结构

```text
.
├── web/        # React + Vite 前端
├── backend/    # FastAPI 后端、运行编排、迁移、测试
├── docs/       # 预留文档目录
├── start.sh    # Linux/macOS/WSL2 开发启动脚本
└── start.bat   # 原生 Windows 提示脚本；后端 sandbox runtime 请使用 WSL2
```

重要文件：

- `AGENTS.md`：仓库级 AI 协作规则。
- `web/AGENTS.md`：前端专项规则。
- `backend/AGENTS.md`：后端专项规则。
- `backend/README.md`：后端细节说明和验证清单。

## 快速启动

前置条件：

- Node.js 和 npm
- Python 3.11+
- `uv`
- Docker Engine，或启用 WSL2 integration 的 Docker Desktop，用于 Agent sandbox 容器
- 首次构建 Agent runtime 镜像时需要网络访问

Linux / macOS：

```sh
sh start.sh
```

Windows：

请在 WSL2 Linux shell 内运行 Mira。Docker Desktop 必须启用 WSL integration，仓库建议放在 WSL 文件系统内，不要放在 `/mnt/c`。

```sh
sh start.sh
```

根启动脚本会先停止 `8000` 和 `5173` 端口上的已有监听进程，然后启动后端和前端。

默认地址：

```text
前端：   http://0.0.0.0:5173
后端：   http://0.0.0.0:8000
API 文档：http://0.0.0.0:8000/api/docs
健康检查：http://0.0.0.0:8000/api/health
```

## 手动开发启动

后端：

```sh
cd backend
uv sync
uv run python scripts/dev.py
```

`scripts/dev.py` 会创建或更新 `.env`，检查或构建 Docker Agent runtime 镜像，初始化管理员账号，并在 `8000` 端口启动 uvicorn。

前端：

```sh
cd web
npm ci
npm run dev -- --host 0.0.0.0
```

打开 `http://0.0.0.0:5173`。Vite 会把 `/api` 代理到 `http://localhost:8000`。

## 基础使用

桌面端使用：

1. 打开前端地址并登录。
2. 使用管理员账号进入 Settings，至少配置一个 Agent provider 及其支持模型。
3. 在 Home 创建应用，使用内置模板，或从应用市场克隆允许克隆的应用。
4. 打开编辑器，添加或编辑工作流节点，或使用自然语言输入条生成变更。在 `generate`、`condition`、`output` 节点的步骤面板中，“生成提示词”会调用当前应用选择的 Agent，结合已有提示词判断是生成新 prompt 还是按用户要求最小修改当前 prompt。
5. 在 Console 和步骤面板中查看流式输出、日志、等待输入和最终结果。
6. 工作流可用后，可以发布应用或在 App View 中打开使用。发布支持公开可克隆、公开仅运行和仅自己可见。Mira 会在运行或发布前执行工作流预检；错误会阻断操作，警告只作为提示。非空工作流必须包含输出节点。

手机端使用：

1. 在手机浏览器打开同一个前端地址；窄屏会从 `/` 自动进入 `/m`。手机端会锁定页面缩放，避免点击输入框时浏览器自动放大页面。
2. 登录后，可在“我的应用”中直接运行已有应用，也可在“最近运行”中打开运行过的应用，或在“应用市场”中使用模板、运行公开应用；只有允许克隆的市场应用能克隆成自己的草稿。
3. 自己的应用运行页右上角可打开运行设置，选择 Agent、模型和 App 级 Tools；该设置会写回应用 graph，桌面端后续运行也会使用同一配置。手机端不编辑节点推理等级。市场只读应用不显示运行设置。
4. 开始运行前会展示工作流预检结果；error 阻断运行，warning 只提示。非空工作流必须包含输出节点。如果应用包含用户输入节点，Preview、App View 和手机端都会在启动前要求填写该输入。运行设置可调整 Agent、模型和 App 级 Tools。
5. 运行中可查看步骤进度、等待补充输入、打开历史记录或查看最终结果。刷新页面或从历史记录重新打开未结束 run 时，会继续显示实时运行进度。

使用自然语言编辑时，Mira 会在后端工作期间保持已提交 prompt 可见。发送按钮会变成停止按钮；点击后会取消后端 compile 会话并解锁 prompt 供继续编辑。第一次 `nlcompile` 调用只生成待确认的结构化方案，不直接返回 graph 或 patches。新建 workflow 或大范围编辑本身不会触发追问；只有缺失的业务决策无法推断且会明显改变用户可见结果或 graph 拓扑时才调用 `ask_user`。NL compile 会话会把 app id、graph 快照、方案、待回答问题和结构化历史保存到后端数据库，因此编辑器刷新或跳转后可通过 `GET /api/apps/{app_id}/nlcompile/active` 恢复最近的 active 会话；没有 active 会话时该接口返回 `204 No Content`。后端在 planning、waiting 或 applying 状态重启时，Mira 会把会话标记为 `interrupted`，并保留足够历史以便重放结构化 prompt 上下文继续处理。确认弹窗默认展示精简审核视图：目标摘要和“本次会改什么”，优先使用 `graph_changes`，必要时才回退到实施步骤。完整方案仍包含假设、数据流、实施步骤、画布变更、预计输入、预计输出和验收标准；关键步骤、变更、输入、输出和验收列表必须包含可核对内容，数据流文本使用中文用户可见节点名称，而不是原始 id 或英文节点类型。

方案阶段的 prompt 会控制 Mira 是否在起草方案前通过 `ask_user` 追问；默认会在关键决策缺失时提问，只在确定性小改动中跳过。每次追问必须包含 `context.title` 和 `context.summary`，前端会先展示本轮提问主题和原因，再展示具体问题。追问要求每组选择一个选项；已选选项可再次点击清除，多问题场景会在用户前后切换时保留每题的补充内容。Mira 要求 Agent 提供每题 2-3 个真实选项对象，每个选项包含 label、取舍说明和一个推荐默认项，然后追加固定选项 `以上都不是`；用户选择该项后会作为普通工具返回交给 Agent 处理。当前问题始终可以添加自定义文字或文件附件，不再依赖选择 `以上都不是`。多问题场景只在最后一题显示最终提交入口。`ask_user` 期间，用户通过独立的“提交回答”按钮提交选项、补充文字和文件；输入条右侧发送按钮只在 `ask_user` 状态隐藏，普通自然语言 prompt 仍可使用。提交后，Mira 会清空输入框，仅显示“已选择 / 已补充”摘要行和上传文件名，并把提交按钮变成红色停止按钮。待回答问题一旦持久化，即使原 runtime tool 等待超时，Mira 也能把用户答案重放进新的方案调用。

`nlcompile` 方案调用使用 planning/read-only policy，包含 `ask_user` 和显式标记为规划可用的 App MCP/Skills；用户确认方案后，Mira 调用 apply 接口，根据已确认方案生成 graph patches、执行校验、运行 Prompt Assistant 后处理、美化布局，并把结果写回画布。内部 patch 协议为五类节点、普通边和 condition 分支边提供明确 JSON 形状；apply 至少需要一个真实 patch，空 patch 不再作为成功返回。Patch 只负责业务拓扑，坐标由后置布局处理；新增节点会先获得 fallback 坐标，因此布局失败时 graph 仍可编辑。确认弹窗中的补充修改说明会调用 `POST /api/nlcompile/{compile_id}/refine`，复用同一个 compile 会话，并重放之前的问题、回答、方案和反馈；refine 最多 5 轮。后端会把生成的 patches 作为批次校验，并在解析或 graph 校验失败时让 Agent 修复，所以失败的 `nlcompile` 请求不会返回部分应用的 graph。普通自然语言 prompt 输入只在后端成功响应后重置。

步骤面板的“生成提示词”操作会完整传入目标节点 prompt，并为相邻节点保留开头和结尾上下文；generate 节点当前的 `output_contract` 也会一并提供，避免最小编辑丢失尾部约束或误改输出形态。完整上下文超过 200 KiB 时接口会明确拒绝，而不是静默截断。请求过于模糊时，该操作也可以暂停并进行一次 `ask_user` 决策。后端会返回带 `generation_id` 的 `waiting_for_user`；前端在生成气泡内渲染相同的选项 UI，通过独立“提交回答”按钮提交，再调用 `POST /api/prompt-assistant/{generation_id}/resume` 继续。Prompt Assistant 等待会话会保存到后端数据库，并可通过 `GET /api/apps/{app_id}/prompt-assistant/active` 恢复；没有等待会话时该接口返回 `204 No Content`。Agent 容器只为快速回答短暂保留，随后释放，但 Mira 会继续保存待回答问题。

应用运行期间，Agent 的 `ask_user` 等待提示使用与 NL compile 追问相同的交互模型：每次问题必须带 `context.title` 和 `context.summary`，每组问题必须选择一个选项，当前问题始终可补充文字或附件。已选选项可再次点击清除。多问题补充输入按问题隔离，并在用户前后切换时保留；最终提交只在最后一题显示，且始终使用独立“提交回答”按钮，而不是输入条右侧发送按钮。提交后，Mira 会隐藏补充输入条，只显示“已选择 / 已补充”摘要行和上传文件名，并把提交按钮变成红色停止按钮，直到 run 恢复。Runtime 的 `generate` / `condition` 节点会先进入受限的 `ask_user` preflight；`output` 节点跳过该阶段并直接执行最终 HTML 渲染；带输出契约且已有直接用户输入的 `generate` 节点在 prompt 未强制 ask_user 时也会跳过 preflight。preflight 只能使用只读 runtime 能力和显式规划可用的 App MCP/Skills。Agent 必须返回结构化 `action=ask` 或 `action=complete` JSON，Mira 会持久化每轮问题和回答后再恢复同一节点。推荐、选择、个性化、需求澄清和方案收敛类任务缺少关键偏好或约束时，应主动提问，而不是静默选择默认方向。正常执行调用只会在用户决策被总结后开始。同一个固定选项 `以上都不是` 会追加到运行时问题里；多选问题中它与其他选项互斥。

Run 执行是依赖驱动的：直接上游完成后，ready 节点即可并发运行。线性 LLM 链会复用上一步的 Agent session；并行分支、fan-out、fan-in 或相互独立的节点会使用独立 session，并通过显式上游输出交换上下文。

Run 生成的文件会在运行完成后作为一等 artifact 展示。Mira 会在 Preview、App View 和 Mobile Run 中列出 workspace 文件以及 `artifact` 输出契约条目；artifact 契约只接受运行工作区内文件路径，下载使用签名 `/api/runs/{run_id}/artifacts/...` URL，不暴露本地 runtime 路径。

后端 seed 的内置模板是只读源应用；后端仍通过模板接口独立返回，前端会在应用市场区合并展示。使用模板会先创建一个可编辑草稿。

后端进程在应用运行期间重启时，Mira 会保留已完成步骤并把 run 标记为 interrupted。继续运行会跳过已完成节点，并从第一个未完成节点开始，使用 run 保存的 graph 快照恢复，因此 run 启动后的 App 编辑不会改变恢复路径。如果中断节点有 Agent session id，Mira 会尝试恢复同一个 Claude/Codex session。这是节点级恢复，不是 token 级 checkpoint，因此中断节点内部的副作用可能需要用户检查。

在桌面编辑器回放已完成 run 时，选择当前 App graph 中的节点可以从该节点创建新 run。新 run 使用当前 App graph 快照，并复用所选历史 run 中匹配的成功或跳过祖先步骤；来源 run 保持只读。失败 run 还会在桌面编辑器显示修复栏：用户可聚焦失败节点、编辑下一次尝试的 run 输入，并从失败节点重新运行。如果被编辑的输入是失败节点的上游依赖，Mira 会从该输入节点开始重放，确保新值传递到下游步骤。Console 中的 `condition` 节点也能创建分支测试 run，在新 run 快照里强制指定分支，但不会修改 App graph。

默认管理员凭据来自 `backend/.env`。本地开发时，`.env.example` 当前使用：

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
```

首次启动前必须把 `ADMIN_PASSWORD` 改成真实密码；后端会拒绝使用占位符启动。

普通用户暂不支持前端注册。需要添加普通用户时，在后端本地执行：

```sh
cd backend
uv run python scripts/create_user.py --username <username>
```

脚本会交互输入密码；开发或自动化场景可传 `--password <password>`。

## 验证命令

前端：

```sh
cd web
npm run typecheck
npm run build
```

后端：

```sh
cd backend
uv run pytest -q
uv run python -m compileall app scripts
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

仅文档改动：

```sh
git diff --check
```

## 注意事项

- Runtime data、logs、本地数据库、生成的 homes 和 workspaces 都是本地开发产物，不应当作为源码维护。
- 应用封面图片以用户 uploads 形式保存，并通过 app cover API 读取；`App.cover` 只保存 upload id。前端按 16:9 居中裁剪展示封面。
- Agent config 内容加密存储在后端数据库中；请把 `AGENT_CONFIG_SECRET` 与数据库备份一起妥善保存。
- 支持模型列表由管理员在 Settings 中手动维护。Mira 不会从 Claude/Codex config 文本、CLI 状态或 auth 文件中自动发现或补入模型。
- 本 README 主要覆盖开发和试用；单机部署参考 `docs/deployment.md`。生产部署涉及的进程守护、HTTPS、备份和监控等，应根据目标环境另行设计。
