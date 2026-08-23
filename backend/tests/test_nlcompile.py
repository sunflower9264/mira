from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import re
import uuid
import time
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.models import NlCompileSessionRow
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus, AskUserRequest
from app.runtime.factory import set_runtime_override
from app.services import nlcompile as nlcompile_service
from app.services.structured_output import (
    NL_COMPILE_PATCH_OUTPUT_SCHEMA,
    NL_COMPILE_PLAN_OUTPUT_SCHEMA,
    PROMPT_ASSISTANT_OUTPUT_SCHEMA,
)
from app.utils import now_utc
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime


def _decision_options(labels: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "description": f"选择 {label} 会按该方向继续。",
            "recommended": index == 0,
        }
        for index, label in enumerate(labels)
    ]


def _option_labels(group: dict[str, Any]) -> list[str]:
    return [option["label"] for option in group["options"]]


def _structured_patch(
    op: str,
    *,
    id: str | None = None,
    node: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    edge_id: str | None = None,
    edge_source: str | None = None,
    edge_target: str | None = None,
    edge_branch_key: str | None = None,
) -> dict[str, Any]:
    return {
        "op": op,
        "id": id,
        "node_json": json.dumps(node, ensure_ascii=False) if node is not None else None,
        "patch_json": json.dumps(patch, ensure_ascii=False) if patch is not None else None,
        "edge_id": edge_id,
        "edge_source": edge_source,
        "edge_target": edge_target,
        "edge_branch_key": edge_branch_key,
    }


class ScriptedRuntime:
    """测试用的 runtime：execute() 返回固定文本，detect_status 始终安装可用。"""

    def __init__(
        self,
        text: str | None = None,
        *,
        texts: list[str] | None = None,
        finished_with: str = "done",
        error: str | None = None,
    ) -> None:
        self.text = text or ""
        self.texts = list(texts or [])
        self.finished_with = finished_with
        self.error = error
        self.last_prompt: str | None = None
        self.prompts: list[str] = []
        self.call_count = 0
        self.assistant_call_count = 0
        self.layout_call_count = 0
        self.models: list[str | None] = []
        self.reasoning_efforts: list[str | None] = []
        self.runtime_tools: list[Any] = []
        self.output_schemas: list[dict | None] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="scripted",
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
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.models.append(model)
        self.reasoning_efforts.append(reasoning_effort)
        self.runtime_tools.append(runtime_tools)
        self.output_schemas.append(output_schema)
        if "你是 Mira 工作流编辑器的 NL 编译方案助手" in prompt:
            self.call_count += 1
            if self.finished_with != "done":
                return AgentExecutionResult(
                    session_id=session_id,
                    total_text="",
                    finished_with=self.finished_with,
                    error=self.error,
                )
            text = _default_plan_response()
            if self.texts and _is_plan_response(self.texts[0]):
                text = self.texts.pop(0)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你是 Mira 的画布布局美化助手" in prompt:
            self.layout_call_count += 1
            text = _layout_response(prompt)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你是 Mira 的提示词助手" in prompt:
            self.assistant_call_count += 1
            text = _assistant_result(_extract_assistant_current_prompt(prompt))
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        self.call_count += 1
        text = self.texts.pop(0) if self.texts else self.text
        if text:
            await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id,
            total_text=text,
            finished_with=self.finished_with,
            error=self.error,
        )


class InitialAttachmentRuntime(ScriptedRuntime):
    def __init__(self, text: str) -> None:
        super().__init__(text=text)
        self.attachment_text_by_phase: dict[str, str] = {}
        self.attachment_prompt_by_phase: dict[str, str] = {}

    async def execute(self, **kwargs) -> AgentExecutionResult:
        output_schema = kwargs.get("output_schema")
        phase = None
        if output_schema == NL_COMPILE_PLAN_OUTPUT_SCHEMA:
            phase = "plan"
        elif output_schema == NL_COMPILE_PATCH_OUTPUT_SCHEMA:
            phase = "apply"
        if phase is not None:
            matches = list((kwargs["cwd"] / ".inputs").rglob("brief.txt"))
            if matches:
                self.attachment_text_by_phase[phase] = matches[0].read_text(encoding="utf-8")
            self.attachment_prompt_by_phase[phase] = kwargs["prompt"]
        return await super().execute(**kwargs)


class AssistantFailsOnceRuntime(ScriptedRuntime):
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
        if "你是 Mira 的提示词助手" not in prompt:
            return await super().execute(
                prompt=prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                on_ask_user=on_ask_user,
                runtime_tools=runtime_tools,
                runtime_policy=runtime_policy,
                output_schema=output_schema,
            )
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.models.append(model)
        self.reasoning_efforts.append(reasoning_effort)
        self.runtime_tools.append(runtime_tools)
        self.output_schemas.append(output_schema)
        self.assistant_call_count += 1
        if self.assistant_call_count == 1:
            return AgentExecutionResult(session_id=session_id, total_text="", finished_with="error", error="boom")
        text = _assistant_result(_extract_assistant_current_prompt(prompt))
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


class PlanRepairRuntime(ScriptedRuntime):
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
        if "你是 Mira 工作流编辑器的 NL 编译方案助手" not in prompt and "你刚才执行「NL 编译方案确认」" not in prompt:
            return await super().execute(
                prompt=prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                on_ask_user=on_ask_user,
                runtime_tools=runtime_tools,
                runtime_policy=runtime_policy,
                output_schema=output_schema,
            )
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.models.append(model)
        self.reasoning_efforts.append(reasoning_effort)
        self.runtime_tools.append(runtime_tools)
        self.output_schemas.append(output_schema)
        self.call_count += 1
        text = self.texts.pop(0)
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


class LayoutFailsRuntime(ScriptedRuntime):
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
        if "你是 Mira 的画布布局美化助手" not in prompt:
            return await super().execute(
                prompt=prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                on_ask_user=on_ask_user,
                runtime_tools=runtime_tools,
                runtime_policy=runtime_policy,
                output_schema=output_schema,
            )
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.models.append(model)
        self.reasoning_efforts.append(reasoning_effort)
        self.runtime_tools.append(runtime_tools)
        self.output_schemas.append(output_schema)
        self.layout_call_count += 1
        return AgentExecutionResult(session_id=session_id, total_text="", finished_with="error", error="layout timeout")


class AssistantContractRuntime(ScriptedRuntime):
    def __init__(self, *, text: str, assistant_prompt: str, output_contract: dict) -> None:
        super().__init__(text=text)
        self.assistant_prompt = assistant_prompt
        self.output_contract = output_contract

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
        if "你是 Mira 的提示词助手" not in prompt:
            return await super().execute(
                prompt=prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                on_ask_user=on_ask_user,
                runtime_tools=runtime_tools,
                runtime_policy=runtime_policy,
                output_schema=output_schema,
            )
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.models.append(model)
        self.reasoning_efforts.append(reasoning_effort)
        self.runtime_tools.append(runtime_tools)
        self.output_schemas.append(output_schema)
        self.assistant_call_count += 1
        text = _assistant_result(self.assistant_prompt, self.output_contract)
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


