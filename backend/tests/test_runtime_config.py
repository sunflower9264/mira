import asyncio
import json
import tomllib
from pathlib import Path

import app.runtime.claude_runtime as claude_runtime
import app.runtime.codex_runtime as codex_runtime
import app.api.settings as settings_api
from app.db import SessionLocal
from app.models import AgentConfig
from app.runtime.base import AgentExecutionResult, AgentProviderStatus
from app.runtime.codex_runtime import CodexCliRuntime
from app.runtime.factory import set_runtime_override
from app.runtime.sandbox import DockerSandboxStatus
from app.services.agent_config import default_content
from app.services.runtime_paths import claude_home, claude_mcp_config_path, codex_home
from app.utils import now_utc
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime


def _agent_config_ciphertext(agent_id: str) -> str:
    async def _read() -> str:
        async with SessionLocal() as db:
            row = await db.get(AgentConfig, agent_id)
            assert row is not None
            return row.content_ciphertext

    return asyncio.run(_read())


class StatusProbeRuntime:
    def __init__(
        self,
        *,
        status: AgentProviderStatus | None = None,
        result: AgentExecutionResult | None = None,
        delay: float = 0,
    ):
        self.status = status or AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="probe",
            method="test",
            checked_at=now_utc(),
        )
        self.result = result or AgentExecutionResult(finished_with="done", total_text="OK")
        self.delay = delay
        self.execute_calls = 0

    async def detect_status(self) -> AgentProviderStatus:
        return self.status

    async def execute(self, **_kwargs) -> AgentExecutionResult:
        self.execute_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def test_mcp_env_whitelist_is_rendered(auth_client, monkeypatch):
    monkeypatch.setenv("FOO_TOKEN", "secret")
    monkeypatch.setenv("MY_OTHER_VAR", "should-not-leak")
    server = {
        "id": "mcp_echo",
        "name": "echo",
        "enabled": True,
        "provider_ids": ["claude-code", "codex"],
        "url": "http://localhost:9999/mcp",
        "headers": [{"name": "X-Test", "value": "1"}],
        "env_var_names": ["FOO_TOKEN"],
    }
    saved = auth_client.post("/api/settings/mcp", json=server)
    assert saved.status_code == 200

    claude_settings = json.loads((claude_home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "mcpServers" not in claude_settings
    assert not claude_mcp_config_path().exists()

    codex_config = (codex_home() / "config.toml").read_text(encoding="utf-8")
    codex_data = tomllib.loads(codex_config)
    assert "mcp_servers" not in codex_data
    assert "FOO_TOKEN" not in codex_config
    assert "MY_OTHER_VAR" not in codex_config
    body = saved.json()
    assert {
        "id": "mcp:mcp_echo",
        "name": "echo",
        "description": "http://localhost:9999/mcp",
        "enabled": True,
        "planning_enabled": False,
    } in body["tools"]


def test_ask_user_plan_policy_keeps_default_claude_tools_available():
    tools = claude_runtime._allowed_tools_for_policy(None, object(), "ask_user_plan")  # noqa: SLF001
    assert tools == ["mcp__ask_user__ask_user", "ask_user"]
    explicit = claude_runtime._allowed_tools_for_policy(["Read"], object(), "ask_user_plan")  # noqa: SLF001
    assert explicit == ["Read", "mcp__ask_user__ask_user", "ask_user"]
    assert claude_runtime._allowed_tools_for_policy(None, None, "ask_user_plan") is None  # noqa: SLF001


def test_ask_user_plan_settings_allows_internal_tool_without_prompt(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {
                    "ask": ["mcp__*", "Bash"],
                    "allow": ["Read"],
                }
            }
        ),
        encoding="utf-8",
    )

    claude_runtime._prepare_ask_user_plan_settings(settings_path, object(), "ask_user_plan")  # noqa: SLF001

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["permissions"]["ask"] == ["Bash"]
    assert data["permissions"]["allow"] == [
        "Read",
        "mcp__ask_user",
        "mcp__ask_user__ask_user",
        "ask_user",
    ]


