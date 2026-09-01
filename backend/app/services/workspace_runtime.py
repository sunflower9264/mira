from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import Workspace, WorkspaceSession, WorkspaceTurn
from app.runtime.base import DecisionRequest, DecisionResult
from app.runtime.codex_runtime import _prepare_scoped_home
from app.runtime.workspace_runtime import (
    WorkspaceRuntimeEvent,
    WorkspaceRuntimeSpec,
    WorkspaceTurnRequest,
    get_workspace_runtime,
)
from app.services.runtime_paths import codex_home
from app.services.tools import runtime_tools_for_graph
from app.services.workspaces import (
    append_workspace_event,
    discard_workspace_wiki_changes,
    finish_workspace_turn,
    get_workspace_git_credentials,
    prepare_workspace_wiki_copy,
    publish_workspace_wiki_copy,
    update_workspace_runtime_state,
    workspace_git_allowed_hosts,
    workspace_metadata_path,
    workspace_project_path,
)
from app.utils import now_utc


@dataclass
class _ActiveWorkspaceTurn:
    cancel_event: asyncio.Event
    decision_future: asyncio.Future[DecisionResult] | None = None
    decision_request_id: str | None = None


@dataclass(frozen=True)
class WorkspaceGitResult:
    status: Literal["success", "requires_confirmation", "failed"]
    duration_ms: int = 0
    error: str | None = None


_active_turns: dict[str, _ActiveWorkspaceTurn] = {}
_turn_tasks: dict[str, asyncio.Task] = {}

SUPPORTED_WORKSPACE_SLASH_COMMANDS = {
    "/new": "新建 Session",
    "/compact": "压缩当前 Session 上下文",
    "/review": "审查当前工作区未提交改动",
    "/model": "为下一轮选择模型",
    "/status": "查看 runtime 与当前任务状态",
}


