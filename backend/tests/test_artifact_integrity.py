from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Run, Step
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus
from app.runtime.factory import set_runtime_override
from app.services import run_orchestrator
from app.services.artifacts import (
    signed_run_artifact_download_url,
    verify_run_artifact_download_token,
)
from app.services.output_contracts import validate_contract_output
from app.services.run_hub import get_run_hub
from app.services.runtime_paths import run_workspace
from app.utils import now_utc
from tests.runtime_mock import MockRuntime


DECLARED_CONTENT = b"declared artifact"
DECLARED_SHA256 = "4a461ec8a71c461e3fd44012ae36d9dd7401327b37f02e95a60ea0194970808b"


class ArtifactCatalogRuntime:
    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="artifact-catalog",
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
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景信息完整。"}'
        elif "你正在生成 Mira output 节点" in prompt:
            text = '{"html":"<section>OK</section>"}'
        else:
            declared = cwd / "declared.txt"
            declared.write_bytes(DECLARED_CONTENT)
            (cwd / "undeclared.tmp").write_text("workspace scratch", encoding="utf-8")
            text = json.dumps(
                {"artifacts": [{"path": str(declared), "name": "Declared"}]},
                ensure_ascii=False,
            )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "artifact_catalog_session",
            total_text=text,
            finished_with="done",
        )


class FinalizeMutatingArtifactRuntime(ArtifactCatalogRuntime):
    def __init__(self, mutation: str):
        self.mutation = mutation

    async def execute(self, **kwargs) -> AgentExecutionResult:
        prompt = kwargs["prompt"]
        cwd = kwargs["cwd"]
        if "你正在生成 Mira output 节点" in prompt:
            declared = next((cwd.parents[3] / "artifacts" / "n_gen").glob("*/declared.txt"))
            declared.chmod(0o644)
            if self.mutation == "delete":
                declared.unlink()
            elif self.mutation == "modify":
                declared.write_bytes(b"modified after manifest")
        return await super().execute(**kwargs)


def _artifact_graph() -> dict:
    return {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Artifact",
                "prompt": "生成声明文件",
                "output_contract": {"type": "artifact", "artifact_kind": "file"},
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "展示结果",
            },
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }


def _build_app(auth_client) -> str:
    created = auth_client.post("/api/apps", json={"name": "ArtifactIntegrity"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _artifact_graph()})
    assert response.status_code == 200, response.text
    return created["id"]


def _wait_for_terminal(auth_client, run_id: str, *, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        last = auth_client.get(f"/api/runs/{run_id}").json()
        if last["status"] in {"success", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run did not finish: {last}")


def _run_artifact_app(
    auth_client,
    enable_claude_agent,
    *,
    runtime: ArtifactCatalogRuntime | None = None,
    expected_status: str = "success",
) -> tuple[str, str, dict]:
    enable_claude_agent()
    app_id = _build_app(auth_client)
    set_runtime_override(runtime or ArtifactCatalogRuntime())
    try:
        created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        final = _wait_for_terminal(auth_client, run_id)
    finally:
        set_runtime_override(MockRuntime())
    assert final["status"] == expected_status, final
    return app_id, run_id, final


def test_artifact_step_persists_versioned_relative_manifest(auth_client, enable_claude_agent):
    _app_id, run_id, final = _run_artifact_app(auth_client, enable_claude_agent)

    output = next(step["output"] for step in final["steps"] if step["node_id"] == "n_gen")
    assert len(output) == 1
    artifact = output[0]
    assert artifact["path"].startswith("artifacts/n_gen/artifact_")
    assert artifact["path"].endswith("/declared.txt")
    assert artifact["name"] == "Declared"
    assert artifact["size"] == len(DECLARED_CONTENT)
    assert artifact["sha256"] == DECLARED_SHA256
    assert artifact["artifact_kind"] == "file"
    assert artifact["manifest_version"] == 1
    assert artifact["artifact_id"].startswith("artifact_")
    assert artifact["holder"]["run_id"] == run_id
    assert artifact["holder"]["node_id"] == "n_gen"
    assert artifact["holder"]["step_id"].startswith("step_")
    assert artifact["origin"] == {
        **artifact["holder"],
        "artifact_id": artifact["artifact_id"],
        "node_title": "Artifact",
    }
    assert artifact["reused_from"] is None
    assert artifact["output_port"] == "artifacts"


def test_files_list_excludes_undeclared_workspace_files(auth_client, enable_claude_agent):
    _app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)

    response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert response.status_code == 200, response.text
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 1
    assert "path" not in artifacts[0]
    assert "source_kind" not in artifacts[0]


def test_trace_uses_declared_artifact_catalog(auth_client, enable_claude_agent):
    _app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)

    response = auth_client.get(f"/api/runs/{run_id}/steps/n_gen/trace")
    assert response.status_code == 200, response.text
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 1
    assert "path" not in artifacts[0]
    assert artifacts[0]["name"] == "Declared"
    assert artifacts[0]["size"] == len(DECLARED_CONTENT)
    assert artifacts[0]["sha256"] == DECLARED_SHA256
    assert artifacts[0]["integrity"] == "verified"
    assert artifacts[0]["download_url"].startswith(f"/api/runs/{run_id}/artifacts/artifacts/n_gen/")