def test_ask_user_plan_settings_without_bridge_keeps_settings_unchanged(tmp_path):
    settings_path = tmp_path / "settings.json"
    original = {"permissions": {"ask": ["mcp__*"], "allow": ["Read"]}}
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    claude_runtime._prepare_ask_user_plan_settings(settings_path, None, "ask_user_plan")  # noqa: SLF001

    assert json.loads(settings_path.read_text(encoding="utf-8")) == original


def test_ask_user_plan_policy_sets_codex_read_only_sandbox():
    cmd = codex_runtime._build_exec_cmd(  # noqa: SLF001
        Path("/bin/codex"),
        Path("/tmp/workspace"),
        "prompt",
        None,
        None,
        None,
        None,
        None,
        "ask_user_plan",
    )
    assert "--sandbox" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd
    assert 'approval_policy="never"' in cmd


def test_codex_execute_policy_bypasses_cli_approvals_inside_sandbox():
    cmd = codex_runtime._build_exec_cmd(  # noqa: SLF001
        Path("/bin/codex"),
        Path("/tmp/workspace"),
        "prompt",
        None,
        None,
        None,
        None,
        None,
        "execute",
    )
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "mcp_servers.ask_user.default_tools_approval_mode" not in " ".join(cmd)


def test_agent_config_file_editor_roundtrip_and_validation(auth_client):
    claude = auth_client.get("/api/settings/agents/claude-code/config")
    assert claude.status_code == 200
    assert claude.json()["content"] == ""
    assert "schemastore" not in _agent_config_ciphertext("claude-code")

    saved_claude = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": '{"model":"sonnet"}\n', "enabled": False, "supported_models": ["sonnet", "sonnet", " "]},
    )
    assert saved_claude.status_code == 200
    assert '"model":"sonnet"' in saved_claude.json()["content"]
    assert saved_claude.json()["settings"]["agents"][0]["enabled"] is False
    assert saved_claude.json()["settings"]["agents"][0]["supported_models"] == ["sonnet"]
    assert "sonnet" not in _agent_config_ciphertext("claude-code")

    missing_models = auth_client.put("/api/settings/agents/claude-code/config", json={"content": "{}\n"})
    assert missing_models.status_code == 400

    empty_models = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": "{}\n", "supported_models": [" ", ""]},
    )
    assert empty_models.status_code == 400

    invalid_json = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": "{bad", "supported_models": ["sonnet"]},
    )
    assert invalid_json.status_code == 400

    initial_codex = auth_client.get("/api/settings/agents/codex/config")
    assert initial_codex.status_code == 200
    initial_codex_config = tomllib.loads(initial_codex.json()["content"])
    assert initial_codex_config == {}
    assert "approval_policy" not in _agent_config_ciphertext("codex")

    saved_codex = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n', "supported_models": ["gpt-5.2"]},
    )
    assert saved_codex.status_code == 200
    assert 'model = "gpt-5.2"' in saved_codex.json()["content"]
    assert 'cli_auth_credentials_store = "file"' not in saved_codex.json()["content"]
    # 未带 auth_content 时响应不附 auth 字段。
    assert saved_codex.json().get("auth") is None

    invalid_toml = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": "model = ", "supported_models": ["gpt-5.2"]},
    )
    assert invalid_toml.status_code == 400


def test_agent_config_models_must_be_provided_manually(auth_client):
    missing_claude_models = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": '{"model":"sonnet"}\n'},
    )
    assert missing_claude_models.status_code == 400

    manual_claude = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": '{"model":"sonnet"}\n', "supported_models": ["opus", "opus", " "]},
    )
    assert manual_claude.status_code == 200, manual_claude.text
    claude_agent = next(
        agent for agent in manual_claude.json()["settings"]["agents"] if agent["id"] == "claude-code"
    )
    assert claude_agent["supported_models"] == ["opus"]

    missing_codex_models = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n'},
    )
    assert missing_codex_models.status_code == 400

    manual_codex = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n', "supported_models": ["gpt-5.1", "gpt-5.1", " "]},
    )
    assert manual_codex.status_code == 200, manual_codex.text
    codex_agent = next(agent for agent in manual_codex.json()["settings"]["agents"] if agent["id"] == "codex")
    assert codex_agent["supported_models"] == ["gpt-5.1"]


