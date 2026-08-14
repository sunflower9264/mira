import asyncio
import json
import tomllib
from types import SimpleNamespace

import app.runtime.claude_runtime as claude_runtime
import app.runtime.codex_runtime as codex_runtime
from app.runtime.claude_runtime import ClaudeAgentRuntime
from app.runtime.claude_runtime import _chunks_from_event as claude_chunks
from app.runtime.claude_runtime import _build_print_cmd as claude_build_print_cmd
from app.runtime.claude_runtime import _clean_env as claude_clean_env
from app.runtime.claude_runtime import _extract_session_id as claude_session
from app.runtime.claude_runtime import _format_failure_detail as claude_failure_detail
from app.runtime.claude_runtime import _permission_mode_from_settings as claude_permission_mode
from app.runtime.claude_runtime import _structured_output_text_from_chunk as claude_structured_output
from app.runtime.codex_runtime import _chunks_from_event as codex_chunks
from app.runtime.codex_runtime import CodexCliRuntime
from app.runtime.codex_runtime import _build_exec_cmd as codex_build_exec_cmd
from app.runtime.codex_runtime import _clean_env as codex_clean_env
from app.runtime.codex_runtime import _extract_session_id as codex_session
from app.runtime.codex_runtime import _format_failure_detail as codex_failure_detail
from app.config import get_settings
from app.runtime.sandbox import DockerSandboxResult, DockerSandboxStatus
from app.schemas import McpHeader, RuntimeMcpServerConfig
from app.services.runtime_paths import _claude_cli_path_for_runtime, claude_cli_path, codex_cli_path
from app.services.tools import RuntimeToolConfig


def _env_value(env: dict[str, str], key: str) -> str | None:
    for env_key, value in env.items():
        if env_key.upper() == key.upper():
            return value
    return None


def test_claude_stream_json_parser_extracts_session_text_and_tools():
    assert claude_session({"message": {"session_id": "sid_1"}}) == "sid_1"
    chunks = claude_chunks(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "name": "Read", "input": {"file": "x"}},
                    {"type": "tool_result", "content": "ok"},
                ]
            }
        }
    )
    assert [chunk.type for chunk in chunks] == ["text", "tool_call", "tool_result"]
    assert chunks[0].text == "hello"


def test_claude_structured_output_tool_use_is_extractable_as_json():
    chunks = claude_chunks(
        {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "StructuredOutput",
                        "input": {"html": "<section>OK</section>"},
                    },
                ]
            }
        }
    )

    assert len(chunks) == 1
    assert chunks[0].type == "tool_call"
    assert claude_structured_output(chunks[0]) == '{"html": "<section>OK</section>"}'


def test_claude_structured_output_ignores_empty_tool_use_input():
    chunks = claude_chunks(
        {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "StructuredOutput",
                        "input": {},
                    },
                ]
            }
        }
    )

    assert len(chunks) == 1
    assert chunks[0].type == "tool_call"
    assert claude_structured_output(chunks[0]) is None


def test_claude_structured_output_pseudo_tool_call_is_extractable_as_json():
    chunks = claude_chunks(
        {
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<tool_call>\n"
                            "<function=StructuredOutput>\n"
                            "<parameter=prompt>生成简洁中文 HTML，保留 REAL_AI_PROMPT_ASSISTANT。</parameter>\n"
                            "<parameter=output_contract>None</parameter>\n"
                            "</function>\n"
                            "</tool_call>"
                        ),
                    },
                ]
            }
        }
    )

    assert len(chunks) == 1
    assert chunks[0].type == "text"
    assert (
        claude_structured_output(chunks[0])
        == '{"prompt": "生成简洁中文 HTML，保留 REAL_AI_PROMPT_ASSISTANT。", "output_contract": null}'
    )


