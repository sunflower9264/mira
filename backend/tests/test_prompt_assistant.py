from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PromptAssistantGenerationRow, User
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus, AskUserRequest
from app.runtime.factory import set_runtime_override
from app.services.structured_output import PROMPT_ASSISTANT_OUTPUT_SCHEMA
from app.utils import now_utc
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime


class PromptAssistantRuntime:
    def __init__(
        self,
        text: str = "Generated prompt",
        *,
        texts: list[str] | None = None,
        wait_for_cancel: bool = False,
        finished_with: str = "done",
        error: str | None = None,
    ) -> None:
        self.text = text
        self.texts = list(texts or [])
        self.wait_for_cancel = wait_for_cancel
        self.finished_with = finished_with
        self.error = error
        self.last_prompt: str | None = None
        self.prompts: list[str] = []
        self.call_count = 0
        self.model: str | None = None
        self.reasoning_effort: str | None = None
        self.runtime_policy: str | None = None
        self.runtime_tools = None
        self.output_schema = None
        self.cancel_seen = False

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="prompt-assistant-test",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools,
        model,
        reasoning_effort,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.call_count += 1
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.runtime_policy = runtime_policy
        self.runtime_tools = runtime_tools
        self.output_schema = output_schema
        if self.wait_for_cancel:
            while not cancel_event.is_set():
                await asyncio.sleep(0.01)
            self.cancel_seen = True
            return AgentExecutionResult(session_id=session_id, total_text="", finished_with="cancelled")
        if self.finished_with != "done":
            return AgentExecutionResult(
                session_id=session_id,
                total_text="",
                finished_with=self.finished_with,
                error=self.error,
            )
        text = self.texts.pop(0) if self.texts else self.text
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


class PromptAssistantAskRuntime(PromptAssistantRuntime):
    def __init__(self) -> None:
        super().__init__(text="")
        self.answers_seen: list[list[dict]] = []

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools,
        model,
        reasoning_effort,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.last_prompt = prompt
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.runtime_policy = runtime_policy
        self.output_schema = output_schema
        if on_ask_user is None:
            return AgentExecutionResult(session_id=session_id, total_text="", finished_with="error", error="missing ask_user")
        request = AskUserRequest(
            context={"title": "确认提示词语气", "summary": "生成提示词前需要确认输出内容应该采用哪种语气。"},
            groups=[
                {
                    "id": "tone",
                    "type": "single",
                    "label": "选择语气",
                    "options": [
                        {"label": "专业", "description": "适合正式报告。", "recommended": True},
                        {"label": "轻松", "description": "适合社媒内容。", "recommended": False},
                    ],
                }
            ],
            tool_use_id="toolu_prompt_assistant",
        )
        result = await on_ask_user(request)
        if not result.ok:
            return AgentExecutionResult(session_id=session_id, total_text="", finished_with="error", error=result.error)
        self.answers_seen.append([answer.model_dump() for answer in result.answers])
        selected = result.answers[0].selected[0] if result.answers and result.answers[0].selected else "专业"
        text = json.dumps(
            {"prompt": f"请用{selected}语气生成市场分析。", "output_contract_json": None},
            ensure_ascii=False,
        )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


def _assistant_result(prompt: str, output_contract=None) -> str:
    output_contract_json = json.dumps(output_contract, ensure_ascii=False) if output_contract is not None else None
    return json.dumps({"prompt": prompt, "output_contract_json": output_contract_json}, ensure_ascii=False)


def _create_app_with_agent(client, agent: str | None = "claude") -> str:
    response = client.post("/api/apps", json={"name": "Prompt Assistant Demo"})
    assert response.status_code == 200, response.text
    app_id = response.json()["id"]
    graph = {"nodes": [], "edges": []}
    if agent is not None:
        graph["agent"] = agent
    patch = client.patch(f"/api/apps/{app_id}", json={"graph": graph})
    assert patch.status_code == 200, patch.text
    return app_id


