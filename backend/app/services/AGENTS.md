# AGENTS.md

本文件约束 `backend/app/services/`。这里是 Mira 后端的业务逻辑层；router 负责 HTTP 边界，service 负责权限、持久化、graph/输出校验、Codex 调用编排、脱敏与运行状态机。

## 模块地图与阅读顺序

- Apps 与权限：`apps.py`、`auth.py`、`admin.py`、`serializers.py`；集中处理 owner、visibility、`market_access`、gallery、clone 和 source redaction。
- Graph：`graph_validation.py`、`graph_inputs.py`、`workflow_lint.py`、`execution_plan.py`；hard validation 决定能否执行，lint 只提供可读 error/warning/info，拓扑与祖先/后代关系以 `ExecutionPlan` 为准。
- Run 入口：`runs.py` 创建/查询/取消/继续/rerun，`run_orchestrator.py` 依赖驱动调度，`node_handlers.py` 执行节点，`run_agent.py` 管理 thread/workspace/checkpoint/分支。
- Run 数据：`workflow_data.py` 定义正式 Envelope，`output_contracts.py` 校验与提交输出，`run_artifacts.py`、`run_trace.py`、`run_serializer.py` 负责查询、完整性、Trace 和脱敏。
- 事件与路径：`run_events.py` 持久化事件，`run_hub.py` 只做当前进程广播/取消/等待；`runtime_paths.py` 是 data/runtime 路径事实来源，`workspace_tree.py` 通过 manifest 与不可变内容对象管理 checkpoint，并物化隔离的可写分支。
- Tools 与 Skills：`tools.py` 管理库存、App 排除、Run 快照与 planning 过滤；`skills.py` 负责上传/状态，`skills_install.py` 校验解压和 runtime mount，`skill_dependencies.py` 构建内容寻址 Python 依赖层。
- Agent 辅助流程：`nlcompile.py`、`prompt_assistant.py`、`graph_layout.py`、`prompts.py`；Codex config/runtime 配置分别在 `codex_config.py`、`runtime_config.py`。

## 权限与数据边界

- 用户资源查询必须按当前登录用户过滤，禁止用请求体/查询参数中的 `user_id` 决定 owner。全局 Settings、Codex、MCP、Skill、Instruction、Prompt Template 写操作保持 admin-only。
- `system_gallery` 是只读 seed 源；gallery 与普通 market 查询分开，编辑前必须 clone。跨用户 clone 时复制受引用 uploads，不复用原 owner 的私有文件。
- `run_only` 对非 owner 仅开放运行；App/Run/SSE/artifact 必须复用集中 serializer/sanitizer，Step Trace 对受保护 viewer 返回 403，不能在 router 临时拼未脱敏字段。
- Prompt Templates 以 `backend/seeds/prompts/` 为源码事实来源；seed 同步覆盖同名数据库记录，Settings 保存必须同步写回 seed，并保持变量名与调用方一致。

## Graph 与 Run 状态机

- 可执行 graph 使用 `execution_edges`，普通边只表达顺序，condition 出边使用 `branch_key`。最多一个 `user_input` 和一个 `output`，正式执行必须恰有一个所有有效路径可达的终点 output。
- Run 创建时把 graph 和当前允许 Tool IDs 写入 `Run.graph_json`；执行、waiting resume、恢复、历史与序列化不回读实时 App graph。Admin 后续禁用/删除 Tool 仍会阻止其注入。
- `run_orchestrator.py` 只等待直接前置，ready 节点可并发；condition 把未选分支置为 skipped。失败保留 error 与 `runtime|contract|routing|integrity|internal`，取消不得被晚到的 success 覆盖。
- `run_agent.py` 统一拥有 branch、Codex thread lineage、workspace 与 checkpoint。线性节点复用 branch；真实 fan-out 从同一 checkpoint 创建 child workspace 并 `thread/fork`；fan-in 由协调 Agent 读取完整 base/branch/context 证据，receipt 的路径、来源和结果 hash 全部校验后才消费来源 branch。
- `user_input` / `asset` 写 `.mira/run-context/` 并将文件复制到 `inputs/`。workspace 中未声明文件可供同 Run 后续推理，但不能进入 Files/Trace/下载接口；不要新增 `/mnt/results`、每节点 workspace 或 handoff sidecar。
- 除 `output` 外的 LLM 节点先运行 read-only planning turn，自主决定是否通过 Codex 原生 `requestUserInput` 等待用户；回答落库并回填同一 JSON-RPC request。提问只收集当前节点能在本次执行中直接采用的信息，不得把返回上游、重新执行节点、改变分支或重试工具错误伪装成用户选项；这些控制流只能走运行结束后的 checkpoint rerun。规划必须明确返回 `ready` 或 `needs_user_input`；后者若未真正发起原生提问，只允许在同一 thread 重试一次，仍未提问则按 contract 失败，不得进入正式执行。正式执行使用同 branch 的可写 thread，output 不再重复 planning。
- startup 将未完成 Run 标记为 `interrupted` 并清理数据库无对应记录的普通 Run workspace；继续运行跳过已成功/已跳过节点，不承诺中断节点副作用去重。
- rerun-from 创建新 Run，使用当前 App graph，但要求来源 cut Step 有有效 pre-checkpoint；cut 前节点按当前拓扑冻结为 checkpoint reuse，复制所需 branch/checkpoint/scoped HOME 与已声明 artifact，cut 及后代重新执行。旧 Run 只读，condition 分支测试仅写入新 Run snapshot。
- checkpoint 不保存逐节点完整目录副本；同一 Run 内相同 SHA-256 内容只保存一次。rerun 克隆 manifest/对象引用并只物化 cut branch。旧目录 checkpoint 保持可读，迁移必须先复验 `tree_hash`，不得在校验前删除旧 tree。
- Run scoped HOME 在终态删除 npm/cache/tmp/logs 与派生 Skills，但保留 thread/session 数据；rerun、Run/App 删除和启动 GC 必须同步清理明确属于 Run 的 HOME。运行创建及 rerun 必须保留配置的最小磁盘余量，容量不足返回简短 507，不泄漏批量文件异常。

