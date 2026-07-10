from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

from app.runtime.base import (
    AgentChunk,
    AgentExecutionResult,
    AgentProviderStatus,
    AskUserRequest,
)
from app.utils import now_utc


# prompt 中允许测试用 ``[[respond:xxx]]`` 注入想要的 LLM 文本输出；
# ``[[delay:0.2]]`` 让 mock 在返回前停顿一段时间（用于触发 cancel 竞速）。
# ``[[ask_user:{...json...}]]`` 让 mock 发起一次 ask_user 调用，json 字段对齐
# AskUserRequest（不需要写 tool_use_id，mock 会自动生成）。
_RESPOND_RE = re.compile(r"\[\[respond:(.*?)\]\]", re.DOTALL)
_DELAY_RE = re.compile(r"\[\[delay:([0-9.]+)\]\]")
_ASK_RE = re.compile(r"\[\[ask_user:(\{.*?\})\]\]", re.DOTALL)


class MockRuntime:
    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="mock",
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
        delay_match = _DELAY_RE.search(prompt)
        if delay_match:
            try:
                seconds = float(delay_match.group(1))
            except ValueError:
                seconds = 0.0
            elapsed = 0.0
            step = 0.05
            while elapsed < seconds:
                if cancel_event.is_set():
                    return AgentExecutionResult(
                        session_id=session_id,
                        total_text="",
                        finished_with="cancelled",
                    )
                await asyncio.sleep(min(step, seconds - elapsed))
                elapsed += step
        if "slow" in prompt:
            for _ in range(50):
                if cancel_event.is_set():
                    return AgentExecutionResult(
                        session_id=session_id,
                        total_text="",
                        finished_with="cancelled",
                    )
                await asyncio.sleep(0.1)
        if "[[fail]]" in prompt or "fail-now" in prompt:
            await on_chunk(AgentChunk(type="error", text="mock failed"))
            return AgentExecutionResult(
                session_id=session_id, total_text="", finished_with="error", error="mock failed"
            )
        if "你是 Mira 的画布布局美化助手" in prompt:
            text = _layout_response(prompt)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "mock_session",
                total_text=text,
                finished_with="done",
            )
        if "你是 Mira 的提示词助手" in prompt:
            text = json.dumps(
                {"prompt": _extract_assistant_current_prompt(prompt), "output_contract_json": None},
                ensure_ascii=False,
            )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "mock_session",
                total_text=text,
                finished_with="done",
            )

        is_plan_prompt = "你是 Mira 工作流编辑器的 NL 编译方案助手" in prompt
        is_apply_prompt = "你是 Mira 工作流编辑器的 NL 编译实施器" in prompt
        if runtime_policy == "ask_user_plan" and not is_plan_prompt:
            text = _run_preflight_plan(prompt)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "mock_session",
                total_text=text,
                finished_with="done",
            )

        # 处理 ask_user 调用：可以多次触发（mock 用「带 respond 的 ask_user 返回结果」拼到最后输出里）。
        collected_segments: list[str] = []
        remaining = prompt
        ask_index = 0
        while True:
            ask_match = _ASK_RE.search(remaining)
            if ask_match is None or on_ask_user is None or is_apply_prompt:
                break
            ask_index += 1
            try:
                payload = json.loads(ask_match.group(1))
            except json.JSONDecodeError:
                payload = {}
            request = _build_ask_request(payload)
            result = await on_ask_user(request)
            if cancel_event.is_set():
                return AgentExecutionResult(
                    session_id=session_id, total_text="", finished_with="cancelled"
                )
            if not result.ok:
                # 协议错误：spec §1.2 由后端回 is_error=true 的 tool_result。
                # 这里 mock 简化为直接报告失败。
                err = result.error or "ask_user 协议错误"
                await on_chunk(AgentChunk(type="error", text=err))
                return AgentExecutionResult(
                    session_id=session_id,
                    total_text="",
                    finished_with="error",
                    error=err,
                )
            collected_segments.append(_serialize_ask_result(result))
            remaining = remaining[: ask_match.start()] + remaining[ask_match.end() :]

        respond_match = _RESPOND_RE.search(remaining)
        if is_plan_prompt:
            base_text = _default_plan_response()
        elif respond_match:
            base_text = respond_match.group(1).strip()
        elif "hello" in remaining:
            base_text = "world"
        else:
            base_text = f"mock: {remaining[:80]}"
        if collected_segments and output_schema is None:
            text = base_text + "\n" + "\n".join(collected_segments)
        else:
            text = base_text
        if _is_html_schema(output_schema) and text.lstrip().startswith("<"):
            text = json.dumps({"html": text}, ensure_ascii=False)
        if runtime_policy == "execute":
            summary_segments = _decision_summary_segments(remaining)
            if summary_segments:
                text = text + "\n" + "\n".join(summary_segments)
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "mock_session",
            total_text=text,
            finished_with="done",
        )