def test_claude_execute_prefers_structured_output_over_followup_text(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    try:
        settings_path = tmp_path / "runtime" / "homes" / "_shared" / "claude_home" / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, _spec, *, on_stdout_line, cancel_event):
                assert not cancel_event.is_set()
                await on_stdout_line(
                    '{"message":{"content":[{"type":"tool_use","name":"StructuredOutput","input":{"html":"<section>OK</section>"}}]}}'
                )
                await on_stdout_line(
                    '{"message":{"content":[{"type":"text","text":"已通过 StructuredOutput 返回最终 HTML 页面。"}]}}'
                )
                return DockerSandboxResult(return_code=0)

        monkeypatch.setattr(claude_runtime, "DockerSandboxRunner", lambda: FakeRunner())
        chunks = []

        async def run():
            return await ClaudeAgentRuntime("user_admin").execute(
                prompt="生成 HTML",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=tmp_path / "workspace",
                on_chunk=lambda chunk: _append_chunk(chunks, chunk),
                cancel_event=asyncio.Event(),
                output_schema={"type": "object", "properties": {"html": {"type": "string"}}, "required": ["html"]},
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert result.finished_with == "done"
    assert result.total_text == '{"html": "<section>OK</section>"}'
    assert [chunk.type for chunk in chunks] == ["tool_call", "text"]


def test_claude_execute_uses_pseudo_tool_call_when_tool_use_input_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    try:
        settings_path = tmp_path / "runtime" / "homes" / "_shared" / "claude_home" / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, _spec, *, on_stdout_line, cancel_event):
                assert not cancel_event.is_set()
                await on_stdout_line(
                    '{"message":{"content":[{"type":"text","text":"<tool_call>\\n<function=StructuredOutput>\\n'
                    '<parameter=prompt>生成简洁中文 HTML，保留 REAL_AI_PROMPT_ASSISTANT。</parameter>\\n'
                    '<parameter=output_contract>None</parameter>\\n</function>\\n</tool_call>"}]}}'
                )
                assert cancel_event.is_set()
                await on_stdout_line(
                    '{"message":{"content":[{"type":"tool_use","name":"StructuredOutput","input":{}}]}}'
                )
                return DockerSandboxResult(return_code=130, stderr="cancelled")

        monkeypatch.setattr(claude_runtime, "DockerSandboxRunner", lambda: FakeRunner())
        chunks = []

        async def run():
            return await ClaudeAgentRuntime("user_admin").execute(
                prompt="生成 Prompt Assistant 输出",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=tmp_path / "workspace",
                on_chunk=lambda chunk: _append_chunk(chunks, chunk),
                cancel_event=asyncio.Event(),
                output_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "output_contract": {"type": ["object", "null"]},
                    },
                    "required": ["prompt", "output_contract"],
                },
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert result.finished_with == "done"
    assert result.total_text == '{"prompt": "生成简洁中文 HTML，保留 REAL_AI_PROMPT_ASSISTANT。", "output_contract": null}'
    assert [chunk.type for chunk in chunks] == ["text", "tool_call"]


def test_claude_execute_stops_after_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    try:
        settings_path = tmp_path / "runtime" / "homes" / "_shared" / "claude_home" / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, _spec, *, on_stdout_line, cancel_event):
                await on_stdout_line(
                    '{"message":{"content":[{"type":"tool_use","name":"StructuredOutput","input":{"html":"<section>OK</section>"}}]}}'
                )
                assert cancel_event.is_set()
                return DockerSandboxResult(return_code=130, stderr="cancelled")

        monkeypatch.setattr(claude_runtime, "DockerSandboxRunner", lambda: FakeRunner())
        chunks = []

        async def run():
            return await ClaudeAgentRuntime("user_admin").execute(
                prompt="生成 HTML",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=tmp_path / "workspace",
                on_chunk=lambda chunk: _append_chunk(chunks, chunk),
                cancel_event=asyncio.Event(),
                output_schema={"type": "object", "properties": {"html": {"type": "string"}}, "required": ["html"]},
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert result.finished_with == "done"
    assert result.total_text == '{"html": "<section>OK</section>"}'
    assert [chunk.type for chunk in chunks] == ["tool_call"]


async def _append_chunk(chunks, chunk):
    chunks.append(chunk)


def test_claude_verbose_stream_event_parser_extracts_text_only():
    text = claude_chunks(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "OK"},
            },
        }
    )
    thinking = claude_chunks(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hidden"},
            },
        }
    )
    assert text[0].type == "text"
    assert text[0].text == "OK"
    assert thinking == []


