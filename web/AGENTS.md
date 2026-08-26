# AGENTS.md

本文件约束 `web/` 前端应用。除非用户明确要求，解释、计划和总结默认使用中文；代码、命令、日志、错误信息和标识符保持原文。

## Scope

`web/` 是 Mira 的 React + Vite + TypeScript 单页前端，负责 Home、Editor、Preview、App View、Mobile Run 和管理员 Settings UI。

前端通过相对路径 `/api` 访问后端，通过 `GET /api/runs/{run_id}/events` 接收 per-run SSE。Vite 开发代理指向 `localhost:8000`。

## Structure

- `src/main.tsx`：React 入口。
- `src/routes.tsx`：桌面/手机路由、鉴权和管理员 Codex 初始化 gate。
- `src/types.ts`：核心数据契约，必须与后端 schema 对齐。
- `src/lib/`：HTTP API、SSE、auth、mobile route helper 和通用工具。
- `src/stores/`：Zustand 状态层，负责 Apps、Editor、Run、Settings、Auth 等跨组件流程。
- `src/pages/`：路由级页面；`src/pages/mobile/` 是手机端执行入口。
- `src/components/`：通用 UI、编辑器、预览/运行、首页、设置和手机端组件。

## Data Flow And Contracts

- 所有 HTTP 请求通过 `src/lib/api.ts`，SSE 通过 `src/lib/ws.ts`；组件不要散落 `fetch`。
- `src/types.ts` 是前端契约事实来源；新增或修改字段时同步后端 schema、serializer、API 和测试。
- Home 数据通过 `useAppStore` 加载我的应用、模板、市场应用和最近运行。模板来自 `GET /api/apps?gallery=true`，市场来自 `GET /api/apps?market=true`，最近运行来自 `GET /api/apps/recent-runs`。
- Editor 的 canonical graph 在 `useEditorStore.app.graph`；Canvas 的 React Flow 本地状态只服务拖拽、选择和连线交互。
- Preview、App View 和 Mobile Run 共用 `useRunStore`。运行态 UI 使用 `useRunStore.runGraph` 的 run 快照，不使用当前编辑器 graph 伪造历史状态。
- `useRunStore.status` 是前端 UI 状态，包含 `idle` / `starting` / 后端真实 run status；只有存在 `runId` 且状态可取消时才能调用 cancel。
- SSE 历史重放必须消费完整的运行生命周期；`run.resumed` 将状态切回 `running` 并清除旧 `waitingInput`，避免刷新后用已回答的 `step.waiting` 覆盖当前 Run 快照。
- 刷新或重新进入时通过 `useRunStore.restoreActiveRun` 恢复 active run；`pending`、`running`、`waiting_for_user` 进入 live SSE，`interrupted` 显示继续运行语义，终态 run 才按历史回放展示。
- 运行和发布前使用 workflow lint；error 阻断，warning 只提示。`can_view_source=false` 的市场应用必须让后端基于真实 graph 预检，前端不展示内部节点/prompt 细节。
- Run artifacts 通过 `GET /api/runs/:id/artifacts` 展示，Step Trace 的 artifacts 也由后端返回；两者只包含成功 artifact contract Step 声明的产物，不扫描 workspace。前端契约接收后端返回的 artifact identity、`origin` / 可选 `reused_from` lineage、`sha256`、`integrity`（`verified` / `modified`）和 `download_url`；响应没有内部 `path`，下载只使用 `download_url`，也不从 HTML 输出扫描文件链接。Output HTML 如需内嵌下载或图片预览，只能使用 `data-mira-artifact-download` / `data-mira-artifact-preview` 按正式 artifact 展示名声明占位；`HtmlOutputFrame` 根据当前 Run artifacts API 返回的同名 `verified` 产物绑定签名 URL，不接受 HTML 自带的文件路径或 URL。
- Prompt Assistant 生成态当前保存在 `useEditorStore.promptAssistantGenerations`，由 StepTab 发起 generate/resume/cancel；前端尚未通过 active endpoint 做刷新后恢复。若补恢复，必须先补 `lib/api.ts` helper，再接入 Editor/StepTab 恢复流程。

## Workflow Rules

