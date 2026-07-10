from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from pathlib import Path

from app.runtime.ask_user_bridge import InternalAskUserBridge
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus, RuntimePolicy
from app.runtime.call_context import RuntimeCallContext
from app.runtime.sandbox import CONTAINER_HOME, DockerSandboxRunner, DockerSandboxSpec
from app.services.runtime_paths import claude_home, scoped_runtime_home
from app.services.skills_install import sync_runtime_skills
from app.services.tools import RuntimeToolConfig
from app.utils import now_utc

logger = logging.getLogger(__name__)

_ASK_USER_TOOL_NAMES = ["mcp__ask_user__ask_user", "ask_user"]

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


class ClaudeAgentRuntime:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.runner = DockerSandboxRunner()

    async def detect_status(self) -> AgentProviderStatus:
        status = await self.runner.check_available()
        if not status.ok:
            return AgentProviderStatus(
                installed=False,
                method="Docker sandbox + settings.json",
                error=status.error,
                checked_at=now_utc(),
            )
        settings_path = claude_home() / ".claude" / "settings.json"
        if not settings_path.exists():
            return AgentProviderStatus(
                installed=False,
                method="Docker sandbox + settings.json",
                error="未找到 Claude 配置文件",
                checked_at=now_utc(),
            )
        return AgentProviderStatus(
            installed=True,
            identity="Docker sandbox",
            method="Docker sandbox + settings.json",
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
        home = _prepare_scoped_home(claude_home(), cwd, effective_runtime_tools)
        settings_path = home / ".claude" / "settings.json"
        env = _clean_env(CONTAINER_HOME)
        permission_mode = "default" if runtime_policy == "ask_user_plan" else "bypassPermissions"
        chunks: list[str] = []
        structured_outputs: list[str] = []
        new_session_id = session_id
        structured_output_requested_stop = False
        unhandled_error_lines: list[str] = []  # stream-json 里看起来含错误但没被分支识别的原始行，便于失败诊断
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
                _prepare_ask_user_plan_settings(settings_path, call.bridge, runtime_policy)
                temp_mcp_config = _write_mcp_config(effective_runtime_tools, call.bridge, call.call_dir)
                cmd = _build_print_cmd(
                    Path("claude"),
                    path_map.host_to_container_path(settings_path),
                    path_map.host_to_container_path(temp_mcp_config),
                    prompt,
                    session_id,
                    _allowed_tools_for_policy(allowed_tools, call.bridge, runtime_policy),
                    model,
                    reasoning_effort,
                    permission_mode,
                    output_schema,
                )
                runner_cancel_event = asyncio.Event()

                async def mirror_external_cancel() -> None:
                    await cancel_event.wait()
                    runner_cancel_event.set()

                mirror_task = asyncio.create_task(mirror_external_cancel())

                async def on_stdout_line(line: str) -> None:
                    nonlocal new_session_id, structured_output_requested_stop
                    raw = line.strip()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        if raw:
                            stdout_text_lines.append(raw)
                            chunks.append(raw)
                            await on_chunk(AgentChunk(type="text", text=raw))
                        return
                    new_session_id = _extract_session_id(data) or new_session_id
                    produced = _chunks_from_event(data)
                    for chunk in produced:
                        if chunk.text:
                            chunk.text = path_map.container_to_host_text(chunk.text)
                        if chunk.type == "text" and chunk.text:
                            chunks.append(chunk.text)
                        structured_output = _structured_output_text_from_chunk(chunk)
                        if output_schema is not None and structured_output:
                            structured_outputs.append(path_map.container_to_host_text(structured_output))
                            structured_output_requested_stop = True
                            runner_cancel_event.set()
                        await on_chunk(chunk)
                    if not produced and _looks_like_error_event(data):
                        unhandled_error_lines.append(raw)
                try:
                    run_result = await self.runner.run(
                        DockerSandboxSpec(
                            provider="claude",
                            command=cmd,
                            prompt=prompt,
                            env=env,
                            path_map=path_map,
                            prompt_path=call.prompt_path,
                        ),
                        on_stdout_line=on_stdout_line,
                        cancel_event=runner_cancel_event,
                    )
                finally:
                    mirror_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await mirror_task
                stderr = run_result.stderr
                code = run_result.return_code
                if cancel_event.is_set():
                    return AgentExecutionResult(
                        session_id=new_session_id,
                        total_text="".join(chunks),
                        finished_with="cancelled",
                    )
                if structured_output_requested_stop and structured_outputs:
                    return AgentExecutionResult(
                        session_id=new_session_id,
                        total_text=structured_outputs[-1],
                        finished_with="done",
                    )
                if code != 0:
                    detail = _format_failure_detail(stderr, unhandled_error_lines, stdout_text_lines, code)
                    logger.warning(
                        "Claude CLI failed: code=%s stderr=%r stdout=%s unhandled=%s cmd=%s",
                        code,
                        stderr.strip(),
                        stdout_text_lines[-3:],
                        unhandled_error_lines[-3:],
                        cmd,
                    )
                    await on_chunk(AgentChunk(type="error", text=detail))
                    return AgentExecutionResult(
                        session_id=new_session_id,
                        total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                        finished_with="error",
                        error=detail,
                    )
                return AgentExecutionResult(
                    session_id=new_session_id,
                    total_text=structured_outputs[-1] if structured_outputs else "".join(chunks),
                    finished_with="done",
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Claude sandbox failed")
            detail = str(exc) or "Claude sandbox 执行失败"
            await on_chunk(AgentChunk(type="error", text=detail))
            return AgentExecutionResult(session_id=new_session_id, total_text="".join(chunks), finished_with="error", error=detail)


def _clean_env(home: Path) -> dict[str, str]:
    keep = _COMMON_ENV_KEYS | (_WINDOWS_ENV_KEYS if os.name == "nt" else _POSIX_ENV_KEYS)
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in keep and not _is_sensitive_env_key(key)
    }
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    env["PATH"] = _CONTAINER_PATH
    return env


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return upper.endswith("_API_KEY") or upper.endswith("_TOKEN")


def _prepare_scoped_home(shared_home: Path, cwd: Path, runtime_tools: RuntimeToolConfig | None) -> Path:
    home = scoped_runtime_home("claude_home", cwd)
    settings_dir = home / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    source_settings = shared_home / ".claude" / "settings.json"
    target_settings = settings_dir / "settings.json"
    if source_settings.exists():
        shutil.copy2(source_settings, target_settings)
    elif not target_settings.exists():
        target_settings.write_text("{}", encoding="utf-8")
    sync_runtime_skills(runtime_tools.skills if runtime_tools else [], settings_dir / "skills")
    return home


def _permission_mode_from_settings(settings_path: Path) -> str:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return "default"
    if not isinstance(data, dict):
        return "default"
    value = data.get("defaultMode") or data.get("permissionMode") or "default"
    return value if isinstance(value, str) and value else "default"


def _build_print_cmd(
    cli: Path,
    settings_path: Path,
    mcp_config_path: Path,
    prompt: str,
    session_id: str | None,
    allowed_tools: list[str] | None,
    model: str | None,
    reasoning_effort: str | None,
    permission_mode: str,
    output_schema: dict | None = None,
) -> list[str]:
    cmd = [
        str(cli),
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--settings",
        str(settings_path),
        "--setting-sources",
        "project",
        "--permission-mode",
        permission_mode,
    ]
    cmd.extend(["--mcp-config", str(mcp_config_path)])
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        cmd.extend(["--effort", reasoning_effort])
    if output_schema is not None:
        cmd.extend(["--json-schema", json.dumps(output_schema, ensure_ascii=False, separators=(",", ":"))])
    if session_id:
        cmd.extend(["--resume", session_id])
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])
    return cmd


