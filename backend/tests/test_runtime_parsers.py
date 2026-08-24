import asyncio
import json
import tomllib
from pathlib import Path

import pytest

import app.runtime.codex_runtime as codex_runtime
from app.runtime.base import DecisionResult
from app.runtime.codex_runtime import (
    CodexRuntime,
    _clean_env,
    _native_answers,
    _normalize_request_user_input,
    _prepare_scoped_home,
    _thread_request,
    _turn_request,
)
from app.runtime.sandbox import DockerSandboxReply, DockerSandboxResult, DockerSandboxStatus
from app.schemas import McpHeader, RuntimeMcpServerConfig
from app.schemas.decision import DecisionAnswer
from app.services.tools import RuntimeToolConfig


def test_codex_thread_requests_start_resume_and_fork() -> None:
    started = _thread_request(
        session_id=None,
        model="gpt-test",
        runtime_policy="execute",
        fork_session=False,
    )
    assert started == {
        "id": 2,
        "method": "thread/start",
        "params": {
            "cwd": "/workspace",
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "model": "gpt-test",
        },
    }

    resumed = _thread_request(
        session_id="thread_1",
        model=None,
        runtime_policy="plan",
        fork_session=False,
    )
    assert resumed["method"] == "thread/resume"
    assert resumed["params"]["threadId"] == "thread_1"
    assert resumed["params"]["sandbox"] == "read-only"

    forked = _thread_request(
        session_id="thread_1",
        model=None,
        runtime_policy="execute",
        fork_session=True,
    )
    assert forked["method"] == "thread/fork"
    with pytest.raises(ValueError, match="session_id"):
        _thread_request(
            session_id=None,
            model=None,
            runtime_policy="execute",
            fork_session=True,
        )


def test_codex_turn_request_uses_native_plan_mode_and_output_schema() -> None:
    schema = {"type": "object", "properties": {"html": {"type": "string"}}}
    request = _turn_request(
        thread_id="thread_1",
        prompt="plan this",
        model="gpt-test",
        reasoning_effort="high",
        runtime_policy="plan",
        output_schema=schema,
    )
    params = request["params"]
    assert request["method"] == "turn/start"
    assert params["input"] == [{"type": "text", "text": "plan this"}]
    assert params["effort"] == "high"
    assert params["outputSchema"] == schema
    assert params["sandboxPolicy"] == {
        "type": "externalSandbox",
        "networkAccess": "restricted",
    }
    assert params["collaborationMode"] == {
        "mode": "plan",
        "settings": {
            "model": "gpt-test",
            "reasoning_effort": "high",
            "developer_instructions": None,
        },
    }


def test_codex_execute_turn_explicitly_restores_full_access() -> None:
    request = _turn_request(
        thread_id="thread_1",
        prompt="execute",
        model=None,
        reasoning_effort=None,
        runtime_policy="execute",
        output_schema=None,
    )
    assert request["params"]["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert "collaborationMode" not in request["params"]


def test_request_user_input_normalizes_native_questions_and_answers() -> None:
    request = _normalize_request_user_input(
        {
            "itemId": "item_1",
            "questions": [
                {
                    "id": "style",
                    "header": "风格",
                    "question": "请选择视觉风格",
                    "isSecret": False,
                    "options": [
                        {"label": "简洁", "description": "留白更多"},
                        {"label": "丰富", "description": "信息密度更高"},
                    ],
                }
            ],
        }
    )
    assert request.request_id == "item_1"
    assert request.context.title == "风格"
    assert request.groups[0].options[0].recommended is True
    assert request.groups[0].options[1].recommended is False

    result = DecisionResult(
        answers=[DecisionAnswer(group_id="style", selected=["简洁"])],
        text="使用黑白配色",
    )
    assert _native_answers(request, result) == {
        "answers": {"style": {"answers": ["简洁", "使用黑白配色"]}}
    }


@pytest.mark.parametrize(
    "question,error",
    [
        ({"id": "x", "header": "X", "question": "Q", "isSecret": True, "options": []}, "敏感"),
        ({"id": "x", "header": "X", "question": "Q", "options": []}, "2-3"),
    ],
)
def test_request_user_input_rejects_unsupported_native_questions(question, error) -> None:
    with pytest.raises(ValueError, match=error):
        _normalize_request_user_input({"itemId": "item_1", "questions": [question]})


def test_codex_scoped_home_contains_only_configured_mcp(tmp_path) -> None:
    tools = RuntimeToolConfig(
        mcp_servers=[
            RuntimeMcpServerConfig(
                name="docs",
                url="https://example.test/mcp",
                headers=[McpHeader(name="Authorization", value="Bearer secret")],
            )
        ],
        skills=[],
    )
    shared_home = tmp_path / "shared-home"
    shared_home.mkdir()
    (shared_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (shared_home / "auth.json").write_text("{}\n", encoding="utf-8")

    home, mounts = _prepare_scoped_home(shared_home, tmp_path / "workspace", tools)
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))

    assert mounts == ()
    assert config["mcp_servers"] == {
        "docs": {
            "url": "https://example.test/mcp",
            "http_headers": {"Authorization": "Bearer secret"},
        }
    }