def test_claude_stream_event_wrapper_does_not_duplicate_mirrored_text():
    chunks = claude_chunks(
        {
            "type": "stream_event",
            "content": '{"action":"complete"}',
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": '{"action":"complete"}'},
            },
        }
    )

    assert [chunk.text for chunk in chunks] == ['{"action":"complete"}']


def test_claude_message_content_takes_precedence_over_top_level_text():
    chunks = claude_chunks(
        {
            "type": "assistant",
            "text": '{"action":"complete"}',
            "message": {
                "content": [
                    {"type": "text", "text": '{"action":"complete"}'},
                ]
            },
        }
    )

    assert [chunk.text for chunk in chunks] == ['{"action":"complete"}']


def test_codex_jsonl_parser_extracts_thread_text_and_tools():
    assert codex_session({"thread": {"thread_id": "thread_1"}}) == "thread_1"
    text = codex_chunks({"type": "agent_message_delta", "delta": "hello"})
    item_text = codex_chunks({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
    tool_call = codex_chunks({"type": "tool_call", "name": "shell"})
    tool_result = codex_chunks({"type": "tool_result", "output": "ok"})
    assert text[0].type == "text"
    assert text[0].text == "hello"
    assert item_text[0].type == "text"
    assert item_text[0].text == "done"
    assert tool_call[0].type == "tool_call"
    assert tool_result[0].type == "tool_result"


def test_codex_execute_prefers_latest_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    try:
        codex_home = tmp_path / "runtime" / "homes" / "_shared" / "codex_home"
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "config.toml").write_text("", encoding="utf-8")
        (codex_home / "auth.json").write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, spec, *, on_stdout_line, cancel_event):
                assert not cancel_event.is_set()
                placeholder = '{"artifacts":[{"path":"/workspace/.placeholder","name":"placeholder"}]}'
                final = '{"artifacts":[{"path":"/workspace/app.tar.gz","name":"app.tar.gz"}]}'
                await on_stdout_line(
                    spec.path_map.container_to_host_text(
                        json.dumps({"type": "agent_message_delta", "delta": placeholder})
                    )
                )
                await on_stdout_line(
                    spec.path_map.container_to_host_text(
                        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": final}})
                    )
                )
                await on_stdout_line('{"type":"agent_message_delta","delta":"done"}')
                return DockerSandboxResult(return_code=0)

        monkeypatch.setattr(codex_runtime, "DockerSandboxRunner", lambda: FakeRunner())
        chunks = []
        workspace = tmp_path / "workspace"

        async def run():
            return await CodexCliRuntime("user_admin").execute(
                prompt="生成 artifact",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=workspace,
                on_chunk=lambda chunk: _append_chunk(chunks, chunk),
                cancel_event=asyncio.Event(),
                output_schema={
                    "type": "object",
                    "properties": {"artifacts": {"type": "array"}},
                    "required": ["artifacts"],
                },
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert result.finished_with == "done"
    assert result.total_text == (
        '{"artifacts": [{"path": "'
        + str(workspace.resolve() / "app.tar.gz")
        + '", "name": "app.tar.gz"}]}'
    )
    assert [chunk.type for chunk in chunks] == ["text", "text", "text"]


def test_codex_execute_recovers_intact_session_text_when_stdout_has_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    session_id = "019fffd1-session-recover"
    intact = json.dumps({"html": "封面图，缺少详情页完整多图"}, ensure_ascii=False)
    damaged = intact.replace("缺", "\ufffd\ufffd\ufffd")
    try:
        shared = tmp_path / "runtime" / "homes" / "_shared" / "codex_home"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "config.toml").write_text("", encoding="utf-8")
        (shared / "auth.json").write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, spec, *, on_stdout_line, cancel_event):
                sess_dir = spec.path_map.home_host / "sessions" / "2026" / "08" / "14"
                sess_dir.mkdir(parents=True, exist_ok=True)
                (sess_dir / f"rollout-{session_id}.jsonl").write_text(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "agent_message", "message": intact},
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                await on_stdout_line(
                    json.dumps(
                        {
                            "type": "item.completed",
                            "session_id": session_id,
                            "item": {"type": "agent_message", "text": damaged},
                        },
                        ensure_ascii=False,
                    )
                )
                return DockerSandboxResult(return_code=0)

        monkeypatch.setattr(codex_runtime, "DockerSandboxRunner", lambda: FakeRunner())
        chunks = []
        workspace = tmp_path / "workspace"

        async def run():
            return await CodexCliRuntime("user_admin").execute(
                prompt="渲染 HTML",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=workspace,
                on_chunk=lambda chunk: _append_chunk(chunks, chunk),
                cancel_event=asyncio.Event(),
                output_schema={
                    "type": "object",
                    "properties": {"html": {"type": "string"}},
                    "required": ["html"],
                },
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    assert result.finished_with == "done"
    assert "\ufffd" not in result.total_text
    assert "缺少详情页完整多图" in result.total_text


def test_runtime_env_uses_fake_home_and_does_not_leak_provider_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-anthropic")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "host-claude")
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("CODEX_HOME", "host-codex")
    monkeypatch.setenv("CUSTOM_TOKEN", "host-token")
    monkeypatch.setenv("PATH", "/host/bin")
    home = tmp_path / "runtime-home"

    claude_env = claude_clean_env(home)
    assert claude_env["HOME"] == str(home)
    assert claude_env["USERPROFILE"] == str(home)
    assert claude_env["CLAUDE_CONFIG_DIR"] == str(home / ".claude")
    assert claude_env["PATH"] == "/home/mira/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert "ANTHROPIC_API_KEY" not in claude_env
    assert "OPENAI_API_KEY" not in claude_env
    assert "CUSTOM_TOKEN" not in claude_env

    codex_env = codex_clean_env(home)
    assert codex_env["HOME"] == str(home)
    assert codex_env["USERPROFILE"] == str(home)
    assert codex_env["CODEX_HOME"] == str(home)
    assert codex_env["PATH"] == "/home/mira/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert "ANTHROPIC_API_KEY" not in codex_env
    assert "OPENAI_API_KEY" not in codex_env
    assert "CUSTOM_TOKEN" not in codex_env


def test_runtime_env_preserves_posix_network_and_cert_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_runtime.os, "name", "posix")
    monkeypatch.setattr(codex_runtime.os, "name", "posix")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/cert.pem")
    monkeypatch.setenv("https_proxy", "http://proxy.local:8080")
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("USER", "runner")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("TMPDIR", "/tmp/custom")
    home = tmp_path / "runtime-home"

    env = codex_clean_env(home)

    assert env["HOME"] == str(home)
    assert env["USERPROFILE"] == str(home)
    assert env["CODEX_HOME"] == str(home)
    assert env["LANG"] == "C.UTF-8"
    assert env["SSL_CERT_FILE"] == "/tmp/cert.pem"
    assert _env_value(env, "HTTPS_PROXY") == "http://proxy.local:8080"
    assert _env_value(env, "NO_PROXY") == "localhost"
    assert env["USER"] == "runner"
    assert env["SHELL"] == "/bin/sh"
    assert env["TMPDIR"] == "/tmp/custom"


