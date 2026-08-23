# Mira Runtime Context

Mira 把一次 Application Run 视为一个逻辑 RunAgent，而不是一组彼此失忆的节点 Agent。

## 核心模型

- 线性执行：节点延续同一个 provider session，并在同一个 branch workspace 中读写。
- 非 Agent 输入：`user_input` 与 `asset` 写入 `/workspace/.mira/run-context/`；附件副本写入 `/workspace/inputs/`。
- 并行：只有一个已完成节点产生多个实际活动后继时才 fan-out。每个后继从同一 checkpoint 原生 fork provider session，并获得 CoW workspace。
- 汇合：RunAgent 创建协调 branch，把共同父 checkpoint、完整分支快照、节点上下文和 diff manifest 交给协调 Agent。后端验证 receipt 的路径、来源和最终 hash 后，才消费并清理源分支。
- 正式输出：共享 workspace 不替代节点输出 Envelope。JSON、HTML 和 artifact 继续执行强契约；首次契约失败只允许在原 session/workspace 修正一次。
- 文件视图：workspace 内未声明文件可以参与本次运行推理，但只有 artifact contract 声明且完整性通过的产物可以进入 Run Files、Trace artifacts 和下载 API。
- Checkpoint rerun：从 cut 节点前 checkpoint 创建新 Run。cut 前 workspace、session lineage 和 step 状态冻结；当前 App graph 只执行 cut 及下游。cut 前 input override 不生效。

## 代码入口

- `backend/app/services/run_agent.py`：session lineage、branch lease、checkpoint、fork、join、receipt。
- `backend/app/services/workspace_tree.py`：CoW tree、immutable checkpoint、diff、tree hash 与安全清理。
- `backend/app/services/run_orchestrator.py`：依赖调度和 Step 生命周期。
- `backend/app/services/node_handlers.py`：节点执行、共享 workspace context 与强输出契约调用。
- `backend/app/services/runs.py`：Run 创建、continue、checkpoint rerun 与删除。
- `backend/app/runtime/claude_runtime.py`、`codex_runtime.py`：provider 原生 session fork。

详细决策与取舍见 `docs/adr/0001-run-agent-session-tree.md`。