- Workflow 节点类型是 `user_input`、`asset`、`generate`、`condition`、`output`。最多一个 `user_input` 和一个 `output`；`output` 是终点节点，不能出边。
- Graph 使用 `execution_edges` 表达执行顺序；画布连线不表达字段绑定。一次 Run 的线性节点共享 Agent 会话和 workspace，真正并行时由后端 fork/merge；不要为了数据引用添加已有传递路径覆盖的冗余直连线。
- 启动运行时，`user_input` 的文本或附件至少一项非空即可提交；桌面与手机必须都先通过 uploads API 上传真实文件，再发送 upload 引用，不能只保存浏览器文件名。`PillInputBar` 在选中文件后立即上传：图片显示缩略图（上传中灰色蒙层+转圈），非图片显示文件名；仍有附件在传或失败时不能提交。
- 素材节点字段必须遵守 `types.ts`：文本 `content`，URL `urls[]`，文件 `uploads[]`，画板 `upload`。
- 所有应用固定使用 Codex；Graph 不保存运行时选择字段或提问开关。`generate` / `condition` / `output` 节点只保存 prompt、model、reasoning_effort、output_contract 等节点级字段。
- App 级 Tools 排除项写入 `graph.tools.disabled_tool_ids`。Preview、App View 和 Mobile Run 可展示/调整 App 级 Tools；不要把 Tools 重新做成 generate 节点配置。
- `generate.output_contract` 只在 generate 节点配置，支持 json/html/artifact；普通 generate 默认自由文本且不保存契约。只有下游需要稳定字段、当前节点明确输出 HTML 片段或需要可下载文件产物时才使用契约。JSON 契约必须携带 strict object `json_schema`，artifact 必须携带 `artifact_kind`；`zip` kind 表示只接受真实 `.zip` 文件，`validate_office_documents` 是 artifact-only 的可选严格打开校验；`output` 节点固定为 HTML 最终展示。
- JSON output contract 和 Schema 仅作为内部校验契约，由 Prompt Assistant 根据提示词维护；普通用户界面不展示 JSON Schema、字段大纲、字段引用或“可引用结果”选项。
- 运行期除 `output` 外的 LLM 节点统一先进入 Codex Plan，由原生 `requestUserInput` 决定是否等待用户输入；前端不提供节点级提问开关。
- 条件分支 edge 必须保持 `branch_key` 与分支 key 对齐；`CONDITION_DEFAULT_BRANCH_KEY` 是系统保留 fallback key。
- 从历史 run 节点重新执行、失败修复和 condition 分支测试走 `useRunStore.rerunFrom`，创建 checkpoint rerun：cut 前状态冻结，当前 App graph 只用于 cut 及下游；不修改来源 run 或 App graph。

## UI And Product Rules

- 桌面 Home 的“我的应用”和“最近使用”是同组 tab；应用市场只在“我的应用”tab 下展示。模板点击先导入为当前用户草稿。
- App View 遇到 `can_edit=false` 或 `/market/apps/:id` 路由必须保持只读，不显示编辑、发布、版本管理等 owner-only 入口。
- Mobile 路由通过 viewport media query 切换到 `/m`，不要改成 UA 判断。手机端只承载登录、应用列表、最近运行、市场、运行、历史、结果和 owner 运行设置，不加节点编辑器或 Settings。
- Mobile Run 不使用 `useEditorStore`；owner 运行设置可写回节点 model 和 `graph.tools.disabled_tool_ids`，不编辑节点 prompt、结构或 reasoning_effort。
- `decision_request` UI 统一使用后端返回的 context/groups/options。面板先显示 `context.title` 和 `context.summary`；每组必须选择选项，补充文字和附件始终可用；多问题只在最后一题提交；提交后显示“已选择 / 已补充”摘要并保留停止入口。
- 自然语言编辑首次提交允许附件；必须先通过 uploads API 获得 upload id，再随 `POST /api/nlcompile` 发送引用，不能只把文件名拼进 instruction。
- HTML 输出只通过 `HtmlOutputFrame` iframe 隔离渲染；滚动应由外层 Preview/App View/Mobile 容器承载。
- Skill 的 `dependency_status` / `dependency_error` 来自后端 Settings 契约；`pending` 或 `failed` 时 UI 只能继续禁用、预览或删除，不能发起启用或设为规划可用。
- 视觉改动保持现有 Tailwind、黑白灰和少量强调色体系；图标优先使用现有组件或 `lucide-react`。
- 桌面 `/wiki` 管理当前用户的长期 Wiki；Mobile 只遵守授权与自动使用规则，不提供 Wiki 管理。原始文件上传只接受可转换文档和图片，压缩包等无法解析的格式由前后端同时拒绝。第三方 App 的 Wiki 授权必须按后端返回的 graph digest 确认，也必须保留“不使用 Wiki 运行”。

## Commands

- 安装依赖：`cd web && npm ci`
- 开发启动：`cd web && npm run dev -- --host 0.0.0.0`
- 类型检查：`cd web && npm run typecheck`
- 构建：`cd web && npm run build`

## Editing Rules

- 保持现有 React 函数组件、hooks、Zustand store、Tailwind class 风格。
- 修改契约时先更新 `src/types.ts`，再更新 API/SSE 客户端、store、组件和后端对应 schema。
- 修改 run 流程必须同时检查 Preview、App View 和 Mobile Run。
- 修改编辑器 graph 流程必须保持 canonical graph、React Flow 本地状态、保存、undo/redo 和 Step 面板一致。
- 文本输入中的 undo/redo 交给浏览器原生行为；画布快捷键不要劫持 `input`、`textarea` 或 `contentEditable`。
- 不引入新的全局 UI 库、toast 系统或设计系统，除非用户明确要求。
- TypeScript 或契约改动至少运行 `npm run typecheck`；UI 行为改动需要手动检查相关路由。
