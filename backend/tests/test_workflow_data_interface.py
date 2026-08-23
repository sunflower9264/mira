from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus
from app.runtime.factory import set_runtime_override
from app.services.admin import ADMIN_USER_ID
from app.services.runtime_paths import run_workspace
from app.utils import now_utc


class WorkflowInterfaceRuntime:
    def __init__(self, *, merge_mutation: str | None = None) -> None:
        self.calls: dict[str, dict[str, object]] = {}
        self.merge_mutation = merge_mutation

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if fork_session and session_id:
            session_id = f"{session_id}-fork"
        if "你是 Mira RunAgent 的 fan-in 合并协调 Agent" in prompt:
            text = _merge_workspace(cwd)
            if self.merge_mutation == "tamper_base":
                (cwd / "hidden.txt").write_text("tampered", encoding="utf-8")
            elif self.merge_mutation == "add_unmanifested":
                (cwd / "surprise.txt").write_text("surprise", encoding="utf-8")
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "session-join",
                total_text=text,
                finished_with="done",
            )
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
        elif "PARALLEL_UNRELATED" in prompt:
            (cwd / "unrelated.txt").write_text("unrelated", encoding="utf-8")
            text = "UNRELATED_OK"
            key = "unrelated"
        else:
            hidden_visible = (cwd / "hidden.txt").is_file()
            declared_visible = (cwd / "declared.txt").is_file()
            text = json.dumps(
                {"html": f"<p>hidden={str(hidden_visible).lower()},declared={str(declared_visible).lower()}</p>"},
                ensure_ascii=False,
            )
            key = "output"

        result_session_id = session_id or f"session-{key}"
        self.calls[key] = {
            "cwd": cwd,
            "prompt": prompt,
            "session_id": session_id,
            "result_session_id": result_session_id,
            "files": sorted(path.name for path in cwd.iterdir()),
        }
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=result_session_id,
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


def test_linear_nodes_share_workspace_and_fanout_is_isolated_until_join(auth_client, configure_codex):
    configure_codex()
    runtime = WorkflowInterfaceRuntime()
    set_runtime_override(runtime)
    try:
        created = auth_client.post("/api/apps", json={"name": "Workflow Interface"})
        assert created.status_code == 200, created.text
        app_id = created.json()["id"]
        graph = {
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
                },
                {
                    "id": "unrelated",
                    "type": "generate",
                    "position": {"x": 300, "y": 200},
                    "title": "Parallel unrelated to relay",
                    "prompt": "PARALLEL_UNRELATED",
                    "ask_user_enabled": False,
                },
            ],
            "execution_edges": [
                {"id": "producer-relay", "source": "producer", "target": "relay"},
                {"id": "relay-output", "source": "relay", "target": "output"},
                {"id": "unrelated-output", "source": "unrelated", "target": "output"},
            ],
        }
        saved = auth_client.patch(f"/api/apps/{app_id}", json={"graph": graph})
        assert saved.status_code == 200, saved.text

        started = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert started.status_code == 200, started.text
        final = _wait_for_terminal(auth_client, started.json()["run_id"])

        assert final["status"] == "success", final
        assert runtime.calls["producer"]["cwd"] == runtime.calls["relay"]["cwd"]
        assert runtime.calls["relay"]["cwd"] != runtime.calls["output"]["cwd"]
        assert runtime.calls["producer"]["result_session_id"] == runtime.calls["relay"]["session_id"]
        assert "/mnt/results" not in str(runtime.calls["relay"]["prompt"])
        assert "hidden.txt" in runtime.calls["relay"]["files"]
        assert "unrelated.txt" not in runtime.calls["relay"]["files"]
        output = next(step for step in final["steps"] if step["node_id"] == "output")
        assert output["output"] == "<p>hidden=true,declared=true</p>"
    finally:
        set_runtime_override(None)