def _extract_assistant_current_prompt(prompt: str) -> str:
    match = re.search(
        r"- 当前提示词：\n(.*?)"
        r"(?:\n- 当前 output_contract：|\n- 分支：|\n\n## 执行祖先节点)",
        prompt,
        flags=re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return "ASSISTED_PROMPT"


def _assistant_result(prompt: str, output_contract: dict | None = None) -> str:
    output_contract_json = json.dumps(output_contract, ensure_ascii=False) if output_contract is not None else None
    return json.dumps({"prompt": prompt, "output_contract_json": output_contract_json}, ensure_ascii=False)


def _layout_response(prompt: str) -> str:
    match = re.search(r"当前 graph JSON：\n(.*?)\n\n节点尺寸 JSON：", prompt, flags=re.DOTALL)
    if not match:
        return '{"positions":[]}'
    graph = json.loads(match.group(1))
    positions = []
    for index, node in enumerate(graph.get("nodes", [])):
        node_id = node.get("id") if isinstance(node, dict) else None
        if isinstance(node_id, str):
            positions.append({"id": node_id, "x": 100 + index * 300, "y": 140})
    return json.dumps({"positions": positions}, ensure_ascii=False)


def _default_plan_response() -> str:
    return json.dumps(
        {
            "plan": {
                "goal_summary": "按用户指令更新 Mira 画布",
                "assumptions": ["沿用当前应用 Agent 和现有画布约束"],
                "data_flow": ["按确认方案连接输入、生成和输出节点"],
                "implementation_steps": ["按确认方案新增或更新节点并连接必要数据流"],
                "graph_changes": ["更新当前画布以落实用户指令"],
                "expected_inputs": ["用户在应用入口提供必要输入"],
                "expected_outputs": ["画布生成符合指令的最终结果"],
                "acceptance_criteria": ["应用后的节点和连线与确认方案一致"],
            }
        },
        ensure_ascii=False,
    )


def _is_plan_response(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("plan"), dict)


class AnswerAwareRuntime:
    """测试用 runtime：根据 ask_user resume 回来的选项生成不同 patch。"""

    def __init__(self) -> None:
        self.call_count = 0
        self.assistant_call_count = 0
        self.layout_call_count = 0
        self.answers_seen: list[list[dict]] = []
        self.assistant_prompts: list[str] = []
        self.prompts: list[str] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="answer-aware",
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
        self.prompts.append(prompt)
        if "你是 Mira 工作流编辑器的 NL 编译方案助手" in prompt:
            self.call_count += 1
            if on_ask_user is None:
                return AgentExecutionResult(
                    session_id=session_id,
                    total_text="",
                    finished_with="error",
                    error="missing ask_user callback",
                )

            request = AskUserRequest(
                context={"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
                groups=[
                    {
                        "id": "intent",
                        "type": "single",
                        "label": "选择用途",
                        "options": _decision_options(["写作", "翻译", "总结"]),
                    }
                ],
                tool_use_id=f"toolu_answer_{self.call_count}",
            )
            result = await on_ask_user(request)
            if cancel_event.is_set():
                return AgentExecutionResult(session_id=session_id, total_text="", finished_with="cancelled")
            if not result.ok:
                return AgentExecutionResult(
                    session_id=session_id,
                    total_text="",
                    finished_with="error",
                    error=result.error or "ask_user failed",
                )

            self.answers_seen.append([answer.model_dump() for answer in result.answers])
            selected = result.answers[0].selected[0] if result.answers and result.answers[0].selected else ""
            text = json.dumps(
                {
                    "plan": {
                        "goal_summary": f"创建{selected}用途节点",
                        "assumptions": [f"用户选择了{selected}用途"],
                        "data_flow": [f"生成节点按{selected}用途处理输入"],
                        "implementation_steps": [f"新增并配置{selected}用途生成节点"],
                        "graph_changes": [f"新增{selected}用途生成节点"],
                        "expected_inputs": ["用户输入主题"],
                        "expected_outputs": [f"{selected}结果"],
                        "acceptance_criteria": [f"运行后生成{selected}结果"],
                    }
                },
                ensure_ascii=False,
            )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你是 Mira 的画布布局美化助手" in prompt:
            self.layout_call_count += 1
            text = _layout_response(prompt)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你是 Mira 的提示词助手" in prompt:
            self.assistant_call_count += 1
            self.assistant_prompts.append(prompt)
            text = _assistant_result(_extract_assistant_current_prompt(prompt))
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        self.call_count += 1
        selected = "写作" if "写作" in prompt else "翻译"
        node_id = "n_writer" if selected == "写作" else "n_translator"
        title = "写作生成" if selected == "写作" else "翻译生成"
        text = json.dumps(
            {
                "patches": [
                    {
                        "op": "add_node",
                        "node": {
                            "id": node_id,
                            "type": "generate",
                            "position": {"x": 10, "y": 20},
                            "title": title,
                            "prompt": f"根据{selected}用途生成内容",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


class ReplayAwareRuntime(AnswerAwareRuntime):
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
        if "你是 Mira 工作流编辑器的 NL 编译方案助手" in prompt and "用户问答历史" in prompt:
            self.prompts.append(prompt)
            self.call_count += 1
            selected = "写作" if "选择用途 → 写作" in prompt else "翻译"
            text = json.dumps(
                {
                    "plan": {
                        "goal_summary": f"重放历史后创建{selected}用途节点",
                        "assumptions": [f"沿用历史回答：{selected}"],
                        "data_flow": [f"生成节点按{selected}用途处理输入"],
                        "implementation_steps": [f"新增并配置{selected}用途生成节点"],
                        "graph_changes": [f"新增{selected}生成节点"],
                        "expected_inputs": ["用户输入主题"],
                        "expected_outputs": [f"{selected}结果"],
                        "acceptance_criteria": [f"运行后生成{selected}结果"],
                    }
                },
                ensure_ascii=False,
            )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        return await super().execute(
            prompt=prompt,
            session_id=session_id,
            allowed_tools=allowed_tools,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
            on_ask_user=on_ask_user,
            runtime_tools=runtime_tools,
            runtime_policy=runtime_policy,
            output_schema=output_schema,
        )


class ApplyAsksRuntime(ScriptedRuntime):
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
        if (
            "你是 Mira 工作流编辑器的 NL 编译实施器" not in prompt
            or "你是 Mira 的画布布局美化助手" in prompt
            or "你是 Mira 的提示词助手" in prompt
        ):
            return await super().execute(
                prompt=prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                on_ask_user=on_ask_user,
                runtime_tools=runtime_tools,
                runtime_policy=runtime_policy,
                output_schema=output_schema,
            )
        self.call_count += 1
        if on_ask_user is None:
            return AgentExecutionResult(session_id=session_id, total_text="", finished_with="error", error="missing ask_user")
        result = await on_ask_user(
            AskUserRequest(
                context={"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
                groups=[
                    {
                        "id": "intent",
                        "type": "single",
                        "label": "选择用途",
                        "options": _decision_options(["写作", "翻译", "总结"]),
                    }
                ],
                tool_use_id="toolu_apply_forbidden",
            )
        )
        return AgentExecutionResult(
            session_id=session_id,
            total_text="",
            finished_with="error",
            error=result.error or "ask_user failed",
        )


def _create_app(client) -> str:
    response = client.post("/api/apps", json={"name": "NlCompile Demo"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _regular_user_token() -> str:
    return create_regular_user(f"nl_{uuid.uuid4().hex[:10]}")["token"]


def _post_nlcompile_and_apply(client, payload: dict) -> Any:
    planned = client.post("/api/nlcompile", json=payload)
    assert planned.status_code == 200, planned.text
    planned_body = planned.json()
    assert planned_body["status"] == "planned"
    assert "new_graph" not in planned_body
    assert "applied_patches" not in planned_body
    assert planned_body["plan"]["goal_summary"]
    applied = client.post(f"/api/nlcompile/{planned_body['compile_id']}/apply")
    return applied


def test_nlcompile_does_not_require_graph_agent(auth_client):
    app_id = _create_app(auth_client)
    response = auth_client.post(
        "/api/nlcompile",
        json={"app_id": app_id, "instruction": "加一个生成节点", "current_graph": {"nodes": [], "execution_edges": []}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "planned"


def test_nlcompile_plan_passes_planning_safe_mcp_tools(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_nl_plan",
            "name": "nl-plan-mcp",
            "enabled": True,
            "planning_enabled": True,
            "url": "http://localhost:9999/nl-plan",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_nl_execute",
            "name": "nl-execute-mcp",
            "enabled": True,
            "planning_enabled": False,
            "url": "http://localhost:9999/nl-execute",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_nl_disabled",
            "name": "nl-disabled-mcp",
            "enabled": True,
            "planning_enabled": True,
            "url": "http://localhost:9999/nl-disabled",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200
    runtime = ScriptedRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "加一个生成节点",
                "current_graph": {
                    "tools": {"disabled_tool_ids": ["mcp:mcp_nl_disabled"]},
                    "_runtime_tools": {
                        "allowed_tool_ids": ["mcp:mcp_nl_plan", "mcp:mcp_nl_disabled"],
                    },
                    "nodes": [],
                    "execution_edges": [],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "planned"
    assert runtime.runtime_tools
    assert [server.name for server in runtime.runtime_tools[0].mcp_servers] == ["nl-plan-mcp"]

    async def stored_graph_json() -> str:
        async with SessionLocal() as db:
            row = await db.get(NlCompileSessionRow, response.json()["compile_id"])
            assert row is not None
            return row.graph_json

    assert "_runtime_tools" not in asyncio.run(stored_graph_json())


def test_nlcompile_structured_schemas_require_effective_content():
    plan_properties = NL_COMPILE_PLAN_OUTPUT_SCHEMA["properties"]["plan"]["properties"]
    for field_name in (
        "implementation_steps",
        "graph_changes",
        "expected_inputs",
        "expected_outputs",
        "acceptance_criteria",
    ):
        assert plan_properties[field_name]["minItems"] == 1
    assert NL_COMPILE_PATCH_OUTPUT_SCHEMA["properties"]["patches"]["minItems"] == 1


def test_nlcompile_fallback_positions_only_fill_added_nodes_without_coordinates():
    graph = {
        "nodes": [
            {"id": "n_existing", "position": {"x": -100, "y": 40}},
            {"id": "n_added_positioned", "position": {"x": 100, "y": 100}},
            {"id": "n_added_missing"},
        ],
        "execution_edges": [],
    }
    patches = [
        {"op": "add_node", "node": {"id": "n_added_positioned"}},
        {"op": "add_node", "node": {"id": "n_added_missing"}},
    ]

    nlcompile_service._ensure_added_node_positions(graph, patches)

    assert graph["nodes"][0]["position"] == {"x": -100, "y": 40}
    assert graph["nodes"][1]["position"] == {"x": 100, "y": 100}
    assert graph["nodes"][2]["position"] == {"x": 420.0, "y": 40.0}


def test_nlcompile_applies_valid_patches_and_renders_plan(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_demo",
                        "type": "generate",
                        "position": {"x": 100, "y": 100},
                        "title": "生成草稿",
                        "prompt": "请按指令生成一段说明",
                        "model": "node-model",
                        "reasoning_effort": "low",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "加一个生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert len(body["applied_patches"]) == 1
    assert body["applied_patches"][0]["op"] == "add_node"
    new_nodes = body["new_graph"]["nodes"]
    assert len(new_nodes) == 1
    assert "agent" not in body["new_graph"]
    assert new_nodes[0]["id"] == "n_generate_demo"
    assert new_nodes[0]["position"] == {"x": 100.0, "y": 140.0}
    assert "agent" not in new_nodes[0]
    assert runtime.call_count == 2
    assert runtime.assistant_call_count == 1
    assert runtime.layout_call_count == 1
    assert runtime.models[0] is None
    assert runtime.reasoning_efforts[0] == "xhigh"
    assert runtime.models[1] is None
    assert runtime.reasoning_efforts[1] == "xhigh"
    assert runtime.models[2] == "node-model"
    assert runtime.reasoning_efforts[2] == "xhigh"
    assert runtime.models[3] is None
    assert runtime.reasoning_efforts[3] == "xhigh"
    assert runtime.output_schemas[0] == NL_COMPILE_PLAN_OUTPUT_SCHEMA
    assert runtime.output_schemas[1] == NL_COMPILE_PATCH_OUTPUT_SCHEMA
    assert runtime.output_schemas[2] == PROMPT_ASSISTANT_OUTPUT_SCHEMA
    assert runtime.output_schemas[3] is None
    assert "新增" in body["plan_markdown"]
    assert "生成草稿" in body["plan_markdown"]
    assert "请按指令生成一段说明" in body["plan_markdown"]
    assert body["warnings"] == []
    # build_prompt 必须把 graph_json 与 instruction 一起渲染下发。
    compile_prompt = runtime.prompts[0]
    assert "加一个生成节点" in compile_prompt
    assert "goal_summary" in compile_prompt
    assert "新建 workflow、节点较多或结构调整较大本身都不是提问理由" in compile_prompt
    assert "只有同时满足以下条件时才向用户提问" in compile_prompt
    assert "可在应用每次运行时采集的信息应设计进 user_input" in compile_prompt
    assert "implementation_steps" in compile_prompt
    assert "expected_inputs" in compile_prompt
    assert "只有同时满足以下条件时才向用户提问" in compile_prompt
    assert "用户补充的回答优先于原始假设" in compile_prompt
    assert "方案必须完整吸收这些信息" in compile_prompt
    # 原生 Plan prompt 不得混入旧 JSON action 状态机。
    assert '"action":"ask"' not in compile_prompt
    assert "JSON action 状态机" not in compile_prompt
    assistant_prompt = runtime.prompts[2]
    assert "## 方案上下文" in assistant_prompt
    assert "原始用户指令：加一个生成节点" in assistant_prompt
    assert "按用户指令更新 Mira 画布" in assistant_prompt
    patch_prompt = runtime.prompts[1]
    assert "已确认方案" in patch_prompt
    assert "graph patch 阶段禁止再次向用户提问" in patch_prompt
    assert "禁止冗余传递连线" in patch_prompt
    assert "只处理业务数据流，不判断画布视觉交叉" in patch_prompt
    assert "每个 properties 业务字段（含嵌套字段）" in patch_prompt
    assert "中文 title 和 description" in patch_prompt
    assert "普通边" in patch_prompt
    assert "condition 分支边" in patch_prompt
    assert "asset drawing" in patch_prompt
    assert "禁止制造交叉连接" not in patch_prompt
    assert "回答采纳规则" not in compile_prompt
    assert "request_user_input" not in compile_prompt
    assert "planning/read-only 阶段不得执行修改、生成产物" in compile_prompt


def test_nlcompile_initial_attachment_is_available_to_plan_and_apply_runtime(
    auth_client,
    configure_codex,
):
    configure_codex()
    app_id = _create_app(auth_client)
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("brief.txt", b"ATTACHMENT_REQUIREMENT_42", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["id"]
    patch_payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_asset_brief",
                        "type": "asset",
                        "asset_kind": "text",
                        "position": {"x": 100, "y": 100},
                        "title": "需求摘要",
                        "content": "ATTACHMENT_REQUIREMENT_42",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = InitialAttachmentRuntime(text=patch_payload)
    set_runtime_override(runtime)
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "根据附件创建应用",
                "current_graph": {"nodes": [], "execution_edges": []},
                "attachments": [{"id": upload_id, "name": "brief.txt"}],
            },
        )
        assert planned.status_code == 200, planned.text
        planned_body = planned.json()
        assert planned_body["status"] == "planned"
        nlcompile_service._compile_sessions.clear()
        response = auth_client.post(
            f"/api/nlcompile/{planned_body['compile_id']}/apply",
        )
    finally:
        nlcompile_service._compile_sessions.clear()
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert runtime.attachment_text_by_phase == {
        "plan": "ATTACHMENT_REQUIREMENT_42",
        "apply": "ATTACHMENT_REQUIREMENT_42",
    }
    assert "/mnt/inputs/" in runtime.attachment_prompt_by_phase["plan"]
    assert "/mnt/inputs/" in runtime.attachment_prompt_by_phase["apply"]


def test_nlcompile_initial_attachment_must_belong_to_current_user(
    client,
    auth_client,
    configure_codex,
):
    configure_codex()
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("private.txt", b"OWNER_ONLY", "text/plain")},
    )
    assert upload.status_code == 200, upload.text

    other_token = _regular_user_token()
    headers = {"Authorization": f"Bearer {other_token}"}
    created = client.post("/api/apps", headers=headers, json={"name": "Other user app"})
    assert created.status_code == 200, created.text
    response = client.post(
        "/api/nlcompile",
        headers=headers,
        json={
            "app_id": created.json()["id"],
            "instruction": "读取附件",
            "current_graph": {"nodes": [], "execution_edges": []},
            "attachments": [{"id": upload.json()["id"], "name": "private.txt"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "附件不存在"


def test_nlcompile_applies_real_structured_fields_for_all_node_types(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_input",
                        "type": "user_input",
                        "title": "输入主题",
                        "input_schema": {"label": "主题", "kind": "text", "required": True},
                    },
                ),
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_asset",
                        "type": "asset",
                        "title": "写作规范",
                        "asset_kind": "text",
                        "content": "保持准确、简洁。",
                    },
                ),
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_generate",
                        "type": "generate",
                        "title": "生成摘要",
                        "prompt": "结合用户主题和写作规范生成摘要。",
                    },
                ),
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_condition",
                        "type": "condition",
                        "title": "质量判断",
                        "mode": "cases",
                        "prompt": "判断摘要是否达到发布标准。",
                        "branches": [
                            {"key": "approved", "label": "通过"},
                            {"key": "review", "label": "复核"},
                        ],
                    },
                ),
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_output",
                        "type": "output",
                        "title": "输出",
                        "prompt": "将判断结果和摘要完整渲染为 HTML。",
                    },
                ),
                _structured_patch("update_node", id="n_generate", patch={"description": "生成发布摘要"}),
                _structured_patch(
                    "add_edge",
                    edge_id="e_input_generate",
                    edge_source="n_input",
                    edge_target="n_generate",
                ),
                _structured_patch(
                    "add_edge",
                    edge_id="e_asset_generate",
                    edge_source="n_asset",
                    edge_target="n_generate",
                ),
                _structured_patch(
                    "add_edge",
                    edge_id="e_generate_condition",
                    edge_source="n_generate",
                    edge_target="n_condition",
                ),
                _structured_patch(
                    "add_edge",
                    edge_id="e_condition_output",
                    edge_source="n_condition",
                    edge_target="n_output",
                    edge_branch_key="approved",
                ),
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "创建一个带规范素材和质量判断的摘要应用",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert {node["type"] for node in body["new_graph"]["nodes"]} == {
        "user_input",
        "asset",
        "generate",
        "condition",
        "output",
    }
    assert all("position" in node for node in body["new_graph"]["nodes"])
    assert any(
        edge.get("branch_key") == "approved"
        for edge in body["new_graph"]["execution_edges"]
    )
    assert body["new_graph"]["nodes"][2]["description"] == "生成发布摘要"
    assert runtime.assistant_call_count == 3
    assert runtime.layout_call_count == 1


def test_nlcompile_repairs_malformed_plan_output_before_apply(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_repaired",
                        "type": "generate",
                        "title": "修复后生成",
                        "prompt": "请生成修复后的内容",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = PlanRepairRuntime(texts=["不是 JSON", _default_plan_response(), patch_payload])
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "加一个生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_generate_repaired"
    assert runtime.output_schemas[0] == NL_COMPILE_PLAN_OUTPUT_SCHEMA
    assert runtime.output_schemas[1] == NL_COMPILE_PLAN_OUTPUT_SCHEMA
    assert runtime.output_schemas[2] == NL_COMPILE_PATCH_OUTPUT_SCHEMA
    assert "校验失败原因" in runtime.prompts[1]
    assert "不要再向用户提问，也不要调用其它工具" in runtime.prompts[1]


def test_nlcompile_repairs_plan_with_empty_required_sections(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    incomplete_plan = json.dumps(
        {
            "plan": {
                "goal_summary": "新增摘要生成节点",
                "assumptions": [],
                "data_flow": [],
                "implementation_steps": [],
                "graph_changes": ["新增摘要生成节点"],
                "expected_inputs": ["用户主题"],
                "expected_outputs": ["摘要文本"],
                "acceptance_criteria": ["运行后产生摘要"],
            }
        },
        ensure_ascii=False,
    )
    patch_payload = json.dumps(
        {
            "patches": [
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_generate_repaired_sections",
                        "type": "generate",
                        "title": "摘要生成",
                        "prompt": "根据用户主题生成摘要。",
                    },
                )
            ]
        },
        ensure_ascii=False,
    )
    runtime = PlanRepairRuntime(texts=[incomplete_plan, _default_plan_response(), patch_payload])
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增摘要生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json()["new_graph"]["nodes"][0]["id"] == "n_generate_repaired_sections"
    assert "plan.implementation_steps 至少需要 1 项" in runtime.prompts[1]
    assert runtime.output_schemas[:2] == [NL_COMPILE_PLAN_OUTPUT_SCHEMA, NL_COMPILE_PLAN_OUTPUT_SCHEMA]


def test_nlcompile_returns_graph_when_layout_beautify_fails(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_generate_demo",
                        "type": "generate",
                        "title": "生成草稿",
                        "prompt": "请按指令生成一段说明",
                    },
                )
            ]
        },
        ensure_ascii=False,
    )
    runtime = LayoutFailsRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "加一个生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["applied_patches"][0]["op"] == "add_node"
    assert body["new_graph"]["nodes"][0]["id"] == "n_generate_demo"
    assert body["new_graph"]["nodes"][0]["position"] == {"x": 0.0, "y": 0.0}
    assert runtime.layout_call_count == 1
    assert body["warnings"] == ["布局美化失败，已保留原布局：Agent 美化布局失败，请检查 Agent 配置或稍后重试"]


def test_nlcompile_plan_markdown_shows_update_before_after(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "update_node",
                    "id": "n_generate_demo",
                    "patch": {
                        "title": "生成最终稿",
                        "prompt": "请输出完整的最终稿，保留用户指定的语气。",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "把草稿节点改成最终稿",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_generate_demo",
                            "type": "generate",
                            "position": {"x": 100, "y": 100},
                            "title": "生成草稿",
                            "prompt": "请先输出一个简短草稿。",
                        }
                    ],
                    "execution_edges": [],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "更新生成「生成草稿」" in plan
    assert "字段 `prompt`" in plan
    assert "请先输出一个简短草稿。" in plan
    assert "请输出完整的最终稿，保留用户指定的语气。" in plan
    assert "字段 `title`" in plan
    assert "生成最终稿" in plan


def test_nlcompile_plan_markdown_renders_input_schema_readably(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_book_input",
                        "type": "user_input",
                        "position": {"x": 0, "y": 0},
                        "title": "推荐偏好",
                        "input_schema": {
                            "label": "请回答几个问题，帮助优化推荐结果",
                            "placeholder": (
                                "1. 你希望推荐更偏文学性、娱乐性还是实用性？\n"
                                "2. 你更喜欢什么节奏：慢热、紧凑还是介于两者之间？"
                            ),
                            "kind": "text",
                            "required": True,
                        },
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增阅读推荐输入",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "显示文案：请回答几个问题，帮助优化推荐结果" in plan
    assert "输入提示：1. 你希望推荐更偏文学性、娱乐性还是实用性？" in plan
    assert "输入类型：文本" in plan
    assert "是否必填：是" in plan
    assert "```json" not in plan
    assert '"label"' not in plan
    assert '"kind"' not in plan


def test_nlcompile_plan_markdown_renders_structured_updates_readably(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "update_node",
                    "id": "n_input",
                    "patch": {
                        "input_schema": {
                            "label": "阅读偏好",
                            "placeholder": "写下最近喜欢或不喜欢的书",
                            "kind": "text",
                            "required": True,
                        },
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "调整输入表单",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_input",
                            "type": "user_input",
                            "position": {"x": 0, "y": 0},
                            "title": "输入",
                            "input_schema": {"label": "主题", "kind": "text"},
                        }
                    ],
                    "execution_edges": [],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "修改前：" in plan
    assert "显示文案：主题" in plan
    assert "输入类型：文本" in plan
    assert "修改后：" in plan
    assert "显示文案：阅读偏好" in plan
    assert "输入类型：文本" in plan
    assert "```json" not in plan


def test_nlcompile_plan_markdown_renders_branches_readably(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_condition_book",
                        "type": "condition",
                        "position": {"x": 0, "y": 0},
                        "title": "判断阅读方向",
                        "mode": "cases",
                        "prompt": "根据用户偏好选择推荐方向。",
                        "branches": [
                            {"key": "literary", "label": "文学性"},
                            {"key": "practical", "label": "实用性"},
                        ],
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "增加阅读方向判断",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "分支：文学性（key：`literary`）" in plan
    assert "分支：实用性（key：`practical`）" in plan
    assert "```json" not in plan


def test_nlcompile_plan_markdown_shows_edge_titles(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_input_to_generate",
                        "source": "n_input",
                        "target": "n_generate",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "把输入连到生成",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_input",
                            "type": "user_input",
                            "position": {"x": 0, "y": 0},
                            "title": "用户主题",
                            "input_schema": {"label": "主题", "kind": "text"},
                        },
                        {
                            "id": "n_generate",
                            "type": "generate",
                            "position": {"x": 200, "y": 0},
                            "title": "生成回答",
                            "prompt": "根据主题生成回答",
                        },
                    ],
                    "execution_edges": [],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "新增连线" in plan
    assert "用户主题 (`n_input`) → 生成回答 (`n_generate`)" in plan
    assert "e_input_to_generate" in plan


def test_nlcompile_plan_markdown_shows_remove_node_edge_impact(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps({"patches": [{"op": "remove_node", "id": "n_generate"}]}, ensure_ascii=False)
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "删除生成节点",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_input",
                            "type": "user_input",
                            "position": {"x": 0, "y": 0},
                            "title": "用户主题",
                            "input_schema": {"label": "主题", "kind": "text"},
                        },
                        {
                            "id": "n_generate",
                            "type": "generate",
                            "position": {"x": 200, "y": 0},
                            "title": "生成回答",
                            "prompt": "根据主题生成回答",
                        },
                    ],
                    "execution_edges": [
                        {
                            "id": "e_input_to_generate",
                            "source": "n_input",
                            "target": "n_generate",
                        }
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    plan = response.json()["plan_markdown"]
    assert "删除生成「生成回答」" in plan
    assert "同时移除关联连线：1 条" in plan
    assert "用户主题 (`n_input`) → 生成回答 (`n_generate`)" in plan


def test_nlcompile_waiting_resume_returns_completed_plan(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_after_choice",
                        "type": "generate",
                        "position": {"x": 10, "y": 20},
                        "title": "按选择生成",
                        "prompt": "ok",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    ask_payload = {
        "context": {"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
        "groups": [
            {
                "id": "intent",
                "type": "single",
                "label": "选择用途",
                "options": _decision_options(["写作", "翻译", "总结"]),
            }
        ]
    }
    set_runtime_override(MockRuntime())
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": app_id,
            "instruction": f"[[ask_user:{json.dumps(ask_payload, ensure_ascii=False)}]] [[respond:{patch_text}]]",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 200, response.text
    waiting = response.json()
    assert waiting["status"] == "waiting_for_user"
    assert waiting["request"]["groups"][0]["label"] == "选择用途"
    assert _option_labels(waiting["request"]["groups"][0]) == ["写作", "翻译", "总结", "以上都不是"]

    resumed = auth_client.post(
        f"/api/nlcompile/{waiting['compile_id']}/resume",
        json={"answers": [{"group_id": "intent", "selected": ["写作"]}]},
    )
    assert resumed.status_code == 200, resumed.text
    planned = resumed.json()
    assert planned["status"] == "planned"
    body = auth_client.post(f"/api/nlcompile/{planned['compile_id']}/apply").json()
    assert body["status"] == "completed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_after_choice"


def test_nlcompile_waiting_resume_with_none_option_continues_to_plan(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_after_none",
                        "type": "generate",
                        "position": {"x": 10, "y": 20},
                        "title": "重新规划",
                        "prompt": "ok",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    ask_payload = {
        "context": {"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
        "groups": [
            {
                "id": "intent",
                "type": "single",
                "label": "选择用途",
                "options": _decision_options(["写作", "翻译", "总结"]),
            }
        ]
    }
    set_runtime_override(MockRuntime())
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": app_id,
            "instruction": f"[[ask_user:{json.dumps(ask_payload, ensure_ascii=False)}]] [[respond:{patch_text}]]",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 200, response.text
    waiting = response.json()
    assert waiting["status"] == "waiting_for_user"
    assert _option_labels(waiting["request"]["groups"][0]) == ["写作", "翻译", "总结", "以上都不是"]

    resumed = auth_client.post(
        f"/api/nlcompile/{waiting['compile_id']}/resume",
        json={"answers": [{"group_id": "intent", "selected": ["以上都不是"]}]},
    )
    assert resumed.status_code == 200, resumed.text
    planned = resumed.json()
    assert planned["status"] == "planned"
    body = auth_client.post(f"/api/nlcompile/{planned['compile_id']}/apply").json()
    assert body["status"] == "completed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_after_none"


def test_nlcompile_waiting_resume_answers_drive_runtime_patch(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    runtime = AnswerAwareRuntime()
    set_runtime_override(runtime)
    try:
        cases = [
            ("写作", "n_writer", "写作生成"),
            ("翻译", "n_translator", "翻译生成"),
        ]
        for selected, expected_id, expected_title in cases:
            response = auth_client.post(
                "/api/nlcompile",
                json={
                    "app_id": app_id,
                    "instruction": "根据用户选择创建对应节点",
                    "current_graph": {"nodes": [], "execution_edges": []},
                },
            )
            assert response.status_code == 200, response.text
            waiting = response.json()
            assert waiting["status"] == "waiting_for_user"
            assert waiting["request"]["groups"][0]["label"] == "选择用途"

            resumed = auth_client.post(
                f"/api/nlcompile/{waiting['compile_id']}/resume",
                json={"answers": [{"group_id": "intent", "selected": [selected]}]},
            )
            assert resumed.status_code == 200, resumed.text
            planned = resumed.json()
            assert planned["status"] == "planned"
            applied = auth_client.post(f"/api/nlcompile/{planned['compile_id']}/apply")
            assert applied.status_code == 200, applied.text
            body = applied.json()
            assert body["status"] == "completed"
            node = body["new_graph"]["nodes"][0]
            assert node["id"] == expected_id
            assert node["title"] == expected_title

        assert runtime.answers_seen == [
            [{"group_id": "intent", "selected": ["写作"]}],
            [{"group_id": "intent", "selected": ["翻译"]}],
        ]
        # 精化阶段必须看到方案上下文与 ask_user 问答摘要。
        assert len(runtime.assistant_prompts) == 2
        assert "已确认方案摘要" in runtime.assistant_prompts[0]
        assert "创建写作用途节点" in runtime.assistant_prompts[0]
        assert "用户问答摘要（必须遵守）" in runtime.assistant_prompts[0]
        assert "选择用途 → 写作" in runtime.assistant_prompts[0]
        assert "选择用途 → 翻译" in runtime.assistant_prompts[1]
    finally:
        set_runtime_override(MockRuntime())


def test_nlcompile_active_returns_planned_session_and_apply_rebuilds_from_db(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_from_db",
                        "type": "generate",
                        "position": {"x": 10, "y": 20},
                        "title": "DB 重建",
                        "prompt": "根据已确认方案生成内容",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "创建一个节点并测试恢复",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert planned.status_code == 200, planned.text
        body = planned.json()
        assert body["status"] == "planned"

        active = auth_client.get(f"/api/apps/{app_id}/nlcompile/active")
        assert active.status_code == 200, active.text
        assert active.json()["compile_id"] == body["compile_id"]
        assert active.json()["status"] == "planned"

        nlcompile_service._compile_sessions.clear()
        applied = auth_client.post(f"/api/nlcompile/{body['compile_id']}/apply")
        assert applied.status_code == 200, applied.text
        assert applied.json()["new_graph"]["nodes"][0]["id"] == "n_from_db"
    finally:
        nlcompile_service._compile_sessions.clear()
        set_runtime_override(MockRuntime())


def test_nlcompile_active_returns_204_when_no_active_session(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)

    active = auth_client.get(f"/api/apps/{app_id}/nlcompile/active")

    assert active.status_code == 204
    assert active.content == b""


def test_nlcompile_resume_replays_from_db_after_memory_session_lost(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    runtime = ReplayAwareRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "根据用户选择创建对应节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert response.status_code == 200, response.text
        waiting = response.json()
        assert waiting["status"] == "waiting_for_user"

        session = nlcompile_service._compile_sessions.get(waiting["compile_id"])
        if session and session.task:
            session.task.cancel()
        nlcompile_service._compile_sessions.clear()

        resumed = auth_client.post(
            f"/api/nlcompile/{waiting['compile_id']}/resume",
            json={"answers": [{"group_id": "intent", "selected": ["写作"]}]},
        )
        assert resumed.status_code == 200, resumed.text
        planned = resumed.json()
        assert planned["status"] == "planned"
        assert planned["plan"]["goal_summary"] == "重放历史后创建写作用途节点"
        assert "用户问答历史" in runtime.prompts[-1]
        assert "选择用途 → 写作" in runtime.prompts[-1]
    finally:
        nlcompile_service._compile_sessions.clear()
        set_runtime_override(MockRuntime())


def test_nlcompile_refine_reuses_history_and_updates_plan(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    first_plan = json.dumps(
        {
            "plan": {
                "goal_summary": "生成初版摘要页面",
                "implementation_steps": ["新增摘要生成节点并配置输出"],
                "graph_changes": ["新增摘要生成节点"],
                "expected_inputs": ["主题"],
                "expected_outputs": ["摘要页面"],
                "acceptance_criteria": ["运行后输出摘要页面"],
            }
        },
        ensure_ascii=False,
    )
    second_plan = json.dumps(
        {
            "plan": {
                "goal_summary": "生成带对比表格的摘要页面",
                "implementation_steps": ["更新摘要生成节点以输出对比表格"],
                "graph_changes": ["更新摘要生成节点，加入对比表格"],
                "expected_inputs": ["主题"],
                "expected_outputs": ["带对比表格的摘要页面"],
                "acceptance_criteria": ["运行后页面包含摘要和对比表格"],
            }
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(texts=[first_plan, second_plan])
    set_runtime_override(runtime)
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "做一个摘要页面",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert planned.status_code == 200, planned.text
        compile_id = planned.json()["compile_id"]

        refined = auth_client.post(
            f"/api/nlcompile/{compile_id}/refine",
            json={"feedback": "补充：需要加入对比表格"},
        )
        assert refined.status_code == 200, refined.text
        body = refined.json()
        assert body["status"] == "planned"
        assert body["compile_id"] == compile_id
        assert body["plan"]["goal_summary"] == "生成带对比表格的摘要页面"
        assert "最近一轮方案 JSON" in runtime.prompts[1]
        assert "生成初版摘要页面" in runtime.prompts[1]
        assert "第 1 轮反馈：补充：需要加入对比表格" in runtime.prompts[1]
    finally:
        set_runtime_override(MockRuntime())


def test_nlcompile_refine_limit(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    plan_text = json.dumps(
        {
            "plan": {
                "goal_summary": "持续调整方案",
                "implementation_steps": ["按本轮反馈更新目标节点"],
                "graph_changes": ["更新节点"],
                "expected_inputs": ["输入"],
                "expected_outputs": ["输出"],
                "acceptance_criteria": ["应用后节点反映本轮反馈"],
            }
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(texts=[plan_text] * 6)
    set_runtime_override(runtime)
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "做一个可调整方案",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert planned.status_code == 200, planned.text
        compile_id = planned.json()["compile_id"]
        for index in range(5):
            response = auth_client.post(
                f"/api/nlcompile/{compile_id}/refine",
                json={"feedback": f"第 {index + 1} 次调整"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "planned"
        blocked = auth_client.post(
            f"/api/nlcompile/{compile_id}/refine",
            json={"feedback": "第 6 次调整"},
        )
        assert blocked.status_code == 400
        assert "方案调整轮数过多" in blocked.json()["detail"]
    finally:
        set_runtime_override(MockRuntime())


def test_nlcompile_waiting_resume_with_text_returns_completed_plan(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_after_text",
                        "type": "generate",
                        "position": {"x": 10, "y": 20},
                        "title": "按输入生成",
                        "prompt": "ok",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    ask_payload = {
        "context": {"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
        "groups": [
            {
                "id": "intent",
                "type": "single",
                "label": "选择用途",
                "options": _decision_options(["写作", "翻译", "总结"]),
            }
        ]
    }
    set_runtime_override(MockRuntime())
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": app_id,
            "instruction": f"[[ask_user:{json.dumps(ask_payload, ensure_ascii=False)}]] [[respond:{patch_text}]]",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 200, response.text
    waiting = response.json()
    assert waiting["status"] == "waiting_for_user"

    resumed = auth_client.post(
        f"/api/nlcompile/{waiting['compile_id']}/resume",
        json={"answers": [], "text": "直接用我的输入"},
    )
    assert resumed.status_code == 200, resumed.text
    planned = resumed.json()
    assert planned["status"] == "planned"
    body = auth_client.post(f"/api/nlcompile/{planned['compile_id']}/apply").json()
    assert body["status"] == "completed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_after_text"


def test_nlcompile_cancel_running_session_returns_409(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    compile_id = f"nlc_cancel_{uuid.uuid4().hex[:8]}"

    def post_compile():
        return auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "compile_id": compile_id,
                "instruction": '[[delay:5]] [[respond:{"patches":[]}]]',
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(post_compile)
        time.sleep(0.2)
        cancel = auth_client.post(f"/api/nlcompile/{compile_id}/cancel")
        assert cancel.status_code == 204, cancel.text
        response = future.result(timeout=3)

    assert response.status_code == 409
    assert response.json()["detail"] == "编译已取消"


def test_nlcompile_cancel_waiting_session_removes_resume(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps({"patches": []})
    ask_payload = {
        "context": {"title": "确认应用用途", "summary": "生成方案前需要确认这个应用主要服务哪类任务。"},
        "groups": [
            {
                "id": "intent",
                "type": "single",
                "label": "选择用途",
                "options": _decision_options(["写作", "翻译", "总结"]),
            }
        ]
    }
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": app_id,
            "instruction": f"[[ask_user:{json.dumps(ask_payload, ensure_ascii=False)}]] [[respond:{patch_text}]]",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 200, response.text
    waiting = response.json()
    assert waiting["status"] == "waiting_for_user"

    cancel = auth_client.post(f"/api/nlcompile/{waiting['compile_id']}/cancel")
    assert cancel.status_code == 204, cancel.text

    resumed = auth_client.post(
        f"/api/nlcompile/{waiting['compile_id']}/resume",
        json={"answers": [{"group_id": "intent", "selected": ["写作"]}]},
    )
    assert resumed.status_code == 404
    assert resumed.json()["detail"] == "编译会话不存在"


def test_nlcompile_pre_cancelled_compile_id_does_not_start_runtime(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    compile_id = f"nlc_precancel_{uuid.uuid4().hex[:8]}"

    cancel = auth_client.post(f"/api/nlcompile/{compile_id}/cancel")
    assert cancel.status_code == 204, cancel.text
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": app_id,
            "compile_id": compile_id,
            "instruction": '[[respond:{"patches":[]}]]',
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "编译已取消"


def test_nlcompile_apply_owned_by_other_user_returns_404(client, auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps(
        {
            "patches": [
                _structured_patch(
                    "add_node",
                    node={
                        "id": "n_owner_only",
                        "type": "generate",
                        "title": "仅所有者应用",
                        "prompt": "生成所有者请求的内容。",
                    },
                )
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=patch_text))
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "添加一个生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert planned.status_code == 200, planned.text
        compile_id = planned.json()["compile_id"]

        other_token = _regular_user_token()
        other_apply = client.post(
            f"/api/nlcompile/{compile_id}/apply",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other_apply.status_code == 404

        owner_apply = auth_client.post(f"/api/nlcompile/{compile_id}/apply")
        assert owner_apply.status_code == 200, owner_apply.text
        assert owner_apply.json()["status"] == "completed"
    finally:
        set_runtime_override(MockRuntime())


def test_nlcompile_apply_rejects_ask_user(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    runtime = ApplyAsksRuntime(text=json.dumps({"patches": []}))
    set_runtime_override(runtime)
    try:
        planned = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "实施时不要再提问",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
        assert planned.status_code == 200, planned.text
        body = planned.json()
        assert body["status"] == "planned"

        applied = auth_client.post(f"/api/nlcompile/{body['compile_id']}/apply")
    finally:
        set_runtime_override(MockRuntime())

    assert applied.status_code == 502
    assert applied.json()["detail"] == "Agent 编译失败，请检查 Agent 配置或稍后重试"
    assert runtime.call_count == 2


def test_nlcompile_rejects_empty_patches_after_repair_attempts(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    runtime = ScriptedRuntime(text=json.dumps({"patches": []}))
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增一个摘要生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "Agent 生成的修改未通过校验，请调整描述后重试"
    assert runtime.call_count == 4
    assert runtime.assistant_call_count == 0
    assert runtime.layout_call_count == 0
    assert runtime.output_schemas[1:] == [NL_COMPILE_PATCH_OUTPUT_SCHEMA] * 3
    assert all("patches 不能为空" in prompt for prompt in runtime.prompts[2:])


def test_nlcompile_rejects_invalid_patches_after_repair_attempts(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_bad",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "缺 prompt",
                        # 故意缺 prompt → 校验失败被拒绝
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_ok",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "合法节点",
                        "prompt": "ok",
                    },
                },
            ]
        }
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增两个 generate",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    body = response.json()
    assert body["detail"] == "Agent 生成的修改未通过校验，请调整描述后重试"
    assert "new_graph" not in body
    assert runtime.call_count == 4
    assert "失败信息 JSON" in runtime.prompts[2]
    assert "add_node.node 不符合节点类型约束" in runtime.prompts[2]


def test_nlcompile_rejects_second_singleton_nodes(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    current_graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "text"},
            },
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Generate",
                "prompt": "根据用户输入生成结果",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 0, "y": 0},
                "title": "Output",
                "prompt": "展示最终 HTML",
            },
        ],
        "execution_edges": [
            {"id": "e_input_gen", "source": "n_input", "target": "n_gen"},
            {"id": "e_gen_out", "source": "n_gen", "target": "n_out"},
        ],
    }
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_output_extra",
                        "type": "output",
                        "position": {"x": 0, "y": 0},
                        "title": "Extra Output",
                        "prompt": "展示另一个 HTML",
                    },
                },
                {"op": "add_edge", "edge": {"id": "e_gen_extra_out", "source": "n_gen", "target": "n_output_extra"}},
            ]
        }
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "再新增一个输出节点",
                "current_graph": current_graph,
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent 生成的修改未通过校验，请调整描述后重试"
    assert runtime.call_count == 4
    assert "工作流只能有一个 output 节点" in runtime.prompts[2]


def test_nlcompile_rejects_second_user_input_node(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    current_graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "text"},
            }
        ],
        "execution_edges": [],
    }
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_input_extra",
                        "type": "user_input",
                        "position": {"x": 0, "y": 0},
                        "title": "Extra Input",
                        "input_schema": {"label": "extra input", "kind": "text"},
                    },
                }
            ]
        }
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "再新增一个输入节点",
                "current_graph": current_graph,
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent 生成的修改未通过校验，请调整描述后重试"
    assert runtime.call_count == 4
    assert "工作流只能有一个 user_input 节点" in runtime.prompts[2]


def test_nlcompile_repairs_invalid_patch_on_second_attempt(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    invalid = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_bad",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "缺 prompt",
                    },
                }
            ]
        }
    )
    repaired = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_fixed",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "已修复",
                        "prompt": "ok",
                    },
                }
            ]
        }
    )
    runtime = ScriptedRuntime(texts=[invalid, repaired])
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增一个 generate",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["warnings"] == []
    assert body["applied_patches"][0]["node"]["id"] == "n_generate_fixed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_generate_fixed"
    assert runtime.call_count == 3
    assert "只输出一个 JSON 对象" in runtime.prompts[2]
    assert "上一轮 Agent 输出" in runtime.prompts[2]


def test_nlcompile_retries_when_prompt_assistant_fails(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    first = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_first",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "首次",
                        "prompt": "首次提示词",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    second = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_second",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "修复",
                        "prompt": "修复后的提示词",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = AssistantFailsOnceRuntime(texts=[first, second])
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增一个 generate",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert runtime.call_count == 3
    assert runtime.assistant_call_count == 2
    assert any("提示词助手生成失败" in prompt for prompt in runtime.prompts)
    assert any("boom" in prompt for prompt in runtime.prompts)
    assert body["new_graph"]["nodes"][0]["id"] == "n_generate_second"
    assert body["new_graph"]["nodes"][0]["prompt"] == "修复后的提示词"


def test_nlcompile_applies_prompt_assistant_output_contract(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    patch_text = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_topics",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "生成选题",
                        "prompt": "生成选题清单。",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    output_contract = {
        "type": "json",
        "json_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                }
            },
            "required": ["topics"],
        },
    }
    runtime = AssistantContractRuntime(
        text=patch_text,
        assistant_prompt="生成结构化选题清单。",
        output_contract=output_contract,
    )
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增一个生成选题的节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    node = body["new_graph"]["nodes"][0]
    assert node["prompt"] == "生成结构化选题清单。"
    assert node["output_contract"] == output_contract
    assert body["applied_patches"][0]["node"]["output_contract"] == output_contract


def test_nlcompile_prunes_redundant_transitive_edge(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_a",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "A",
                        "prompt": "生成 A",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_b",
                        "type": "generate",
                        "position": {"x": 300, "y": 0},
                        "title": "B",
                        "prompt": "基于 A 生成 B",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_c",
                        "type": "generate",
                        "position": {"x": 600, "y": 0},
                        "title": "C",
                        "prompt": "基于 B 生成 C",
                    },
                },
                {"op": "add_edge", "edge": {"id": "e_a_b", "source": "n_a", "target": "n_b"}},
                {"op": "add_edge", "edge": {"id": "e_b_c", "source": "n_b", "target": "n_c"}},
                {"op": "add_edge", "edge": {"id": "e_a_c", "source": "n_a", "target": "n_c"}},
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增三步串行生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert runtime.call_count == 2
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_a_b", "e_b_c"}
    assert all(
        patch.get("op") != "add_edge" or patch.get("edge", {}).get("id") != "e_a_c"
        for patch in body["applied_patches"]
    )
    assert body["warnings"] == ["已自动清理 1 条冗余连线"]


