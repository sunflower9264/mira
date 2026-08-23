from __future__ import annotations

import asyncio
import json
import tomllib

import app.api.settings as settings_api
from app.db import SessionLocal
from app.models import CodexConfig
from app.runtime.base import AgentExecutionResult, AgentRuntimeStatus
from app.runtime.factory import set_runtime_override
from app.services.runtime_paths import codex_home
from app.utils import now_utc
from tests.runtime_mock import MockRuntime


def _codex_ciphertexts() -> tuple[str, str]:
    async def _read() -> tuple[str, str]:
        async with SessionLocal() as db:
            row = await db.get(CodexConfig, "codex")
            assert row is not None
            return row.config_ciphertext, row.auth_ciphertext

    return asyncio.run(_read())


class StatusProbeRuntime:
    def __init__(
        self,
        *,
        status: AgentRuntimeStatus | None = None,
        result: AgentExecutionResult | None = None,
        delay: float = 0,
    ):
        self.status = status or AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="probe",
            method="test",
            checked_at=now_utc(),
        )
        self.result = result or AgentExecutionResult(finished_with="done", total_text="OK")
        self.delay = delay
        self.execute_calls = 0

    async def detect_status(self) -> AgentRuntimeStatus:
        return self.status

    async def execute(self, **_kwargs) -> AgentExecutionResult:
        self.execute_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _save_codex(auth_client, *, model: str = "gpt-5.2", api_key: str = "sk-test"):
    return auth_client.put(
        "/api/settings/codex/config",
        json={
            "content": f'model = "{model}"\n',
            "auth_content": json.dumps({"OPENAI_API_KEY": api_key}),
            "supported_models": [model, model, " "],
        },
    )


def test_codex_config_roundtrip_encrypts_both_files_and_models(auth_client):
    initial = auth_client.get("/api/settings/codex/config")
    assert initial.status_code == 200
    assert initial.json()["content"] == ""
    assert initial.json()["auth"]["content"] == ""

    saved = _save_codex(auth_client)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert tomllib.loads(body["content"])["model"] == "gpt-5.2"
    assert json.loads(body["auth"]["content"]) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }
    assert body["settings"]["supported_models"] == ["gpt-5.2"]
    config_ciphertext, auth_ciphertext = _codex_ciphertexts()
    assert "gpt-5.2" not in config_ciphertext
    assert "sk-test" not in auth_ciphertext
    assert tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))["model"] == "gpt-5.2"
    assert json.loads((codex_home() / "auth.json").read_text(encoding="utf-8"))["OPENAI_API_KEY"] == "sk-test"


def test_codex_config_rejects_missing_models_and_invalid_content(auth_client):
    missing_models = auth_client.put(
        "/api/settings/codex/config",
        json={"content": "", "auth_content": "{}"},
    )
    assert missing_models.status_code == 422

    empty_models = auth_client.put(
        "/api/settings/codex/config",
        json={"content": "", "auth_content": "{}", "supported_models": [" "]},
    )
    assert empty_models.status_code == 400

    invalid_toml = auth_client.put(
        "/api/settings/codex/config",
        json={"content": "model = ", "auth_content": "{}", "supported_models": ["gpt-5.2"]},
    )
    assert invalid_toml.status_code == 400

    invalid_auth = auth_client.put(
        "/api/settings/codex/config",
        json={"content": "", "auth_content": "{bad", "supported_models": ["gpt-5.2"]},
    )
    assert invalid_auth.status_code == 400


def test_codex_config_save_is_atomic(auth_client):
    assert _save_codex(auth_client, model="gpt-5.2", api_key="sk-old").status_code == 200
    rejected = auth_client.put(
        "/api/settings/codex/config",
        json={
            "content": 'model = "gpt-5.3"\n',
            "auth_content": "{bad",
            "supported_models": ["gpt-5.3"],
        },
    )
    assert rejected.status_code == 400
    current = auth_client.get("/api/settings/codex/config").json()
    assert tomllib.loads(current["content"])["model"] == "gpt-5.2"
    assert json.loads(current["auth"]["content"])["OPENAI_API_KEY"] == "sk-old"
    assert auth_client.get("/api/settings").json()["supported_models"] == ["gpt-5.2"]


