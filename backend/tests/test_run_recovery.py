from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import App, Run, Step, User
from app.services.runs import create_run_record, mark_active_runs_interrupted
from app.utils import dumps


USER_INPUT_NODE = {
    "id": "n_input",
    "type": "user_input",
    "position": {"x": 0, "y": 0},
    "title": "Input",
    "input_schema": {"label": "input", "kind": "text"},
}


def _generate_node(node_id: str, prompt: str) -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 100, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _output_node(node_id: str, source_node_id: str) -> dict:
    return {
        "id": node_id,
        "type": "output",
        "position": {"x": 200, "y": 0},
        "title": node_id,
        "prompt": "render [[respond:<section>ok</section>]]",
        "source_node_id": source_node_id,
    }


def _build_app(auth_client, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "RecoveryApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 200, response.text
    return created["id"]


def _wait_for_status(auth_client, run_id: str, statuses: set[str], *, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = auth_client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


async def _admin_user() -> User:
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == "test-admin"))
        ).scalar_one()
        return user


async def _create_unscheduled_run(app_id: str, inputs: dict | None = None) -> str:
    user = await _admin_user()
    async with SessionLocal() as db:
        run_id, _ = await create_run_record(db, user.id, app_id, inputs or {})
        return run_id


async def _step(run_id: str, node_id: str) -> Step:
    async with SessionLocal() as db:
        return (
            await db.execute(select(Step).where(Step.run_id == run_id, Step.node_id == node_id))
        ).scalar_one()


def test_startup_scan_marks_running_run_interrupted(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [USER_INPUT_NODE, _output_node("n_out", "n_input")],
        "edges": [{"id": "e_out", "source": "n_input", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph)
    run_id = asyncio.run(_create_unscheduled_run(app_id, {"n_input": "x"}))

    async def seed_running() -> int:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            run.status = "running"
            step = (
                await db.execute(select(Step).where(Step.run_id == run_id, Step.node_id == "n_input"))
            ).scalar_one()
            step.status = "running"
            await db.commit()
            return await mark_active_runs_interrupted(db)

    changed = asyncio.run(seed_running())
    assert changed == 1
    body = auth_client.get(f"/api/runs/{run_id}").json()
    assert body["status"] == "interrupted"
    assert body["recovery"]["resumable"] is True
    assert body["recovery"]["resume_from_node_id"] == "n_input"
    assert body["steps"][0]["status"] == "interrupted"


def test_continue_run_skips_completed_steps(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", "[[respond:generated]]"),
            _output_node("n_out", "n_gen"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph)
    run_id = asyncio.run(_create_unscheduled_run(app_id, {"n_input": "hello"}))

    async def seed_interrupted() -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            run.status = "interrupted"
            run.resume_from_node_id = "n_gen"
            steps = (
                await db.execute(select(Step).where(Step.run_id == run_id))
            ).scalars().all()
            for step in steps:
                if step.node_id == "n_input":
                    step.status = "success"
                    step.output_json = dumps({"value": "hello"})
            await db.commit()

    asyncio.run(seed_interrupted())
    changed_graph = {
        "agent": "claude",
        "nodes": [USER_INPUT_NODE],
        "edges": [],
    }
    changed = auth_client.patch(f"/api/apps/{app_id}", json={"graph": changed_graph})
    assert changed.status_code == 200, changed.text

    snapshot = auth_client.get(f"/api/runs/{run_id}").json()
    assert [node["id"] for node in snapshot["graph"]["nodes"]] == ["n_input", "n_gen", "n_out"]

    response = auth_client.post(f"/api/runs/{run_id}/continue")
    assert response.status_code == 200, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed"})
    assert final["status"] == "success", final
    first = next(step for step in final["steps"] if step["node_id"] == "n_input")
    generated = next(step for step in final["steps"] if step["node_id"] == "n_gen")
    assert first["status"] == "success"
    assert generated["status"] == "success"
    assert "generated" in generated["output"]


def test_resume_waiting_run_without_memory_future_continues_from_db(auth_client, enable_claude_agent):
    enable_claude_agent()
    ask = {
        "context": {"title": "确认恢复选择", "summary": "继续恢复运行前需要确认之前等待的问题。"},
        "groups": [
            {
                "id": "choice",
                "label": "Pick",
                "type": "single",
                "options": [
                    {"label": "A", "description": "选择 A 会按该方向继续。", "recommended": True},
                    {"label": "B", "description": "选择 B 会按该方向继续。", "recommended": False},
                    {"label": "C", "description": "选择 C 会按该方向继续。", "recommended": False},
                    {"label": "以上都不是", "description": "我会补充其它答案或否定这些选项。", "recommended": False},
                ],
            }
        ],
        "tool_use_id": "toolu_persisted",
    }
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", f"[[respond:resumed]] [[ask_user:{dumps({'context': ask['context'], 'groups': ask['groups']})}]]"),
            _output_node("n_out", "n_gen"),
        ],
        "edges": [{"id": "e_out", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph)
    run_id = asyncio.run(_create_unscheduled_run(app_id))

    async def seed_waiting() -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            assert run is not None
            run.status = "waiting_for_user"
            step = (
                await db.execute(select(Step).where(Step.run_id == run_id, Step.node_id == "n_gen"))
            ).scalar_one()
            step.status = "waiting_for_user"
            step.input_json = dumps({"prompt": "waiting", "ask_user": ask})
            await db.commit()

    asyncio.run(seed_waiting())
    body = auth_client.get(f"/api/runs/{run_id}").json()
    assert body["recovery"]["waiting_request"]["tool_use_id"] == "toolu_persisted"

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": "toolu_persisted",
            "answers": [{"group_id": "choice", "selected": ["A"]}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed"})
    assert final["status"] == "success", final
    step = next(item for item in final["steps"] if item["node_id"] == "n_gen")
    assert step["status"] == "success"
    assert step["input"]["resume"]["answers"] == [{"group_id": "choice", "selected": ["A"]}]
    assert "resumed" in step["output"]