def test_nlcompile_prunes_existing_redundant_edge_on_unrelated_patch(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "update_node",
                    "id": "n_b",
                    "patch": {"title": "B updated"},
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "只更新 B 节点标题",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_a",
                            "type": "generate",
                            "position": {"x": 0, "y": 0},
                            "title": "A",
                            "prompt": "生成 A",
                        },
                        {
                            "id": "n_b",
                            "type": "generate",
                            "position": {"x": 300, "y": 0},
                            "title": "B",
                            "prompt": "基于 A 生成 B",
                        },
                        {
                            "id": "n_c",
                            "type": "generate",
                            "position": {"x": 600, "y": 0},
                            "title": "C",
                            "prompt": "基于 B 生成 C",
                        },
                    ],
                    "execution_edges": [
                        {"id": "e_a_b", "source": "n_a", "target": "n_b"},
                        {"id": "e_b_c", "source": "n_b", "target": "n_c"},
                        {"id": "e_a_c", "source": "n_a", "target": "n_c"},
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_a_b", "e_b_c"}
    assert {"op": "remove_edge", "id": "e_a_c"} in body["applied_patches"]
    assert body["warnings"] == ["已自动清理 1 条冗余连线"]


def test_nlcompile_repair_prompt_rejects_delete_edge_and_restates_protocol(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)

    def patch_payload(*, include_redundant: bool, delete_edge: bool = False) -> str:
        patches: list[dict[str, Any]] = [
            {
                "op": "add_node",
                "node": {
                    "id": "n_a",
                    "type": "generate",
                    "position": {"x": 0, "y": 0},
                    "title": "A",
                    "prompt": "生成 A",
                },
            },
            {
                "op": "add_node",
                "node": {
                    "id": "n_b",
                    "type": "generate",
                    "position": {"x": 300, "y": 0},
                    "title": "B",
                    "prompt": "基于 A 生成 B",
                },
            },
            {
                "op": "add_node",
                "node": {
                    "id": "n_c",
                    "type": "generate",
                    "position": {"x": 600, "y": 0},
                    "title": "C",
                    "prompt": "基于 B 生成 C",
                },
            },
            {"op": "add_edge", "edge": {"id": "e_a_b", "source": "n_a", "target": "n_b"}},
            {"op": "add_edge", "edge": {"id": "e_b_c", "source": "n_b", "target": "n_c"}},
        ]
        if include_redundant:
            patches.append({"op": "add_edge", "edge": {"id": "e_a_c", "source": "n_a", "target": "n_c"}})
        if delete_edge:
            patches.append({"op": "delete_edge", "id": "e_a_c"})
        return json.dumps({"patches": patches}, ensure_ascii=False)

    runtime = ScriptedRuntime(
        texts=[
            patch_payload(include_redundant=False, delete_edge=True),
            patch_payload(include_redundant=True),
        ]
    )
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增三步串行生成节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_a_b", "e_b_c"}
    assert runtime.call_count == 3
    assert "Mira graph patch 协议（后端强制）" in runtime.prompts[1]
    assert '{"op":"remove_edge","id":"...","node_json":null' in runtime.prompts[1]
    assert "delete_edge" in runtime.prompts[1]
    assert "patch.op 不支持；删除连线只能使用 remove_edge" in runtime.prompts[2]
    assert '"patch_op": "delete_edge"' in runtime.prompts[2]
    assert '{"op":"remove_edge","id":"...","node_json":null' in runtime.prompts[2]
    assert all(patch["op"] != "delete_edge" for patch in body["applied_patches"])
    assert all(
        patch.get("op") != "add_edge" or patch.get("edge", {}).get("id") != "e_a_c"
        for patch in body["applied_patches"]
    )
    assert body["warnings"] == ["已自动清理 1 条冗余连线"]


def test_nlcompile_allows_parallel_upstreams_to_same_target(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_a",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "A",
                        "prompt": "生成 A",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_b",
                        "type": "generate",
                        "position": {"x": 0, "y": 160},
                        "title": "B",
                        "prompt": "生成 B",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_c",
                        "type": "generate",
                        "position": {"x": 320, "y": 80},
                        "title": "C",
                        "prompt": "综合 A 和 B 生成 C",
                    },
                },
                {"op": "add_edge", "edge": {"id": "e_a_c", "source": "n_a", "target": "n_c"}},
                {"op": "add_edge", "edge": {"id": "e_b_c", "source": "n_b", "target": "n_c"}},
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增两个并行上游汇聚到一个节点",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert runtime.call_count == 2
    assert runtime.layout_call_count == 1
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_a_c", "e_b_c"}
    assert body["warnings"] == []


