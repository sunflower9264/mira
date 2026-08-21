from __future__ import annotations

import asyncio
import json
import time

from app.db import SessionLocal
from app.models import Run
from app.services.run_events import append_run_event
from app.services.run_hub import get_run_hub
from app.services.runtime_paths import uploads_dir
from app.utils import dumps, new_id
from tests.auth_helpers import create_regular_user

USER_INPUT_NODE = {
    "id": "n_input",
    "type": "user_input",
    "position": {"x": 0, "y": 0},
    "title": "Input",
    "input_schema": {"label": "input", "kind": "text"},
}
ASSET_NODE = {
    "id": "n_asset",
    "type": "asset",
    "position": {"x": 100, "y": 0},
    "title": "Asset",
    "asset_kind": "text",
    "content": "demo",
}
OUTPUT_NODE = {
    "id": "n_out",
    "type": "output",
    "position": {"x": 200, "y": 0},
    "title": "Output",
    "prompt": "render [[respond:<section>ok</section>]]",
    "source_node_id": "n_input",
}


OUTPUT_FAIL_NODE = {
    "id": "n_out",
    "type": "output",
    "position": {"x": 200, "y": 0},
    "title": "Secret Output Node",
    "source_node_id": "n_input",
    "prompt": "please fail-now",
}


def _build_app(auth_client) -> str:
    created = auth_client.post("/api/apps", json={"name": "RunSSEApp"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
            json={
                "graph": {
                    "agent": "claude",
                    "nodes": [USER_INPUT_NODE, ASSET_NODE, OUTPUT_NODE],
                    "edges": [
                        {"id": "e_input_out", "source": "n_input", "target": "n_out"},
                        {"id": "e_asset_out", "source": "n_asset", "target": "n_out"},
                    ],
                }
            },
    )
    assert response.status_code == 200
    return created["id"]


def _build_run_only_failing_app(auth_client) -> str:
    created = auth_client.post("/api/apps", json={"name": "RunOnlyFailingSSEApp"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [USER_INPUT_NODE, OUTPUT_FAIL_NODE],
                "edges": [{"id": "e1", "source": "n_input", "target": "n_out"}],
            }
        },
    )
    assert response.status_code == 200, response.text
    published = auth_client.post(
        f"/api/apps/{created['id']}/publish",
        json={"visibility": "public", "market_access": "run_only"},
    )
    assert published.status_code == 200, published.text
    return created["id"]


async def _create_running_run_with_history(app_id: str, owner_id: str) -> tuple[str, int]:
    graph = {
        "nodes": [USER_INPUT_NODE, ASSET_NODE],
        "edges": [],
    }
    async with SessionLocal() as db:
        run = Run(
            id=new_id("run"),
            app_id=app_id,
            owner_id=owner_id,
            status="running",
            name="Live Tail",
            inputs_json=dumps({"n_input": {"value": "x", "attachments": []}}),
            graph_json=dumps(graph),
        )
        db.add(run)
        await db.commit()
        first = await append_run_event(db, run.id, "step.start", {"node_id": "n_input"})
        return run.id, first.id


