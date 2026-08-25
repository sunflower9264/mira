from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.services.runtime_paths import run_workspace, runtime_dir
from app.services.runtime_paths import scoped_codex_home
from app.services.workspace_gc import cleanup_orphan_run_homes, cleanup_orphan_run_workspaces


def test_workspace_gc_removes_only_orphan_run_directories(auth_client, configure_codex):
    configure_codex()
    created = auth_client.post("/api/apps", json={"name": "Workspace GC"}).json()
    graph = {
        "nodes": [
            {
                "id": "input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "Input", "kind": "text"},
            },
            {
                "id": "output",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render",
            },
        ],
        "execution_edges": [{"id": "edge", "source": "input", "target": "output"}],
    }
    saved = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert saved.status_code == 200, saved.text
    run = auth_client.post("/api/runs", json={"app_id": created["id"], "inputs": {}}).json()
    live = run_workspace("user_admin", created["id"], run["run_id"])
    (live / "live.txt").write_text("live", encoding="utf-8")

    root = runtime_dir() / "workspaces" / "user_admin"
    orphan = root / created["id"] / "run_orphan"
    orphan.mkdir(parents=True)
    (orphan / "old.txt").write_text("old", encoding="utf-8")
    nlcompile = root / "_nlcompile"
    nlcompile.mkdir(parents=True)
    (nlcompile / "keep.txt").write_text("keep", encoding="utf-8")

    async def cleanup() -> int:
        async with SessionLocal() as db:
            return await cleanup_orphan_run_workspaces(db)

    assert asyncio.run(cleanup()) == 1
    assert live.exists()
    assert not orphan.exists()
    assert (nlcompile / "keep.txt").exists()


def test_workspace_gc_removes_only_marked_orphan_run_homes(auth_client, configure_codex):
    configure_codex()
    live = auth_client.post("/api/apps", json={"name": "HOME GC"}).json()
    graph = {
        "nodes": [
            {
                "id": "input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "Input", "kind": "text"},
            },
            {
                "id": "output",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render",
            },
        ],
        "execution_edges": [{"id": "edge", "source": "input", "target": "output"}],
    }
    auth_client.patch(f"/api/apps/{live['id']}", json={"graph": graph})
    run = auth_client.post("/api/runs", json={"app_id": live["id"], "inputs": {}}).json()
    live_home = scoped_codex_home(runtime_dir(), session_scope=f"run:{run['run_id']}")
    orphan = scoped_codex_home(runtime_dir(), session_scope="run:run_orphan")
    unmarked = runtime_dir() / "homes" / "_scoped" / "unmarked" / "codex_home"
    unmarked.mkdir(parents=True)

    async def cleanup() -> int:
        async with SessionLocal() as db:
            return await cleanup_orphan_run_homes(db)

    assert asyncio.run(cleanup()) == 1
    assert live_home.exists()
    assert not orphan.exists()
    assert unmarked.exists()