def test_runtime_env_preserves_windows_vars_case_insensitively(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_runtime.os, "name", "nt")
    monkeypatch.setattr(codex_runtime.os, "name", "nt")
    monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
    monkeypatch.setenv("ComSpec", "C:\\Windows\\System32\\cmd.exe")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\me\\AppData\\Local")
    monkeypatch.setenv("ProgramData", "C:\\ProgramData")
    home = tmp_path / "runtime-home"

    env = claude_clean_env(home)

    assert env["HOME"] == str(home)
    assert env["USERPROFILE"] == str(home)
    assert env["CLAUDE_CONFIG_DIR"] == str(home / ".claude")
    assert _env_value(env, "SYSTEMROOT") == "C:\\Windows"
    assert _env_value(env, "COMSPEC") == "C:\\Windows\\System32\\cmd.exe"
    assert _env_value(env, "PATHEXT") == ".COM;.EXE;.BAT;.CMD"
    assert _env_value(env, "LOCALAPPDATA") == "C:\\Users\\me\\AppData\\Local"
    assert _env_value(env, "PROGRAMDATA") == "C:\\ProgramData"


def test_claude_print_command_includes_mcp_config_when_present(tmp_path):
    cli = tmp_path / "claude.cmd"
    settings_path = tmp_path / "settings.json"
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text("{}", encoding="utf-8")

    cmd = claude_build_print_cmd(
        cli,
        settings_path,
        mcp_path,
        "hi",
        "session-1",
        ["Read"],
        "sonnet",
        "low",
        "default",
    )

    assert "--mcp-config" in cmd
    assert "--verbose" in cmd
    assert "--include-partial-messages" not in cmd
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert cmd[cmd.index("--mcp-config") + 1] == str(mcp_path)
    assert "hi" not in cmd

    mcp_path.unlink()
    cmd = claude_build_print_cmd(cli, settings_path, mcp_path, "hi", None, None, None, None, "default")
    assert "--mcp-config" in cmd
    assert "hi" not in cmd


