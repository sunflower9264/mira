"""Run / Step / StepLog 数据库行 -> 出参 schema 的纯函数转换。

这些函数不读 DB；调用方负责先把 Step、StepLog 都 select 出来按 step_id 聚合。
"""

from __future__ import annotations

from app.models import Run, Step, StepLog
from app.schemas import LogLineOut, RunOut, RunRecoveryOut, RunSummaryOut, RunWaitingRequestOut, StepOut
from app.services.apps import public_run_graph
from app.services.graph_validation import sanitize_prompt_template_tokens
from app.services.run_output_sanitizer import RunSanitizeContext, build_run_sanitize_context, sanitize_run_value
from app.services.workflow_data import visible_output
from app.utils import iso, loads

REDACTED_RUN_ERROR = "运行失败"


def log_to_out(log: StepLog) -> LogLineOut:
    return LogLineOut(
        ts=iso(log.ts) or "",
        level=log.level if log.level in {"info", "warn", "error", "tool"} else "info",  # type: ignore[arg-type]
        text=log.text,
    )


def step_to_out(
    step: Step,
    logs: list[StepLog],
    run: Run | RunSanitizeContext | None = None,
    node_type: str | None = None,
) -> StepOut:
    input_value = loads(step.input_json, None)
    output_value = visible_output(loads(step.output_json, None)) if step.output_json else None
    if run is not None:
        input_value = sanitize_run_value(input_value, run)
        output_value = sanitize_run_value(output_value, run, html_mode=node_type == "output")
    return StepOut(
        node_id=step.node_id,
        status=step.status,  # type: ignore[arg-type]
        input=input_value,
        output=output_value,
        started_at=iso(step.started_at),
        finished_at=iso(step.finished_at),
        duration_ms=step.duration_ms,
        error=step.error,
        failure_kind=step.failure_kind,
        reused_from_run_id=step.reused_from_run_id,
        reused_from_step_id=step.reused_from_step_id,
        logs=[log_to_out(log) for log in sorted(logs, key=lambda item: item.ts)],
    )


def run_to_out(
    run: Run,
    steps: list[Step],
    logs_by_step: dict[str, list[StepLog]],
    *,
    redact_source: bool = False,
) -> RunOut:
    recovery = _recovery_to_out(run, steps)
    sanitize_context = build_run_sanitize_context(run)
    source_graph = sanitize_prompt_template_tokens(loads(run.graph_json, {"nodes": [], "execution_edges": []}) or {"nodes": [], "execution_edges": []})
    graph = public_run_graph(source_graph) if redact_source else source_graph
    nodes_by_id = {
        node.get("id"): node
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    visible_node_ids = set(nodes_by_id)
    return RunOut(
        id=run.id,
        app_id=run.app_id,
        status=run.status,  # type: ignore[arg-type]
        name=run.name,
        inputs=loads(run.inputs_json, {}) or {},
        graph=graph,
        steps=_steps_to_out(sanitize_context, steps, logs_by_step, nodes_by_id, visible_node_ids, redact_source),
        started_at=iso(run.started_at),
        finished_at=iso(run.finished_at),
        error=_redacted_error(run.error, redact_source),
        failure_kind=run.failure_kind,
        source_run_id=None if redact_source else run.source_run_id,
        rerun_from_node_id=None if redact_source else run.rerun_from_node_id,
        recovery=recovery,
    )


def run_to_summary_out(run: Run, *, redact_source: bool = False) -> RunSummaryOut:
    return RunSummaryOut(
        id=run.id,
        app_id=run.app_id,
        status=run.status,  # type: ignore[arg-type]
        name=run.name,
        inputs=loads(run.inputs_json, {}) or {},
        started_at=iso(run.started_at),
        finished_at=iso(run.finished_at),
        error=_redacted_error(run.error, redact_source),
        failure_kind=run.failure_kind,
        source_run_id=None if redact_source else run.source_run_id,
        rerun_from_node_id=None if redact_source else run.rerun_from_node_id,
    )


def _steps_to_out(
    sanitize_context: RunSanitizeContext,
    steps: list[Step],
    logs_by_step: dict[str, list[StepLog]],
    nodes_by_id: dict[str, dict],
    visible_node_ids: set[str],
    redact_source: bool,
) -> list[StepOut]:
    out: list[StepOut] = []
    for step in steps:
        if redact_source and step.node_id not in visible_node_ids:
            continue
        node_type = (nodes_by_id.get(step.node_id) or {}).get("type")
        item = step_to_out(step, logs_by_step.get(step.id, []), sanitize_context, node_type)
        if redact_source:
            item.input = None
            item.logs = []
            item.error = _redacted_error(item.error, True)
            if node_type != "output":
                item.output = None
        out.append(item)
    return out


def _redacted_error(error: str | None, redact_source: bool) -> str | None:
    if redact_source and error:
        return REDACTED_RUN_ERROR
    return error


def _recovery_to_out(run: Run, steps: list[Step]) -> RunRecoveryOut | None:
    if run.status not in {"interrupted", "waiting_for_user"}:
        return None
    waiting_request = None
    waiting_step = next((step for step in steps if step.status == "waiting_for_user"), None)
    if waiting_step is not None:
        payload = loads(waiting_step.input_json, {}) or {}
        request = payload.get("decision_request") if isinstance(payload, dict) else None
        if isinstance(request, dict):
            try:
                waiting_request = RunWaitingRequestOut.model_validate(request)
            except Exception:  # noqa: BLE001
                waiting_request = None
    return RunRecoveryOut(
        resumable=run.status == "interrupted",
        resume_from_node_id=run.resume_from_node_id,
        reason=run.recovery_reason,
        waiting_request=waiting_request,
    )
