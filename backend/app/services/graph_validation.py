from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.execution_plan import ExecutionPlanError, compile_execution_plan
from app.services.template import contains_template_token, strip_template_tokens
from app.services.output_contracts import validate_output_contract_config


PROMPT_NODE_TYPES = {"generate", "condition", "output"}
NODE_TYPES = {"user_input", "generate", "output", "asset", "condition"}
DEFAULT_BRANCH_KEY = "__default__"
BRANCH_KEY_PATTERN = r"^[a-zA-Z0-9_]+$"


class GraphValidationError(Exception):
    pass


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """返回执行顺序；拓扑与祖先关系由 ExecutionPlan 统一计算。"""

    try:
        plan = compile_execution_plan(graph)
    except ExecutionPlanError as exc:
        raise GraphValidationError(str(exc)) from exc
    return [plan.nodes_by_id[node_id] for node_id in plan.ordered_node_ids]


def validate_graph_structure(graph: dict[str, Any]) -> None:
    if "edges" in graph:
        raise GraphValidationError("旧 graph.edges 契约不再支持，请使用 graph.execution_edges")
    nodes_value = graph.get("nodes")
    edges_value = graph.get("execution_edges")
    if not isinstance(nodes_value, list):
        raise GraphValidationError("graph.nodes 必须是数组")
    if not isinstance(edges_value, list):
        raise GraphValidationError("graph.execution_edges 必须是数组")
    nodes = list(nodes_value)
    edges = list(edges_value)
    by_id = _nodes_by_id(nodes)
    for node in by_id.values():
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            raise GraphValidationError(f"未知节点类型：{node_type}")
        if "ask_user_enabled" in node:
            label = node.get("title") or node.get("id") or "?"
            if node_type != "generate":
                raise GraphValidationError(f"节点「{label}」只有 generate 支持 ask_user_enabled")
            if not isinstance(node.get("ask_user_enabled"), bool):
                raise GraphValidationError(f"节点「{label}」ask_user_enabled 必须是 bool")
    output_count = sum(1 for node in by_id.values() if node.get("type") == "output")
    if output_count > 1:
        raise GraphValidationError("工作流只能有一个 output 节点")
    user_input_count = sum(1 for node in by_id.values() if node.get("type") == "user_input")
    if user_input_count > 1:
        raise GraphValidationError("工作流只能有一个 user_input 节点")
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
            handle = edge.get("branch_key")
            if not isinstance(handle, str) or not handle:
                raise GraphValidationError("condition 出边必须设置 branch_key")
            if handle not in _condition_handles(source_node):
                raise GraphValidationError(f"condition 出边 branch_key 无效：{handle}")
            condition_key = (source_id, handle)
            if condition_key in condition_handles:
                raise GraphValidationError("同一个 condition 分支最多只能连接一条出边")
            condition_handles.add(condition_key)
        else:
            if "branch_key" in edge:
                raise GraphValidationError("非 condition 出边不能包含 branch_key")
            normal_key = (source_id, target_id)
            if normal_key in normal_edges:
                raise GraphValidationError("连线重复")
            normal_edges.add(normal_key)

    for node_id, node in by_id.items():
        if node.get("type") == "condition":
            _condition_handles(node)

    topological_order(graph)


def validate_executable_graph(graph: dict[str, Any]) -> None:
    validate_graph_structure(graph)
    nodes = list(graph.get("nodes", []))
    if not nodes:
        raise GraphValidationError("工作流没有节点")
    outputs = [node for node in nodes if isinstance(node, dict) and node.get("type") == "output"]
    if not outputs:
        raise GraphValidationError("工作流必须包含 output 节点")
    output = outputs[0]
    output_id = str(output.get("id") or "")
    edges = [edge for edge in graph.get("execution_edges", []) if isinstance(edge, dict)]
    if not any(edge.get("target") == output_id for edge in edges):
        raise GraphValidationError("output 节点必须连接正式上游输入")

    parents: dict[str, set[str]] = {}
    condition_handles: dict[str, set[str]] = {}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and isinstance(target, str):
            parents.setdefault(target, set()).add(source)
        handle = edge.get("branch_key")
        if isinstance(source, str) and isinstance(handle, str):
            condition_handles.setdefault(source, set()).add(handle)

    reaches_output = {output_id}
    queue = [output_id]
    while queue:
        current = queue.pop(0)
        for parent in parents.get(current, set()):
            if parent in reaches_output:
                continue
            reaches_output.add(parent)
            queue.append(parent)

    for node in nodes:
        node_id = node.get("id") if isinstance(node, dict) else None
        if isinstance(node, dict) and node.get("type") == "condition" and isinstance(node_id, str):
            declared = {
                branch.get("key")
                for branch in node.get("branches", [])
                if isinstance(branch, dict) and isinstance(branch.get("key"), str)
            }
            connected = condition_handles.get(node_id, set())
            missing = sorted(declared - connected)
            if missing:
                raise GraphValidationError(
                    f"condition 节点 {node_id} 的分支 {', '.join(missing)} 未连接到 output 路径"
                )
            for handle in sorted(declared | ({DEFAULT_BRANCH_KEY} if DEFAULT_BRANCH_KEY in connected else set())):
                targets = {
                    edge.get("target")
                    for edge in edges
                    if edge.get("source") == node_id and edge.get("branch_key") == handle
                }
                if any(isinstance(target, str) and target not in reaches_output for target in targets):
                    raise GraphValidationError(
                        f"condition 节点 {node_id} 的分支 {handle} 无法到达 output"
                    )
        if isinstance(node_id, str) and node_id not in reaches_output:
            raise GraphValidationError(f"节点 {node_id} 无法到达 output")


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
            raise GraphValidationError(f"节点「{label}」提示词不能包含变量占位符，上游上下文由同一 RunAgent 会话与 workspace 保留")


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


def sanitize_prompt_template_tokens(graph: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(graph)
    for node in sanitized.get("nodes", []):
        if node.get("type") not in PROMPT_NODE_TYPES:
            continue
        prompt = node.get("prompt")
        if isinstance(prompt, str) and contains_template_token(prompt):
            node["prompt"] = strip_template_tokens(prompt)
    return sanitized