def test_codex_setup_state_requires_config_and_auth(auth_client):
    assert auth_client.get("/api/settings/codex/setup-state").json() == {"completed": False}
    assert _save_codex(auth_client).status_code == 200
    assert auth_client.get("/api/settings/codex/setup-state").json() == {"completed": True}


def test_codex_instruction_roundtrip(auth_client):
    initial = auth_client.get("/api/settings/instructions")
    assert initial.status_code == 200
    assert initial.json()["path"].endswith("AGENTS.md")

    saved = auth_client.put(
        "/api/settings/instructions",
        json={"content": "# Codex instructions\n"},
    )
    assert saved.status_code == 200
    assert saved.json()["content"] == "# Codex instructions\n"
    assert (codex_home() / "AGENTS.md").read_text(encoding="utf-8") == "# Codex instructions\n"


def test_mcp_metadata_is_not_written_into_codex_config(auth_client):
    server = {
        "id": "mcp_echo",
        "name": "echo",
        "enabled": True,
        "url": "http://localhost:9999/mcp",
        "headers": [{"name": "X-Test", "value": "1"}],
        "env_var_names": [],
    }
    saved = auth_client.post("/api/settings/mcp", json=server)
    assert saved.status_code == 200
    assert set(saved.json()["mcp_servers"][0]) == {
        "id",
        "name",
        "enabled",
        "planning_enabled",
        "url",
        "headers",
        "env_var_names",
    }
    assert "mcp_servers" not in tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))


def test_codex_control_plane_is_admin_only(user_client):
    assert user_client.get("/api/settings").status_code == 200
    assert user_client.get("/api/settings/codex/config").status_code == 403
    assert user_client.get("/api/settings/codex/setup-state").status_code == 403
    assert user_client.put(
        "/api/settings/codex/config",
        json={"content": "", "auth_content": "{}", "supported_models": ["gpt-5.2"]},
    ).status_code == 403
    assert user_client.get("/api/settings/instructions").status_code == 403
    assert user_client.put("/api/settings/instructions", json={"content": "hello"}).status_code == 403
    assert user_client.post("/api/settings/codex/status").status_code == 403


def test_codex_status_runs_smoke_probe(auth_client):
    runtime = StatusProbeRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/codex/status")
    finally:
        set_runtime_override(MockRuntime())
    assert response.status_code == 200
    assert response.json()["runnable"] is True
    assert runtime.execute_calls == 1


def test_codex_status_reports_probe_failure(auth_client):
    runtime = StatusProbeRuntime(
        result=AgentExecutionResult(finished_with="error", error="probe failed")
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/codex/status")
    finally:
        set_runtime_override(MockRuntime())
    assert response.status_code == 200
    assert response.json()["runnable"] is False
    assert response.json()["error"] == "probe failed"


def test_codex_status_reports_probe_timeout(auth_client, monkeypatch):
    runtime = StatusProbeRuntime(delay=0.1)
    monkeypatch.setattr(settings_api, "SMOKE_STATUS_TIMEOUT_SEC", 0.01)
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/codex/status")
    finally:
        set_runtime_override(MockRuntime())
    assert response.status_code == 200
    assert response.json()["runnable"] is False
    assert "超时" in response.json()["error"]


def test_codex_status_skips_probe_when_runtime_is_unavailable(auth_client):
    runtime = StatusProbeRuntime(
        status=AgentRuntimeStatus(
            installed=False,
            identity="probe",
            method="test",
            error="missing config file",
            checked_at=now_utc(),
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/codex/status")
    finally:
        set_runtime_override(MockRuntime())
    assert response.status_code == 200
    assert response.json()["installed"] is False
    assert response.json()["runnable"] is False
    assert runtime.execute_calls == 0