def _prompt_graph(agent: str | None = "claude") -> dict:
    graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "输入行业",
                "input_schema": {"label": "行业", "kind": "text", "required": True},
            },
            {
                "id": "n_generate",
                "type": "generate",
                "position": {"x": 320, "y": 0},
                "title": "生成草稿",
                "description": "产出市场分析草稿。",
                "prompt": "保留这个输出格式。",
                "model": "test-model",
                "reasoning_effort": "high",
            },
            {
                "id": "n_output",
                "type": "output",
                "position": {"x": 640, "y": 0},
                "title": "输出报告",
                "source_node_id": "n_generate",
                "prompt": "整理成最终报告。",
            },
        ],
        "edges": [
            {"id": "e_input_generate", "source": "n_input", "target": "n_generate"},
            {"id": "e_generate_output", "source": "n_generate", "target": "n_output"},
        ],
    }
    if agent is not None:
        graph["agent"] = agent
    return graph


def _assistant_payload(*, app_id: str, graph: dict | None = None, **overrides) -> dict:
    payload = {
        "app_id": app_id,
        "agent": "claude",
        "graph": graph if graph is not None else _prompt_graph(),
        "node_id": "n_generate",
        "user_request": "写一个提示词",
        "model": "test-model",
        "reasoning_effort": "high",
    }
    payload.update(overrides)
    return payload


def _create_interrupted_prompt_assistant_generation(client, *, app_id: str, generation_id: str) -> None:
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    username = me.json()["username"]

    async def _insert() -> None:
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.username == username))
            assert user is not None
            db.add(
                PromptAssistantGenerationRow(
                    id=generation_id,
                    user_id=user.id,
                    app_id=app_id,
                    status="interrupted",
                    agent="claude",
                    prompt_json=json.dumps({"prompt": "prompt", "graph": _prompt_graph()}, ensure_ascii=False),
                    history_json="[]",
                    error="后端进程重启，提示词生成已暂停",
                    created_at=now_utc(),
                    updated_at=now_utc(),
                )
            )
            await db.commit()

    asyncio.run(_insert())


