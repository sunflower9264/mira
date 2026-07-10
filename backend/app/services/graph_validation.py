from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.template import contains_template_token, strip_template_tokens
from app.services.output_contracts import validate_output_contract_config


PROMPT_NODE_TYPES = {"generate", "condition", "output"}
AGENT_NODE_TYPES = PROMPT_NODE_TYPES
NODE_TYPES = {"user_input", "generate", "output", "asset", "condition"}
DEFAULT_BRANCH_KEY = "__default__"
BRANCH_KEY_PATTERN = r"^[a-zA-Z0-9_]+$"


class GraphValidationError(Exception):
    pass


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """对 graph 做 Kahn 拓扑排序；存在环或非法连线时抛 GraphValidationError。

    返回的是节点对象列表（保持原 dict，调用方按需取 id/type）。
    """

    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str):
            raise GraphValidationError("节点缺少 id")
        if node_id in by_id:
            raise GraphValidationError(f"节点 id 重复：{node_id}")
        by_id[node_id] = node

    indegree: dict[str, int] = {node_id: 0 for node_id in by_id}
    children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        source = edge.get("source") if isinstance(edge, dict) else None
        target = edge.get("target") if isinstance(edge, dict) else None
        if source not in by_id or target not in by_id:
            raise GraphValidationError("连线引用了不存在的节点")
        children[source].append(target)
        indegree[target] = indegree.get(target, 0) + 1

    queue = [node_id for node_id, deg in indegree.items() if deg == 0]
    queue.sort()
    ordered: list[dict[str, Any]] = []
    while queue:
        node_id = queue.pop(0)
        ordered.append(by_id[node_id])
        for child in children[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
        queue.sort()
    if len(ordered) != len(by_id):
        raise GraphValidationError("graph 存在环路，无法拓扑排序")
    return ordered


def validate_graph_structure(graph: dict[str, Any]) -> None:
    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    by_id = _nodes_by_id(nodes)
    for node in by_id.values():
        if node.get("type") not in NODE_TYPES:
            raise GraphValidationError(f"未知节点类型：{node.get('type')}")
    output_count = sum(1 for node in by_id.values() if node.get("type") == "output")
    if output_count > 1:
        raise GraphValidationError("工作流只能有一个 output 节点")
    user_input_count = sum(1 for node in by_id.values() if node.get("type") == "user_input")
    if user_input_count > 1:
        raise GraphValidationError("工作流只能有一个 user_input 节点")
    incoming_sources: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    edge_ids: set[str] = set()
    normal_edges: set[tuple[str, str]] = set()
    condition_handles: set[tuple[str, str]] = set()

    for edge in edges:
        if not isinstance(edge, dict):
            raise GraphValidationError("连线格式非法")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id:
            raise GraphValidationError("连线缺少 id")
        if edge_id in edge_ids:
            raise GraphValidationError(f"连线 id 重复：{edge_id}")
        edge_ids.add(edge_id)

        source_id = edge.get("source")
        target_id = edge.get("target")
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            raise GraphValidationError("连线缺少 source 或 target")
        if source_id not in by_id or target_id not in by_id:
            raise GraphValidationError("连线引用了不存在的节点")
        if source_id == target_id:
            raise GraphValidationError("节点不能连接到自身")

        source_node = by_id[source_id]
        target_node = by_id[target_id]
        source_type = source_node.get("type")
        target_type = target_node.get("type")
        if source_type == "output":
            raise GraphValidationError(f"{source_type} 节点不能作为连线起点")
        if target_type in {"user_input", "asset"}:
            raise GraphValidationError("user_input 和 asset 节点不能作为连线终点")

        if source_type == "condition":
            handle = edge.get("source_handle")
            if not isinstance(handle, str) or not handle:
                raise GraphValidationError("condition 出边必须设置 source_handle")
            if handle not in _condition_handles(source_node):
                raise GraphValidationError(f"condition 出边 source_handle 无效：{handle}")
            condition_key = (source_id, handle)
            if condition_key in condition_handles:
                raise GraphValidationError("同一个 condition 分支最多只能连接一条出边")
            condition_handles.add(condition_key)
        else:
            if "source_handle" in edge:
                raise GraphValidationError("非 condition 出边不能包含 source_handle")
            normal_key = (source_id, target_id)
            if normal_key in normal_edges:
                raise GraphValidationError("连线重复")
            normal_edges.add(normal_key)

        incoming_sources[target_id].add(source_id)

    for node_id, node in by_id.items():
        if node.get("type") == "condition":
            _condition_handles(node)
        if node.get("type") != "output":
            continue
        sources = incoming_sources[node_id]
        source_node_id = node.get("source_node_id")
        if not sources:
            if source_node_id not in (None, ""):
                raise GraphValidationError("output 节点没有上游连线时 source_node_id 必须为空")
            continue
        if not isinstance(source_node_id, str) or source_node_id not in sources:
            raise GraphValidationError("output 节点 source_node_id 必须指向一个上游节点")

    topological_order(graph)


def validate_executable_graph(graph: dict[str, Any]) -> None:
    validate_graph_structure(graph)
    nodes = list(graph.get("nodes", []))
    if not nodes:
        raise GraphValidationError("工作流没有节点")
    if not any(isinstance(node, dict) and node.get("type") == "output" for node in nodes):
        raise GraphValidationError("工作流必须包含 output 节点")


def user_input_node_ids(graph: dict[str, Any]) -> set[str]:
    return {
        node["id"]
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
        and node.get("type") == "user_input"
        and isinstance(node.get("id"), str)
    }


def validate_prompt_nodes(graph: dict[str, Any]) -> None:
    for node in graph.get("nodes", []):
        contract_error = validate_output_contract_config(node)
        if contract_error:
            raise GraphValidationError(contract_error)
        if node.get("type") not in PROMPT_NODE_TYPES:
            continue
        label = node.get("title") or node.get("id") or "?"
        prompt = node.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise GraphValidationError(f"节点「{label}」必须填写提示词")
        if contains_template_token(prompt):
            raise GraphValidationError(f"节点「{label}」提示词不能包含变量占位符，请通过连线提供上游上下文")


def _nodes_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise GraphValidationError("节点格式非法")
        node_id = node.get("id")
        if not isinstance(node_id, str):
            raise GraphValidationError("节点缺少 id")
        if node_id in by_id:
            raise GraphValidationError(f"节点 id 重复：{node_id}")
        by_id[node_id] = node
    return by_id


def _condition_handles(node: dict[str, Any]) -> set[str]:
    mode = node.get("mode")
    branches = node.get("branches")
    if mode not in {"binary", "cases"}:
        raise GraphValidationError("condition.mode 只能是 binary 或 cases")
    if not isinstance(branches, list) or len(branches) < 2:
        raise GraphValidationError("condition.branches 至少需要 2 个分支")

    keys: list[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            raise GraphValidationError("condition.branches 格式非法")
        key = branch.get("key")
        if not isinstance(key, str) or not key:
            raise GraphValidationError("condition 分支 key 不能为空")
        if key == DEFAULT_BRANCH_KEY:
            raise GraphValidationError(f"保留 key {DEFAULT_BRANCH_KEY} 不能用作自定义分支")
        if re.match(BRANCH_KEY_PATTERN, key) is None:
            raise GraphValidationError("condition 分支 key 只能包含字母、数字和下划线")
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise GraphValidationError("condition 分支 key 不能重复")
    if mode == "binary":
        if set(keys) != {"true", "false"} or len(keys) != 2:
            raise GraphValidationError("binary condition 只能使用 true / false 分支")
        return {"true", "false"}
    return set(keys) | {DEFAULT_BRANCH_KEY}


def validate_graph_agent_enabled(graph: dict[str, Any], enabled_agents: set[str]) -> None:
    needs_agent = any(node.get("type") in AGENT_NODE_TYPES for node in graph.get("nodes", []))
    if not needs_agent:
        return
    agent = graph.get("agent")
    agent_kind = agent.strip() if isinstance(agent, str) else ""
    if not agent_kind:
        raise GraphValidationError("应用必须选择 Agent")
    if agent_kind not in enabled_agents:
        raise GraphValidationError(
            f"应用默认 Agent \"{agent_kind}\" 未启用，请在 Settings 启用或切换应用 Agent"
        )


def sanitize_prompt_template_tokens(graph: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(graph)
    for node in sanitized.get("nodes", []):
        if node.get("type") not in PROMPT_NODE_TYPES:
            continue
        prompt = node.get("prompt")
        if isinstance(prompt, str) and contains_template_token(prompt):
            node["prompt"] = strip_template_tokens(prompt)
    return sanitized
