from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, Run, RunEvent, Step, StepLog
from app.schemas import RunStepTraceOut, RunTraceArtifactOut, RunTraceChunkOut
from app.services.artifacts import signed_run_artifact_download_url
from app.services.apps import should_redact_app_source
from app.services.run_output_sanitizer import RunSanitizeContext, build_run_sanitize_context, sanitize_run_text, sanitize_run_value
from app.services.run_serializer import log_to_out, step_to_out
from app.services.runtime_paths import run_workspace
from app.utils import loads

LLM_NODE_TYPES = {"generate", "condition", "output"}
TRACE_CHUNK_LIMIT = 500
TRACE_ARTIFACT_LIMIT = 200


async def get_run_step_trace(db: AsyncSession, run_id: str, node_id: str, user_id: str) -> RunStepTraceOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    app = await db.get(App, run.app_id)
    if should_redact_app_source(app, user_id):
        raise HTTPException(status_code=403, detail="该应用不允许查看 Trace")

    graph = loads(run.graph_json, {"nodes": [], "edges": []}) or {"nodes": [], "edges": []}
    node = next(
        (
            item
            for item in graph.get("nodes", [])
            if isinstance(item, dict) and item.get("id") == node_id
        ),
        None,
    )
    if not isinstance(node, dict):
        raise HTTPException(status_code=404, detail="运行步骤不存在")
    node_type = str(node.get("type") or "")
    if node_type not in LLM_NODE_TYPES:
        raise HTTPException(status_code=400, detail="Trace 仅支持 LLM 节点")

    step = (
        await db.execute(select(Step).where(Step.run_id == run.id, Step.node_id == node_id))
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(status_code=404, detail="运行步骤不存在")

    logs = (
        await db.execute(select(StepLog).where(StepLog.step_id == step.id).order_by(StepLog.ts.asc()))
    ).scalars().all()
    sanitize_context = build_run_sanitize_context(run)
    step_out = step_to_out(step, list(logs), sanitize_context, node_type)
    input_payload = step_out.input if isinstance(step_out.input, dict) else {}
    prompt = input_payload.get("prompt") if isinstance(input_payload, dict) else ""
    if not isinstance(prompt, str):
        prompt = ""

    chunks, chunks_truncated, raw_text = await _trace_chunks(db, run, node_id, sanitize_context)
    artifacts, artifacts_truncated = _trace_artifacts(run)

    return RunStepTraceOut(
        run_id=run.id,
        node_id=node_id,
        node_title=str(node.get("title") or node_id),
        node_type=node_type,  # type: ignore[arg-type]
        status=step_out.status,
        agent=str(graph.get("agent") or "") or None,
        model=str(node.get("model") or "") or None,
        reasoning_effort=str(node.get("reasoning_effort") or "") or None,
        agent_session_id=step_out.agent_session_id,
        started_at=step_out.started_at,
        finished_at=step_out.finished_at,
        duration_ms=step_out.duration_ms,
        error=step_out.error,
        prompt=prompt,
        input=step_out.input,
        output=step_out.output,
        logs=[log_to_out(log) for log in logs],
        chunks=chunks,
        chunks_truncated=chunks_truncated,
        raw_text=raw_text,
        artifacts=artifacts,
        artifacts_truncated=artifacts_truncated,
    )


async def _trace_chunks(
    db: AsyncSession,
    run: Run,
    node_id: str,
    sanitize_context: RunSanitizeContext,
) -> tuple[list[RunTraceChunkOut], bool, str]:
    rows = (
        await db.execute(
            select(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.event == "step.delta")
            .order_by(RunEvent.id.asc())
        )
    ).scalars().all()
    chunks: list[RunTraceChunkOut] = []
    text_parts: list[str] = []
    truncated = False
    for row in rows:
        data = loads(row.data_json, {}) or {}
        if not isinstance(data, dict) or data.get("node_id") != node_id:
            continue
        chunk = data.get("chunk")
        if not isinstance(chunk, dict):
            continue
        chunk_type = chunk.get("type")
        if chunk_type not in {"text", "tool_call", "tool_result", "error", "done"}:
            continue
        chunk_text = chunk.get("text") if isinstance(chunk.get("text"), str) else None
        if chunk_type == "text" and chunk_text:
            text_parts.append(sanitize_run_text(chunk_text, sanitize_context))
        if len(chunks) >= TRACE_CHUNK_LIMIT:
            truncated = True
            continue
        raw = chunk.get("raw") if isinstance(chunk.get("raw"), dict) else None
        chunks.append(
            RunTraceChunkOut(
                event_id=row.id,
                type=chunk_type,  # type: ignore[arg-type]
                text=sanitize_run_text(chunk_text, sanitize_context) if chunk_text else None,
                raw=sanitize_run_value(raw, sanitize_context) if raw is not None else None,
            )
        )
    return chunks, truncated, "".join(text_parts)


def _trace_artifacts(run: Run) -> tuple[list[RunTraceArtifactOut], bool]:
    workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
    if not workspace.exists():
        return [], False
    artifacts: list[RunTraceArtifactOut] = []
    truncated = False
    for path in sorted((item for item in workspace.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        if len(artifacts) >= TRACE_ARTIFACT_LIMIT:
            truncated = True
            break
        try:
            relative = path.resolve().relative_to(workspace).as_posix()
        except ValueError:
            continue
        artifacts.append(
            RunTraceArtifactOut(
                path=relative,
                name=path.name,
                size=path.stat().st_size,
                download_url=signed_run_artifact_download_url(run, relative),
            )
        )
    return artifacts, truncated