def test_agent_setup_state_completed_by_claude_config(auth_client):
    initial = auth_client.get("/api/settings/agents/setup-state")
    assert initial.status_code == 200
    assert initial.json() == {"completed": False}

    empty = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": "", "supported_models": ["sonnet"]},
    )
    assert empty.status_code == 200
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    invalid_json = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": "{bad", "supported_models": ["sonnet"]},
    )
    assert invalid_json.status_code == 400
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    saved = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": '{"model":"sonnet"}\n', "supported_models": ["sonnet"]},
    )
    assert saved.status_code == 200
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": True}


def test_agent_setup_state_requires_codex_auth_config(auth_client):
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    saved_codex_only = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n', "supported_models": ["gpt-5.2"]},
    )
    assert saved_codex_only.status_code == 200
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    empty_auth = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n', "auth_content": "", "supported_models": ["gpt-5.2"]},
    )
    assert empty_auth.status_code == 200
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    invalid_auth = auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": 'model = "gpt-5.2"\n', "auth_content": "{bad", "supported_models": ["gpt-5.2"]},
    )
    assert invalid_auth.status_code == 400
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": False}

    saved_with_auth = auth_client.put(
        "/api/settings/agents/codex/config",
        json={
            "content": 'model = "gpt-5.2"\n',
            "auth_content": '{"OPENAI_API_KEY":"sk-test"}\n',
            "supported_models": ["gpt-5.2"],
        },
    )
    assert saved_with_auth.status_code == 200
    assert auth_client.get("/api/settings/agents/setup-state").json() == {"completed": True}


def test_codex_auth_json_editor_roundtrip(auth_client):
    # auth.json 仍然可以独立 GET，便于前端打开设置时读初始值。
    initial = auth_client.get("/api/settings/agents/codex-auth/config")
    assert initial.status_code == 200, initial.text
    assert initial.json()["content"] == ""
    assert initial.json()["path"].endswith("auth.json")

    # 写入合并到 codex 的 PUT：同一次请求同步保存 config.toml + auth.json。
    saved = auth_client.put(
        "/api/settings/agents/codex/config",
        json={
            "content": 'model = "gpt-5.2"\n',
            "auth_content": '{"OPENAI_API_KEY":"sk-test"}\n',
            "supported_models": ["gpt-5.2"],
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert 'model = "gpt-5.2"' in body["content"]
    assert body["auth"]["agent_id"] == "codex-auth"
    assert json.loads(body["auth"]["content"]) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }
    assert "sk-test" not in _agent_config_ciphertext("codex-auth")
    codex_config = tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))
    assert codex_config["model"] == "gpt-5.2"
    assert json.loads((codex_home() / "auth.json").read_text(encoding="utf-8")) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }

    # 再次 GET codex-auth 应拿到刚保存的内容。
    refreshed = auth_client.get("/api/settings/agents/codex-auth/config")
    assert refreshed.status_code == 200
    assert json.loads(refreshed.json()["content"]) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }

    # 非法 auth_content 必须 400，且不能覆盖原 auth.json 和 config.toml。
    invalid = auth_client.put(
        "/api/settings/agents/codex/config",
        json={
            "content": 'model = "gpt-5.3"\n',
            "auth_content": "{bad",
            "supported_models": ["gpt-5.3"],
        },
    )
    assert invalid.status_code == 400
    assert json.loads((codex_home() / "auth.json").read_text(encoding="utf-8")) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }
    assert tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))["model"] == "gpt-5.2"
    assert "gpt-5.2" in auth_client.get("/api/settings/agents/codex/config").json()["content"]
    assert json.loads(auth_client.get("/api/settings/agents/codex-auth/config").json()["content"]) == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test",
    }

    # auth_content 仅允许跟 codex 一起保存，其它 agent 携带它应当被拒绝。
    misused = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": "{}\n", "auth_content": "{}\n", "supported_models": ["sonnet"]},
    )
    assert misused.status_code == 400

    # PUT codex-auth 路由已经不再受支持（FastAPI 对路径 Literal 不匹配返回 422）。
    rejected = auth_client.put(
        "/api/settings/agents/codex-auth/config",
        json={"content": "{}\n", "supported_models": ["gpt-5.2"]},
    )
    assert rejected.status_code == 422