def test_claude_ask_user_plan_does_not_enter_claude_plan_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    captured = {}
    try:
        settings_path = tmp_path / "runtime" / "homes" / "_shared" / "claude_home" / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")

        class FakeRunner:
            async def check_available(self):
                return DockerSandboxStatus(ok=True)

            async def run(self, spec, *, on_stdout_line, cancel_event):
                captured["command"] = spec.command
                await on_stdout_line('{"message":{"content":[{"type":"text","text":"{\\"plan\\":{}}"}]}}')
                return DockerSandboxResult(return_code=0)

        monkeypatch.setattr(claude_runtime, "DockerSandboxRunner", lambda: FakeRunner())

        async def run():
            return await ClaudeAgentRuntime("user_admin").execute(
                prompt="生成 NL compile 方案",
                session_id=None,
                allowed_tools=None,
                model=None,
                reasoning_effort=None,
                cwd=tmp_path / "workspace",
                on_chunk=lambda chunk: _append_chunk([], chunk),
                cancel_event=asyncio.Event(),
                on_ask_user=lambda _request: None,
                runtime_policy="ask_user_plan",
            )

        result = asyncio.run(run())
    finally:
        get_settings.cache_clear()

    cmd = captured["command"]
    assert result.finished_with == "done"
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert "plan" not in cmd
    assert "--allowedTools" in cmd
    assert "mcp__ask_user__ask_user,ask_user" in cmd


def test_runtime_failure_detail_includes_stdout_lines():
    assert "stdout: Error: bad args" in claude_failure_detail("", [], ["Error: bad args"], 1)
    assert "stdout: Error: bad args" in codex_failure_detail("", [], ["Error: bad args"], 1)


def test_claude_permission_mode_reads_default_mode_before_legacy_permission_mode(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"defaultMode":"plan","permissionMode":"default"}', encoding="utf-8")
    assert claude_permission_mode(settings_path) == "plan"

    settings_path.write_text('{"permissionMode":"acceptEdits"}', encoding="utf-8")
    assert claude_permission_mode(settings_path) == "acceptEdits"

    settings_path.write_text("{bad", encoding="utf-8")
    assert claude_permission_mode(settings_path) == "default"


def test_codex_exec_command_includes_model_for_new_and_resumed_sessions(tmp_path):
    cli = tmp_path / "codex.exe"
    cwd = tmp_path / "workspace"
    assert codex_build_exec_cmd(cli, cwd, "hi", None, "gpt-5.2", "low") == [
        str(cli),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        str(cwd),
        "--model",
        "gpt-5.2",
        "-c",
        'model_reasoning_effort="low"',
        "-",
    ]
    assert codex_build_exec_cmd(cli, cwd, "again", "session-1", "gpt-5.2", "low") == [
        str(cli),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "resume",
        "--model",
        "gpt-5.2",
        "-c",
        'model_reasoning_effort="low"',
        "session-1",
        "-",
    ]


