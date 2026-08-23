"""Run 执行编排：拓扑遍历 graph，调用 node_handlers 执行每个节点。

事件流：
- 进入节点 → ``step.start``；
- 节点过程中 runtime 推 chunk → ``step.delta``（由 node_handlers._run_llm 转发）；
- 节点过程中写入 StepLog → ``step.log``（由 node_handlers._append_log 转发）；
- 节点完成 → ``step.end`` 携带 Step 终态 + logs；
- 全部完成 / 失败 / 取消 → ``run.end``。

终态规则：
- 任一节点 ``failed`` → run.status=failed，后续未运行 step 保持 pending（不在 SSE 中再推帧）。
- 取消信号设置后，当前节点尽快返回 cancelled；未运行的节点写库为 cancelled，
  emit ``step.start`` + ``step.end cancelled`` 帧让前端 UI 闭合。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import App, Run, RunAgentBranch, Step, StepLog
from app.schemas import RunInputValue
from app.services.execution_plan import ExecutionPlan, compile_execution_plan
from app.services.node_handlers import (
    ExecutionContext,
    NodeResult,
    build_context,
    run_node,
)
from app.services.run_artifacts import validate_run_artifact_integrity
from app.services.run_agent import RunAgent, RunAgentError
from app.services.run_hub import RunChannel, get_run_hub
from app.services.run_serializer import step_to_out
from app.services.runs import touch_run_heartbeat
from app.services.runtime_paths import run_workspace
from app.services.tools import planning_runtime_tools_for_graph, runtime_tools_for_graph
from app.services.workflow_data import build_output_envelope
from app.utils import iso, loads, now_utc

logger = logging.getLogger(__name__)
WAITING_SIBLING_SETTLE_SECONDS = 0.05


@dataclass
class StepState:
    id: str
    node_id: str
    ordering: int
    status: str
    output: Any = None
    agent_session_id: str | None = None
    branch_id: str | None = None


@dataclass
class StepTaskResult:
    node_id: str
    status: str
    output: Any = None
    error: str | None = None
    failure_kind: str | None = None
    agent_session_id: str | None = None
    skipped_nodes: set[str] | None = None
    branch_id: str | None = None


# --- 对外 API ----------------------------------------------------------------


def schedule_run(run_id: str, *, continuation: bool = False) -> asyncio.Task[None]:
    return asyncio.create_task(start_run(run_id, continuation=continuation), name=f"run-{run_id}")


async def start_run(run_id: str, *, continuation: bool = False) -> None:
    hub = get_run_hub()
    channel = hub.get(run_id)
    if channel is None:
        logger.warning("start_run called but channel missing for run=%s", run_id)
        return
    heartbeat_task = asyncio.create_task(_heartbeat_loop(run_id), name=f"run-heartbeat-{run_id}")
    close_channel = True
    try:
        close_channel = await _orchestrate(run_id, channel, continuation=continuation)
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator crashed for run=%s", run_id)
        await _finalize_failed(run_id, channel, error="服务器内部错误")
    finally:
        heartbeat_task.cancel()
        if close_channel:
            await channel.close()


def cancel_run(run_id: str) -> bool:
    channel = get_run_hub().get(run_id)
    if channel is None:
        return False
    channel.cancel_event.set()
    return True


# --- 主循环 -----------------------------------------------------------------


async def _heartbeat_loop(run_id: str) -> None:
    while True:
        await touch_run_heartbeat(run_id)
        await asyncio.sleep(5)


async def _orchestrate(run_id: str, channel: RunChannel, *, continuation: bool = False) -> bool:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None:
            await channel.publish(
                "run.end",
                {"status": "failed", "error": "运行记录不存在", "failure_kind": "internal"},
            )
            return True
        app = await db.get(App, run.app_id)
        if app is None:
            run.status = "failed"
            run.error = "应用不存在"
            run.failure_kind = "internal"
            run.finished_at = now_utc()
            await db.commit()
            await channel.publish(
                "run.end",
                {"status": "failed", "error": "应用不存在", "failure_kind": "internal"},
            )
            return True

        graph = loads(run.graph_json, {"nodes": [], "execution_edges": []})
        execution_plan = compile_execution_plan(graph)
        agent = str(graph.get("agent") or "").strip()
        runtime_tools = await runtime_tools_for_graph(db, graph, agent, trust_snapshot=True)
        planning_runtime_tools = await planning_runtime_tools_for_graph(db, graph, agent, trust_snapshot=True)
        steps_query = await db.execute(
            select(Step)
            .where(Step.run_id == run_id)
            .order_by(Step.ordering.asc(), Step.id.asc())
        )
        steps = list(steps_query.scalars().all())
        inputs_raw = loads(run.inputs_json, {}) or {}
        inputs = {
            key: RunInputValue.model_validate(value) if isinstance(value, dict) else RunInputValue(value=str(value))
            for key, value in inputs_raw.items()
        }
        ctx = build_context(
            db,
            channel,
            user_id=run.owner_id,
            asset_owner_id=app.owner_id,
            app_id=run.app_id,
            run_id=run.id,
            graph=graph,
            agent=agent,
            workspace=run_workspace(run.owner_id, run.app_id, run.id),
            inputs=inputs,
            runtime_tools=runtime_tools,
            planning_runtime_tools=planning_runtime_tools,
            execution_plan=execution_plan,
        )

        run.status = "running"
        run.error = None
        run.failure_kind = None
        run.finished_at = None
        run.heartbeat_at = now_utc()
        if run.started_at is None:
            run.started_at = now_utc()
        await db.commit()
        states = _step_states(steps)
        if run.runtime_version < 2 and continuation:
            raise RunAgentError("旧运行只支持只读历史，不能继续执行")
        run_agent = RunAgent(
            db,
            run,
            channel,
            agent=agent,
            runtime_tools=runtime_tools,
        )
        root_branch = await run_agent.ensure_root()
        outputs = {
            step.node_id: loads(step.output_json, None)
            for step in steps
            if step.status == "success" and step.output_json is not None
        }
        predecessors = execution_plan.predecessors
        edge_branches = await _restore_edge_branches(db, run.id, states, execution_plan)
        await _allocate_root_branches(
            db,
            run_agent,
            root_branch,
            states,
            execution_plan,
            edge_branches,
        )
        active: dict[asyncio.Task[StepTaskResult], str] = {}
        launched: set[str] = set()
        blocked_by_waiting = False
        run_failed = False
        run_cancelled = False
        run_error: str | None = None
        run_failure_kind: str | None = None

        while True:
            if channel.cancel_event.is_set():
                run_cancelled = True

            if not run_failed and not run_cancelled and not blocked_by_waiting:
                ready = _ready_node_ids(states, predecessors, launched)
                for node_id in ready:
                    node = ctx.nodes_by_id.get(node_id)
                    state = states[node_id]
                    if node is None:
                        step = await db.get(Step, state.id)
                        if step is not None:
                            await _finish_step(
                                db,
                                channel,
                                step,
                                status="failed",
                                error="graph 中找不到节点",
                                failure_kind="routing",
                            )
                        state.status = "failed"
                        run_failed = True
                        run_error = run_error or "graph 中找不到节点"
                        run_failure_kind = run_failure_kind or "routing"
                        continue
                    try:
                        branch_id = await _branch_for_ready_node(
                            run_agent,
                            node_id,
                            states,
                            execution_plan,
                            edge_branches,
                            root_branch.id,
                        )
                    except RunAgentError as exc:
                        step = await db.get(Step, state.id)
                        if step is not None:
                            await _finish_step(
                                db,
                                channel,
                                step,
                                status="failed",
                                error=f"RunAgent 分支准备失败：{exc}",
                                failure_kind="internal",
                            )
                        state.status = "failed"
                        run_failed = True
                        run_error = str(exc)
                        run_failure_kind = "internal"
                        continue
                    state.branch_id = branch_id
                    launched.add(node_id)
                    active[
                        asyncio.create_task(
                            _run_step_task(
                                channel,
                                graph,
                                node,
                                state,
                                inputs,
                                runtime_tools,
                                planning_runtime_tools,
                                app.owner_id,
                                execution_plan,
                                run_id=run.id,
                                run_owner_id=run.owner_id,
                                run_app_id=run.app_id,
                                branch_id=branch_id,
                                outputs={
                                    source_id: outputs[source_id]
                                    for source_id in execution_plan.ancestor_ids(node_id)
                                    if source_id in outputs
                                },
                                skipped_nodes=set(ctx.skipped_nodes),
                            ),
                            name=f"run-{run_id}-step-{node_id}",
                        )
                    ] = node_id

            if not active:
                if run_failed or run_cancelled:
                    break
                if blocked_by_waiting and await _sync_resumed_waiting_steps(db, channel, states, launched):
                    blocked_by_waiting = False
                    continue
                queued_waiting = _queued_waiting_step(states)
                if queued_waiting is not None:
                    await _publish_existing_waiting_step(db, channel, run, queued_waiting)
                    return False
                break

            done, _pending = await asyncio.wait(active.keys(), return_when=asyncio.FIRST_COMPLETED)
            ordered_done = sorted(done, key=lambda item: _task_sort_key(active.get(item), states))
            for task in ordered_done:
                active.pop(task, None)
                try:
                    result = task.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("step task crashed for run=%s", run_id)
                    result = StepTaskResult(
                        node_id="",
                        status="failed",
                        error=f"节点执行异常: {exc}",
                        failure_kind="internal",
                    )
                state = states.get(result.node_id)
                if state is None:
                    run_failed = True
                    run_error = run_error or result.error or "节点执行异常"
                    run_failure_kind = run_failure_kind or result.failure_kind or "internal"
                    continue
                state.status = result.status
                state.output = result.output
                state.agent_session_id = result.agent_session_id
                state.branch_id = result.branch_id or state.branch_id
                if result.skipped_nodes:
                    await _mark_skipped_nodes(db, channel, states, result.skipped_nodes)
                    ctx.skipped_nodes.update(result.skipped_nodes)
                if result.status == "success":
                    outputs[result.node_id] = result.output
                    try:
                        await _allocate_successor_branches(
                            run_agent,
                            result.node_id,
                            state,
                            states,
                            execution_plan,
                            edge_branches,
                        )
                    except RunAgentError as exc:
                        run_failed = True
                        run_error = f"RunAgent fan-out 失败：{exc}"
                        run_failure_kind = "internal"
                elif result.status == "waiting_for_user":
                    blocked_by_waiting = True
                    await _settle_active_siblings(active)
                    await _publish_existing_waiting_step(db, channel, run, state)
                elif result.status == "cancelled":
                    run_cancelled = True
                elif result.status == "failed":
                    run_failed = True
                    run_error = run_error or result.error
                    run_failure_kind = run_failure_kind or result.failure_kind or "internal"

        # 收尾：未触达的 step 已经按状态在 DB 中保持 pending。run 终态决策：
        finished_at = now_utc()
        if run_cancelled:
            await _cancel_unfinished_steps(db, channel, states)
            run.status = "cancelled"
            run.finished_at = finished_at
            _clear_recovery(run)
            await db.commit()
            await channel.publish("run.end", {"status": "cancelled"})
            return True
        if not run_failed:
            routing_error = _terminal_routing_error(graph, states)
            if routing_error:
                run_failed = True
                run_error = routing_error
                run_failure_kind = "routing"
        if not run_failed:
            artifact_error = await validate_run_artifact_integrity(db, run)
            if artifact_error:
                run_failed = True
                run_error = f"artifact 完整性校验失败：{artifact_error}"
                run_failure_kind = "integrity"
        if await _run_was_cancelled(db, run.id, channel):
            run_cancelled = True
        if run_cancelled:
            await _cancel_unfinished_steps(db, channel, states)
            run.status = "cancelled"
            run.finished_at = finished_at
            _clear_recovery(run)
            await db.commit()
            await channel.publish("run.end", {"status": "cancelled"})
            return True
        if run_failed:
            run.status = "failed"
            run.error = run_error or "运行失败"
            run.failure_kind = run_failure_kind or "internal"
            run.finished_at = finished_at
            _clear_recovery(run)
            await db.commit()
            await channel.publish(
                "run.end",
                {"status": "failed", "error": run.error, "failure_kind": run.failure_kind},
            )
            return True
        success_update = await db.execute(
            update(Run)
            .where(Run.id == run.id, Run.status == "running")
            .values(
                status="success",
                finished_at=finished_at,
                resume_from_node_id=None,
                recovery_reason=None,
                interrupted_at=None,
                error=None,
                failure_kind=None,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        if success_update.rowcount != 1:
            await db.refresh(run)
            if run.status == "cancelled":
                await _cancel_unfinished_steps(db, channel, states)
                await channel.publish("run.end", {"status": "cancelled"})
                return True
            raise RuntimeError(f"run 终态写入冲突：{run.status}")
        await db.refresh(run)
        await channel.publish("run.end", {"status": "success"})
        return True


async def _execute_node(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    try:
        return await run_node(ctx, node, step)
    except Exception as exc:  # noqa: BLE001
        logger.exception("node handler crashed: node=%s", node.get("id"))
        return NodeResult(status="failed", error=f"节点执行异常: {exc}", failure_kind="internal")


def _step_states(steps: list[Step]) -> dict[str, StepState]:
    states: dict[str, StepState] = {}
    for step in steps:
        states[step.node_id] = StepState(
            id=step.id,
            node_id=step.node_id,
            ordering=step.ordering,
            status=step.status,
            output=loads(step.output_json, None) if step.output_json is not None else None,
            agent_session_id=step.agent_session_id,
            branch_id=step.branch_id,
        )
    return states


async def _restore_edge_branches(
    db,
    run_id: str,
    states: dict[str, StepState],
    plan: ExecutionPlan,
) -> dict[tuple[str, str], str]:
    edges: dict[tuple[str, str], str] = {}
    branches = (
        await db.execute(
            select(RunAgentBranch)
            .where(RunAgentBranch.run_id == run_id)
            .order_by(RunAgentBranch.created_at.asc(), RunAgentBranch.id.asc())
        )
    ).scalars().all()
    for branch in branches:
        if not branch.parent_branch_id or not branch.fork_node_id:
            continue
        candidates = [
            state
            for state in states.values()
            if state.branch_id == branch.parent_branch_id
            and branch.fork_node_id in plan.children.get(state.node_id, frozenset())
        ]
        if candidates:
            source = max(candidates, key=lambda item: (item.ordering, item.node_id))
            edges[(source.node_id, branch.fork_node_id)] = branch.id
        elif not plan.predecessors.get(branch.fork_node_id):
            edges[("", branch.fork_node_id)] = branch.id
    for target_id, state in states.items():
        if not state.branch_id:
            continue
        for source_id in plan.predecessors.get(target_id, frozenset()):
            source = states[source_id]
            if source.status in {"success", "checkpoint_reused"}:
                edges.setdefault((source_id, target_id), state.branch_id)
    return edges


async def _allocate_root_branches(
    db,
    run_agent: RunAgent,
    root_branch: RunAgentBranch,
    states: dict[str, StepState],
    plan: ExecutionPlan,
    edge_branches: dict[tuple[str, str], str],
) -> None:
    roots = [
        node_id
        for node_id in plan.ordered_node_ids
        if not plan.predecessors.get(node_id)
        and states[node_id].status in {"pending", "interrupted"}
        and not states[node_id].branch_id
    ]
    if not roots:
        return
    if len(roots) == 1:
        edge_branches.setdefault(("", roots[0]), root_branch.id)
        return
    created = False
    for node_id in roots:
        if ("", node_id) in edge_branches:
            continue
        child = await run_agent.fork(root_branch.id, fork_node_id=node_id)
        edge_branches[("", node_id)] = child.id
        created = True
    if created:
        await db.commit()
        await run_agent.close_fanout_parent(root_branch.id)


async def _branch_for_ready_node(
    run_agent: RunAgent,
    node_id: str,
    states: dict[str, StepState],
    plan: ExecutionPlan,
    edge_branches: dict[tuple[str, str], str],
    root_branch_id: str,
) -> str:
    state = states[node_id]
    if state.branch_id:
        return state.branch_id
    predecessors = plan.predecessors.get(node_id, frozenset())
    if not predecessors:
        return edge_branches.get(("", node_id), root_branch_id)
    incoming: set[str] = set()
    for source_id in predecessors:
        source = states[source_id]
        if source.status not in {"success", "checkpoint_reused"}:
            continue
        branch_id = edge_branches.get((source_id, node_id)) or source.branch_id
        if branch_id:
            incoming.add(branch_id)
    if not incoming:
        raise RunAgentError(f"节点 {node_id} 没有可继承的成功上游 branch")
    if len(incoming) == 1:
        return next(iter(incoming))
    coordinator = await run_agent.join(incoming, node_id=node_id)
    return coordinator.id


async def _allocate_successor_branches(
    run_agent: RunAgent,
    node_id: str,
    state: StepState,
    states: dict[str, StepState],
    plan: ExecutionPlan,
    edge_branches: dict[tuple[str, str], str],
) -> None:
    if not state.branch_id:
        raise RunAgentError(f"节点 {node_id} 完成后缺少 branch")
    children = [
        child_id
        for child_id in sorted(plan.children.get(node_id, frozenset()))
        if states[child_id].status not in {"skipped", "cancelled"}
    ]
    if not children:
        return
    if len(children) == 1:
        edge_branches[(node_id, children[0])] = state.branch_id
        return
    for child_id in children:
        child = await run_agent.fork(state.branch_id, fork_node_id=child_id)
        edge_branches[(node_id, child_id)] = child.id
    await run_agent.db.commit()
    await run_agent.close_fanout_parent(state.branch_id)


def _terminal_routing_error(graph: dict[str, Any], states: dict[str, StepState]) -> str | None:
    output_ids = [
        node.get("id")
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and node.get("type") == "output" and isinstance(node.get("id"), str)
    ]
    if len(output_ids) != 1:
        return "Workflow 必须且只能有一个最终输出节点"
    output_state = states.get(output_ids[0])
    if output_state is None or output_state.status != "success":
        return "最终输出节点未执行成功"
    unfinished = [
        state.node_id
        for state in states.values()
        if state.status not in {"success", "skipped", "checkpoint_reused"}
    ]
    if unfinished:
        return f"Workflow 存在未完成节点：{', '.join(sorted(unfinished))}"
    return None


def _ready_node_ids(
    states: dict[str, StepState],
    predecessors: dict[str, frozenset[str]],
    launched: set[str],
) -> list[str]:
    ready: list[StepState] = []
    for node_id, state in states.items():
        if node_id in launched:
            continue
        if state.status not in {"pending", "interrupted"}:
            continue
        upstream = predecessors.get(node_id, set())
        if all(
            states[source].status in {"success", "skipped", "checkpoint_reused"}
            for source in upstream
        ):
            ready.append(state)
    ready.sort(key=lambda item: (item.ordering, item.node_id))
    return [state.node_id for state in ready]


async def _run_step_task(
    channel: RunChannel,
    graph: dict[str, Any],
    node: dict[str, Any],
    state: StepState,
    inputs: dict[str, RunInputValue],
    runtime_tools,
    planning_runtime_tools,
    asset_owner_id: str,
    execution_plan: ExecutionPlan,
    *,
    run_id: str,
    run_owner_id: str,
    run_app_id: str,
    branch_id: str,
    outputs: dict[str, Any],
    skipped_nodes: set[str],
) -> StepTaskResult:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None:
            return StepTaskResult(
                node_id=state.node_id,
                status="failed",
                error="运行记录不存在",
                failure_kind="internal",
            )
        step = await db.get(Step, state.id)
        if step is None:
            return StepTaskResult(
                node_id=state.node_id,
                status="failed",
                error="运行节点不存在",
                failure_kind="internal",
            )
        if channel.cancel_event.is_set():
            await _emit_step_start(db, channel, step)
            await _finish_step(db, channel, step, status="cancelled")
            return StepTaskResult(node_id=state.node_id, status="cancelled")

        await _emit_step_start(db, channel, step)
        run_agent = RunAgent(
            db,
            run,
            channel,
            agent=str(graph.get("agent") or "").strip(),
            runtime_tools=runtime_tools,
        )
        lease = await run_agent.lease(branch_id)
        step.branch_id = branch_id
        step.pre_checkpoint_id = lease.pre_checkpoint_id
        await db.commit()
        ctx = build_context(
            db,
            channel,
            user_id=run_owner_id,
            asset_owner_id=asset_owner_id,
            app_id=run_app_id,
            run_id=run_id,
            graph=graph,
            agent=str(graph.get("agent") or "").strip(),
            workspace=lease.workspace,
            inputs=inputs,
            runtime_tools=runtime_tools,
            planning_runtime_tools=planning_runtime_tools,
            execution_plan=execution_plan,
        )
        ctx.agent_session_id = lease.session_id
        ctx.fork_session = lease.fork_session
        ctx.outputs.update(outputs)
        ctx.skipped_nodes.update(skipped_nodes)
        before_skipped = set(ctx.skipped_nodes)

        result = await _execute_node(ctx, node, step)
        effective_session_id = result.agent_session_id or ctx.agent_session_id or lease.session_id
        await run_agent.record_session(branch_id, effective_session_id)
        skipped_delta = set(ctx.skipped_nodes) - before_skipped
        if result.status == "waiting":
            return StepTaskResult(
                node_id=state.node_id,
                status="waiting_for_user",
                agent_session_id=effective_session_id,
                skipped_nodes=skipped_delta,
                branch_id=branch_id,
            )
        if result.status == "success":
            try:
                stored_output = await asyncio.to_thread(
                    build_output_envelope,
                    node,
                    result.output,
                    step_workspace=ctx.workspace,
                    run_workspace=run_workspace(run_owner_id, run_app_id, run_id),
                    run_id=run_id,
                    node_id=state.node_id,
                    step_id=step.id,
                )
            except (OSError, ValueError) as exc:
                error = f"节点输出提交失败：{exc}"
                await _finish_step(
                    db,
                    channel,
                    step,
                    status="failed",
                    error=error,
                    failure_kind="contract",
                    agent_session_id=result.agent_session_id,
                )
                return StepTaskResult(
                    node_id=state.node_id,
                    status="failed",
                    error=error,
                    failure_kind="contract",
                    agent_session_id=result.agent_session_id,
                    skipped_nodes=skipped_delta,
                    branch_id=branch_id,
                )
            branch = await db.get(RunAgentBranch, branch_id)
            if branch is None:
                raise RunAgentError(f"run branch 不存在：{branch_id}")
            await run_agent.checkpoint(
                branch,
                step=step,
                node_id=state.node_id,
                output=stored_output,
            )
            await _finish_step(
                db,
                channel,
                step,
                status="success",
                output=stored_output,
                agent_session_id=effective_session_id,
            )
            return StepTaskResult(
                node_id=state.node_id,
                status="success",
                output=stored_output,
                agent_session_id=effective_session_id,
                skipped_nodes=skipped_delta,
                branch_id=branch_id,
            )
        if result.status == "cancelled":
            await _finish_step(db, channel, step, status="cancelled")
            return StepTaskResult(
                node_id=state.node_id,
                status="cancelled",
                skipped_nodes=skipped_delta,
                branch_id=branch_id,
            )
        if result.status == "skipped":
            await _finish_step(db, channel, step, status="skipped")
            return StepTaskResult(
                node_id=state.node_id,
                status="skipped",
                skipped_nodes=skipped_delta,
                branch_id=branch_id,
            )
        await _finish_step(
            db,
            channel,
            step,
            status="failed",
            error=result.error,
            failure_kind=result.failure_kind or "internal",
            agent_session_id=effective_session_id,
        )
        return StepTaskResult(
            node_id=state.node_id,
            status="failed",
            error=result.error,
            failure_kind=result.failure_kind or "internal",
            agent_session_id=effective_session_id,
            skipped_nodes=skipped_delta,
            branch_id=branch_id,
        )


async def _mark_skipped_nodes(
    db,
    channel: RunChannel,
    states: dict[str, StepState],
    node_ids: set[str],
) -> None:
    for state in sorted(
        (states[node_id] for node_id in node_ids if node_id in states),
        key=lambda item: (item.ordering, item.node_id),
    ):
        if state.status not in {"pending", "interrupted"}:
            continue
        step = await db.get(Step, state.id)
        if step is None:
            continue
        await _emit_step_start(db, channel, step)
        await _finish_step(db, channel, step, status="skipped")
        state.status = "skipped"


async def _cancel_unfinished_steps(db, channel: RunChannel, states: dict[str, StepState]) -> None:
    for state in sorted(states.values(), key=lambda item: (item.ordering, item.node_id)):
        if state.status not in {"pending", "interrupted", "running", "waiting_for_user"}:
            continue
        step = await db.get(Step, state.id)
        if step is None:
            continue
        if step.status == "pending":
            await _emit_step_start(db, channel, step)
        await _finish_step(db, channel, step, status="cancelled")
        state.status = "cancelled"


async def _run_was_cancelled(db, run_id: str, channel: RunChannel) -> bool:
    if channel.cancel_event.is_set():
        return True
    with db.no_autoflush:
        status = await db.scalar(select(Run.status).where(Run.id == run_id))
    return status == "cancelled"


def _queued_waiting_step(states: dict[str, StepState]) -> StepState | None:
    waiting = [state for state in states.values() if state.status == "waiting_for_user"]
    if not waiting:
        return None
    waiting.sort(key=lambda item: (item.ordering, item.node_id))
    return waiting[0]


def _task_sort_key(node_id: str | None, states: dict[str, StepState]) -> tuple[int, str]:
    state = states.get(node_id or "")
    if state is None:
        return (10**9, node_id or "")
    return (state.ordering, state.node_id)


async def _settle_active_siblings(active: dict[asyncio.Task[StepTaskResult], str]) -> None:
    if not active:
        return
    await asyncio.wait(active.keys(), timeout=WAITING_SIBLING_SETTLE_SECONDS, return_when=asyncio.FIRST_COMPLETED)


async def _sync_resumed_waiting_steps(
    db,
    channel: RunChannel,
    states: dict[str, StepState],
    launched: set[str],
) -> bool:
    resumed = False
    for state in states.values():
        if state.status != "waiting_for_user":
            continue
        step = await db.get(Step, state.id)
        if step is None:
            continue
        await db.refresh(step)
        if step.status != "interrupted":
            continue
        state.status = "interrupted"
        state.output = None
        state.agent_session_id = step.agent_session_id
        launched.discard(state.node_id)
        resumed = True
        async with channel.waiting_lock:
            if channel.waiting_node_id == state.node_id:
                channel.waiting_node_id = None
    return resumed


async def _publish_existing_waiting_step(
    db,
    channel: RunChannel,
    run: Run,
    state: StepState,
) -> None:
    step = await db.get(Step, state.id)
    if step is None:
        return
    await db.refresh(step)
    payload = loads(step.input_json, {}) or {}
    request = payload.get("ask_user") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        return
    await db.refresh(run)
    run.status = "waiting_for_user"
    run.resume_from_node_id = step.node_id
    await db.commit()
    should_publish = False
    async with channel.waiting_lock:
        if channel.waiting_node_id is None:
            channel.waiting_node_id = step.node_id
            should_publish = True
    if should_publish:
        await channel.publish("step.waiting", {"node_id": step.node_id, "request": request})
        await channel.publish("run.waiting_for_user", {"node_id": step.node_id})


async def _emit_step_start(db, channel: RunChannel, step: Step) -> None:
    step.status = "running"
    step.started_at = now_utc()
    step.finished_at = None
    step.error = None
    step.failure_kind = None
    step.output_json = None
    step.attempt = (step.attempt or 0) + 1
    await db.commit()
    await channel.publish(
        "step.start",
        {"node_id": step.node_id, "ts": iso(step.started_at) or ""},
    )


async def _finish_step(
    db,
    channel: RunChannel,
    step: Step,
    *,
    status: str,
    output: Any = None,
    error: str | None = None,
    failure_kind: str | None = None,
    agent_session_id: str | None = None,
) -> None:
    finished_at = now_utc()
    with db.no_autoflush:
        status_row = (
            await db.execute(
                select(Run.status, Step.status)
                .join(Step, Step.run_id == Run.id)
                .where(Step.id == step.id)
            )
        ).first()
    run_status = status_row[0] if status_row is not None else None
    step_status = status_row[1] if status_row is not None else None
    if status != "cancelled" and (step_status == "cancelled" or run_status == "cancelled"):
        await db.rollback()
        await db.refresh(step)
        log_rows = (
            await db.execute(select(StepLog).where(StepLog.step_id == step.id))
        ).scalars().all()
        await channel.publish(
            "step.end",
            {"node_id": step.node_id, "step": step_to_out(step, list(log_rows)).model_dump(mode="json")},
        )
        return

    started_at = step.started_at or finished_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=finished_at.tzinfo)
    step.status = status
    if step.started_at is None:
        step.started_at = finished_at
    step.finished_at = finished_at
    step.duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    if output is not None:
        from app.utils import dumps as _dumps

        step.output_json = _dumps(output)
    if error is not None:
        step.error = error
    step.failure_kind = failure_kind if status == "failed" else None
    if status == "cancelled":
        step.agent_session_id = None
    elif agent_session_id is not None:
        step.agent_session_id = agent_session_id
    await db.commit()

    # 读最新 logs 一并塞入 step.end 帧，方便前端历史回放对齐 GET /api/runs/{id}。
    log_rows = (
        await db.execute(select(StepLog).where(StepLog.step_id == step.id))
    ).scalars().all()
    run = await db.get(Run, step.run_id)
    node_type = _node_type_for_step(run, step.node_id) if run is not None else None
    await channel.publish(
        "step.end",
        {"node_id": step.node_id, "step": step_to_out(step, list(log_rows), run, node_type).model_dump(mode="json")},
    )


def _node_type_for_step(run: Run, node_id: str) -> str | None:
    graph = loads(run.graph_json, {"nodes": [], "execution_edges": []}) or {"nodes": [], "execution_edges": []}
    if not isinstance(graph, dict):
        return None
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("id") == node_id:
            node_type = node.get("type")
            return node_type if isinstance(node_type, str) else None
    return None


async def _finalize_failed(run_id: str, channel: RunChannel, *, error: str) -> None:
    try:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if run is not None and run.status not in {"success", "failed", "cancelled"}:
                run.status = "failed"
                run.error = error
                run.failure_kind = "internal"
                run.finished_at = now_utc()
                _clear_recovery(run)
                await db.commit()
        await channel.publish(
            "run.end",
            {"status": "failed", "error": error, "failure_kind": "internal"},
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to finalize run=%s", run_id)


def _clear_recovery(run: Run) -> None:
    run.resume_from_node_id = None
    run.recovery_reason = None
    run.interrupted_at = None