def test_nlcompile_prune_keeps_condition_branch_edges(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "update_node",
                    "id": "n_b",
                    "patch": {"title": "B updated"},
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "只更新 B 节点标题",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_cond",
                            "type": "condition",
                            "mode": "binary",
                            "branches": [{"key": "true", "label": "是"}, {"key": "false", "label": "否"}],
                            "position": {"x": 0, "y": 0},
                            "title": "判断",
                            "prompt": "判断是否继续。",
                        },
                        {
                            "id": "n_b",
                            "type": "generate",
                            "position": {"x": 300, "y": 0},
                            "title": "B",
                            "prompt": "基于判断生成 B。",
                        },
                        {
                            "id": "n_c",
                            "type": "generate",
                            "position": {"x": 600, "y": 0},
                            "title": "C",
                            "prompt": "基于 B 生成 C。",
                        },
                    ],
                    "execution_edges": [
                        {"id": "e_cond_b", "source": "n_cond", "target": "n_b", "branch_key": "true"},
                        {"id": "e_b_c", "source": "n_b", "target": "n_c"},
                        {"id": "e_cond_c", "source": "n_cond", "target": "n_c", "branch_key": "false"},
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_cond_b", "e_b_c", "e_cond_c"}
    assert body["warnings"] == []


def test_nlcompile_prunes_redundant_transitive_execution_edge(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "update_node",
                    "id": "n_b",
                    "patch": {"title": "B updated"},
                }
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "只更新 B 节点标题",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_a",
                            "type": "generate",
                            "position": {"x": 0, "y": 0},
                            "title": "A",
                            "prompt": "生成 A。",
                        },
                        {
                            "id": "n_b",
                            "type": "generate",
                            "position": {"x": 300, "y": 0},
                            "title": "B",
                            "prompt": "基于 A 生成 B。",
                        },
                        {
                            "id": "n_output",
                            "type": "output",
                            "position": {"x": 600, "y": 0},
                            "title": "输出",
                            "prompt": "输出最终结果。",
                        },
                    ],
                    "execution_edges": [
                        {"id": "e_a_b", "source": "n_a", "target": "n_b"},
                        {"id": "e_b_output", "source": "n_b", "target": "n_output"},
                        {"id": "e_a_output", "source": "n_a", "target": "n_output"},
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {"e_a_b", "e_b_output"}
    assert body["warnings"] == ["已自动清理 1 条冗余连线"]


def test_nlcompile_logs_invalid_patch_reason(auth_client, configure_codex, monkeypatch):
    configure_codex()
    app_id = _create_app(auth_client)
    logged: list[str] = []

    def record_warning(message: str, *args, **kwargs) -> None:
        logged.append(message % args)

    monkeypatch.setattr(nlcompile_service.logger, "warning", record_warning)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_bad",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "缺 prompt",
                    },
                }
            ]
        }
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "新增一个非法 generate",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert runtime.call_count == 4
    assert any("nlcompile patch generation failed" in item for item in logged)
    assert any("nlcompile final failure" in item for item in logged)
    assert any("add_node.node 不符合节点类型约束" in item for item in logged)