def test_failed_artifact_contract_step_is_not_listed(auth_client, enable_claude_agent):
    _app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)

    async def mark_step_failed() -> None:
        async with SessionLocal() as db:
            step = (
                await db.execute(select(Step).where(Step.run_id == run_id, Step.node_id == "n_gen"))
            ).scalar_one()
            step.status = "failed"
            step.error = "forced failure"
            await db.commit()

    asyncio.run(mark_step_failed())

    response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert response.status_code == 200, response.text
    assert response.json()["artifacts"] == []


def test_undeclared_workspace_file_cannot_be_downloaded(auth_client, enable_claude_agent):
    _app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)

    response = auth_client.get(f"/api/runs/{run_id}/artifacts/undeclared.tmp")
    assert response.status_code == 404


def test_modified_manifest_artifact_cannot_be_downloaded(auth_client, enable_claude_agent):
    app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)
    artifact = auth_client.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"][0]
    relative_path = urlparse(artifact["download_url"]).path.removeprefix(
        f"/api/runs/{run_id}/artifacts/"
    )
    path = run_workspace("user_admin", app_id, run_id) / relative_path
    path.chmod(0o644)
    path.write_bytes(b"modified")
    refreshed = auth_client.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"][0]
    assert refreshed["sha256"] == DECLARED_SHA256
    assert refreshed["integrity"] == "modified"

    headers = dict(auth_client.headers)
    auth_client.headers.pop("Authorization", None)
    try:
        response = auth_client.get(artifact["download_url"])
    finally:
        auth_client.headers.update(headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "文件完整性校验失败"


def test_run_fails_when_declared_artifact_is_missing_before_success(auth_client, enable_claude_agent):
    _app_id, _run_id, final = _run_artifact_app(
        auth_client,
        enable_claude_agent,
        runtime=FinalizeMutatingArtifactRuntime("delete"),
        expected_status="failed",
    )

    assert "artifact 完整性校验失败" in final["error"]
    assert final["failure_kind"] == "integrity"
    assert "文件缺失" in final["error"]
    assert "declared.txt" in final["error"]


def test_run_fails_when_declared_artifact_is_modified_before_success(auth_client, enable_claude_agent):
    _app_id, _run_id, final = _run_artifact_app(
        auth_client,
        enable_claude_agent,
        runtime=FinalizeMutatingArtifactRuntime("modify"),
        expected_status="failed",
    )

    assert "artifact 完整性校验失败" in final["error"]
    assert final["failure_kind"] == "integrity"
    assert "已被修改" in final["error"]
    assert "declared.txt" in final["error"]


def test_cancel_during_final_artifact_validation_stays_cancelled(
    auth_client,
    enable_claude_agent,
    monkeypatch,
):
    entered_validation = threading.Event()
    release_validation = threading.Event()
    original_validate = run_orchestrator.validate_run_artifact_integrity

    async def pause_final_validation(db, run):  # noqa: ANN001, ANN202
        entered_validation.set()
        released = await asyncio.to_thread(release_validation.wait, 5)
        assert released, "test did not release final artifact validation"
        return await original_validate(db, run)

    monkeypatch.setattr(run_orchestrator, "validate_run_artifact_integrity", pause_final_validation)
    enable_claude_agent()
    app_id = _build_app(auth_client)
    set_runtime_override(ArtifactCatalogRuntime())
    try:
        created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        channel = get_run_hub().get(run_id)
        assert channel is not None
        assert entered_validation.wait(5), "run did not enter final artifact validation"

        cancelled = auth_client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 204, cancelled.text
    finally:
        release_validation.set()
        set_runtime_override(MockRuntime())

    deadline = time.time() + 5
    while not channel.closed and time.time() < deadline:
        time.sleep(0.01)
    assert channel.closed, "orchestrator did not finish after validation was released"
    final = auth_client.get(f"/api/runs/{run_id}").json()
    assert final["status"] == "cancelled"


def test_versioned_artifact_rejects_legacy_download_token(auth_client, enable_claude_agent):
    app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)
    artifact = auth_client.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"][0]
    run = Run(id=run_id, app_id=app_id, owner_id="user_admin")
    relative_path = urlparse(artifact["download_url"]).path.removeprefix(
        f"/api/runs/{run_id}/artifacts/"
    )
    legacy_url = signed_run_artifact_download_url(run, relative_path)

    headers = dict(auth_client.headers)
    auth_client.headers.pop("Authorization", None)
    try:
        response = auth_client.get(legacy_url)
    finally:
        auth_client.headers.update(headers)

    assert response.status_code == 401
    assert response.json()["detail"] == "下载链接已失效"


