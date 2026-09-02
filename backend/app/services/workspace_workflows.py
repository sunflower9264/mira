from __future__ import annotations

import asyncio
import mimetypes
import shutil
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, Run, Step, Workspace, WorkspaceSession, WorkspaceTurn
from app.runtime.workspace_runtime import WorkspaceDynamicToolResult
from app.schemas.runs import RunResumeIn
from app.config import get_settings
from app.services.apps import can_run_app, get_visible_app_or_404, should_redact_app_source, visible_app_filter
from app.services.run_artifacts import catalog_run_artifacts
from app.services.run_hub import get_run_hub
from app.services.run_orchestrator import cancel_run, schedule_run
from app.services.runs import (
    cancel_run_record,
    create_run_record,
    submit_resume,
    validate_live_resume,
)
from app.services.uploads import seed_upload_from_file
from app.services.workspaces import append_workspace_event, workspace_project_path
from app.utils import dumps, iso, loads, new_id
from app.schemas.workspaces import WorkspaceWorkflowRunFileOut, WorkspaceWorkflowRunOut
from app.services.workflow_data import visible_output
from app.services.run_output_sanitizer import sanitize_run_value


def workflow_dynamic_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "namespace",
            "name": "mira_workflows",
            "description": "查询并调用当前用户有权限运行的可视化工作流。",
            "tools": [
                {
                    "type": "function",
                    "name": "list",
                    "description": "列出可运行的工作流。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                {
                    "type": "function",
                    "name": "run",
                    "description": "运行一个工作流并等待结果；仅在用户明确要求或已确认后调用。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "app_id": {"type": "string"},
                            "input": {"type": "string"},
                            "files": {"type": "array", "items": {"type": "string"}},
                            "confirmation": {
                                "type": "string",
                                "enum": ["explicit_user_request", "confirmed_in_conversation"],
                            },
                        },
                        "required": ["app_id", "confirmation"],
                        "additionalProperties": False,
                    },
                },
            ],
        }
    ]


async def call_workspace_workflow_tool(
    db: AsyncSession,
    *,
    workspace: Workspace,
    session: WorkspaceSession,
    turn: WorkspaceTurn,
    owner_id: str,
    namespace: str,
    tool: str,
    arguments: dict[str, Any],
    on_decision_request,
    cancel_event: asyncio.Event,
) -> WorkspaceDynamicToolResult:
    if namespace != "mira_workflows":
        return WorkspaceDynamicToolResult(False, "未知工作流工具命名空间")
    if tool == "list":
        return WorkspaceDynamicToolResult(True, dumps(await _list_workflows(db, owner_id, arguments)))
    if tool == "run":
        return await _run_workflow(
            db,
            workspace=workspace,
            session=session,
            turn=turn,
            owner_id=owner_id,
            arguments=arguments,
            on_decision_request=on_decision_request,
            cancel_event=cancel_event,
        )
    return WorkspaceDynamicToolResult(False, "未知工作流工具")


