from __future__ import annotations

import asyncio
import json
import logging
import os
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Any

import tomli_w

from app.runtime.base import (
    AgentChunk,
    AgentExecutionResult,
    AgentRuntimeStatus,
    DecisionCallback,
    DecisionRequest,
    DecisionResult,
    RuntimePolicy,
)
from app.runtime.sandbox import (
    CONTAINER_HOME,
    CONTAINER_WORKSPACE,
    DockerBindMount,
    DockerSandboxReply,
    DockerSandboxRunner,
    DockerSandboxSpec,
    RuntimePathMap,
)
from app.schemas.decision import DecisionGroup, DecisionOption, DecisionRequestContext
from app.services.runtime_paths import codex_home, scoped_codex_home
from app.services.runtime_uploads import current_runtime_upload_context, stage_decision_request_result_for_runtime
from app.services.skills_install import sync_runtime_skills
from app.services.tools import RuntimeToolConfig
from app.services.wiki import runtime_wiki_mount
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
_INITIALIZE_REQUEST_ID = 1
_THREAD_REQUEST_ID = 2
_TURN_REQUEST_ID = 3


class CodexRuntime:
    def __init__(self):
        self.runner = DockerSandboxRunner()

    async def detect_status(self) -> AgentRuntimeStatus:
        status = await self.runner.check_available()
        if not status.ok:
            return AgentRuntimeStatus(
                installed=False,
                method="Docker sandbox + Codex App Server",
                error=status.error,
                checked_at=now_utc(),
            )
        home = codex_home()
        if not (home / "config.toml").exists():
            return AgentRuntimeStatus(
                installed=False,
                method="Docker sandbox + Codex App Server",
                error="未找到 Codex config.toml",
                checked_at=now_utc(),
            )
        if not (home / "auth.json").exists():
            return AgentRuntimeStatus(
                installed=False,
                method="Docker sandbox + Codex App Server",
                error="未找到 Codex auth.json 凭据",
                checked_at=now_utc(),
            )
        return AgentRuntimeStatus(
            installed=True,
            identity="Codex App Server",
            method="Docker sandbox + Codex App Server",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_decision_request: DecisionCallback | None = None,
        runtime_tools: RuntimeToolConfig | None = None,
        runtime_policy: RuntimePolicy = "execute",
        output_schema: dict | None = None,
        session_scope: str | None = None,
        fork_session: bool = False,
    ) -> AgentExecutionResult:
        status = await self.detect_status()
        if not status.installed:
            await on_chunk(AgentChunk(type="error", text=status.error))
            return AgentExecutionResult(finished_with="error", error=status.error)

        cwd.mkdir(parents=True, exist_ok=True)
        home, skill_mounts = _prepare_scoped_home(
            codex_home(), cwd, runtime_tools, session_scope=session_scope
        )
        wiki_mounts: tuple[DockerBindMount, ...] = ()
        if isinstance(session_scope, str) and session_scope.startswith("run:"):
            try:
                wiki_tree = runtime_wiki_mount(session_scope.removeprefix("run:"))
            except Exception as exc:  # noqa: BLE001
                detail = str(exc) or "Run Wiki 快照不可用"
                await on_chunk(AgentChunk(type="error", text=detail))
                return AgentExecutionResult(session_id=session_id, finished_with="error", error=detail)
            if wiki_tree is not None:
                wiki_mounts = (DockerBindMount(source=wiki_tree, target=Path("/mnt/wiki"), read_only=True),)
                prompt = (
                    prompt.rstrip()
                    + "\n\n## Mira Wiki（只读）\n"
                    + "当前用户的冻结 Wiki 挂载在 /mnt/wiki。仅在任务需要时使用 find、rg、sed 等读取；"
                    + "任务生成内容必须写入 /workspace，绝不能尝试回写 /mnt/wiki。"
                )
        path_map = RuntimePathMap.for_call(workspace=cwd, home=home)
        effective_model = (model or "").strip() or _configured_model(home)
        if runtime_policy == "plan" and not effective_model:
            detail = "Codex Plan 模式需要配置模型"
            await on_chunk(AgentChunk(type="error", text=detail))
            return AgentExecutionResult(session_id=session_id, finished_with="error", error=detail)

        thread_request = _thread_request(
            session_id=session_id,
            model=effective_model,
            runtime_policy=runtime_policy,
            fork_session=fork_session,
        )
        initial_input = _jsonl(
            {
                "id": _INITIALIZE_REQUEST_ID,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "mira", "title": "Mira", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            },
            {"method": "initialized", "params": {}},
            thread_request,
        )
        chunks: list[str] = []
        final_messages: list[str] = []
        errors: list[str] = []
        active_thread_id = session_id
        turn_status: str | None = None
        sandbox_cancel_event = asyncio.Event()

        async def mirror_cancel() -> None:
            await cancel_event.wait()
            sandbox_cancel_event.set()

        cancel_mirror = asyncio.create_task(mirror_cancel())

        async def on_stdout_line(line: str) -> DockerSandboxReply | None:
            nonlocal active_thread_id, turn_status
            raw = line.strip()
            if not raw:
                return None
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                errors.append(f"App Server 返回了非 JSON 输出：{raw[:500]}")
                return None
            if not isinstance(message, dict):
                return None

            response_id = message.get("id")
            is_response = "method" not in message
            if is_response and response_id == _INITIALIZE_REQUEST_ID and message.get("error"):
                errors.append(_rpc_error_text(message["error"]))
                return DockerSandboxReply(complete=True)
            if is_response and response_id == _THREAD_REQUEST_ID:
                if message.get("error"):
                    errors.append(_rpc_error_text(message["error"]))
                    return DockerSandboxReply(complete=True)
                active_thread_id = _thread_id_from_result(message.get("result")) or active_thread_id
                if not active_thread_id:
                    errors.append("Codex App Server 未返回 thread id")
                    return DockerSandboxReply(complete=True)
                await on_chunk(
                    AgentChunk(
                        type="session",
                        raw={"thread": {"id": active_thread_id}},
                    )
                )
                return DockerSandboxReply(
                    input=_jsonl(
                        _turn_request(
                            thread_id=active_thread_id,
                            prompt=prompt,
                            model=effective_model,
                            reasoning_effort=reasoning_effort,
                            runtime_policy=runtime_policy,
                            output_schema=output_schema,
                        )
                    )
                )
            if is_response and response_id == _TURN_REQUEST_ID and message.get("error"):
                errors.append(_rpc_error_text(message["error"]))
                return DockerSandboxReply(complete=True)

            method = str(message.get("method") or "")
            params = message.get("params")
            if method == "item/tool/requestUserInput":
                response = await _request_user_input_response(
                    message,
                    on_decision_request,
                    sandbox_cancel_event,
                )
                if sandbox_cancel_event.is_set():
                    return DockerSandboxReply(complete=True)
                return DockerSandboxReply(
                    input=response
                )
            if method == "thread/started" and isinstance(params, dict):
                active_thread_id = _thread_id_from_result(params) or active_thread_id
                return None
            if method == "item/agentMessage/delta" and isinstance(params, dict):
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    chunks.append(delta)
                    await on_chunk(AgentChunk(type="text", text=delta, raw=message))
                return None
            if method == "item/completed" and isinstance(params, dict):
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        final_messages.append(text)
                else:
                    chunk = _chunk_from_completed_item(item, message)
                    if chunk is not None:
                        await on_chunk(chunk)
                return None
            if method == "error" and isinstance(params, dict):
                errors.append(_notification_error_text(params))
                return None
            if method == "turn/completed" and isinstance(params, dict):
                turn = params.get("turn")
                if isinstance(turn, dict):
                    turn_status = str(turn.get("status") or "")
                    if turn.get("error"):
                        errors.append(_rpc_error_text(turn["error"]))
                return DockerSandboxReply(complete=True)
            return None

        try:
            try:
                run_result = await self.runner.run_interactive(
                    DockerSandboxSpec(
                        command=["codex", "app-server"],
                        prompt=initial_input,
                        env=_clean_env(CONTAINER_HOME),
                        path_map=path_map,
                        mounts=skill_mounts + wiki_mounts,
                        workspace_read_only=runtime_policy == "plan",
                    ),
                    on_stdout_line=on_stdout_line,
                    cancel_event=sandbox_cancel_event,
                )
            finally:
                cancel_mirror.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_mirror
        except Exception as exc:  # noqa: BLE001
            logger.exception("Codex App Server sandbox failed")
            detail = str(exc) or "Codex App Server 执行失败"
            await on_chunk(AgentChunk(type="error", text=detail))
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=final_messages[-1] if final_messages else "".join(chunks),
                finished_with="error",
                error=detail,
            )

        total_text = final_messages[-1] if final_messages else "".join(chunks)
        total_text = path_map.container_to_host_text(total_text)
        total_text = _final_codex_text(home, active_thread_id, total_text, path_map)
        if cancel_event.is_set() or run_result.return_code == 130 or turn_status == "interrupted":
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=total_text,
                finished_with="cancelled",
            )
        if run_result.return_code != 0 or errors or turn_status not in {None, "completed"}:
            detail = errors[-1] if errors else run_result.stderr.strip()
            if not detail:
                detail = f"Codex App Server 执行失败（turn status: {turn_status or 'unknown'}）"
            logger.warning(
                "Codex App Server failed: code=%s turn_status=%s error=%r",
                run_result.return_code,
                turn_status,
                detail,
            )
            await on_chunk(AgentChunk(type="error", text=detail))
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=total_text,
                finished_with="error",
                error=detail,
            )
        return AgentExecutionResult(
            session_id=active_thread_id,
            total_text=total_text,
            finished_with="done",
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
    *,
    session_scope: str | None = None,
) -> tuple[Path, tuple[DockerBindMount, ...]]:
    home = scoped_codex_home(cwd, session_scope=session_scope)
    home.mkdir(parents=True, exist_ok=True)
    for filename in ("config.toml", "auth.json"):
        source = shared_home / filename
        target = home / filename
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    config_path = home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        config = {}
    mcp_config = _runtime_mcp_config(runtime_tools)
    if mcp_config:
        config["mcp_servers"] = mcp_config
    else:
        config.pop("mcp_servers", None)
    config_path.write_text(tomli_w.dumps(config), encoding="utf-8")
    skill_mounts = sync_runtime_skills(
        runtime_tools.skills if runtime_tools else [],
        home / ".agents" / "skills",
    )
    return home, skill_mounts


