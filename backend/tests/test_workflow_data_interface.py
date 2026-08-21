from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus
from app.runtime.factory import set_runtime_override
from app.services.admin import ADMIN_USER_ID
from app.services.runtime_paths import run_workspace
from app.utils import now_utc


class WorkflowInterfaceRuntime:
    def __init__(self) -> None:
        self.calls: dict[str, dict[str, object]] = {}

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="workflow-interface",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = json.dumps(
                {
                    "action": "complete",
                    "decision_summary": "测试输入已经完整。",
                    "reason": "无需补充决策。",
                },
                ensure_ascii=False,
            )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=None, total_text=text, finished_with="done")

        if "PRODUCE_DECLARED" in prompt:
            (cwd / "declared.txt").write_text("declared", encoding="utf-8")
            (cwd / "hidden.txt").write_text("hidden", encoding="utf-8")
            text = json.dumps(
                {"artifacts": [{"path": str(cwd / "declared.txt"), "name": "declared.txt"}]},
                ensure_ascii=False,
            )
            key = "producer"
        elif "RELAY_DECLARED" in prompt:
            text = "RELAY_OK"
            key = "relay"
        else:
            hidden_visible = (cwd / "hidden.txt").exists()
            declared_visible = (cwd / "declared.txt").exists()
            text = json.dumps(
                {"html": f"<p>hidden={str(hidden_visible).lower()},declared={str(declared_visible).lower()}</p>"},
                ensure_ascii=False,
            )
            key = "output"

        self.calls[key] = {"cwd": cwd, "prompt": prompt, "session_id": session_id}
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=f"session-{key}",
            total_text=text,
            finished_with="done",
        )


