from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import tomli_w

from app.runtime.ask_user_bridge import InternalAskUserBridge
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus, RuntimePolicy
from app.runtime.call_context import RuntimeCallContext
from app.runtime.sandbox import CONTAINER_HOME, CONTAINER_WORKSPACE, DockerSandboxRunner, DockerSandboxSpec
from app.services.runtime_paths import codex_home, scoped_runtime_home
from app.services.skills_install import sync_runtime_skills
from app.services.tools import RuntimeToolConfig
from app.utils import now_utc

logger = logging.getLogger(__name__)

_COMMON_ENV_KEYS = {
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "NO_COLOR",
    "CI",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
}
_CONTAINER_PATH = "/home/mira/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
_WINDOWS_ENV_KEYS = {
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
}
_POSIX_ENV_KEYS = {
    "USER",
    "SHELL",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
}


class CodexCliRuntime:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.runner = DockerSandboxRunner()

    async def detect_status(self) -> AgentProviderStatus:
        status = await self.runner.check_available()
        if not status.ok:
            return AgentProviderStatus(
                installed=False,
                method="Docker sandbox + config.toml + auth.json",
                error=status.error,
                checked_at=now_utc(),
            )
        home = codex_home()
        if not (home / "config.toml").exists():
            return AgentProviderStatus(
                installed=False,
                method="Docker sandbox + config.toml + auth.json",
                error="未找到 Codex 配置文件",
                checked_at=now_utc(),
            )
        if not (home / "auth.json").exists():
            return AgentProviderStatus(
                installed=False,
                method="Docker sandbox + config.toml + auth.json",
                error="未找到 Codex auth.json 凭据",
                checked_at=now_utc(),
            )
        return AgentProviderStatus(
            installed=True,
            identity="Docker sandbox",
            method="Docker sandbox + config.toml + auth.json",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt,
        session_id,
        allowed_tools,
        model,
        reasoning_effort,
        cwd: Path,
        on_chunk,
        cancel_event,
        on_ask_user=None,
        runtime_tools: RuntimeToolConfig | None = None,
        runtime_policy: RuntimePolicy = "execute",
        output_schema: dict | None = None,
    ):
        status = await self.detect_status()
        if not status.installed:
            await on_chunk(AgentChunk(type="error", text=status.error))
            return AgentExecutionResult(finished_with="error", error=status.error)
        effective_runtime_tools = runtime_tools
        home = _prepare_scoped_home(codex_home(), cwd, effective_runtime_tools)
        env = _clean_env(CONTAINER_HOME)
        chunks: list[str] = []
        structured_outputs: list[str] = []
        new_session = session_id
        unhandled_error_lines: list[str] = []  # 看起来含错误但没被分支识别的原始行，便于失败诊断
        stdout_text_lines: list[str] = []
        try:
            async with RuntimeCallContext(
                user_id=self.user_id,
                workspace=cwd,
                home=home,
                on_ask_user=on_ask_user,
            ) as call:
                path_map = call.require_path_map()
                env.update(call.bridge_env())
                output_schema_path = None
                if output_schema is not None:
                    output_schema_path = call.call_dir / "output_schema.json"
                    output_schema_path.write_text(json.dumps(output_schema, ensure_ascii=False), encoding="utf-8")
                cmd = _build_exec_cmd(
                    Path("codex"),
                    CONTAINER_WORKSPACE,
                    prompt,
                    session_id,
                    model,
                    reasoning_effort,
                    effective_runtime_tools,
                    call.bridge,
                    runtime_policy,
                    output_schema_path=(
                        path_map.host_to_container_path(output_schema_path) if output_schema_path is not None else None
                    ),
                )

                async def on_stdout_line(line: str) -> None:
                    nonlocal new_session
                    raw = line.strip()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        if raw:
                            stdout_text_lines.append(raw)
                        data = {"type": "text", "content": raw}
                    new_session = _extract_session_id(data) or new_session
                    produced = _chunks_from_event(data)
                    for chunk in produced:
                        if chunk.text:
                            chunk.text = path_map.container_to_host_text(chunk.text)
                        if chunk.type == "text" and chunk.text:
                            chunks.append(chunk.text)
                            structured_output = _structured_output_text_from_chunk(chunk)
                            if output_schema is not None and structured_output:
                                structured_outputs.append(structured_output)
                        await on_chunk(chunk)
                    if not produced and _looks_like_error_event(data):
                        unhandled_error_lines.append(raw)
                run_result = await self.runner.run(
                    DockerSandboxSpec(
                        provider="codex",
                        command=cmd,
                        prompt=prompt,
                        env=env,
                        path_map=path_map,
                        prompt_path=call.prompt_path,
                    ),
                    on_stdout_line=on_stdout_line,
                    cancel_event=cancel_event,
                )
                stderr = run_result.stderr
                code = run_result.return_code
                if cancel_event.is_set():
                    return AgentExecutionResult(
                        session_id=new_session,
                        total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                        finished_with="cancelled",
                    )
                if code != 0:
                    detail = _format_failure_detail(stderr, unhandled_error_lines, stdout_text_lines, code)
                    logger.warning(
                        "Codex CLI failed: code=%s stderr=%r stdout=%s unhandled=%s cmd=%s",
                        code,
                        stderr.strip(),
                        stdout_text_lines[-3:],
                        unhandled_error_lines[-3:],
                        _redact_command_for_log(cmd),
                    )
                    await on_chunk(AgentChunk(type="error", text=detail))
                    return AgentExecutionResult(
                        session_id=new_session,
                        total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                        finished_with="error",
                        error=detail,
                    )
                return AgentExecutionResult(
                    session_id=new_session,
                    total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                    finished_with="done",
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Codex sandbox failed")
            detail = str(exc) or "Codex sandbox 执行失败"
            await on_chunk(AgentChunk(type="error", text=detail))
            return AgentExecutionResult(
                session_id=new_session,
                total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                finished_with="error",
                error=detail,
            )


def _clean_env(home: Path) -> dict[str, str]:
    keep = _COMMON_ENV_KEYS | (_WINDOWS_ENV_KEYS if os.name == "nt" else _POSIX_ENV_KEYS)
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in keep and not _is_sensitive_env_key(key)
    }
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["CODEX_HOME"] = str(home)
    env["PATH"] = _CONTAINER_PATH
    return env


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return upper.endswith("_API_KEY") or upper.endswith("_TOKEN")