def _configured_model(home: Path) -> str | None:
    try:
        data = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("model")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _thread_request(
    *,
    session_id: str | None,
    model: str | None,
    runtime_policy: RuntimePolicy,
    fork_session: bool,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "cwd": str(CONTAINER_WORKSPACE),
        "approvalPolicy": "never",
        "sandbox": "read-only" if runtime_policy == "plan" else "danger-full-access",
    }
    if model:
        params["model"] = model
    if session_id:
        params["threadId"] = session_id
        method = "thread/fork" if fork_session else "thread/resume"
    else:
        if fork_session:
            raise ValueError("fork_session 需要已有 Codex session_id")
        method = "thread/start"
    return {"id": _THREAD_REQUEST_ID, "method": method, "params": params}


def _turn_request(
    *,
    thread_id: str,
    prompt: str,
    model: str | None,
    reasoning_effort: str | None,
    runtime_policy: RuntimePolicy,
    output_schema: dict | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "approvalPolicy": "never",
        "sandboxPolicy": (
            {"type": "externalSandbox", "networkAccess": "restricted"}
            if runtime_policy == "plan"
            else {"type": "dangerFullAccess"}
        ),
    }
    if model:
        params["model"] = model
    if reasoning_effort:
        params["effort"] = reasoning_effort
    if output_schema is not None:
        params["outputSchema"] = output_schema
    if runtime_policy == "plan":
        params["collaborationMode"] = {
            "mode": "plan",
            "settings": {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "developer_instructions": None,
            },
        }
    return {"id": _TURN_REQUEST_ID, "method": "turn/start", "params": params}