def _wait_for_terminal(auth_client, run_id: str) -> dict:
    deadline = time.time() + 6
    last: dict | None = None
    while time.time() < deadline:
        last = auth_client.get(f"/api/runs/{run_id}").json()
        if last["status"] in {"success", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run did not finish: {last}")


def test_graph_edge_is_the_only_cross_node_data_interface(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = WorkflowInterfaceRuntime()
    set_runtime_override(runtime)
    try:
        created = auth_client.post("/api/apps", json={"name": "Workflow Interface"})
        assert created.status_code == 200, created.text
        app_id = created.json()["id"]
        graph = {
            "agent": "claude",
            "nodes": [
                {
                    "id": "producer",
                    "type": "generate",
                    "position": {"x": 0, "y": 0},
                    "title": "Producer",
                    "prompt": "PRODUCE_DECLARED",
                    "ask_user_enabled": False,
                    "output_contract": {"type": "artifact", "artifact_kind": "file"},
                },
                {
                    "id": "relay",
                    "type": "generate",
                    "position": {"x": 300, "y": 0},
                    "title": "Relay",
                    "prompt": "RELAY_DECLARED",
                    "ask_user_enabled": False,
                },
                {
                    "id": "output",
                    "type": "output",
                    "position": {"x": 600, "y": 0},
                    "title": "Output",
                    "prompt": "VERIFY_NO_HIDDEN",
                    "source_node_id": "relay",
                },
            ],
            "edges": [
                {"id": "producer-relay", "source": "producer", "target": "relay"},
                {"id": "relay-output", "source": "relay", "target": "output"},
            ],
        }
        saved = auth_client.patch(f"/api/apps/{app_id}", json={"graph": graph})
        assert saved.status_code == 200, saved.text

        started = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert started.status_code == 200, started.text
        final = _wait_for_terminal(auth_client, started.json()["run_id"])

        assert final["status"] == "success", final
        assert runtime.calls["producer"]["cwd"] != runtime.calls["relay"]["cwd"]
        assert runtime.calls["relay"]["cwd"] != runtime.calls["output"]["cwd"]
        assert "/mnt/inputs/" in str(runtime.calls["relay"]["prompt"])
        assert runtime.calls["relay"]["session_id"] is None
        assert runtime.calls["output"]["session_id"] is None
        output = next(step for step in final["steps"] if step["node_id"] == "output")
        assert output["output"] == "<p>hidden=false,declared=false</p>"
    finally:
        set_runtime_override(None)


def test_rerun_reuses_only_declared_artifacts_through_the_same_interface(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = WorkflowInterfaceRuntime()
    set_runtime_override(runtime)
    try:
        created = auth_client.post("/api/apps", json={"name": "Workflow Rerun Interface"})
        app_id = created.json()["id"]
        graph = {
            "agent": "claude",
            "nodes": [
                {
                    "id": "producer",
                    "type": "generate",
                    "position": {"x": 0, "y": 0},
                    "title": "Producer",
                    "prompt": "PRODUCE_DECLARED",
                    "ask_user_enabled": False,
                    "output_contract": {"type": "artifact", "artifact_kind": "file"},
                },
                {
                    "id": "relay",
                    "type": "generate",
                    "position": {"x": 300, "y": 0},
                    "title": "Relay",
                    "prompt": "RELAY_DECLARED",
                    "ask_user_enabled": False,
                },
                {
                    "id": "output",
                    "type": "output",
                    "position": {"x": 600, "y": 0},
                    "title": "Output",
                    "prompt": "VERIFY_NO_HIDDEN",
                    "source_node_id": "relay",
                },
            ],
            "edges": [
                {"id": "producer-relay", "source": "producer", "target": "relay"},
                {"id": "relay-output", "source": "relay", "target": "output"},
            ],
        }
        saved = auth_client.patch(f"/api/apps/{app_id}", json={"graph": graph})
        assert saved.status_code == 200, saved.text
        started = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        source_run_id = started.json()["run_id"]
        source_final = _wait_for_terminal(auth_client, source_run_id)
        assert source_final["status"] == "success", source_final
        source_files = auth_client.get(f"/api/runs/{source_run_id}/artifacts").json()["artifacts"]
        assert len(source_files) == 1
        assert "/mnt/inputs/" in str(runtime.calls["relay"]["prompt"])

        rerun = auth_client.post(
            f"/api/runs/{source_run_id}/rerun-from",
            json={"app_id": app_id, "node_id": "relay"},
        )
        assert rerun.status_code == 200, rerun.text
        rerun_id = rerun.json()["run_id"]
        rerun_final = _wait_for_terminal(auth_client, rerun_id)
        assert rerun_final["status"] == "success", rerun_final
        rerun_files = auth_client.get(f"/api/runs/{rerun_id}/artifacts").json()["artifacts"]

        assert len(rerun_files) == 1
        assert rerun_files[0]["sha256"] == source_files[0]["sha256"]
        assert rerun_files[0]["id"] != source_files[0]["id"]
        assert rerun_files[0]["origin_run_id"] == source_run_id
        assert rerun_files[0]["origin_artifact_id"] == source_files[0]["id"]
        assert rerun_files[0]["origin_node_id"] == "producer"
        assert rerun_files[0]["reused_from_run_id"] == source_run_id
        assert rerun_files[0]["reused_from_artifact_id"] == source_files[0]["id"]
        assert "path" not in rerun_files[0]
        assert "source_kind" not in rerun_files[0]
        assert "/mnt/inputs/" in str(runtime.calls["relay"]["prompt"])

        relative_path = unquote(urlparse(source_files[0]["download_url"]).path).removeprefix(
            f"/api/runs/{source_run_id}/artifacts/"
        )
        source_path = run_workspace(ADMIN_USER_ID, app_id, source_run_id) / relative_path
        source_path.chmod(0o644)
        source_path.write_text("modified", encoding="utf-8")
        rejected = auth_client.post(
            f"/api/runs/{source_run_id}/rerun-from",
            json={"app_id": app_id, "node_id": "relay"},
        )
        assert rejected.status_code == 409
        assert "历史节点结果不可复用" in rejected.json()["detail"]
    finally:
        set_runtime_override(None)