def test_nlcompile_retries_order_dependent_output_source_update(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    output_prompt = (
        "基于上游生成代码和交付文档，整理最终交付结果。必须提供生成代码和文档相关文件的下载链接或可下载文件路径；"
        "如果无法生成真实链接，应明确列出可下载文件路径。直接输出最终内容。"
    )
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_generate_docs",
                        "type": "generate",
                        "position": {"x": 960, "y": 0},
                        "title": "生成交付文档",
                        "description": "基于确认需求和生成代码撰写完整交付文档。",
                        "prompt": "基于上游已确认需求和生成代码，生成最终交付文档。",
                        "reasoning_effort": "high",
                        "model": "gpt-5.5",
                    },
                },
                {
                    "op": "update_node",
                    "id": "n_output_docs",
                    "patch": {
                        "position": {"x": 1280, "y": 0},
                        "prompt": output_prompt,
                    },
                },
                {"op": "remove_edge", "id": "e_code_to_docs"},
                {"op": "remove_edge", "id": "e_requirements_to_docs"},
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_code_to_generated_docs",
                        "source": "n_generate_code",
                        "target": "n_generate_docs",
                    },
                },
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_generated_docs_to_output",
                        "source": "n_generate_docs",
                        "target": "n_output_docs",
                    },
                },
            ]
        },
        ensure_ascii=False,
    )
    set_runtime_override(ScriptedRuntime(text=payload))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "输出文档，拆成一个节点，在最终输出前，拆分对应的提示词",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_input_industry",
                            "type": "user_input",
                            "position": {"x": 0, "y": 0},
                            "title": "输入行业",
                            "input_schema": {"label": "目标行业", "kind": "text", "required": True},
                        },
                        {
                            "id": "n_generate_requirements",
                            "type": "generate",
                            "position": {"x": 320, "y": 0},
                            "title": "需求发散与确认",
                            "prompt": "根据用户输入的行业进行发散。",
                        },
                        {
                            "id": "n_generate_code",
                            "type": "generate",
                            "position": {"x": 640, "y": 0},
                            "title": "自动生成代码",
                            "prompt": "根据已确认的产品需求生成代码。",
                        },
                        {
                            "id": "n_output_docs",
                            "type": "output",
                            "position": {"x": 960, "y": 0},
                            "title": "输出文档与下载链接",
                            "prompt": "基于上游已确认需求和生成代码，输出最终交付文档。",
                        },
                    ],
                    "execution_edges": [
                        {
                            "id": "e_industry_to_requirements",
                            "source": "n_input_industry",
                            "target": "n_generate_requirements",
                        },
                        {
                            "id": "e_requirements_to_code",
                            "source": "n_generate_requirements",
                            "target": "n_generate_code",
                        },
                        {"id": "e_code_to_docs", "source": "n_generate_code", "target": "n_output_docs"},
                        {
                            "id": "e_requirements_to_docs",
                            "source": "n_generate_requirements",
                            "target": "n_output_docs",
                        },
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"] == []
    assert len(body["applied_patches"]) == 6
    nodes = {node["id"]: node for node in body["new_graph"]["nodes"]}
    assert "source_node_id" not in nodes["n_output_docs"]
    assert nodes["n_output_docs"]["prompt"] == output_prompt
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert "e_generated_docs_to_output" in edge_ids


def test_nlcompile_soft_copyright_replaces_manual_supplement_nodes_with_ai(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    instruction = "删除用户补充公司主体信息，删除测试账号补充，由ai生成"
    payload = json.dumps(
        {
            "patches": [
                {"op": "remove_node", "id": "n_company_subject_manual"},
                {"op": "remove_node", "id": "n_test_account_manual"},
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_ai_company_subject",
                        "type": "generate",
                        "position": {"x": 320, "y": 0},
                        "title": "AI生成公司主体信息",
                        "prompt": "基于用户输入和软著申请材料要求，自动生成公司主体信息，不再要求用户补充。",
                    },
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_ai_test_account",
                        "type": "generate",
                        "position": {"x": 640, "y": 0},
                        "title": "AI生成测试账号说明",
                        "prompt": "基于软件功能、运行方式和申请材料要求，自动生成测试账号说明，不再要求用户补充测试账号。",
                    },
                },
                {"op": "add_edge", "edge": {"id": "e_input_to_ai_company", "source": "n_input", "target": "n_ai_company_subject"}},
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_ai_company_to_ai_test_account",
                        "source": "n_ai_company_subject",
                        "target": "n_ai_test_account",
                    },
                },
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_ai_test_account_to_materials",
                        "source": "n_ai_test_account",
                        "target": "n_generate_materials",
                    },
                },
            ]
        },
        ensure_ascii=False,
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": instruction,
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_input",
                            "type": "user_input",
                            "position": {"x": 0, "y": 0},
                            "title": "输入软著基础信息",
                            "input_schema": {"label": "软件基础信息", "kind": "text", "required": True},
                        },
                        {
                            "id": "n_company_subject_manual",
                            "type": "generate",
                            "position": {"x": 320, "y": 0},
                            "title": "用户补充公司主体信息",
                            "prompt": "请用户补充公司主体信息，用于软著申请材料。",
                        },
                        {
                            "id": "n_test_account_manual",
                            "type": "generate",
                            "position": {"x": 640, "y": 0},
                            "title": "测试账号补充",
                            "prompt": "请用户补充测试账号、密码和测试地址。",
                        },
                        {
                            "id": "n_generate_materials",
                            "type": "generate",
                            "position": {"x": 960, "y": 0},
                            "title": "生成软著材料",
                            "prompt": "基于上游信息生成软著申请材料。",
                        },
                        {
                            "id": "n_output",
                            "type": "output",
                            "position": {"x": 1280, "y": 0},
                            "title": "输出软著材料",
                            "prompt": "输出最终软著材料。",
                        },
                    ],
                    "execution_edges": [
                        {"id": "e_input_to_company", "source": "n_input", "target": "n_company_subject_manual"},
                        {
                            "id": "e_company_to_test_account",
                            "source": "n_company_subject_manual",
                            "target": "n_test_account_manual",
                        },
                        {
                            "id": "e_test_account_to_materials",
                            "source": "n_test_account_manual",
                            "target": "n_generate_materials",
                        },
                        {"id": "e_materials_to_output", "source": "n_generate_materials", "target": "n_output"},
                    ],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    nodes = {node["id"]: node for node in body["new_graph"]["nodes"]}
    assert "n_company_subject_manual" not in nodes
    assert "n_test_account_manual" not in nodes
    assert nodes["n_ai_company_subject"]["title"] == "AI生成公司主体信息"
    assert nodes["n_ai_test_account"]["title"] == "AI生成测试账号说明"
    assert "source_node_id" not in nodes["n_output"]
    edge_ids = {edge["id"] for edge in body["new_graph"]["execution_edges"]}
    assert edge_ids == {
        "e_input_to_ai_company",
        "e_ai_company_to_ai_test_account",
        "e_ai_test_account_to_materials",
        "e_materials_to_output",
    }
    assert all(
        edge.get("source") not in {"n_company_subject_manual", "n_test_account_manual"}
        and edge.get("target") not in {"n_company_subject_manual", "n_test_account_manual"}
        for edge in body["new_graph"]["execution_edges"]
    )
    assert body["warnings"] == []
    assert runtime.output_schemas[0] == NL_COMPILE_PLAN_OUTPUT_SCHEMA
    assert runtime.output_schemas[1] == NL_COMPILE_PATCH_OUTPUT_SCHEMA