def _runtime_mcp_config(runtime_tools: RuntimeToolConfig | None) -> dict[str, Any]:
    if runtime_tools is None:
        return {}
    servers: dict[str, Any] = {}
    for server in runtime_tools.mcp_servers:
        config: dict[str, object] = {"url": server.url}
        if server.headers:
            config["http_headers"] = {header.name: header.value for header in server.headers}
        servers[server.name] = config
    return servers

async def _request_user_input_response(
    message: dict[str, Any],
    callback: DecisionCallback | None,
    cancel_event: asyncio.Event,
) -> str:
    request_id = message.get("id")
    if callback is None:
        return _jsonl(_jsonrpc_error(request_id, "Mira 当前流程不接受用户提问"))
    try:
        request = _normalize_request_user_input(message.get("params"))
    except ValueError as exc:
        return _jsonl(_jsonrpc_error(request_id, str(exc)))
    callback_task = asyncio.create_task(callback(request))
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait(
        {callback_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cancel_task in done:
        callback_task.cancel()
        with suppress(asyncio.CancelledError):
            await callback_task
        return _jsonl(_jsonrpc_error(request_id, "运行已取消"))
    cancel_task.cancel()
    with suppress(asyncio.CancelledError):
        await cancel_task
    result = await callback_task
    upload_context = current_runtime_upload_context()
    if upload_context is not None:
        result = stage_decision_request_result_for_runtime(upload_context, result)
    if not result.ok:
        return _jsonl(_jsonrpc_error(request_id, result.error or "用户输入未通过校验"))
    return _jsonl({"id": request_id, "result": _native_answers(request, result)})


def _normalize_request_user_input(params: Any) -> DecisionRequest:
    if not isinstance(params, dict):
        raise ValueError("request_user_input 缺少 params")
    questions = params.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("request_user_input 必须包含问题")
    groups: list[DecisionGroup] = []
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("request_user_input 问题格式无效")
        if question.get("isSecret") is True:
            raise ValueError("Mira 不通过运行提问收集密钥或其他敏感信息")
        question_id = str(question.get("id") or "").strip()
        label = str(question.get("header") or "").strip()
        text = str(question.get("question") or "").strip()
        options = question.get("options")
        if not question_id or not label or not text:
            raise ValueError("request_user_input 问题缺少 id、header 或 question")
        if not isinstance(options, list) or not 2 <= len(options) <= 3:
            raise ValueError("Mira 的 request_user_input 问题必须提供 2-3 个选项")
        normalized_options: list[DecisionOption] = []
        for index, option in enumerate(options):
            if not isinstance(option, dict):
                raise ValueError("request_user_input 选项格式无效")
            option_label = str(option.get("label") or "").strip()
            description = str(option.get("description") or "").strip()
            if not option_label or not description:
                raise ValueError("request_user_input 选项缺少 label 或 description")
            normalized_options.append(
                DecisionOption(
                    label=option_label,
                    description=description,
                    recommended=index == 0,
                )
            )
        groups.append(
            DecisionGroup(
                id=question_id,
                label=text,
                type="single",
                options=normalized_options,
            )
        )
    first = questions[0]
    title = _clip(str(first.get("header") or "需要补充信息"), 80)
    summary = _clip("；".join(str(item.get("question") or "").strip() for item in questions), 240)
    return DecisionRequest(
        context=DecisionRequestContext(title=title, summary=summary),
        groups=groups,
        request_id=str(params.get("itemId") or "request_user_input"),
    )


def _native_answers(request: DecisionRequest, result: DecisionResult) -> dict[str, Any]:
    selected_by_group = {answer.group_id: list(answer.selected) for answer in result.answers}
    extras: list[str] = []
    if result.text:
        extras.append(result.text)
    for attachment in result.attachments:
        if attachment.path:
            extras.append(f"附件 {attachment.name}: {attachment.path}")
        else:
            extras.append(f"附件: {attachment.name}")
    answers: dict[str, dict[str, list[str]]] = {}
    for index, group in enumerate(request.groups):
        values = selected_by_group.get(group.id, [])
        if index == 0:
            values = [*values, *extras]
        answers[group.id] = {"answers": values}
    return {"answers": answers}


def _jsonrpc_error(request_id: Any, message: str) -> dict[str, Any]:
    return {"id": request_id, "error": {"code": -32602, "message": message}}


def _thread_id_from_result(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    thread = value.get("thread")
    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
        return thread["id"]
    if isinstance(value.get("threadId"), str):
        return value["threadId"]
    return None


def _chunk_from_completed_item(item: Any, raw: dict[str, Any]) -> AgentChunk | None:
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    if item_type in {"commandExecution", "mcpToolCall", "dynamicToolCall"}:
        return AgentChunk(type="tool_result", text=json.dumps(item, ensure_ascii=False), raw=raw)
    return None


def _rpc_error_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "detail", "error"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    text = str(value or "").strip()
    return text or "Codex App Server 请求失败"


def _notification_error_text(params: dict[str, Any]) -> str:
    return _rpc_error_text(params.get("error") or params)


def _jsonl(*messages: dict[str, Any]) -> str:
    return "\n".join(json.dumps(message, ensure_ascii=False, separators=(",", ":")) for message in messages) + "\n"


def _clip(value: str, limit: int) -> str:
    text = value.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _final_codex_text(home: Path, session_id: str | None, stdout_text: str, path_map) -> str:
    if "\ufffd" not in stdout_text:
        return stdout_text
    recovered = _read_last_session_agent_message(home, session_id)
    if not recovered or "\ufffd" in recovered:
        return stdout_text
    return path_map.container_to_host_text(recovered)


def _read_last_session_agent_message(home: Path, session_id: str | None) -> str | None:
    if not session_id:
        return None
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None
    matches = sorted(sessions.rglob(f"*{session_id}*.jsonl"))
    if not matches:
        return None
    last: str | None = None
    try:
        with matches[-1].open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or record.get("type") != "event_msg":
                    continue
                if payload.get("type") == "agent_message":
                    message = payload.get("message")
                    if isinstance(message, str) and message.strip():
                        last = message
                elif payload.get("type") == "task_complete":
                    message = payload.get("last_agent_message")
                    if isinstance(message, str) and message.strip():
                        last = message
    except OSError:
        return last
    return last