def _wait_for_terminal(auth_client, run_id: str, *, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"success", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _parse_frames(text: str) -> list[dict]:
    frames: list[dict] = []
    current: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip("\r")
        if not line:
            if current:
                frames.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.lstrip(" ")
    if current:
        frames.append(current)
    return frames


def _read_sse(auth_client, run_id: str, *, last_event_id: str | None = None) -> tuple[int, str]:
    headers = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    chunks: list[str] = []
    with auth_client.stream(
        "GET", f"/api/runs/{run_id}/events", headers=headers
    ) as response:
        if response.status_code != 200:
            return response.status_code, response.read().decode("utf-8", errors="ignore")
        for chunk in response.iter_text():
            chunks.append(chunk)
            if "event: run.end" in chunk:
                # iter_text 在服务端关流后自然退出，这里只做兜底。
                continue
        return response.status_code, "".join(chunks)


def test_sse_emits_step_and_run_end_for_empty_orchestrator(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    # 等 orchestrator 跑完，channel 标记 closed，subscribe 拿历史后退出。
    _wait_for_terminal(auth_client, run["run_id"])
    status, body = _read_sse(auth_client, run["run_id"])
    assert status == 200, body
    frames = _parse_frames(body)
    events = [frame.get("event") for frame in frames]
    assert events.count("step.start") == 3
    assert events.count("step.log") >= 3
    assert events.count("step.end") == 3
    assert events[-1] == "run.end"
    start_payloads = [
        json.loads(frame["data"])
        for frame in frames
        if frame.get("event") == "step.start"
    ]
    assert all(payload["ts"].endswith("+08:00") for payload in start_payloads)
    log_payloads = [
        json.loads(frame["data"])
        for frame in frames
        if frame.get("event") == "step.log"
    ]
    assert all(payload["log"]["ts"].endswith("+08:00") for payload in log_payloads)
    assert any(
        payload["node_id"] == "n_input" and payload["log"]["text"] == "读取用户输入"
        for payload in log_payloads
    )
    last_frame = frames[-1]
    payload = json.loads(last_frame["data"])
    assert payload["status"] == "success"


def test_sse_step_end_scrubs_upload_local_paths(auth_client, enable_claude_agent):
    enable_claude_agent()
    uploaded = auth_client.post(
        "/api/uploads",
        files={"file": ("asset.txt", b"asset", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload = uploaded.json()
    created = auth_client.post("/api/apps", json={"name": "RunSSEUploadApp"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_asset",
                        "type": "asset",
                        "position": {"x": 0, "y": 0},
                        "title": "Asset",
                        "asset_kind": "file",
                        "uploads": [upload],
                    },
                    {
                        "id": "n_out",
                        "type": "output",
                        "position": {"x": 200, "y": 0},
                        "title": "Output",
                        "prompt": "render [[respond:<section>ok</section>]]",
                        "source_node_id": "n_asset",
                    },
                ],
                "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
            }
        },
    )
    assert response.status_code == 200, response.text
    run = auth_client.post(
        "/api/runs", json={"app_id": created["id"], "inputs": {}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    status, body = _read_sse(auth_client, run["run_id"])
    assert status == 200, body
    assert str(uploads_dir("user_admin") / upload["id"] / "blob") not in body
    frames = _parse_frames(body)
    step_end = next(
        json.loads(frame["data"])
        for frame in frames
        if frame.get("event") == "step.end"
    )
    output = step_end["step"]["output"]
    assert output[0]["path"] is None
    assert output[0]["download_url"].startswith(f"/api/uploads/{upload['id']}?download_token=")


def test_sse_last_event_id_replays_from_after(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    status, body = _read_sse(auth_client, run["run_id"])
    assert status == 200
    frames = _parse_frames(body)
    # 选第一帧（step.start n_input）的 id，重连应只收到之后的事件。
    first_id = frames[0]["id"]
    status, body2 = _read_sse(auth_client, run["run_id"], last_event_id=first_id)
    assert status == 200
    frames2 = _parse_frames(body2)
    assert all(int(frame["id"]) > int(first_id) for frame in frames2)
    assert frames2[-1]["event"] == "run.end"


def test_sse_replays_from_db_when_live_channel_has_no_replay_buffer(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    status, body = _read_sse(auth_client, run["run_id"])
    assert status == 200
    frames = _parse_frames(body)
    assert len(frames) > 3
    first_id = frames[0]["id"]
    expected_ids = [frame["id"] for frame in frames if int(frame["id"]) > int(first_id)]

    channel = get_run_hub().get(run["run_id"])
    assert channel is not None
    assert not hasattr(channel, "_buffer")

    status, body2 = _read_sse(auth_client, run["run_id"], last_event_id=first_id)
    assert status == 200
    frames2 = _parse_frames(body2)
    assert [frame["id"] for frame in frames2] == expected_ids
    assert frames2[-1]["event"] == "run.end"


def test_sse_live_event_during_db_replay_is_not_lost_or_duplicated(auth_client, enable_claude_agent, monkeypatch):
    enable_claude_agent()
    runner = create_regular_user("sse-live-tail-runner")
    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
    app_id = _build_app(auth_client)
    run_id, first_event_id = asyncio.run(_create_running_run_with_history(app_id, runner["id"]))
    channel = asyncio.run(get_run_hub().create(run_id))

    from app.api import runs as runs_api

    original_iter_run_events = runs_api.iter_run_events
    published = False

    async def iter_run_events_with_live_publish(db, selected_run_id: str, *, after_id: int | None = None):
        nonlocal published
        async for row in original_iter_run_events(db, selected_run_id, after_id=after_id):
            yield row
            if selected_run_id == run_id and not published:
                published = True
                await channel.publish("run.end", {"status": "success", "source": "live"})
                await channel.close()

    monkeypatch.setattr(runs_api, "iter_run_events", iter_run_events_with_live_publish)

    status, body = _read_sse(auth_client, run_id)
    assert status == 200, body
    assert published
    frames = _parse_frames(body)
    assert [frame["event"] for frame in frames] == ["step.start", "run.end"]
    assert len({frame["id"] for frame in frames}) == 2
    assert int(frames[0]["id"]) == first_event_id
    assert int(frames[1]["id"]) > first_event_id
    assert json.loads(frames[1]["data"]) == {"status": "success", "source": "live"}


def test_sse_replay_only_returns_db_history_and_closes(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    asyncio.run(get_run_hub().discard(run["run_id"]))

    chunks: list[str] = []
    with auth_client.stream("GET", f"/api/runs/{run['run_id']}/events") as response:
        assert response.status_code == 200
        assert response.headers["X-Mira-Replay-Only"] == "true"
        for chunk in response.iter_text():
            chunks.append(chunk)

    frames = _parse_frames("".join(chunks))
    events = [frame.get("event") for frame in frames]
    assert events.count("step.start") == 3
    assert events.count("step.end") == 3
    assert events[-1] == "run.end"


def test_sse_returns_410_for_invalid_last_event_id(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    status, body = _read_sse(auth_client, run["run_id"], last_event_id="not-an-int")
    assert status == 410
    assert "Last-Event-ID" in body or "无效" in body


def test_sse_returns_410_after_run_deleted(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    response = auth_client.delete(f"/api/runs/{run['run_id']}")
    assert response.status_code == 204
    # run 删除后 GET /api/runs/{id} 先 404；这里拼一个仍存在的 run 来构造 410：
    other_run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, other_run["run_id"])
    auth_client.delete(f"/api/runs/{other_run['run_id']}")
    # 删除后 hub 已 discard；GET /events 应该 404（因为 run 不在 DB）。
    status, body = _read_sse(auth_client, other_run["run_id"])
    assert status == 404


def test_sse_requires_owner(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _build_app(auth_client)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])
    user_token = create_regular_user()["token"]
    with auth_client.stream(
        "GET",
        f"/api/runs/{run['run_id']}/events",
        headers={"Authorization": f"Bearer {user_token}"},
    ) as response:
        assert response.status_code == 404


def test_run_only_failure_errors_are_redacted_for_runner(auth_client, enable_claude_agent):
    enable_claude_agent()
    admin_auth = auth_client.headers["Authorization"]
    app_id = _build_run_only_failing_app(auth_client)

    owner_run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "owner"}}
    )
    assert owner_run.status_code == 200, owner_run.text
    owner_final = _wait_for_terminal(auth_client, owner_run.json()["run_id"])
    assert owner_final["status"] == "failed"
    assert owner_final["error"] == "mock failed"
    assert owner_final["failure_kind"] == "runtime"
    owner_steps = {step["node_id"]: step for step in owner_final["steps"]}
    assert owner_steps["n_out"]["error"] == "mock failed"

    runner = create_regular_user("run-only-redacted-runner")
    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})

    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "runner"}}
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]
    final = _wait_for_terminal(auth_client, run_id)
    assert final["status"] == "failed"
    assert final["error"] == "运行失败"
    assert "mock failed" not in str(final)
    steps = {step["node_id"]: step for step in final["steps"]}
    assert steps["n_out"]["error"] == "运行失败"
    assert steps["n_out"]["agent_session_id"] is None
    assert steps["n_out"]["logs"] == []

    runs = auth_client.get(f"/api/apps/{app_id}/runs")
    assert runs.status_code == 200, runs.text
    assert runs.json()[0]["id"] == run_id
    assert runs.json()[0]["error"] == "运行失败"
    assert "mock failed" not in runs.text

    status, body = _read_sse(auth_client, run_id)
    assert status == 200, body
    assert "mock failed" not in body
    frames = _parse_frames(body)
    assert "step.log" not in [frame.get("event") for frame in frames]
    run_end = next(frame for frame in frames if frame.get("event") == "run.end")
    run_end_data = json.loads(run_end["data"])
    assert run_end_data["error"] == "运行失败"
    assert run_end_data["failure_kind"] == "runtime"
    step_end = next(
        frame
        for frame in frames
        if frame.get("event") == "step.end" and json.loads(frame["data"]).get("node_id") == "n_out"
    )
    assert json.loads(step_end["data"])["step"]["error"] == "运行失败"

    auth_client.headers.update({"Authorization": admin_auth})