def test_nlcompile_rejects_invalid_condition_edge_handle(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    payload = json.dumps(
        {
            "patches": [
                {
                    "op": "add_edge",
                    "edge": {
                        "id": "e_bad",
                        "source": "n_cond",
                        "target": "n_gen",
                        "branch_key": "maybe",
                    },
                }
            ]
        }
    )
    runtime = ScriptedRuntime(text=payload)
    set_runtime_override(runtime)
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "连到错误分支",
                "current_graph": {
                    "nodes": [
                        {
                            "id": "n_cond",
                            "type": "condition",
                            "position": {"x": 0, "y": 0},
                            "title": "判断",
                            "prompt": "ok",
                            "mode": "binary",
                            "branches": [{"key": "true"}, {"key": "false"}],
                        },
                        {
                            "id": "n_gen",
                            "type": "generate",
                            "position": {"x": 0, "y": 0},
                            "title": "生成",
                            "prompt": "ok",
                        },
                    ],
                    "execution_edges": [],
                },
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent 生成的修改未通过校验，请调整描述后重试"
    assert runtime.call_count == 4


def test_nlcompile_accepts_markdown_codeblock(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    inner = json.dumps(
        {
            "patches": [
                {
                    "op": "add_node",
                    "node": {
                        "id": "n_from_codeblock",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "代码块节点",
                        "prompt": "ok",
                    },
                }
            ]
        }
    )
    fenced = f"```json\n{inner}\n```"
    set_runtime_override(ScriptedRuntime(text=fenced))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "尝试删除一条不存在的连线",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["new_graph"]["nodes"][0]["id"] == "n_from_codeblock"


def test_nlcompile_runtime_failure_returns_502(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    set_runtime_override(ScriptedRuntime(text="", finished_with="error", error="boom"))
    try:
        response = auth_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "instruction": "随便",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent 编译失败，请检查 Agent 配置或稍后重试"


def test_nlcompile_unparseable_output_returns_502(auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)
    set_runtime_override(ScriptedRuntime(text="not json at all"))
    try:
        response = _post_nlcompile_and_apply(
            auth_client,
            {
                "app_id": app_id,
                "instruction": "随便",
                "current_graph": {"nodes": [], "execution_edges": []},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502


def test_nlcompile_app_not_found_returns_404(auth_client, configure_codex):
    configure_codex()
    response = auth_client.post(
        "/api/nlcompile",
        json={
            "app_id": "app_does_not_exist",
            "instruction": "随便",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 404


def test_nlcompile_app_owned_by_other_user_returns_404(client, auth_client, configure_codex):
    configure_codex()
    app_id = _create_app(auth_client)

    # 创建一个普通用户，用它的 token 访问 admin 的 app。
    other_token = _regular_user_token()
    response = client.post(
        "/api/nlcompile",
        headers={"Authorization": f"Bearer {other_token}"},
        json={
            "app_id": app_id,
            "instruction": "随便",
            "current_graph": {"nodes": [], "execution_edges": []},
        },
    )
    assert response.status_code == 404


def test_slim_graph_for_prompt_strips_layout_and_truncates_assets():
    graph = {
        "viewport": {"x": 1, "y": 2, "zoom": 0.5},
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "asset_kind": "text",
                "position": {"x": 1, "y": 2},
                "width": 100,
                "content": "长" * 3000,
            },
            {
                "id": "n_generate",
                "type": "generate",
                "position": {"x": 3, "y": 4},
                "title": "生成",
                "prompt": "写一段说明",
            },
        ],
        "execution_edges": [{"id": "e1", "source": "n_asset", "target": "n_generate"}],
    }
    prompt = nlcompile_service.build_plan_prompt("改一下", graph, "$graph_json|$instruction")
    assert '"position"' not in prompt
    assert '"viewport"' not in prompt
    assert '"width"' not in prompt
    assert "素材内容已截断" in prompt
    assert '"e1"' in prompt
    assert "写一段说明" in prompt
    # 瘦身只影响 prompt 文本，原 graph 不被修改。
    assert graph["viewport"] == {"x": 1, "y": 2, "zoom": 0.5}
    assert graph["nodes"][0]["position"] == {"x": 1, "y": 2}
    assert len(graph["nodes"][0]["content"]) == 3000