def test_prompt_assistant_requires_enabled_app_agent(auth_client):
    app_id = _create_app_with_agent(auth_client)
    response = auth_client.post(
        "/api/prompt-assistant/generate",
        json=_assistant_payload(app_id=app_id),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "无可用 Agent，请先在设置中启用 Agent"


def test_prompt_assistant_uses_ai_with_graph_context_and_user_request(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(text=_assistant_result("新的完整提示词"))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "新的完整提示词", "output_contract": None}
    assert runtime.model == "test-model"
    assert runtime.reasoning_effort == "high"
    assert runtime.runtime_policy == "ask_user_plan"
    assert runtime.output_schema == PROMPT_ASSISTANT_OUTPUT_SCHEMA
    assert runtime.last_prompt is not None
    assert "生成草稿" in runtime.last_prompt
    assert "保留这个输出格式。" in runtime.last_prompt
    assert "改成更适合市场分析" in runtime.last_prompt
    assert "先判断用户输入属于哪类" in runtime.last_prompt
    assert "以当前提示词为唯一底稿" in runtime.last_prompt
    assert "未涉及内容必须原样保留" in runtime.last_prompt
    assert "不要把修改指令本身写进最终 prompt" in runtime.last_prompt
    assert "输入行业" in runtime.last_prompt
    assert "输出报告" in runtime.last_prompt
    assert "直接上游节点" in runtime.last_prompt
    assert "直接下游节点" in runtime.last_prompt
    assert "output_contract" in runtime.last_prompt
    assert "自由文本是默认选择" in runtime.last_prompt
    assert "只有用户明确要求 JSON、结构化字段、固定字段" in runtime.last_prompt


def test_prompt_assistant_keeps_full_target_prompt_and_related_prompt_tail(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    graph = _prompt_graph()
    target_prompt = "TARGET_HEAD\n" + ("目标正文。" * 900) + "\nTARGET_TAIL_REQUIREMENT"
    downstream_prompt = "DOWNSTREAM_HEAD\n" + ("下游说明。" * 500) + "\nDOWNSTREAM_TAIL_REQUIREMENT"
    output_contract = {
        "type": "json",
        "json_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    }
    for node in graph["nodes"]:
        if node["id"] == "n_generate":
            node["prompt"] = target_prompt
            node["output_contract"] = output_contract
        elif node["id"] == "n_output":
            node["prompt"] = downstream_prompt

    runtime = PromptAssistantRuntime(text=_assistant_result(target_prompt))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, graph=graph, user_request="只修改开头措辞，其余内容保持不变"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert runtime.last_prompt is not None
    assert target_prompt in runtime.last_prompt
    assert "DOWNSTREAM_HEAD" in runtime.last_prompt
    assert "DOWNSTREAM_TAIL_REQUIREMENT" in runtime.last_prompt
    assert "中间已省略" in runtime.last_prompt
    assert '"summary"' in runtime.last_prompt
    assert "当前 output_contract" in runtime.last_prompt
    assert "未涉及内容必须原样保留" in runtime.last_prompt
    assert "当前设置仍合适且无需修改时返回 null" in runtime.last_prompt


def test_prompt_assistant_rejects_target_prompt_over_200_kib(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    graph = _prompt_graph()
    for node in graph["nodes"]:
        if node["id"] == "n_generate":
            node["prompt"] = "x" * (200 * 1024 + 1)
            break
    runtime = PromptAssistantRuntime(text=_assistant_result("unused"))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, graph=graph),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 400
    assert response.json()["detail"] == "当前节点提示词超过 200 KiB，提示词助手无法完整处理，请先缩短后重试"
    assert runtime.call_count == 0


def test_prompt_assistant_rejects_total_context_over_200_kib(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    graph = _prompt_graph()
    runtime = PromptAssistantRuntime(text=_assistant_result("unused"))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(
                app_id=app_id,
                graph=graph,
                user_request="x" * (200 * 1024),
            ),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 400
    assert response.json()["detail"] == "提示词助手上下文超过 200 KiB，请缩短用户说明或相邻节点提示词后重试"
    assert runtime.call_count == 0


def test_prompt_assistant_rejects_plain_text_output(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(text="新的完整提示词")
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent 未按 JSON 格式返回提示词，请稍后重试"
    assert runtime.output_schema == PROMPT_ASSISTANT_OUTPUT_SCHEMA


def test_prompt_assistant_repairs_malformed_structured_output(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        texts=[
            "新的完整提示词",
            _assistant_result("修复后的提示词"),
        ]
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "修复后的提示词", "output_contract": None}
    assert runtime.call_count == 2
    assert runtime.output_schema == PROMPT_ASSISTANT_OUTPUT_SCHEMA
    assert "校验失败原因" in runtime.prompts[1]
    assert "不要调用 ask_user 或其它工具" in runtime.prompts[1]


def test_prompt_assistant_includes_runtime_error_detail(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(text="", finished_with="error", error="Invalid schema for response_format")
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502
    assert "Agent 生成提示词失败" in response.json()["detail"]
    assert "Invalid schema for response_format" in response.json()["detail"]
    assert runtime.output_schema == PROMPT_ASSISTANT_OUTPUT_SCHEMA


def test_prompt_assistant_accepts_explanation_wrapped_json(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text="现在我来分析。\n\n```json\n"
        + _assistant_result("真正的提示词")
        + "\n```"
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "真正的提示词", "output_contract": None}


def test_prompt_assistant_accepts_full_fenced_json(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(text="```json\n" + _assistant_result("完整提示词") + "\n```")
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="改成更适合市场分析"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "完整提示词", "output_contract": None}


def test_prompt_assistant_accepts_prompt_with_inner_codeblock(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    prompt = '输出示例：\n```json\n{"title":"示例"}\n```'
    runtime = PromptAssistantRuntime(text=_assistant_result(prompt))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="生成 JSON 摘要"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": prompt, "output_contract": None}


def test_prompt_assistant_guides_recommendation_nodes_to_ask_when_key_basis_is_missing(
    auth_client,
    enable_claude_agent,
):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "读者需求",
                "input_schema": {"label": "想读什么书", "kind": "text", "required": True},
            },
            {
                "id": "n_generate",
                "type": "generate",
                "position": {"x": 320, "y": 0},
                "title": "生成书单推荐",
                "description": "根据用户需求推荐书单。",
                "prompt": "",
            },
        ],
        "edges": [{"id": "e_input_generate", "source": "n_input", "target": "n_generate"}],
    }
    runtime = PromptAssistantRuntime(text=_assistant_result("书单推荐提示词"))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(
                app_id=app_id,
                graph=graph,
                user_request="帮我生成这个书单推荐节点的提示词",
            ),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert runtime.last_prompt is not None
    assert "推荐、选择、排序、个性化类 generate 节点" in runtime.last_prompt
    assert "缺少目标、偏好、约束、数量或理由形式等关键依据时，应先问最关键的一项" in runtime.last_prompt
    assert "信息足够时不要确认" in runtime.last_prompt


def test_prompt_assistant_allows_intermediate_graph_and_strips_runtime_snapshot(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    graph = {
        "agent": "claude",
        "_runtime_tools": {"allowed_tool_ids": ["mcp:injected"]},
        "nodes": [
            {
                "id": "n_generate",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "生成草稿",
                "prompt": "保留这个输出格式。",
            }
        ],
        "edges": [],
    }
    runtime = PromptAssistantRuntime(text=_assistant_result("新的完整提示词"))
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, graph=graph, generation_id=generation_id),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"

    async def stored_prompt_json() -> str:
        async with SessionLocal() as db:
            row = await db.get(PromptAssistantGenerationRow, generation_id)
            assert row is not None
            return row.prompt_json

    assert "_runtime_tools" not in asyncio.run(stored_prompt_json())


def test_prompt_assistant_passes_planning_safe_mcp_tools(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_prompt_plan",
            "name": "prompt-plan-mcp",
            "enabled": True,
            "planning_enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/prompt-plan",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_prompt_execute",
            "name": "prompt-execute-mcp",
            "enabled": True,
            "planning_enabled": False,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/prompt-execute",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_prompt_disabled",
            "name": "prompt-disabled-mcp",
            "enabled": True,
            "planning_enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/prompt-disabled",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    runtime = PromptAssistantRuntime(text=_assistant_result("新的完整提示词"))
    graph = {
        **_prompt_graph(),
        "tools": {"disabled_tool_ids": ["mcp:mcp_prompt_disabled"]},
        "_runtime_tools": {
            "allowed_tool_ids": ["mcp:mcp_prompt_plan", "mcp:mcp_prompt_disabled"],
        },
    }
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, graph=graph),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert runtime.runtime_tools is not None
    assert [server.name for server in runtime.runtime_tools.mcp_servers] == ["prompt-plan-mcp"]


def test_prompt_assistant_waits_for_user_and_resumes(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    runtime = PromptAssistantAskRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, generation_id=generation_id, user_request="帮我生成提示词"),
        )
        assert response.status_code == 200, response.text
        waiting = response.json()
        assert waiting["status"] == "waiting_for_user"
        assert waiting["generation_id"] == generation_id
        labels = [option["label"] for option in waiting["request"]["groups"][0]["options"]]
        assert labels == ["专业", "轻松", "以上都不是"]
        assert runtime.runtime_policy == "ask_user_plan"
        assert "生成提示词提问规则" in (runtime.last_prompt or "")

        resumed = auth_client.post(
            f"/api/prompt-assistant/{generation_id}/resume",
            json={"answers": [{"group_id": "tone", "selected": ["轻松"]}]},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json() == {
            "status": "completed",
            "prompt": "请用轻松语气生成市场分析。",
            "output_contract": None,
        }
        assert runtime.answers_seen == [[{"group_id": "tone", "selected": ["轻松"]}]]
    finally:
        set_runtime_override(MockRuntime())


def test_prompt_assistant_waiting_active_endpoint(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    runtime = PromptAssistantAskRuntime()
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, generation_id=generation_id, user_request="帮我生成提示词"),
        )
        assert response.status_code == 200, response.text

        active = auth_client.get(f"/api/apps/{app_id}/prompt-assistant/active")
        assert active.status_code == 200, active.text
        assert active.json()["status"] == "waiting_for_user"
        assert active.json()["generation_id"] == generation_id
        assert active.json()["request"]["groups"][0]["id"] == "tone"
    finally:
        auth_client.post(f"/api/prompt-assistant/{generation_id}/cancel")
        set_runtime_override(MockRuntime())


def test_prompt_assistant_interrupted_active_endpoint(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    _create_interrupted_prompt_assistant_generation(auth_client, app_id=app_id, generation_id=generation_id)

    active = auth_client.get(f"/api/apps/{app_id}/prompt-assistant/active")

    assert active.status_code == 200, active.text
    assert active.json() == {
        "status": "interrupted",
        "generation_id": generation_id,
        "error": "后端进程重启，提示词生成已暂停",
    }


def test_prompt_assistant_active_returns_204_when_no_waiting_session(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)

    active = auth_client.get(f"/api/apps/{app_id}/prompt-assistant/active")

    assert active.status_code == 204
    assert active.content == b""


def test_prompt_assistant_guides_format_cleanup_as_edit(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    graph = _prompt_graph()
    for node in graph["nodes"]:
        if node["id"] == "n_generate":
            node["prompt"] = "第一段目标。\n\n\n第二段约束。"
            break
    runtime = PromptAssistantRuntime(
        text=json.dumps({"prompt": "第一段目标。\n第二段约束。", "output_contract_json": None}, ensure_ascii=False)
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, graph=graph, user_request="清除提示词多余的换行，换成正常的 txt 格式"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "第一段目标。\n第二段约束。", "output_contract": None}
    assert runtime.last_prompt is not None
    assert "第一段目标。\n\n\n第二段约束。" in runtime.last_prompt
    assert "清除提示词多余的换行，换成正常的 txt 格式" in runtime.last_prompt
    assert "格式清理：只整理换行、空格、列表层级、标点和可读性" in runtime.last_prompt
    assert "不改变业务语义、输出要求或节点职责" in runtime.last_prompt


def test_prompt_assistant_returns_generate_output_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text=json.dumps(
            {
                "prompt": "生成一个可下载的 PPT 大纲文件。",
                "output_contract_json": json.dumps({"type": "artifact", "artifact_kind": "ppt"}, ensure_ascii=False),
            },
            ensure_ascii=False,
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="生成 PPT 文件"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "completed",
        "prompt": "生成一个可下载的 PPT 大纲文件。",
        "output_contract": {
            "type": "artifact",
        "json_schema": None,
        "artifact_kind": "ppt",
        "max_count": None,
        "validate_office_documents": None,
        },
    }
    assert runtime.last_prompt is not None
    assert "当用户明确要求图片、代码包、HTML 文件" in runtime.last_prompt


def test_prompt_assistant_accepts_zip_output_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text=json.dumps(
            {
                "prompt": "生成一个可下载的 ZIP 源码包。",
                "output_contract_json": json.dumps(
                    {"type": "artifact", "artifact_kind": "zip"},
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="生成 ZIP 源码包"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json()["output_contract"] == {
        "type": "artifact",
        "json_schema": None,
        "artifact_kind": "zip",
        "max_count": None,
        "validate_office_documents": None,
    }
    assert runtime.last_prompt is not None
    assert "|archive|zip|file" in runtime.last_prompt


def test_prompt_assistant_preserves_office_validation_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text=json.dumps(
            {
                "prompt": "生成可实际打开的 Office 资料包。",
                "output_contract_json": json.dumps(
                    {
                        "type": "artifact",
                        "artifact_kind": "zip",
                        "max_count": 1,
                        "validate_office_documents": True,
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="精简提示词但保留 Office 验收"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json()["output_contract"] == {
        "type": "artifact",
        "json_schema": None,
        "artifact_kind": "zip",
        "max_count": 1,
        "validate_office_documents": True,
    }


def test_prompt_assistant_drops_json_field_level_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text=json.dumps(
            {
                "prompt": "生成结构化摘要。",
                "output_contract_json": json.dumps(
                    {"type": "json", "required_fields": ["title"], "json_schema": {"type": "object"}},
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="生成 JSON 摘要"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "生成结构化摘要。", "output_contract": None}


def test_prompt_assistant_drops_invalid_output_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    runtime = PromptAssistantRuntime(
        text=json.dumps(
            {
                "prompt": "生成结构化摘要。",
                "output_contract_json": json.dumps({"type": "artifact", "artifact_kind": "exe"}, ensure_ascii=False),
            },
            ensure_ascii=False,
        )
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, user_request="生成摘要"),
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "completed", "prompt": "生成结构化摘要。", "output_contract": None}


def test_prompt_assistant_requires_graph_agent(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client, agent=None)
    response = auth_client.post(
        "/api/prompt-assistant/generate",
        json=_assistant_payload(app_id=app_id, graph=_prompt_graph(agent=None), node_id="n_output"),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "应用未配置 Agent"


def test_prompt_assistant_rejects_stale_agent(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client, agent="claude")
    response = auth_client.post(
        "/api/prompt-assistant/generate",
        json=_assistant_payload(app_id=app_id, agent="codex"),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "应用 Agent 已变更，请刷新后重试"


def test_prompt_assistant_app_owned_by_other_user_returns_404(client, auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    user = create_regular_user(f"prompt_assistant_{uuid.uuid4().hex[:10]}")
    client.headers.update({"Authorization": f"Bearer {user['token']}"})

    response = client.post(
        "/api/prompt-assistant/generate",
        json=_assistant_payload(app_id=app_id),
    )
    assert response.status_code == 404


def test_prompt_assistant_cancel_sets_runtime_cancel_event(auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    runtime = PromptAssistantRuntime(wait_for_cancel=True)
    set_runtime_override(runtime)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                auth_client.post,
                "/api/prompt-assistant/generate",
                json=_assistant_payload(app_id=app_id, generation_id=generation_id, user_request="优化"),
            )
            _wait_for_runtime(runtime)
            cancel = auth_client.post(f"/api/prompt-assistant/{generation_id}/cancel")
            response = future.result(timeout=5)
    finally:
        set_runtime_override(MockRuntime())

    assert cancel.status_code == 204, cancel.text
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "提示词生成已取消"
    assert runtime.cancel_seen is True


def test_prompt_assistant_cancel_foreign_generation_returns_404(client, auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    runtime = PromptAssistantRuntime(wait_for_cancel=True)
    set_runtime_override(runtime)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                auth_client.post,
                "/api/prompt-assistant/generate",
                json=_assistant_payload(app_id=app_id, generation_id=generation_id, user_request="优化"),
            )
            _wait_for_runtime(runtime)
            user = create_regular_user(f"prompt_assistant_{uuid.uuid4().hex[:10]}")
            foreign_cancel = client.post(
                f"/api/prompt-assistant/{generation_id}/cancel",
                headers={"Authorization": f"Bearer {user['token']}"},
            )
            own_cancel = auth_client.post(f"/api/prompt-assistant/{generation_id}/cancel")
            response = future.result(timeout=5)
    finally:
        set_runtime_override(MockRuntime())

    assert foreign_cancel.status_code == 404
    assert own_cancel.status_code == 204
    assert response.status_code == 409


def test_prompt_assistant_resume_foreign_generation_returns_404(client, auth_client, enable_claude_agent):
    enable_claude_agent()
    app_id = _create_app_with_agent(auth_client)
    generation_id = f"pa_{uuid.uuid4().hex[:10]}"
    runtime = PromptAssistantAskRuntime()
    set_runtime_override(runtime)
    try:
        waiting = auth_client.post(
            "/api/prompt-assistant/generate",
            json=_assistant_payload(app_id=app_id, generation_id=generation_id, user_request="帮我生成提示词"),
        )
        assert waiting.status_code == 200, waiting.text
        user = create_regular_user(f"prompt_assistant_{uuid.uuid4().hex[:10]}")
        foreign_resume = client.post(
            f"/api/prompt-assistant/{generation_id}/resume",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={"answers": [{"group_id": "tone", "selected": ["专业"]}]},
        )
        own_cancel = auth_client.post(f"/api/prompt-assistant/{generation_id}/cancel")
    finally:
        set_runtime_override(MockRuntime())

    assert foreign_resume.status_code == 404
    assert own_cancel.status_code == 204


def _wait_for_runtime(runtime: PromptAssistantRuntime) -> None:
    deadline = time.monotonic() + 5
    while runtime.last_prompt is None:
        if time.monotonic() > deadline:
            raise AssertionError("runtime did not start")
        time.sleep(0.01)