def _write_mcp_config(
    runtime_tools: RuntimeToolConfig | None,
    bridge: InternalAskUserBridge | None,
    call_dir: Path,
) -> Path:
    config: dict = {"mcpServers": {}}
    if runtime_tools is not None:
        for server in runtime_tools.mcp_servers:
            config["mcpServers"][server.name] = {
                "type": "http",
                "url": server.url,
                "headers": {header.name: header.value for header in server.headers},
            }
    if bridge is not None:
        config["mcpServers"]["ask_user"] = {
            "type": "stdio",
            "command": "python",
            "args": ["/opt/mira/ask_user_mcp_server.py"],
            "env": {
                "MIRA_ASK_USER_BRIDGE_URL": bridge.url,
                "MIRA_ASK_USER_BRIDGE_TOKEN": bridge.token,
            },
        }
    path = call_dir / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def _ask_user_mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "ask_user_mcp_server.py"


def _prepare_ask_user_plan_settings(
    settings_path: Path,
    bridge: InternalAskUserBridge | None,
    runtime_policy: RuntimePolicy,
) -> None:
    if runtime_policy != "ask_user_plan" or bridge is None:
        return
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return
    if not isinstance(data, dict):
        data = {}
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        data["permissions"] = permissions
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        allow = []
    else:
        allow = list(allow)
    for rule in ("mcp__ask_user", *_ASK_USER_TOOL_NAMES):
        if rule not in allow:
            allow.append(rule)
    permissions["allow"] = allow
    ask = permissions.get("ask")
    if isinstance(ask, list):
        permissions["ask"] = [rule for rule in ask if not _asks_for_internal_ask_user(rule)]
    settings_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _asks_for_internal_ask_user(rule: object) -> bool:
    if not isinstance(rule, str):
        return False
    normalized = rule.strip()
    return normalized in {
        "*",
        "mcp__*",
        "mcp__ask_user",
        "mcp__ask_user__*",
        "mcp__ask_user__ask_user",
        "ask_user",
    }