def test_instruction_file_editor_roundtrip(auth_client):
    claude = auth_client.get("/api/settings/instructions/claude-code")
    assert claude.status_code == 200
    assert claude.json()["path"].endswith("CLAUDE.md")
    assert claude.json()["content"] == ""

    saved_claude = auth_client.put(
        "/api/settings/instructions/claude-code",
        json={"content": "# Claude instructions\nplain text {not json}\n"},
    )
    assert saved_claude.status_code == 200
    assert saved_claude.json()["content"] == "# Claude instructions\nplain text {not json}\n"
    assert (claude_home() / "CLAUDE.md").read_text(encoding="utf-8") == "# Claude instructions\nplain text {not json}\n"

    saved_codex = auth_client.put(
        "/api/settings/instructions/codex",
        json={"content": "# Codex instructions\nplain text = ok\n"},
    )
    assert saved_codex.status_code == 200
    assert saved_codex.json()["path"].endswith("AGENTS.md")
    assert (codex_home() / "AGENTS.md").read_text(encoding="utf-8") == "# Codex instructions\nplain text = ok\n"


def test_admin_only_endpoints_reject_normal_user(user_client):
    # 普通用户能读 settings（StepTab / AppLaunchView 依赖），但分项写、agent 配置、全局指令、skill 上传都应 403。
    assert user_client.get("/api/settings").status_code == 200
    payload = user_client.get("/api/settings").json()
    assert user_client.put("/api/settings", json=payload).status_code == 405
    assert user_client.put("/api/settings/agents", json={"agents": payload["agents"]}).status_code == 404
    assert user_client.patch("/api/settings/skills/skill_x", json={"enabled": False}).status_code == 403
    assert user_client.delete("/api/settings/skills/skill_x").status_code == 403
    assert user_client.post("/api/settings/mcp", json={
        "id": "mcp_x",
        "name": "x",
        "enabled": True,
        "provider_ids": ["claude-code"],
        "url": "http://localhost/mcp",
        "headers": [],
        "env_var_names": [],
    }).status_code == 403
    assert user_client.put("/api/settings/mcp/mcp_x", json={
        "id": "mcp_x",
        "name": "x",
        "enabled": True,
        "provider_ids": ["claude-code"],
        "url": "http://localhost/mcp",
        "headers": [],
        "env_var_names": [],
    }).status_code == 403
    assert user_client.delete("/api/settings/mcp/mcp_x").status_code == 403
    assert user_client.get("/api/settings/agents/claude-code/config").status_code == 403
    assert user_client.get("/api/settings/agents/setup-state").status_code == 403
    assert user_client.put(
        "/api/settings/agents/codex/config",
        json={"content": ""},
    ).status_code == 403
    # auth.json 现在通过 codex 的 PUT 合并保存，普通用户带 auth_content 仍应 403。
    assert user_client.put(
        "/api/settings/agents/codex/config",
        json={"content": "", "auth_content": "{}\n"},
    ).status_code == 403
    assert user_client.get("/api/settings/agents/codex-auth/config").status_code == 403
    assert user_client.get("/api/settings/instructions/claude-code").status_code == 403
    assert user_client.put(
        "/api/settings/instructions/codex",
        json={"content": "hello"},
    ).status_code == 403
    assert user_client.post("/api/settings/agents/refresh").status_code == 404
    assert user_client.post(
        "/api/skills/parse",
        files={"archive": ("x.zip", b"no", "application/zip")},
    ).status_code == 403
    assert user_client.get("/api/settings/skills/skill_x/skill-md").status_code == 403