def schedule_workspace_turn(turn_id: str) -> None:
    current = _turn_tasks.get(turn_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(run_workspace_turn(turn_id))
    _turn_tasks[turn_id] = task
    task.add_done_callback(lambda _task: _turn_tasks.pop(turn_id, None))


async def start_workspace_runtime(db: AsyncSession, workspace: Workspace) -> None:
    spec = _runtime_spec(workspace)
    await _sync_codex_home(db, spec)
    try:
        handle = await get_workspace_runtime().ensure_started(spec)
    except Exception as exc:  # noqa: BLE001
        await update_workspace_runtime_state(
            db,
            workspace_id=workspace.id,
            status="error",
            last_error=_safe_error(exc),
        )
        raise
    await update_workspace_runtime_state(
        db,
        workspace_id=workspace.id,
        status="ready",
        container_id=handle.container_id,
        started_at=handle.started_at,
        last_error=None,
    )


async def run_workspace_turn(turn_id: str) -> None:
    session_state = _ActiveWorkspaceTurn(cancel_event=asyncio.Event())
    _active_turns[turn_id] = session_state
    async with SessionLocal() as db:
        result = await db.execute(
            select(Workspace, WorkspaceSession, WorkspaceTurn)
            .join(WorkspaceSession, WorkspaceSession.workspace_id == Workspace.id)
            .join(WorkspaceTurn, WorkspaceTurn.session_id == WorkspaceSession.id)
            .where(WorkspaceTurn.id == turn_id)
        )
        item = result.one_or_none()
        if item is None:
            _active_turns.pop(turn_id, None)
            return
        workspace, session, turn = item
        if turn.status != "pending":
            _active_turns.pop(turn_id, None)
            return
        turn.status = "running"
        turn.started_at = now_utc()
        session.status = "running"
        session.updated_at = turn.started_at
        workspace.runtime_status = "busy"
        await db.commit()

        try:
            wiki_sync = await prepare_workspace_wiki_copy(db, workspace)
            if wiki_sync.get("status") != "ready":
                raise RuntimeError(wiki_sync.get("error") or "Wiki 同步未完成")
            await start_workspace_runtime(db, workspace)

            async def on_event(event: WorkspaceRuntimeEvent) -> None:
                if event.type == "thread_started":
                    thread_id = event.payload.get("thread_id")
                    if isinstance(thread_id, str) and thread_id:
                        session.thread_id = thread_id
                        session.updated_at = now_utc()
                await append_workspace_event(
                    db,
                    workspace_id=workspace.id,
                    session_id=session.id,
                    turn_id=turn.id,
                    event_type=event.type,
                    payload=event.payload,
                )

            async def on_decision_request(request: DecisionRequest) -> DecisionResult:
                await append_workspace_event(
                    db,
                    workspace_id=workspace.id,
                    session_id=session.id,
                    turn_id=turn.id,
                    event_type="decision_request",
                    payload=request.model_dump(mode="json"),
                )
                turn.status = "waiting"
                session.status = "waiting"
                await db.commit()
                future = asyncio.get_running_loop().create_future()
                session_state.decision_future = future
                session_state.decision_request_id = request.request_id
                try:
                    decision = await future
                finally:
                    session_state.decision_future = None
                    session_state.decision_request_id = None
                if decision.ok:
                    turn.status = "running"
                    session.status = "running"
                    await db.commit()
                return decision

            prompt = _workspace_prompt(turn.prompt)
            execution = await get_workspace_runtime().execute_turn(
                _runtime_spec(workspace),
                WorkspaceTurnRequest(
                    prompt=prompt,
                    thread_id=session.thread_id,
                    model=turn.model,
                    reasoning_effort=turn.reasoning_effort,
                ),
                on_event=on_event,
                cancel_event=session_state.cancel_event,
                on_decision_request=on_decision_request,
            )
            if execution.finished_with == "done":
                wiki_publish = await publish_workspace_wiki_copy(db, workspace)
                if wiki_publish.get("status") != "ready":
                    await append_workspace_event(
                        db,
                        workspace_id=workspace.id,
                        session_id=session.id,
                        turn_id=turn.id,
                        event_type="wiki_sync_failed",
                        payload={"message": wiki_publish.get("error") or "Wiki 发布需要重试"},
                    )
                await finish_workspace_turn(db, turn.id, status="success")
            elif execution.finished_with == "cancelled":
                discard_workspace_wiki_changes(workspace)
                await finish_workspace_turn(db, turn.id, status="cancelled")
            else:
                discard_workspace_wiki_changes(workspace)
                await finish_workspace_turn(
                    db,
                    turn.id,
                    status="failed",
                    error=execution.error or "Codex turn 执行失败",
                )
        except asyncio.CancelledError:
            session_state.cancel_event.set()
            discard_workspace_wiki_changes(workspace)
            await finish_workspace_turn(db, turn.id, status="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            discard_workspace_wiki_changes(workspace)
            await append_workspace_event(
                db,
                workspace_id=workspace.id,
                session_id=session.id,
                turn_id=turn.id,
                event_type="error",
                payload={"message": _safe_error(exc)},
            )
            await finish_workspace_turn(db, turn.id, status="failed", error=_safe_error(exc))
        finally:
            refreshed_workspace = await db.get(Workspace, workspace.id)
            if refreshed_workspace is not None and refreshed_workspace.runtime_status == "busy":
                refreshed_workspace.runtime_status = "ready"
                refreshed_workspace.updated_at = now_utc()
                await db.commit()
            _active_turns.pop(turn_id, None)


async def cancel_workspace_turn(turn_id: str) -> bool:
    state = _active_turns.get(turn_id)
    if state is None:
        return False
    state.cancel_event.set()
    if state.decision_future is not None and not state.decision_future.done():
        state.decision_future.set_result(DecisionResult(ok=False, error="运行已取消"))
    async with SessionLocal() as db:
        turn = await db.get(WorkspaceTurn, turn_id)
        if turn is None:
            return True
        workspace = await db.get(Workspace, turn.workspace_id)
        if workspace is not None:
            with suppress(Exception):
                await get_workspace_runtime().interrupt(_runtime_spec(workspace))
    return True


async def resume_workspace_turn(
    turn_id: str,
    request_id: str,
    result: DecisionResult,
) -> bool:
    state = _active_turns.get(turn_id)
    if (
        state is None
        or state.decision_future is None
        or state.decision_future.done()
        or state.decision_request_id != request_id
    ):
        return False
    state.decision_future.set_result(result)
    return True


async def mark_active_workspace_turns_interrupted(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(WorkspaceTurn).where(WorkspaceTurn.status.in_({"pending", "running", "waiting"}))
        )
    ).scalars().all()
    now = now_utc()
    for turn in rows:
        turn.status = "interrupted"
        turn.error = "后端进程重启，Workspace turn 已中断"
        turn.finished_at = now
        session = await db.get(WorkspaceSession, turn.session_id)
        if session is not None:
            session.status = "error"
            session.updated_at = now
    await db.commit()
    return len(rows)


async def reconcile_workspace_runtimes(db: AsyncSession) -> None:
    rows = (await db.execute(select(Workspace))).scalars().all()
    for workspace in rows:
        try:
            await start_workspace_runtime(db, workspace)
        except Exception:
            # 单个 Workspace runtime 失效不能阻断其他 Workspace 或后端启动。
            continue


async def run_workspace_session_action(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
    action: Literal["compact", "archive", "review"],
    instructions: str | None = None,
) -> dict:
    if not session.thread_id:
        raise HTTPException(status_code=409, detail="当前 Session 尚未创建 Codex thread")
    if session.status in {"running", "waiting"}:
        raise HTTPException(status_code=409, detail="当前 Session 正在执行")
    await start_workspace_runtime(db, workspace)
    runtime = get_workspace_runtime()
    spec = _runtime_spec(workspace)
    if action == "review":
        return await runtime.start_review(
            spec,
            thread_id=session.thread_id,
            instructions=instructions,
        )
    return await runtime.thread_action(
        spec,
        action=action,
        thread_id=session.thread_id,
    )


async def rename_workspace_thread(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
    name: str,
) -> None:
    if not session.thread_id:
        return
    await start_workspace_runtime(db, workspace)
    await get_workspace_runtime().thread_action(
        _runtime_spec(workspace),
        action="rename",
        thread_id=session.thread_id,
        name=name,
    )


async def delete_workspace_thread(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
) -> None:
    if not session.thread_id:
        return
    if session.status in {"running", "waiting"}:
        raise HTTPException(status_code=409, detail="当前 Session 正在执行")
    await start_workspace_runtime(db, workspace)
    await get_workspace_runtime().delete_thread(
        _runtime_spec(workspace), thread_id=session.thread_id
    )


async def get_workspace_goal(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
) -> dict | None:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    return await get_workspace_runtime().get_goal(_runtime_spec(workspace), thread_id=session.thread_id)


async def set_workspace_goal(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
    *,
    objective: str | None,
    status: str | None,
    token_budget: int | None,
) -> dict:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    return await get_workspace_runtime().set_goal(
        _runtime_spec(workspace),
        thread_id=session.thread_id,
        objective=objective,
        status=status,
        token_budget=token_budget,
    )


async def clear_workspace_goal(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
) -> None:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    await get_workspace_runtime().clear_goal(_runtime_spec(workspace), thread_id=session.thread_id)


async def list_workspace_processes(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
) -> list[dict]:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    return await get_workspace_runtime().list_processes(
        _runtime_spec(workspace), thread_id=session.thread_id
    )


async def stop_workspace_process(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
    process_id: str,
) -> None:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    await get_workspace_runtime().stop_process(
        _runtime_spec(workspace),
        thread_id=session.thread_id,
        process_id=process_id,
    )


async def clean_workspace_processes(
    db: AsyncSession,
    workspace: Workspace,
    session: WorkspaceSession,
) -> None:
    _require_thread(session)
    await start_workspace_runtime(db, workspace)
    await get_workspace_runtime().clean_processes(
        _runtime_spec(workspace), thread_id=session.thread_id
    )


async def get_workspace_runtime_status(workspace: Workspace) -> dict:
    state = await get_workspace_runtime().state(workspace.id)
    status = {"running": "ready", "stopped": "stopped", "failed": "error"}[state.status]
    return {
        "status": "busy" if status == "ready" and workspace.runtime_status == "busy" else status,
        "container_id": state.container_id,
        "started_at": state.started_at,
        "error": state.error,
    }


async def run_workspace_git_operation(
    db: AsyncSession,
    workspace: Workspace,
    *,
    operation: Literal["clone", "pull", "push"],
    confirmed: bool = False,
) -> WorkspaceGitResult:
    if operation == "push" and not confirmed:
        return WorkspaceGitResult(status="requires_confirmation")
    credentials = await get_workspace_git_credentials(db, workspace.id)
    if credentials is None:
        return WorkspaceGitResult(status="failed", error="尚未配置 Git 仓库")
    repository_url, branch, token = credentials
    allowed_hosts = await workspace_git_allowed_hosts(db)
    try:
        _validate_git_target(repository_url, allowed_hosts)
    except ValueError as exc:
        return WorkspaceGitResult(status="failed", error=str(exc))
    started = time.monotonic()
    result = await asyncio.to_thread(
        _run_git_sync,
        workspace_project_path(workspace.owner_id, workspace.id),
        repository_url,
        branch,
        token,
        operation,
    )
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    return WorkspaceGitResult(status=result[0], duration_ms=duration_ms, error=result[1])


def _runtime_spec(workspace: Workspace) -> WorkspaceRuntimeSpec:
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id)
    return WorkspaceRuntimeSpec(
        workspace_id=workspace.id,
        project_path=workspace_project_path(workspace.owner_id, workspace.id),
        codex_home=metadata / "codex-home",
        wiki_path=metadata / "wiki" / "working-tree",
    )