@pytest.mark.parametrize("merge_mutation", ["tamper_base", "add_unmanifested"])
def test_fan_in_rejects_unmanifested_workspace_changes(
    auth_client,
    configure_codex,
    merge_mutation,
):
    configure_codex()
    runtime = WorkflowInterfaceRuntime(merge_mutation=merge_mutation)
    set_runtime_override(runtime)
    try:
        created = auth_client.post("/api/apps", json={"name": "Protected merge base"})
        app_id = created.json()["id"]
        graph = {
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
                    "position": {"x": 300, "y": -100},
                    "title": "Relay",
                    "prompt": "RELAY_DECLARED",
                    "ask_user_enabled": False,
                },
                {
                    "id": "unrelated",
                    "type": "generate",
                    "position": {"x": 300, "y": 100},
                    "title": "Parallel",
                    "prompt": "PARALLEL_UNRELATED",
                    "ask_user_enabled": False,
                },
                {
                    "id": "output",
                    "type": "output",
                    "position": {"x": 600, "y": 0},
                    "title": "Output",
                    "prompt": "VERIFY_NO_HIDDEN",
                },
            ],
            "execution_edges": [
                {"id": "producer-relay", "source": "producer", "target": "relay"},
                {"id": "producer-unrelated", "source": "producer", "target": "unrelated"},
                {"id": "relay-output", "source": "relay", "target": "output"},
                {"id": "unrelated-output", "source": "unrelated", "target": "output"},
            ],
        }
        saved = auth_client.patch(f"/api/apps/{app_id}", json={"graph": graph})
        assert saved.status_code == 200, saved.text

        started = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert started.status_code == 200, started.text
        final = _wait_for_terminal(auth_client, started.json()["run_id"])

        assert final["status"] == "failed", final
        output = next(step for step in final["steps"] if step["node_id"] == "output")
        assert output["failure_kind"] == "internal"
        assert "未声明" in output["error"]
    finally:
        set_runtime_override(None)


def test_checkpoint_rerun_restores_workspace_and_reuses_declared_artifact_manifest(auth_client, configure_codex):
    configure_codex()
    runtime = WorkflowInterfaceRuntime()
    set_runtime_override(runtime)
    try:
        created = auth_client.post("/api/apps", json={"name": "Workflow Rerun Interface"})
        app_id = created.json()["id"]
        graph = {
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
                },
            ],
            "execution_edges": [
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
        assert "/mnt/results" not in str(runtime.calls["relay"]["prompt"])
        assert (runtime.calls["relay"]["cwd"] / "declared.txt").read_text(encoding="utf-8") == "declared"
        assert (runtime.calls["relay"]["cwd"] / "hidden.txt").read_text(encoding="utf-8") == "hidden"

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
        assert "/mnt/results" not in str(runtime.calls["relay"]["prompt"])
        assert (runtime.calls["relay"]["cwd"] / "declared.txt").read_text(encoding="utf-8") == "declared"
        assert (runtime.calls["relay"]["cwd"] / "hidden.txt").read_text(encoding="utf-8") == "hidden"

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


def _merge_workspace(cwd: Path) -> str:
    merge_root = cwd / ".mira" / "merge"
    manifests = json.loads((merge_root / "manifest.json").read_text(encoding="utf-8"))
    sources_by_path: dict[str, list[str]] = {}
    final_by_path: dict[str, dict] = {}
    for branch_id in sorted(manifests):
        for change in manifests[branch_id]:
            path = change["path"]
            sources_by_path.setdefault(path, []).append(branch_id)
            final_by_path[path] = {**change, "branch_id": branch_id}
    receipt: list[dict] = []
    for path in sorted(final_by_path):
        change = final_by_path[path]
        target = cwd / path
        if change["kind"] == "deleted":
            target.unlink(missing_ok=True)
            digest = None
            deleted = True
        else:
            source = merge_root / "branches" / change["branch_id"] / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            deleted = False
        receipt.append(
            {
                "path": path,
                "sources": sources_by_path[path],
                "result_sha256": digest,
                "deleted": deleted,
            }
        )
    return json.dumps({"paths": receipt}, ensure_ascii=False)
