from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    App,
    NlCompileSessionRow,
    PromptAssistantGenerationRow,
    Run,
    Wiki,
    WikiOperation,
    Workspace,
    WorkspaceTurn,
)
from app.schemas.activities import AiActivityOut
from app.utils import iso


async def list_ai_activities(db: AsyncSession, user_id: str) -> list[AiActivityOut]:
    activities: list[AiActivityOut] = []

    run_rows = (
        await db.execute(
            select(Run, App.name)
            .join(App, App.id == Run.app_id)
            .where(
                Run.owner_id == user_id,
                Run.workspace_id.is_(None),
                Run.status.in_({"pending", "running", "waiting_for_user"}),
            )
        )
    ).all()
    for run, app_name in run_rows:
        status = "waiting_user" if run.status == "waiting_for_user" else "queued" if run.status == "pending" else "working"
        activities.append(
            AiActivityOut(
                id=run.id,
                kind="app_run",
                status=status,
                title=app_name,
                phase={"pending": "排队中", "running": "正在执行工作流", "waiting_for_user": "等待你回复"}[run.status],
                started_at=iso(run.started_at),
                cancellable=True,
                href=f"/apps/{run.app_id}/view",
            )
        )

    turn_rows = (
        await db.execute(
            select(WorkspaceTurn, Workspace.name)
            .join(Workspace, Workspace.id == WorkspaceTurn.workspace_id)
            .where(
                Workspace.owner_id == user_id,
                WorkspaceTurn.status.in_({"pending", "running", "waiting"}),
            )
        )
    ).all()
    for turn, workspace_name in turn_rows:
        status = "waiting_user" if turn.status == "waiting" else "queued" if turn.status == "pending" else "working"
        activities.append(
            AiActivityOut(
                id=turn.id,
                kind="workspace_turn",
                status=status,
                title=workspace_name,
                phase={"pending": "准备中", "running": "正在生成回复", "waiting": "等待你回复"}[turn.status],
                started_at=iso(turn.started_at or turn.created_at),
                cancellable=True,
                href=f"/workspaces/{turn.workspace_id}",
            )
        )

    compile_rows = (
        await db.execute(
            select(NlCompileSessionRow, App.name)
            .join(App, App.id == NlCompileSessionRow.app_id)
            .where(
                NlCompileSessionRow.user_id == user_id,
                NlCompileSessionRow.status.in_({"planning", "applying", "waiting_for_user", "planned"}),
            )
        )
    ).all()
    for session, app_name in compile_rows:
        waiting = session.status in {"waiting_for_user", "planned"}
        activities.append(
            AiActivityOut(
                id=session.id,
                kind="nl_compile",
                status="waiting_user" if waiting else "working",
                title=app_name,
                phase={
                    "planning": "正在生成画布方案",
                    "applying": "正在应用到画布",
                    "waiting_for_user": "等待你回复",
                    "planned": "等待你确认方案",
                }[session.status],
                started_at=iso(session.created_at),
                cancellable=True,
                href=f"/apps/{session.app_id}/editor",
            )
        )

    prompt_rows = (
        await db.execute(
            select(PromptAssistantGenerationRow, App.name)
            .join(App, App.id == PromptAssistantGenerationRow.app_id)
            .where(
                PromptAssistantGenerationRow.user_id == user_id,
                PromptAssistantGenerationRow.status.in_({"running", "waiting_for_user"}),
            )
        )
    ).all()
    for generation, app_name in prompt_rows:
        waiting = generation.status == "waiting_for_user"
        activities.append(
            AiActivityOut(
                id=generation.id,
                kind="prompt_assistant",
                status="waiting_user" if waiting else "working",
                title=app_name,
                phase="等待你回复" if waiting else "正在优化提示词",
                started_at=iso(generation.created_at),
                cancellable=True,
                href=f"/apps/{generation.app_id}/editor",
            )
        )

    wiki_rows = (
        await db.execute(
            select(WikiOperation)
            .join(Wiki, Wiki.id == WikiOperation.wiki_id)
            .where(Wiki.owner_id == user_id, WikiOperation.status.in_({"pending", "running"}))
        )
    ).scalars().all()
    for operation in wiki_rows:
        activities.append(
            AiActivityOut(
                id=operation.id,
                kind="wiki",
                status="queued" if operation.status == "pending" else "working",
                title="LLM Wiki",
                phase="等待处理" if operation.status == "pending" else "正在整理 Wiki",
                started_at=iso(operation.started_at or operation.created_at),
                cancellable=True,
                href="/wiki",
            )
        )

    activities.sort(key=lambda item: item.started_at or "", reverse=True)
    return activities