def _allowed_tools_for_policy(
    allowed_tools: list[str] | None,
    bridge: InternalAskUserBridge | None,
    runtime_policy: RuntimePolicy,
) -> list[str] | None:
    if runtime_policy == "ask_user_plan":
        return _allowed_tools_with_ask_user(allowed_tools) if bridge is not None else allowed_tools
    if bridge is None:
        return allowed_tools
    return _allowed_tools_with_ask_user(allowed_tools)


def _allowed_tools_with_ask_user(allowed_tools: list[str] | None) -> list[str] | None:
    if allowed_tools is None:
        return list(_ASK_USER_TOOL_NAMES)
    tools = list(allowed_tools)
    for name in _ASK_USER_TOOL_NAMES:
        if name not in tools:
            tools.append(name)
    return tools


def _extract_session_id(data: dict) -> str | None:
    for key in ("session_id", "sessionId"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    nested = data.get("message") or data.get("data")
    if isinstance(nested, dict):
        return _extract_session_id(nested)
    return None


def _structured_output_text_from_chunk(chunk: AgentChunk) -> str | None:
    raw = chunk.raw
    if chunk.type == "tool_call" and isinstance(raw, dict):
        name = raw.get("name")
        if name != "StructuredOutput":
            return None
        payload = raw.get("input")
        if not isinstance(payload, dict) or not payload:
            return None
        return json.dumps(payload, ensure_ascii=False)
    if chunk.type == "text" and chunk.text:
        return _structured_output_text_from_pseudo_tool_call(chunk.text)
    return None


def _structured_output_text_from_pseudo_tool_call(text: str) -> str | None:
    if "StructuredOutput" not in text or "<parameter=" not in text:
        return None
    match = re.search(r"<function=StructuredOutput>\s*(.*?)\s*</function>", text, flags=re.DOTALL)
    if not match:
        return None
    payload: dict[str, object] = {}
    for name, raw_value in re.findall(
        r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</parameter>",
        match.group(1),
        flags=re.DOTALL,
    ):
        payload[name] = _coerce_structured_parameter(raw_value)
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False)


def _coerce_structured_parameter(value: str) -> object:
    cleaned = value.strip()
    if cleaned.lower() in {"none", "null"}:
        return None
    if cleaned.startswith(("{", "[", '"')) or cleaned.lower() in {"true", "false"}:
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
    return cleaned


def _chunks_from_event(data: dict) -> list[AgentChunk]:
    chunks: list[AgentChunk] = []
    event = data.get("event")
    if isinstance(event, dict):
        event_chunks = _chunks_from_event(event)
        for chunk in event_chunks:
            if chunk.raw is None:
                chunk.raw = data
        return event_chunks
    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return [AgentChunk(type="text", text=content, raw=data)]
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    chunks.append(AgentChunk(type="text", text=block["text"], raw=block))
                elif block_type in {"tool_use", "tool_call"}:
                    name = block.get("name") or block.get("id") or "tool"
                    chunks.append(AgentChunk(type="tool_call", text=str(name), raw=block))
                elif block_type == "tool_result":
                    output = block.get("content") or block.get("text") or "tool_result"
                    chunks.append(AgentChunk(type="tool_result", text=str(output), raw=block))
            if chunks:
                return chunks
    delta = data.get("delta")
    if isinstance(delta, dict):
        delta_text = delta.get("text")
        if isinstance(delta_text, str):
            return [AgentChunk(type="text", text=delta_text, raw=data)]
    text = data.get("text") or data.get("delta") or data.get("content")
    if isinstance(text, str):
        chunks.append(AgentChunk(type="text", text=text, raw=data))
    if data.get("type") == "error":
        chunks.append(AgentChunk(type="error", text=str(data.get("error") or data), raw=data))
    return chunks


def _looks_like_error_event(data: dict) -> bool:
    """启发式：识别 stream-json 中可能藏错误信息但未被 _chunks_from_event 抓住的帧。

    例如 Claude CLI 在 result/system 帧里通过 is_error / subtype / error 字段
    报告认证失败、无效模型等，stderr 又恰好为空。把这类原始行收下来，便于失败时打日志。
    """
    if data.get("is_error") is True:
        return True
    subtype = str(data.get("subtype") or "").lower()
    if "error" in subtype:
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
        # 只保留最后两条，避免把整个 stream 灌给前端。
        parts.append("最近事件: " + " | ".join(unhandled[-2:]))
    if not parts:
        parts.append(f"Claude exited {code}")
    return "\n".join(parts)
