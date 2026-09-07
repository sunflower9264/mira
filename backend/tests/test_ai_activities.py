from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    App,
    NlCompileSessionRow,
    PromptAssistantGenerationRow,
    Run,
    User,
    Wiki,
    WikiOperation,
    Workspace,
    WorkspaceSession,
    WorkspaceTurn,
)
from app.utils import dumps, new_id, now_utc
from tests.auth_helpers import create_regular_user
from tests.conftest import TEST_ADMIN_USERNAME


def test_ai_activities_returns_only_current_users_active_parent_tasks(auth_client):
    other = create_regular_user("activity-outsider")

    async def seed() -> dict[str, str]:
        async with SessionLocal() as db:
            owner = (await db.execute(select(User).where(User.username == TEST_ADMIN_USERNAME))).scalar_one()
            app = App(
                id=new_id("app"),
                owner_id=owner.id,
                name="旅行影像",
                graph_json=dumps({"nodes": [], "execution_edges": []}),
            )
            workspace = Workspace(
                id=new_id("workspace"),
                owner_id=owner.id,
                name="产品工作空间",
                project_relpath="activity-project",
            )
            session = WorkspaceSession(id=new_id("session"), workspace_id=workspace.id)
            db.add_all([app, workspace, session])
            await db.flush()

            run = Run(
                id=new_id("run"), app_id=app.id, owner_id=owner.id, status="running",
                inputs_json="{}", graph_json=app.graph_json, started_at=now_utc(),
            )
            child_run = Run(
                id=new_id("run"), app_id=app.id, owner_id=owner.id, workspace_id=workspace.id,
                status="running", inputs_json="{}", graph_json=app.graph_json, started_at=now_utc(),
            )
            completed_run = Run(
                id=new_id("run"), app_id=app.id, owner_id=owner.id, status="success",
                inputs_json="{}", graph_json=app.graph_json, started_at=now_utc(),
            )
            turn = WorkspaceTurn(
                id=new_id("turn"), workspace_id=workspace.id, session_id=session.id,
                status="waiting", prompt="继续吗", started_at=now_utc(),
            )
            compile_session = NlCompileSessionRow(
                id=new_id("nlc"), user_id=owner.id, app_id=app.id, status="planning",
                instruction="生成应用", graph_json=app.graph_json,
            )
            generation = PromptAssistantGenerationRow(
                id=new_id("pa"), user_id=owner.id, app_id=app.id, status="waiting_for_user",
                prompt_json="{}",
            )
            wiki = Wiki(id=new_id("wiki"), owner_id=owner.id)
            operation = WikiOperation(
                id=new_id("wikiop"), wiki_id=wiki.id, kind="maintenance", status="pending",
            )
            other_run = Run(
                id=new_id("run"), app_id=app.id, owner_id=other["id"], status="running",
                inputs_json="{}", graph_json=app.graph_json, started_at=now_utc(),
            )
            db.add_all([
                run, child_run, completed_run, turn, compile_session, generation,
                wiki, operation, other_run,
            ])
            await db.commit()
            return {"run": run.id, "child_run": child_run.id}

    ids = asyncio.run(seed())
    response = auth_client.get("/api/ai-activities")
    assert response.status_code == 200, response.text
    activities = response.json()

    assert {item["kind"] for item in activities} == {
        "app_run", "workspace_turn", "nl_compile", "prompt_assistant", "wiki"
    }
    assert ids["run"] in {item["id"] for item in activities}
    assert ids["child_run"] not in {item["id"] for item in activities}
    assert next(item for item in activities if item["kind"] == "workspace_turn")["status"] == "waiting_user"
    assert next(item for item in activities if item["kind"] == "workspace_turn")["cancellable"] is True
    assert next(item for item in activities if item["kind"] == "app_run")["href"].endswith("/view")

    outsider = create_regular_user("activity-reader")
    auth_client.headers.update({"Authorization": f"Bearer {outsider['token']}"})
    assert auth_client.get("/api/ai-activities").json() == []