def test_codex_exec_command_injects_runtime_mcp_config_without_profile_v2(tmp_path):
    cli = tmp_path / "codex"
    cwd = tmp_path / "workspace"
    runtime_tools = RuntimeToolConfig(
        mcp_servers=[
            RuntimeMcpServerConfig(
                name="server name",
                url="http://localhost:9999/mcp",
                headers=[McpHeader(name="X-Test", value="1")],
            )
        ]
    )
    bridge = SimpleNamespace(url="http://host.docker.internal:8000/ask-user/session", token="secret-token")

    cmd = codex_build_exec_cmd(
        cli,
        cwd,
        "hi",
        None,
        None,
        None,
        runtime_tools=runtime_tools,
        bridge=bridge,
        runtime_policy="ask_user_plan",
    )

    assert "--profile-v2" not in cmd
    mcp_arg = cmd[cmd.index("-c") + 1]
    mcp_config = tomllib.loads(mcp_arg)["mcp_servers"]
    assert mcp_config["server name"]["url"] == "http://localhost:9999/mcp"
    assert mcp_config["server name"]["http_headers"]["X-Test"] == "1"
    assert mcp_config["ask_user"]["command"] == "python"
    assert mcp_config["ask_user"]["args"] == ["/opt/mira/ask_user_mcp_server.py"]
    assert mcp_config["ask_user"]["env"] == {
        "MIRA_ASK_USER_BRIDGE_URL": "http://host.docker.internal:8000/ask-user/session",
        "MIRA_ASK_USER_BRIDGE_TOKEN": "secret-token",
    }
    assert mcp_config["ask_user"]["default_tools_approval_mode"] == "approve"


def test_codex_command_log_redaction_hides_runtime_mcp_config():
    cmd = [
        "codex",
        "exec",
        "-c",
        'mcp_servers={ ask_user = { env = { MIRA_ASK_USER_BRIDGE_TOKEN = "secret" } } }',
        "-c",
        'model_reasoning_effort="low"',
    ]

    assert codex_runtime._redact_command_for_log(cmd) == [  # noqa: SLF001
        "codex",
        "exec",
        "-c",
        "mcp_servers=<redacted>",
        "-c",
        'model_reasoning_effort="low"',
    ]


def test_runtime_cli_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    get_settings.cache_clear()
    try:
        assert claude_cli_path().is_absolute()
        assert codex_cli_path().is_absolute()
        assert str(claude_cli_path()).startswith(str(tmp_path))
        assert str(codex_cli_path()).startswith(str(tmp_path))
    finally:
        get_settings.cache_clear()


def test_claude_cli_path_prefers_posix_npm_shim(tmp_path):
    runtime_root = tmp_path / "runtime"
    shim = runtime_root / "bin" / "claude" / "node_modules" / ".bin" / "claude"
    bundled = (
        runtime_root
        / "bin"
        / "claude"
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    shim.parent.mkdir(parents=True, exist_ok=True)
    bundled.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("", encoding="utf-8")
    bundled.write_text("", encoding="utf-8")

    assert _claude_cli_path_for_runtime(runtime_root, "posix") == shim.resolve()


def test_claude_cli_path_prefers_windows_cmd_shim(tmp_path):
    runtime_root = tmp_path / "runtime"
    cmd = runtime_root / "bin" / "claude" / "node_modules" / ".bin" / "claude.cmd"
    posix_shim = runtime_root / "bin" / "claude" / "node_modules" / ".bin" / "claude"
    cmd.parent.mkdir(parents=True, exist_ok=True)
    cmd.write_text("", encoding="utf-8")
    posix_shim.write_text("", encoding="utf-8")

    assert _claude_cli_path_for_runtime(runtime_root, "nt") == cmd.resolve()


def test_claude_cli_path_falls_back_to_current_package_binary(tmp_path):
    runtime_root = tmp_path / "runtime"
    bundled = (
        runtime_root
        / "bin"
        / "claude"
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_text("", encoding="utf-8")

    assert _claude_cli_path_for_runtime(runtime_root, "posix") == bundled.resolve()