def test_hash_bound_token_requires_matching_sha256(auth_client, enable_claude_agent):
    app_id, run_id, _final = _run_artifact_app(auth_client, enable_claude_agent)
    artifact = auth_client.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"][0]
    run = Run(id=run_id, app_id=app_id, owner_id="user_admin")
    relative_path = urlparse(artifact["download_url"]).path.removeprefix(
        f"/api/runs/{run_id}/artifacts/"
    )
    url = signed_run_artifact_download_url(run, relative_path, "0" * 64)
    token = parse_qs(urlparse(url).query)["download_token"][0]

    with pytest.raises(HTTPException) as exc_info:
        verify_run_artifact_download_token(
            run_id,
            relative_path,
            token,
            sha256=DECLARED_SHA256,
        )

    assert exc_info.value.status_code == 401


def test_artifact_remains_in_shared_workspace_without_ancestor_results_view(auth_client, enable_claude_agent):
    app_id, run_id, final = _run_artifact_app(auth_client, enable_claude_agent)
    output_step = next(step for step in final["steps"] if step["node_id"] == "n_out")
    prompt = output_step["input"]["prompt"]
    assert DECLARED_SHA256 not in prompt
    assert "/mnt/results" not in prompt

    root = run_workspace("user_admin", app_id, run_id) / "agent" / "branches"
    workspaces = list(root.glob("*/work"))
    assert len(workspaces) == 1
    assert (workspaces[0] / "declared.txt").read_bytes() == DECLARED_CONTENT
    assert (workspaces[0] / "undeclared.tmp").read_text(encoding="utf-8") == "workspace scratch"


def test_artifact_validator_enforces_max_count(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    node = {
        "id": "n_gen",
        "type": "generate",
        "output_contract": {"type": "artifact", "artifact_kind": "file", "max_count": 1},
    }
    text = json.dumps(
        {"artifacts": [{"path": str(first), "name": "First"}, {"path": str(second), "name": "Second"}]}
    )

    result = validate_contract_output(node, text, workspace=tmp_path)

    assert result.ok is False
    assert "max_count" in (result.error or "")


def test_artifact_validator_rejects_duplicate_resolved_paths(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("report", encoding="utf-8")
    node = {
        "id": "n_gen",
        "type": "generate",
        "output_contract": {"type": "artifact", "artifact_kind": "file"},
    }
    text = json.dumps(
        {
            "artifacts": [
                {"path": "report.txt", "name": "First"},
                {"path": "./report.txt", "name": "Duplicate"},
            ]
        }
    )

    result = validate_contract_output(node, text, workspace=tmp_path)

    assert result.ok is False
    assert "重复" in (result.error or "")


def test_artifact_validator_rejects_upload_staging_directory(tmp_path):
    staged = tmp_path / ".inputs" / "upl_test" / "blob"
    staged.parent.mkdir(parents=True)
    staged.write_text("uploaded source", encoding="utf-8")
    node = {
        "id": "n_gen",
        "type": "generate",
        "output_contract": {"type": "artifact", "artifact_kind": "file"},
    }
    text = json.dumps({"artifacts": [{"path": str(staged), "name": "Upload"}]})

    result = validate_contract_output(node, text, workspace=tmp_path)

    assert result.ok is False
    assert "上传暂存目录" in (result.error or "")