def test_codex_clean_env_does_not_forward_host_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("CUSTOM_TOKEN", "secret")
    env = _clean_env(tmp_path)
    assert env["HOME"] == str(tmp_path)
    assert env["CODEX_HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in env
    assert "CUSTOM_TOKEN" not in env


def test_codex_runtime_drives_app_server_json_rpc(tmp_path, monkeypatch) -> None:
    shared_home = tmp_path / "shared-home"
    shared_home.mkdir()
    (shared_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (shared_home / "auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(codex_runtime, "codex_home", lambda: shared_home)

    class FakeRunner:
        def __init__(self) -> None:
            self.spec = None
            self.replies: list[DockerSandboxReply | None] = []

        async def check_available(self):
            return DockerSandboxStatus(ok=True)

        async def run_interactive(self, spec, *, on_stdout_line, cancel_event):
            self.spec = spec
            assert not cancel_event.is_set()
            assert spec.command == ["codex", "app-server"]
            initialize = json.loads(spec.prompt.splitlines()[0])
            assert initialize["method"] == "initialize"
            assert initialize["params"]["capabilities"]["experimentalApi"] is True
            self.replies.append(await on_stdout_line('{"id":1,"result":{}}'))
            self.replies.append(
                await on_stdout_line('{"id":2,"result":{"thread":{"id":"thread_new"}}}')
            )
            assert json.loads(self.replies[-1].input)["method"] == "turn/start"
            await on_stdout_line('{"method":"item/agentMessage/delta","params":{"delta":"hel"}}')
            await on_stdout_line('{"method":"item/agentMessage/delta","params":{"delta":"lo"}}')
            await on_stdout_line(
                '{"method":"item/completed","params":{"item":{"type":"agentMessage","text":"hello"}}}'
            )
            completed = await on_stdout_line(
                '{"method":"turn/completed","params":{"turn":{"status":"completed"}}}'
            )
            assert completed.complete is True
            return DockerSandboxResult(return_code=0)

    runner = FakeRunner()
    runtime = CodexRuntime()
    runtime.runner = runner
    chunks = []

    async def run():
        return await runtime.execute(
            prompt="hello",
            session_id=None,
            model=None,
            reasoning_effort="medium",
            cwd=tmp_path / "workspace",
            on_chunk=lambda chunk: _append_chunk(chunks, chunk),
            cancel_event=asyncio.Event(),
        )

    result = asyncio.run(run())
    assert result.finished_with == "done"
    assert result.session_id == "thread_new"
    assert result.total_text == "hello"
    assert [chunk.type for chunk in chunks] == ["session", "text", "text"]


def test_codex_runtime_routes_request_user_input_when_id_collides_with_thread_request(
    tmp_path, monkeypatch
) -> None:
    shared_home = tmp_path / "shared-home"
    shared_home.mkdir()
    (shared_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (shared_home / "auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(codex_runtime, "codex_home", lambda: shared_home)

    class FakeRunner:
        async def check_available(self):
            return DockerSandboxStatus(ok=True)

        async def run_interactive(self, spec, *, on_stdout_line, cancel_event):
            await on_stdout_line('{"id":1,"result":{}}')
            await on_stdout_line('{"id":2,"result":{"thread":{"id":"thread_plan"}}}')
            reply = await on_stdout_line(
                '{"method":"item/tool/requestUserInput","id":2,"params":{'
                '"itemId":"call_collision","questions":[{'
                '"id":"choice","header":"选择","question":"请选择",'
                '"isSecret":false,"options":['
                '{"label":"A","description":"选择 A"},'
                '{"label":"B","description":"选择 B"}'
                "]}]}}"
            )
            assert json.loads(reply.input) == {
                "id": 2,
                "result": {"answers": {"choice": {"answers": ["A"]}}},
            }
            await on_stdout_line(
                '{"method":"turn/completed","params":{"turn":{"status":"completed"}}}'
            )
            return DockerSandboxResult(return_code=0)

    runtime = CodexRuntime()
    runtime.runner = FakeRunner()
    seen_requests = []

    async def on_decision_request(request):
        seen_requests.append(request.request_id)
        return DecisionResult(
            answers=[DecisionAnswer(group_id="choice", selected=["A"])]
        )

    async def run():
        return await runtime.execute(
            prompt="plan this",
            session_id=None,
            model=None,
            reasoning_effort="medium",
            cwd=tmp_path / "workspace",
            on_chunk=lambda _chunk: asyncio.sleep(0),
            cancel_event=asyncio.Event(),
            on_decision_request=on_decision_request,
            runtime_policy="plan",
        )

    result = asyncio.run(run())
    assert result.finished_with == "done"
    assert seen_requests == ["call_collision"]


def test_codex_plan_runtime_uses_read_only_outer_workspace(tmp_path, monkeypatch) -> None:
    shared_home = tmp_path / "shared-home"
    shared_home.mkdir()
    (shared_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (shared_home / "auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(codex_runtime, "codex_home", lambda: shared_home)

    class FakeRunner:
        def __init__(self) -> None:
            self.spec = None

        async def check_available(self):
            return DockerSandboxStatus(ok=True)

        async def run_interactive(self, spec, *, on_stdout_line, cancel_event):
            self.spec = spec
            await on_stdout_line('{"id":1,"result":{}}')
            reply = await on_stdout_line('{"id":2,"result":{"thread":{"id":"thread_plan"}}}')
            turn = json.loads(reply.input)
            assert turn["params"]["sandboxPolicy"] == {
                "type": "externalSandbox",
                "networkAccess": "restricted",
            }
            await on_stdout_line(
                '{"method":"item/completed","params":{"item":{"type":"agentMessage",'
                '"text":"{\\"decision_state\\":\\"ready\\",\\"decision_summary\\":'
                '\\"输入充分\\",\\"reason\\":\\"无需提问\\"}"}}}'
            )
            await on_stdout_line(
                '{"method":"turn/completed","params":{"turn":{"status":"completed"}}}'
            )
            return DockerSandboxResult(return_code=0)

    runner = FakeRunner()
    runtime = CodexRuntime()
    runtime.runner = runner

    async def run():
        return await runtime.execute(
            prompt="plan this",
            session_id=None,
            model=None,
            reasoning_effort="medium",
            cwd=tmp_path / "workspace",
            on_chunk=lambda _chunk: asyncio.sleep(0),
            cancel_event=asyncio.Event(),
            runtime_policy="plan",
        )

    result = asyncio.run(run())
    assert result.finished_with == "done"
    assert runner.spec.workspace_read_only is True


async def _append_chunk(chunks: list, chunk) -> None:
    chunks.append(chunk)