def _build_ask_request(payload: dict) -> AskUserRequest:
    request_payload = dict(payload)
    request_payload["tool_use_id"] = str(payload.get("tool_use_id") or f"toolu_mock_{uuid.uuid4().hex[:8]}")
    return AskUserRequest.model_validate(request_payload)


def _is_html_schema(schema: dict | None) -> bool:
    return (
        isinstance(schema, dict)
        and isinstance(schema.get("properties"), dict)
        and "html" in schema["properties"]
    )


def _extract_assistant_current_prompt(prompt: str) -> str:
    match = re.search(
        r"- 当前提示词：\n(.*?)(?:\n- 分支：|\n- 主输入 source_node_id：|\n\n## 直接上游节点)",
        prompt,
        flags=re.DOTALL,
    )
    if match:
        current = match.group(1).strip()
        if current and current != "（当前没有提示词）":
            return current
    return "ASSISTED_PROMPT"


def _run_preflight_plan(prompt: str) -> str:
    history = _preflight_history(prompt)
    ask_payloads = [json.loads(match.group(1)) for match in _ASK_RE.finditer(prompt)]
    if len(history) < len(ask_payloads):
        return json.dumps(
            {
                "action": "ask",
                "rationale": "测试 prompt 声明需要 ask_user",
                "request": ask_payloads[len(history)],
            },
            ensure_ascii=False,
        )
    summary = _history_summary(history)
    return json.dumps(
        {
            "action": "complete",
            "decision_summary": summary or "无需额外提问。",
            "reason": "测试 preflight 已完成所需决策收敛。",
        },
        ensure_ascii=False,
    )


def _preflight_history(prompt: str) -> list[dict]:
    marker = "# 已有提问历史 JSON"
    if marker not in prompt:
        return []
    tail = prompt.split(marker, 1)[1].lstrip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(tail)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _history_summary(history: list[dict]) -> str:
    segments: list[str] = []
    for item in history:
        response = item.get("response") if isinstance(item, dict) else None
        if not isinstance(response, dict):
            continue
        result = type(
            "Result",
            (),
            {
                "answers": [
                    type("Answer", (), answer)
                    for answer in response.get("answers", [])
                    if isinstance(answer, dict)
                ],
                "text": response.get("text"),
                "attachments": [
                    type("Attachment", (), attachment)
                    for attachment in response.get("attachments", [])
                    if isinstance(attachment, dict)
                ],
            },
        )()
        segments.append(_serialize_ask_result(result))
    return "\n".join(segment for segment in segments if segment)


def _serialize_ask_result(result) -> str:  # noqa: ANN001 - simple test helper
    parts: list[str] = []
    if result.answers:
        serialized = []
        for answer in result.answers:
            serialized.append(f"{answer.group_id}=" + "|".join(answer.selected))
        parts.append("answers=" + ",".join(serialized))
    if result.text:
        parts.append("text=" + result.text)
    if result.attachments:
        parts.append(
            "attachments=" + "|".join(att.name or att.id for att in result.attachments)
        )
    return "ask_result " + "; ".join(parts) if parts else "ask_result empty"


def _decision_summary_segments(prompt: str) -> list[str]:
    marker = "# 用户决策摘要"
    if marker not in prompt:
        return []
    tail = prompt.split(marker, 1)[1]
    return [line.strip() for line in tail.splitlines() if line.strip().startswith("ask_result ")]


def _layout_response(prompt: str) -> str:
    match = re.search(r"当前 graph JSON：\n(.*?)\n\n节点尺寸 JSON：", prompt, flags=re.DOTALL)
    if not match:
        return '{"positions":[]}'
    try:
        graph = json.loads(match.group(1))
    except json.JSONDecodeError:
        graph = {"nodes": []}
    positions = []
    for index, node in enumerate(graph.get("nodes", [])):
        node_id = node.get("id") if isinstance(node, dict) else None
        if isinstance(node_id, str):
            positions.append({"id": node_id, "x": 80 + index * 320, "y": 120})
    return json.dumps({"positions": positions}, ensure_ascii=False)


def _default_plan_response() -> str:
    return json.dumps(
        {
            "plan": {
                "goal_summary": "按用户指令更新 Mira 画布",
                "assumptions": ["沿用当前应用 Agent 和现有画布约束"],
                "data_flow": ["按确认方案连接输入、生成和输出节点"],
                "implementation_steps": ["更新相关节点配置", "确认连线后生成最终输出"],
                "graph_changes": ["按确认方案调整画布节点和连线"],
                "expected_inputs": ["用户在应用入口提供必要输入"],
                "expected_outputs": ["画布生成符合指令的最终结果"],
                "acceptance_criteria": ["生成后的 graph 通过后端校验"],
            }
        },
        ensure_ascii=False,
    )
