# ADR 0001：RunAgent Session Tree 与共享 Workspace

- 状态：Accepted
- 日期：2026-08-23

## 背景

旧运行时把每个 LLM 节点当成独立 Agent session，并为每个 attempt 创建独立 workspace。上下游只能依赖后端生成的只读 `/mnt/results` 视图显式传递 Envelope。这个模型隔离清晰，但会切断模型在长任务中的工作记忆，也要求后端预先判断哪些信息值得传递；未进入 Envelope 的推理状态和工作文件会丢失。

目标是让一次应用运行表现为一个持续工作的 Agent，同时保留图调度、真正并行和节点级强输出契约。

## 决策

一次 Application Run 对应一个逻辑 RunAgent。

1. 同一 branch 上的顺序节点复用 provider session 和可写 workspace。
2. `user_input` 与 `asset` 不创建独立数据总线，而是在 Agent 首次使用前写入 branch workspace 的 `.mira/run-context/`；附件复制到 `inputs/`。
3. 真正 fan-out 在父节点 post-checkpoint 上创建子 branch：workspace 优先 reflink、失败时 copy fallback；Claude 使用 `--resume ... --fork-session`，Codex 使用 app-server `thread/fork`。
4. fan-in 不做“最后写入胜出”或后端主分支选择。协调 Agent必须读取共同父、所有 branch snapshot、branch context 和 diff manifest，完成合并并返回严格 receipt。后端验证覆盖路径、来源集合、删除状态、hash 以及证据目录未被篡改。
5. 每个成功节点创建 immutable checkpoint。节点正式回复仍经过统一 Envelope 和 output contract；一次 repair 在原 session/workspace 内完成，不回滚工作区。
6. rerun-from 改为 checkpoint rerun：新 Run 克隆 cut 前 checkpoint、必要的 session HOME 与 lineage，冻结 cut 前 step；当前 App graph 只执行 cut 与 descendants。当前 Graph 新增的 cut 前节点标记 `checkpoint_reused`。来源 Run 永远只读。
7. provider session ID、branch ID 和 checkpoint ID 是后端内部状态，不出现在 Step/Trace 前端契约。

## 不变量

- `/mnt/results` 不存在，也不得重新引入每节点 handoff/sidecar 文件。
- fan-out 前必须有可验证 checkpoint；并行 branch 不共享可写 workspace 或同一物理 session。
- fan-in receipt 未通过时不得清理源 branch，也不得把 Run 标为成功。
- workspace 可承载运行内隐式信息，但 artifacts API 只读取成功 artifact contract Step 的正式 manifest，不扫描 workspace。
- Run 只有唯一 output Step 成功、其它 Step 为 `success` / `skipped` / `checkpoint_reused` 且 artifact 复验通过时才成功。
- 没有有效 pre-checkpoint 的历史 Run/cut 不支持新式 continue 或 rerun。

## 影响

收益是线性任务不再因节点边界丢失上下文，节点图只负责控制流；并行仍有明确隔离，汇合决策拥有完整证据。代价是后端必须持久化 session tree、workspace checkpoint 和 join 操作，并承担 checkpoint 存储与 provider fork 能力差异。

开发阶段不保留旧运行时或旧 Run 数据；数据库中的 Run 均使用本 ADR 定义的 session/workspace 架构。