def _prepare_scoped_home(
    shared_home: Path,
    cwd: Path,
    runtime_tools: RuntimeToolConfig | None,
) -> Path:
    home = scoped_runtime_home("codex_home", cwd)
    home.mkdir(parents=True, exist_ok=True)
    for filename in ("config.toml", "auth.json"):
        source = shared_home / filename
        target = home / filename
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    sync_runtime_skills(runtime_tools.skills if runtime_tools else [], home / ".agents" / "skills")
    return home


def _build_exec_cmd(
    cli: Path,
    cwd: Path,
    prompt: str,
    session_id: str | None,
    model: str | None,
    reasoning_effort: str | None,
    runtime_tools: RuntimeToolConfig | None = None,
    bridge: InternalAskUserBridge | None = None,
    runtime_policy: RuntimePolicy = "execute",
    output_schema_path: Path | None = None,
) -> list[str]:
    cmd = [str(cli), "exec", "--json"]
    cmd.append("--skip-git-repo-check")
    mcp_config = _runtime_mcp_config(
        runtime_tools,
        bridge,
        auto_approve=runtime_policy == "ask_user_plan",
    )
    if mcp_config:
        cmd.extend(["-c", f"mcp_servers={_toml_inline_value(mcp_config)}"])
    if runtime_policy == "ask_user_plan":
        cmd.extend(["--sandbox", "read-only"])
        cmd.extend(_ask_user_plan_config_args())
    else:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    if output_schema_path is not None:
        cmd.extend(["--output-schema", str(output_schema_path)])
    if session_id:
        cmd.append("resume")
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        cmd.extend([session_id, "-"])
        return cmd
    cmd.extend(["--cd", str(cwd)])
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    cmd.append("-")
    return cmd


def _ask_user_plan_config_args() -> list[str]:
    return ["-c", 'approval_policy="never"']