def test_me_reports_is_admin_flag(client):
    admin_login = client.post(
        "/api/auth/login",
        json={"username": "test-admin", "password": "admin-test-secret"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["token"]
    admin_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_me.status_code == 200
    assert admin_me.json()["is_admin"] is True

    user_token = create_regular_user("plain-user")["token"]
    user_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
    assert user_me.status_code == 200
    assert user_me.json()["is_admin"] is False


def test_runtime_config_uses_db_agent_config_not_existing_runtime_files(auth_client, monkeypatch):
    monkeypatch.setenv("FOO_TOKEN", "secret")
    (claude_home() / ".claude" / "settings.json").write_text(
        '{"model":"opus","mcpServers":{"old":{"url":"http://old"}}}',
        encoding="utf-8",
    )
    (codex_home() / "config.toml").write_text('model = "gpt-5.2"\n', encoding="utf-8")
    server = {
        "id": "mcp_echo",
        "name": "echo",
        "enabled": True,
        "provider_ids": ["claude-code", "codex"],
        "url": "http://localhost:9999/mcp",
        "headers": [],
        "env_var_names": ["FOO_TOKEN"],
    }
    assert auth_client.post("/api/settings/mcp", json=server).status_code == 200
    claude_settings = json.loads((claude_home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert claude_settings == {}
    assert "model" not in claude_settings
    assert "mcpServers" not in claude_settings
    assert not claude_mcp_config_path().exists()
    codex_config = (codex_home() / "config.toml").read_text(encoding="utf-8")
    codex_data = tomllib.loads(codex_config)
    assert "model" not in codex_data
    assert "mcp_servers" not in codex_data
    assert "FOO_TOKEN" not in codex_config


def test_runtime_config_does_not_rewrite_plain_text_editor_content(auth_client):
    claude_text = '{\n  "model": "opus"\n}\n'
    codex_text = 'model = "gpt-5.2"\n'
    claude_instruction = "# Keep Claude instructions\n"
    codex_instruction = "# Keep Codex instructions\n"
    assert auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": claude_text, "supported_models": ["opus"]},
    ).status_code == 200
    assert auth_client.put(
        "/api/settings/agents/codex/config",
        json={"content": codex_text, "supported_models": ["gpt-5.2"]},
    ).status_code == 200
    assert auth_client.put("/api/settings/instructions/claude-code", json={"content": claude_instruction}).status_code == 200
    assert auth_client.put("/api/settings/instructions/codex", json={"content": codex_instruction}).status_code == 200
    assert auth_client.get("/api/settings/agents/claude-code/config").json()["content"] == claude_text
    assert auth_client.get("/api/settings/agents/codex/config").json()["content"] == codex_text
    assert json.loads((claude_home() / ".claude" / "settings.json").read_text(encoding="utf-8")) == {"model": "opus"}
    assert tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8")) == {
        "model": "gpt-5.2",
    }
    assert (claude_home() / "CLAUDE.md").read_text(encoding="utf-8") == claude_instruction
    assert (codex_home() / "AGENTS.md").read_text(encoding="utf-8") == codex_instruction


def test_disabling_mcp_clears_runtime_config(auth_client):
    server = {
        "id": "mcp_clean",
        "name": "clean",
        "enabled": True,
        "provider_ids": ["claude-code", "codex"],
        "url": "http://localhost:9999/mcp",
        "headers": [],
        "env_var_names": [],
    }
    assert auth_client.post("/api/settings/mcp", json=server).status_code == 200

    claude_path = claude_home() / ".claude" / "settings.json"
    codex_path = codex_home() / "config.toml"
    assert "mcpServers" not in json.loads(claude_path.read_text(encoding="utf-8"))
    assert not claude_mcp_config_path().exists()
    assert "mcp_servers" not in tomllib.loads(codex_path.read_text(encoding="utf-8"))

    disabled = {**server, "enabled": False}
    assert auth_client.put(f"/api/settings/mcp/{server['id']}", json=disabled).status_code == 200

    assert "mcpServers" not in json.loads(claude_path.read_text(encoding="utf-8"))
    assert not claude_mcp_config_path().exists()
    assert "mcp_servers" not in tomllib.loads(codex_path.read_text(encoding="utf-8"))


def test_codex_status_requires_config_and_auth(auth_client, monkeypatch):
    # detect_status 只检查 Docker sandbox 和文件是否存在，不解析配置内容；
    # 真正能否运行由 refresh_agent_status 的 smoke run 决定。
    class FakeRunner:
        async def check_available(self):
            return DockerSandboxStatus(ok=True)

    monkeypatch.setattr(codex_runtime, "DockerSandboxRunner", lambda: FakeRunner())

    (codex_home() / "config.toml").write_text("", encoding="utf-8")
    (codex_home() / "auth.json").unlink(missing_ok=True)
    status = asyncio.run(CodexCliRuntime("user_admin").detect_status())
    assert status.installed is False
    assert status.runnable is None
    assert "auth.json" in (status.error or "")

    (codex_home() / "config.toml").unlink(missing_ok=True)
    (codex_home() / "auth.json").write_text("{}\n", encoding="utf-8")
    status = asyncio.run(CodexCliRuntime("user_admin").detect_status())
    assert status.installed is False
    assert "配置文件" in (status.error or "")

    (codex_home() / "config.toml").write_text("", encoding="utf-8")
    (codex_home() / "auth.json").write_text("not-a-json\n", encoding="utf-8")
    status = asyncio.run(CodexCliRuntime("user_admin").detect_status())
    assert status.installed is True
    assert status.runnable is None
    assert status.error is None


def test_agent_status_runs_smoke_probe(auth_client):
    runtime = StatusProbeRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/agents/claude-code/status")
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200
    body = response.json()
    assert body["runnable"] is True
    assert body["error"] is None
    assert runtime.execute_calls == 1


def test_agent_status_reports_smoke_probe_failure(auth_client):
    runtime = StatusProbeRuntime(
        result=AgentExecutionResult(
            finished_with="error",
            error="probe failed",
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/agents/claude-code/status")
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is True
    assert body["runnable"] is False
    assert "probe failed" in body["error"]
    assert runtime.execute_calls == 1


def test_agent_status_reports_smoke_probe_timeout(auth_client, monkeypatch):
    runtime = StatusProbeRuntime(delay=0.1)
    monkeypatch.setattr(settings_api, "SMOKE_STATUS_TIMEOUT_SEC", 0.01)
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/agents/claude-code/status")
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200
    body = response.json()
    assert body["runnable"] is False
    assert "超时" in body["error"]
    assert runtime.execute_calls == 1


def test_agent_status_skips_smoke_probe_when_not_installed(auth_client):
    # detect_status 报告 not installed 时直接短路：不再跑真实 smoke。
    runtime = StatusProbeRuntime(
        status=AgentProviderStatus(
            installed=False,
            identity="probe",
            method="test",
            error="missing config file",
            checked_at=now_utc(),
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/agents/claude-code/status")
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is False
    assert body["runnable"] is False
    assert body["error"] == "missing config file"
    assert runtime.execute_calls == 0


def test_agent_config_builtin_defaults_are_empty():
    assert default_content("claude-code") == ""
    assert default_content("codex") == ""
    assert default_content("codex-auth") == ""


def test_status_sync_overwrites_runtime_files_from_db(auth_client):
    claude_path = claude_home() / ".claude" / "settings.json"
    codex_path = codex_home() / "config.toml"
    auth_path = codex_home() / "auth.json"
    claude_text = '{"model":"opus"}\n'
    codex_text = 'model = "gpt-5.2"\n'
    auth_text = '{"auth_mode":"apikey","OPENAI_API_KEY":"sk-keep"}\n'
    claude_path.write_text(claude_text, encoding="utf-8")
    codex_path.write_text(codex_text, encoding="utf-8")
    auth_path.write_text(auth_text, encoding="utf-8")

    status = auth_client.post("/api/settings/agents/codex/status")
    assert status.status_code == 200

    assert json.loads(claude_path.read_text(encoding="utf-8")) == {}
    codex_config = tomllib.loads(codex_path.read_text(encoding="utf-8"))
    assert codex_config == {}
    assert "model" not in codex_config
    assert auth_path.read_text(encoding="utf-8") == ""
