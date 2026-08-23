# AGENTS.md

本文件约束 `backend/app/services/`。

## Role

`services/` 是后端业务逻辑层，负责 Apps、Runs、Settings、Skills、Prompt Templates、Uploads、Tools、runtime config、run orchestration、serialization、NL compile、Prompt Assistant、artifacts 和 Trace。

## Rules

- Router 只编排 HTTP；权限判断、持久化、graph 校验、runtime 调用、脱敏和业务流程放 service。
- 数据访问必须带权限边界；用户资源查询按当前用户过滤，禁止使用外部传入 `user_id` 决定 owner。
- Apps service 集中处理可见性、`market_access`、`can_edit`、`can_clone`、source redaction、gallery seed 和跨用户 clone 上传复制。
- `system_gallery` 源应用永远只读；模板通过 gallery 列表返回，不混入普通 market 列表。
- Workflow lint 是运行/发布前可读预检层；error 阻断，warning/info 只提示，不替代 hard graph validation。
- Run 编排必须使用 `Run.graph_json` 快照；创建后 App graph 编辑不影响该 run。
- `execution_plan.py` 是执行拓扑、直接前置/后继、传递祖先和后代关系的统一 module；调度只等待直接前置。
- `run_agent.py` 统一拥有 Run 的 Codex thread lineage、branch workspace、checkpoint、fan-out 和 fan-in；orchestrator 不得另建跨节点数据通道。
- Run 执行按依赖驱动，ready 节点可并发；线性节点共享 thread/workspace，真实 fan-out 才调用 `thread/fork`，fan-in 必须经协调 Agent 合并和 receipt 校验。
- Run 事件持久化用于刷新、SSE replay 和历史；`RunHub` 只处理当前进程广播、取消和等待信号。
- Rerun-from 创建新 run，冻结来源 run 的 cut 前 checkpoint、Codex thread lineage 和 step 状态，读取当前 App graph 执行 cut 及后代；不得因上游 input override 扩大 cut。
- condition 分支测试通过新 run snapshot 的 override 强制分支，不修改 App graph。
- `node_handlers.py` 集中执行节点；asset 节点按 `content` / `urls` / `uploads` / `upload` 读取并写入共享 workspace context；output 节点保持 HTML-only。未声明文件可留在 workspace 供本 Run 后续节点使用，但不得生成下载旁路。
- `output_contracts.py` 负责 generate/output 输出契约 schema 生成、校验和一次原 thread/workspace 修正；JSON 必须按 strict object schema 校验，HTML 只通过 wrapper 解析并原样保存，artifact 统一按工作区文件 path 处理。
- Run artifacts 和 Trace 按需组装，必须使用 run owner 权限和 `runtime_paths.py` 计算路径，只读取统一 Envelope 中带 `holder` / `origin` / 可选 `reused_from` / hash 的声明，对外只返回签名下载链接而不返回内部路径，不新增 artifact 表。
- `tools.py` 管理 MCP/Skills 库存、App disabled tools、run snapshot allowed tools 和 planning-only `planning_enabled` 过滤。
- NL compile 使用 `nlcompile_sessions` 持久化 plan/wait/apply/refine/cancel；首次请求附件以 upload 引用写入会话历史，并在 plan/apply 调用时校验归属后挂载到 `/mnt/inputs`；plan 阶段通过 Codex 原生 `requestUserInput` 交互，apply 阶段禁止继续交互。
- NL compile apply 将 patches 作为整批在临时 graph 上模拟和校验，失败可让 Agent 重新生成；不得返回半应用 graph。
- NL compile apply 返回前调用 Prompt Assistant 后处理 prompt，并调用 graph layout 美化；这些后处理不进入 ask_user。
- Prompt Assistant 使用 `prompt_assistant_generations` 持久化生成、waiting、resume、cancel、active；内存 session 只用于当前进程快速等待和取消。
- Codex 原生 `requestUserInput` 在 runtime 层归一化为 Mira decision request；等待、resume、SSE 和附件落地仍由 service 层维护，不通过 prompt 或 MCP 约定工具路由。
- `runtime_paths.py` 是 runtime 文件路径优先来源；现有少量直接使用 `runtime_dir()` 的清理逻辑不要继续扩散，新增路径 helper 优先放回 `runtime_paths.py`。
- Prompt Templates 以 `backend/seeds/prompts/` 为源码事实来源；seed 同步会覆盖数据库同名模板，Settings 保存 Prompt Template 时必须同步写回同名 seed 文件。
- Settings、Codex、MCP、Skill、Instruction、Prompt Template 写操作保持 admin-only。

## Verification

- Run 相关改动优先运行 `tests/test_run_executor.py`、`tests/test_runs_sse.py`、`tests/test_run_waiting_resume.py`、`tests/test_run_recovery.py`。
- Trace/artifacts 改动优先运行 `tests/test_run_trace.py` 和相关 run artifacts 测试。
- Prompt/NL compile/condition 改动优先运行 `tests/test_prompt_templates.py`、`tests/test_nlcompile.py`、`tests/test_condition_node.py`。
- Settings/Skills/Tools 改动运行 settings、runtime config、prompt templates、apps settings 相关测试。
