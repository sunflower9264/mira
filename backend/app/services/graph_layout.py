from __future__ import annotations

import asyncio
import json
import math
import re
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.runtime.base import AgentChunk
from app.runtime.factory import get_runtime
from app.services import runtime_config
from app.services.graph_inputs import prepare_structural_graph
from app.services.graph_validation import GraphValidationError
from app.services.prompts import get_prompt_content, render_prompt
from app.services.reasoning_effort import max_reasoning_effort_for_agent
from app.services.runtime_paths import graph_layout_workspace
from app.services.settings import NO_ENABLED_AGENT_DETAIL, settings_out


async def beautify_graph_layout(
    db: AsyncSession,
    user_id: str,
    graph: dict[str, Any],
    node_sizes: dict[str, dict[str, float]] | None = None,
    *,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Use the app Agent to calculate node positions, then merge positions only."""

    try:
        graph = prepare_structural_graph(graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not graph.get("nodes"):
        return _clone_graph(graph)

    graph_agent = str(graph.get("agent") or "").strip()
    if not graph_agent:
        raise HTTPException(status_code=400, detail="应用未配置 Agent")

    settings = await settings_out(db, reveal_keys=True)
    agent = next((item for item in settings.agents if item.enabled and item.runtime == graph_agent), None)
    if not agent:
        raise HTTPException(status_code=400, detail=NO_ENABLED_AGENT_DETAIL)

    await runtime_config.write_configs(db)
    template = await get_prompt_content(db, "graph_layout_beautify")
    runtime = get_runtime(agent.runtime, user_id)
    return await beautify_graph_layout_with_runtime(
        runtime=runtime,
        user_id=user_id,
        agent=agent.runtime,
        graph=graph,
        node_sizes=node_sizes or {},
        template=template,
        cancel_event=cancel_event or asyncio.Event(),
    )


async def beautify_graph_layout_with_runtime(
    *,
    runtime: object,
    user_id: str,
    agent: str,
    graph: dict[str, Any],
    node_sizes: dict[str, dict[str, float]] | None,
    template: str,
    cancel_event: asyncio.Event,
) -> dict[str, Any]:
    try:
        graph = prepare_structural_graph(graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not graph.get("nodes"):
        return _clone_graph(graph)

    prompt = build_graph_layout_prompt(graph, node_sizes or {}, template)
    output = await _run_layout_agent(runtime, user_id, agent, prompt, cancel_event)
    positions = extract_layout_positions(output, graph)
    next_graph = _clone_graph(graph)
    for node in next_graph.get("nodes", []):
        node_id = node.get("id")
        if isinstance(node_id, str):
            node["position"] = positions[node_id]
    try:
        prepare_structural_graph(next_graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=502, detail=f"布局结果未通过校验：{exc}") from None
    return next_graph


def build_graph_layout_prompt(
    graph: dict[str, Any],
    node_sizes: dict[str, dict[str, float]],
    template: str,
) -> str:
    helper_instruction = render_prompt(
        template,
        {
            "graph_json": json.dumps(graph, ensure_ascii=False),
            "node_sizes_json": json.dumps(node_sizes, ensure_ascii=False),
        },
    ).strip()
    return f"""你是 Mira 的画布布局美化助手。

任务：只为现有 graph 节点计算新的 position 坐标。

硬性规则：
- 只输出一个 JSON 对象，形状必须是 {{"positions":[{{"id":"node_id","x":number,"y":number}}]}}。
- positions 必须覆盖当前 graph 的全部节点；每个节点只能出现一次。
- 禁止新增、删除、改名或改写节点；禁止修改 edges、prompt、title、description、agent、viewport 或其它字段。
- 不要输出 markdown、解释、注释或代码块。
- 不要调用 ask_user；直接完成布局。

管理员美化样式模板：
{helper_instruction}
"""


async def _run_layout_agent(
    runtime: object,
    user_id: str,
    agent: str,
    prompt: str,
    cancel_event: asyncio.Event,
) -> str:
    chunks: list[str] = []

    async def on_chunk(chunk: AgentChunk) -> None:
        if chunk.type == "text" and chunk.text:
            chunks.append(chunk.text)

    result = await runtime.execute(
        prompt=prompt,
        session_id=None,
        allowed_tools=None,
        model=None,
        reasoning_effort=max_reasoning_effort_for_agent(agent),
        cwd=graph_layout_workspace(user_id),
        on_chunk=on_chunk,
        cancel_event=cancel_event,
        on_ask_user=None,
        runtime_policy="execute",
    )
    if cancel_event.is_set() or result.finished_with == "cancelled":
        raise HTTPException(status_code=409, detail="布局美化已取消")
    if result.finished_with != "done":
        raise HTTPException(status_code=502, detail="Agent 美化布局失败，请检查 Agent 配置或稍后重试")
    return result.total_text or "".join(chunks)


def extract_layout_positions(text: str, graph: dict[str, Any]) -> dict[str, dict[str, float]]:
    data = _extract_json_object(text)
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list):
        raise HTTPException(status_code=502, detail="布局结果缺少 positions 数组")

    expected_ids = {
        node.get("id")
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    positions: dict[str, dict[str, float]] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="positions 数组项必须是对象")
        node_id = item.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise HTTPException(status_code=502, detail="布局结果包含无效节点 id")
        if node_id not in expected_ids:
            raise HTTPException(status_code=502, detail=f"布局结果包含未知节点：{node_id}")
        if node_id in positions:
            raise HTTPException(status_code=502, detail=f"布局结果包含重复节点：{node_id}")
        x = _layout_number(item.get("x"), node_id, "x")
        y = _layout_number(item.get("y"), node_id, "y")
        positions[node_id] = {"x": x, "y": y}

    missing = sorted(expected_ids - set(positions))
    if missing:
        raise HTTPException(status_code=502, detail=f"布局结果缺少节点：{', '.join(missing)}")
    return positions


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "positions" in candidate:
            return candidate
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"布局结果 JSON 解析失败：{exc}") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="布局结果必须是 JSON 对象")
    return data


def _layout_number(value: object, node_id: str, field: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise HTTPException(status_code=502, detail=f"节点 {node_id} 的 {field} 坐标无效")
    return round(float(value), 2)


def _clone_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(graph)