async def list_workspace_workflow_runs(
    db: AsyncSession,
    workspace_id: str,
    owner_id: str,
) -> list[WorkspaceWorkflowRunOut]:
    rows = (
        await db.execute(
            select(Run, App)
            .join(App, App.id == Run.app_id)
            .where(Run.workspace_id == workspace_id, Run.owner_id == owner_id)
            .order_by(Run.started_at.desc().nulls_last(), Run.id.desc())
            .limit(100)
        )
    ).all()
    result: list[WorkspaceWorkflowRunOut] = []
    workspace_root = workspace_project_path(owner_id, workspace_id).resolve()
    for run, app in rows:
        run_root = workspace_root / "workflow-runs" / run.id
        result_path: str | None = None
        files: list[WorkspaceWorkflowRunFileOut] = []
        if run_root.is_dir() and not run_root.is_symlink():
            for path in sorted(run_root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    relative = path.resolve().relative_to(workspace_root).as_posix()
                except ValueError:
                    continue
                if path.relative_to(run_root).as_posix() == "result.html":
                    result_path = relative
                    continue
                files.append(
                    WorkspaceWorkflowRunFileOut(
                        path=relative,
                        name=path.name,
                        mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        size=path.stat().st_size,
                    )
                )
        result.append(WorkspaceWorkflowRunOut(
            run_id=run.id,
            app_id=run.app_id,
            app_name=app.name,
            status=run.status,
            session_id=run.workspace_session_id,
            turn_id=run.workspace_turn_id,
            started_at=iso(run.started_at),
            finished_at=iso(run.finished_at),
            error="运行失败" if run.error and should_redact_app_source(app, owner_id) else run.error,
            result_path=result_path,
            files=files,
        ))
    return result


async def _list_workflows(db: AsyncSession, owner_id: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    query = str(arguments.get("query") or "").strip().lower()
    rows = (
        await db.execute(select(App).where(visible_app_filter(owner_id)).order_by(App.updated_at.desc()).limit(100))
    ).scalars().all()
    result = []
    for app in rows:
        if not can_run_app(app):
            continue
        haystack = f"{app.name} {app.description}".lower()
        if query and query not in haystack:
            continue
        graph = loads(app.graph_json, {}) or {}
        input_node = next(
            (node for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("type") == "user_input"),
            None,
        )
        schema = input_node.get("input_schema") if isinstance(input_node, dict) else None
        result.append(
            {
                "app_id": app.id,
                "name": app.name,
                "description": app.description,
                "input": (
                    {
                        "required": True,
                        "label": str((schema or {}).get("label") or input_node.get("title") or "输入"),
                        "accepts_files": True,
                    }
                    if input_node
                    else {"required": False, "accepts_files": False}
                ),
            }
        )
    return result


async def _run_workflow(
    db: AsyncSession,
    *,
    workspace: Workspace,
    session: WorkspaceSession,
    turn: WorkspaceTurn,
    owner_id: str,
    arguments: dict[str, Any],
    on_decision_request,
    cancel_event: asyncio.Event,
) -> WorkspaceDynamicToolResult:
    confirmation = arguments.get("confirmation")
    if confirmation not in {"explicit_user_request", "confirmed_in_conversation"}:
        return WorkspaceDynamicToolResult(False, "调用工作流前需要用户确认")
    app_id = arguments.get("app_id")
    if not isinstance(app_id, str) or not app_id:
        return WorkspaceDynamicToolResult(False, "app_id 必填")
    try:
        app = await get_visible_app_or_404(db, app_id, owner_id)
    except HTTPException as exc:
        return WorkspaceDynamicToolResult(False, str(exc.detail)[:1_000])
    if not can_run_app(app):
        return WorkspaceDynamicToolResult(False, "工作流当前不可运行")
    graph = loads(app.graph_json, {}) or {}
    input_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("type") == "user_input"]
    input_value = str(arguments.get("input") or "")
    file_refs: list[dict[str, str]] = []
    for item in arguments.get("files") or []:
        if not isinstance(item, str):
            return WorkspaceDynamicToolResult(False, "files 必须是工作空间相对路径数组")
        upload = _workspace_file_to_upload(owner_id, workspace, item)
        if upload is None:
            return WorkspaceDynamicToolResult(False, f"工作空间文件不存在或路径不安全：{item}")
        file_refs.append({"id": upload.id, "name": upload.name})
    if (input_value or file_refs) and not input_nodes:
        return WorkspaceDynamicToolResult(False, "该工作流没有可接收输入的节点")
    inputs: dict[str, Any] = {}
    if input_nodes:
        node_id = str(input_nodes[0].get("id"))
        inputs[node_id] = {"value": input_value, "attachments": file_refs} if file_refs else input_value
    run_id: str | None = None
    try:
        run_id, _ = await create_run_record(
            db,
            owner_id,
            app.id,
            inputs,
            "auto",
            workspace_id=workspace.id,
            workspace_session_id=session.id,
            workspace_turn_id=turn.id,
        )
        await get_run_hub().create(run_id)
        schedule_run(run_id)
        await append_workspace_event(
            db,
            workspace_id=workspace.id,
            session_id=session.id,
            turn_id=turn.id,
            event_type="workflow_run_started",
            payload={"run_id": run_id, "app_id": app.id, "app_name": app.name, "status": "running"},
        )
        await _wait_for_run(
            db,
            run_id,
            owner_id,
            app.name,
            workspace.id,
            session.id,
            turn.id,
            on_decision_request,
            cancel_event,
        )
    except asyncio.CancelledError:
        if run_id is not None:
            try:
                changed = await cancel_run_record(db, run_id, owner_id)
                if changed:
                    cancel_run(run_id)
            except HTTPException:
                pass
        raise
    except HTTPException as exc:
        return WorkspaceDynamicToolResult(False, str(exc.detail)[:1_000])
    except Exception:  # noqa: BLE001
        return WorkspaceDynamicToolResult(False, "工作流调用失败")
    assert run_id is not None
    run = await db.get(Run, run_id)
    if run is None:
        return WorkspaceDynamicToolResult(False, "工作流运行记录不存在")
    await db.refresh(run)
    if run.status != "success":
        error = "工作流运行失败" if should_redact_app_source(app, owner_id) else (run.error or "工作流运行失败")
        await append_workspace_event(
            db,
            workspace_id=workspace.id,
            session_id=session.id,
            turn_id=turn.id,
            event_type="workflow_run_finished",
            payload={"run_id": run.id, "app_id": app.id, "app_name": app.name, "status": run.status, "error": error},
        )
        return WorkspaceDynamicToolResult(False, error)
    output = await _copy_run_results(db, run, owner_id, workspace)
    artifacts, _ = await catalog_run_artifacts(db, run, limit=100)
    copied = []
    target_root = workspace_project_path(owner_id, workspace.id) / "workflow-runs" / run.id
    target_root.mkdir(parents=True, exist_ok=True)
    for entry in artifacts:
        if entry.integrity != "verified":
            continue
        target = (target_root / entry.relative_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.file_path, target)
        copied.append(target.relative_to(workspace_project_path(owner_id, workspace.id)).as_posix())
    await append_workspace_event(
        db,
        workspace_id=workspace.id,
        session_id=session.id,
        turn_id=turn.id,
        event_type="workflow_run_finished",
        payload={"run_id": run.id, "app_id": app.id, "app_name": app.name, "status": "success"},
    )
    return WorkspaceDynamicToolResult(
        True,
        dumps({"run_id": run.id, "result_path": output, "artifacts": copied}),
    )


async def _wait_for_run(
    db: AsyncSession,
    run_id: str,
    owner_id: str,
    app_name: str,
    workspace_id: str,
    session_id: str,
    turn_id: str,
    on_decision_request,
    cancel_event: asyncio.Event,
) -> None:
    answered: set[str] = set()
    while True:
        if cancel_event.is_set():
            await cancel_run_record(db, run_id, owner_id)
            cancel_run(run_id)
            raise asyncio.CancelledError
        run = await db.get(Run, run_id)
        if run is not None:
            await db.refresh(run)
        if run is None or run.status in {"success", "failed", "cancelled"}:
            return
        if run.status == "waiting_for_user":
            step = (await db.execute(select(Step).where(Step.run_id == run_id, Step.status == "waiting_for_user"))).scalars().first()
            payload = loads(step.input_json, {}) if step else {}
            request = payload.get("decision_request") if isinstance(payload, dict) else None
            request_id = request.get("request_id") if isinstance(request, dict) else None
            if isinstance(request_id, str) and request_id not in answered and isinstance(request, dict):
                from app.runtime.base import DecisionRequest

                original = DecisionRequest.model_validate(request)
                await append_workspace_event(
                    db,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type="workflow_run_waiting",
                    payload={"run_id": run_id, "app_name": app_name, "status": "waiting_for_user"},
                )
                decision = await on_decision_request(
                    original.model_copy(update={"request_id": f"workflow:{run_id}:{request_id}"})
                )
                answered.add(request_id)
                resume = RunResumeIn(
                    node_id=step.node_id,
                    request_id=request_id,
                    answers=decision.answers,
                    text=decision.text,
                    attachments=[
                        {"id": attachment.id, "name": attachment.name}
                        for attachment in decision.attachments
                    ],
                )
                child_decision = await submit_resume(db, owner_id, run_id, resume)
                await validate_live_resume(db, owner_id, run_id, resume, child_decision)
                channel = get_run_hub().get(run_id)
                ack = channel.submit_resume(step.node_id, request_id, child_decision) if channel else None
                if ack is None or not await asyncio.shield(ack):
                    raise RuntimeError("工作流提问会话已中断")
        await asyncio.sleep(0.25)


def _workspace_file_to_upload(owner_id: str, workspace: Workspace, relative: str):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
        return None
    root = workspace_project_path(owner_id, workspace.id).resolve()
    source = (root / path).resolve()
    try:
        source.relative_to(root)
    except ValueError:
        return None
    candidate = root
    if any((candidate := candidate / part).is_symlink() for part in path.parts):
        return None
    if not source.is_file() or source.stat().st_size > get_settings().max_upload_bytes:
        return None
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return seed_upload_from_file(owner_id, new_id("upl"), source, source.name, mime)


async def _copy_run_results(
    db: AsyncSession,
    run: Run,
    owner_id: str,
    workspace: Workspace,
) -> str | None:
    graph = loads(run.graph_json, {}) or {}
    output_ids = [
        node["id"]
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str) and node.get("type") == "output"
    ]
    step = (
        await db.execute(
            select(Step)
            .where(Step.run_id == run.id, Step.node_id.in_(output_ids), Step.status == "success")
            .order_by(Step.ordering.desc())
        )
    ).scalars().first()
    if step is None or not step.output_json:
        return None
    output = sanitize_run_value(visible_output(loads(step.output_json, None)), run, html_mode=True)
    if not isinstance(output, str):
        return None
    root = workspace_project_path(owner_id, workspace.id)
    target = root / "workflow-runs" / run.id / "result.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    return target.relative_to(root).as_posix()