def _runtime_mcp_config(
    runtime_tools: RuntimeToolConfig | None,
    bridge: InternalAskUserBridge | None,
    *,
    auto_approve: bool,
) -> dict[str, Any]:
    mcp_servers: dict[str, Any] = {}
    if runtime_tools is not None:
        for server in runtime_tools.mcp_servers:
            server_config: dict[str, object] = {
                "url": server.url,
            }
            if server.headers:
                server_config["http_headers"] = {header.name: header.value for header in server.headers}
            mcp_servers[server.name] = server_config
    if bridge is not None:
        server_config = {
            "command": "python",
            "args": ["/opt/mira/ask_user_mcp_server.py"],
            "env": {
                "MIRA_ASK_USER_BRIDGE_URL": bridge.url,
                "MIRA_ASK_USER_BRIDGE_TOKEN": bridge.token,
            },
        }
        if auto_approve:
            server_config["default_tools_approval_mode"] = "approve"
        mcp_servers["ask_user"] = server_config
    return mcp_servers


def _toml_inline_value(value: Any) -> str:
    if isinstance(value, dict):
        items = [f"{_toml_key(key)} = {_toml_inline_value(item)}" for key, item in value.items()]
        return "{ " + ", ".join(items) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline_value(item) for item in value) + "]"
    return tomli_w.dumps({"value": value}).split("=", 1)[1].strip()


def _toml_key(key: str) -> str:
    if key and all(char.isalnum() or char in {"_", "-"} for char in key):
        return key
    return _toml_inline_value(key)


def _extract_session_id(data: dict) -> str | None:
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    nested = data.get("message") or data.get("data") or data.get("thread")
    if isinstance(nested, dict):
        return _extract_session_id(nested)
    return None


def _chunks_from_event(data: dict) -> list[AgentChunk]:
    chunks: list[AgentChunk] = []
    event_type = str(data.get("type") or data.get("event") or "")
    text = data.get("content") or data.get("delta") or data.get("text")
    if isinstance(text, str) and text:
        chunks.append(AgentChunk(type="text", text=text, raw=data))
    item = data.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "")
        item_text = item.get("text") or item.get("content")
        if item_type == "agent_message" and isinstance(item_text, str) and item_text:
            chunks.append(AgentChunk(type="text", text=item_text, raw=data))
        elif "command" in item_type or "tool" in item_type:
            command = item.get("command") or item.get("name") or item_type
            if str(data.get("type") or "").endswith(".started"):
                chunks.append(AgentChunk(type="tool_call", text=str(command), raw=data))
            elif str(data.get("type") or "").endswith(".completed"):
                output = item.get("aggregated_output") or item.get("output") or command
                chunks.append(AgentChunk(type="tool_result", text=str(output), raw=data))
    if "tool" in event_type and ("call" in event_type or "use" in event_type):
        name = data.get("name") or data.get("tool") or event_type
        chunks.append(AgentChunk(type="tool_call", text=str(name), raw=data))
    elif "tool" in event_type and "result" in event_type:
        output = data.get("output") or data.get("content") or event_type
        chunks.append(AgentChunk(type="tool_result", text=str(output), raw=data))
    elif "error" in event_type:
        chunks.append(AgentChunk(type="error", text=str(data.get("error") or data), raw=data))
    return chunks


def _structured_output_text_from_chunk(chunk: AgentChunk) -> str | None:
    if chunk.type != "text" or not chunk.text:
        return None
    try:
        parsed = json.loads(chunk.text.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return json.dumps(parsed, ensure_ascii=False)


def _looks_like_error_event(data: dict) -> bool:
    """启发式：识别 stream-json 中可能藏错误信息但未被 _chunks_from_event 抓住的帧。"""
    if data.get("is_error") is True:
        return True
    event_type = str(data.get("type") or data.get("event") or "").lower()
    if "error" in event_type or "fail" in event_type:
        return True
    if data.get("error"):
        return True
    return False


def _format_failure_detail(stderr: str, unhandled: list[str], stdout_lines: list[str], code: int) -> str:
    parts: list[str] = []
    cleaned = stderr.strip()
    if cleaned:
        parts.append(cleaned)
    if stdout_lines:
        parts.append("stdout: " + " | ".join(stdout_lines[-2:]))
    if unhandled:
        parts.append("最近事件: " + " | ".join(unhandled[-2:]))
    if not parts:
        parts.append(f"Codex exited {code}")
    return "\n".join(parts)


def _redact_command_for_log(cmd: list[str]) -> list[str]:
    redacted: list[str] = []
    for item in cmd:
        if item.startswith("mcp_servers="):
            redacted.append("mcp_servers=<redacted>")
        else:
            redacted.append(item)
    return redacted