## 输出与 Artifact

- `output_contracts.py` 仅允许 generate 配置 `json`、`html`、`artifact`；自由文本不设契约。JSON 使用受限 Draft 2020-12 strict object schema，HTML 通过 `{"html":"..."}` wrapper，artifact 返回 `{"artifacts":[{"path","name"}]}` 并限制在当前 branch workspace。
- output 节点始终使用 HTML wrapper。契约失败最多进行一次最小修正；普通修正复用当前 thread/workspace，U+FFFD 场景按当前实现重新生成受损内容，不得以删除坏字符代替语义修复。基础设施型 Office 校验不可用不交给 Agent repair。
- `workflow_data.py` 将成功结果包装为版本化 Envelope。Artifact 提交时复制到 Run 根的 `artifacts/<node>/<artifact>/`、去除写权限，并记录 holder、origin、可选 reused_from、相对路径、size、SHA-256、kind 和 manifest version。
- Files 以及 Trace 中的 artifact 列表只从成功 artifact-contract Step 的 Envelope 按需组装，不扫描 workspace、不新增 artifact 表。对外只给 hash-bound 签名 URL，不返回内部路径；大小/hash 改变标记 `modified` 且不可下载，Run success 前统一复验。
- Artifact/归档/OOXML 文本完整性、真实容器和安全成员由既有集中校验处理。Office 深检通过宿主机 `office_documents.py` 的 root-owned helper、专用无 Docker 组账号和 transient unit 执行，最多并发 2、总时限 120 秒；隔离或工具缺失时 fail closed。

## Tools、Skills 与 Agent 辅助流程

- App 默认使用当前启用 Tools，`graph.tools.disabled_tool_ids` 只表达排除；Run 创建时快照 allowed IDs，运行时再与当前启用状态求交集。NL compile、Prompt Assistant 和 Run planning 额外要求 `planning_enabled=true`。
- Skill ZIP 只能有一个 canonical `SKILL.md`，archive MD5 与 root 在安装时复验。`requirements.lock` 优先于同目录 `requirements.txt`；`skill_dependencies.py` 只接受包索引 requirement，不允许 URL/路径/pip 执行选项，并以 `--only-binary=:all:` 下载 wheel、离线安装到 `.deps`。
- 依赖 builder 使用 runtime 镜像但不挂载 Codex 凭据、workspace、uploads 或 Docker socket；下载阶段可联网，安装阶段禁网。缓存绑定 requirements、policy、Python ABI 和镜像身份，挂载前复验 manifest/tree hash；失败 Skill 禁用。
- NL compile 以 `nlcompile_sessions` 持久化 plan/wait/refine/apply/cancel：plan 可原生提问，apply 禁止再提问；patch batch 必须在临时 graph 整批模拟、重试与完整校验，失败不返回半应用 graph，成功前使用独立 `nlcompile_prompt_refiner` 整理节点提示词并做 layout。
- Prompt Assistant 以 `prompt_assistant_generations` 持久化 running/waiting/resume/cancel/active；内存 session 只做当前进程快速等待。页面生成使用 Codex 配置默认模型与推理强度，最多接受一轮 1–3 个问题，用户可见提示词拒绝内部字段或协议。数据库状态是恢复事实来源。

## 修改与验证

- 新路径 helper 放 `runtime_paths.py`；新增执行关系放 `execution_plan.py`；不要复制权限、graph、artifact 或 prompt 校验逻辑。
- Run/恢复改动优先运行 `tests/test_run_executor.py`、`tests/test_runs_sse.py`、`tests/test_run_waiting_resume.py`、`tests/test_run_recovery.py`、`tests/test_execution_plan.py`。
- Artifact/Trace 改动运行 `tests/test_artifact_integrity.py`、`tests/test_run_trace.py`、`tests/test_output_text_integrity.py`、`tests/test_office_documents.py`。
- Tools/Skills 改动运行 `tests/test_apps_settings.py`、`tests/test_skill_dependencies.py`、`tests/test_runtime_skill_mounts.py`、`tests/test_skill_lifecycle.py`；NL compile/Prompt Assistant 运行对应测试与 prompt template 测试。
- 至少执行 `cd backend && uv run python -m compileall app scripts`；跨 API shape 同步前端并运行 `cd web && npm run typecheck`。