def _require_thread(session: WorkspaceSession) -> str:
    if not session.thread_id:
        raise HTTPException(status_code=409, detail="当前 Session 尚未创建 Codex thread")
    return session.thread_id


async def _sync_codex_home(db: AsyncSession, spec: WorkspaceRuntimeSpec) -> None:
    # 复用现有 HOME 构建逻辑；Workspace scope 固定，因此 thread/session 数据可跨 turn 保留。
    runtime_tools = await runtime_tools_for_graph(db, {})
    _prepare_scoped_home(
        codex_home(),
        spec.project_path,
        runtime_tools,
        session_scope=f"workspace:{spec.workspace_id}",
        target_home=spec.codex_home,
    )


def _workspace_prompt(prompt: str) -> str:
    return (
        prompt.rstrip()
        + "\n\n## Mira Workspace Wiki\n"
        + "当前用户 Wiki 的完整 working copy 位于 /mnt/wiki。你可以按任务需要修改其中的 Markdown；"
        + "只能修改 /mnt/wiki/wiki/**/*.md，不得修改 /mnt/wiki/raw、purpose.md 或 schema.md。"
        + "成功完成后 Mira 会发布新 Wiki revision；失败或取消不会写回。"
    )


def _validate_git_target(repository_url: str, allowed_hosts: list[str]) -> None:
    parsed = urlparse(repository_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {item.strip().lower().rstrip(".") for item in allowed_hosts if item.strip()}
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("Git 仅支持不含凭据的 HTTPS 仓库地址")
    if host not in allowed:
        raise ValueError("Git 仓库域名不在管理员白名单中")


def _run_git_sync(
    project_path: Path,
    repository_url: str,
    branch: str,
    token: str | None,
    operation: Literal["clone", "pull", "push"],
) -> tuple[Literal["success", "failed"], str | None]:
    project_path.mkdir(parents=True, exist_ok=True)
    if operation == "clone" and any(project_path.iterdir()):
        return "failed", "工作空间不是空目录，不能克隆"
    if operation != "clone" and not (project_path / ".git").is_dir():
        return "failed", "工作空间尚未初始化 Git 仓库"
    with tempfile.TemporaryDirectory(prefix="mira-git-helper-") as temp_name:
        askpass = Path(temp_name) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\ncase \"$1\" in *Username*) printf '%s' \"$MIRA_GIT_USERNAME\" ;; "
            "*) printf '%s' \"$MIRA_GIT_TOKEN\" ;; esac\n",
            encoding="utf-8",
        )
        os.chmod(askpass, 0o700)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": temp_name,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(askpass),
            "MIRA_GIT_USERNAME": "oauth2",
            "MIRA_GIT_TOKEN": token or "",
        }
        if operation == "clone":
            command = [
                "git", "clone", "--single-branch", "--branch", branch, "--", repository_url,
                str(project_path),
            ]
            cwd = project_path.parent
        elif operation == "pull":
            command = ["git", "pull", "--ff-only", "origin", branch]
            cwd = project_path
        else:
            command = ["git", "push", "origin", branch]
            cwd = project_path
        try:
            completed = subprocess.run(  # noqa: S603 - argv is fixed and URL/branch are validated
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "failed", "Git 操作未完成"
    if completed.returncode != 0:
        return "failed", "Git 操作失败"
    return "success", None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "Workspace 文件权限异常"
    value = str(exc).strip() or "Workspace 执行失败"
    settings = get_settings()
    for root in (settings.runtime_dir, settings.data_dir):
        value = value.replace(str(root.resolve()), "[Mira data]")
    return value[:1_000]
