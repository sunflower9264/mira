from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ExecutionPlanError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionPlan:
    ordered_node_ids: tuple[str, ...]
    nodes_by_id: dict[str, dict[str, Any]]
    predecessors: dict[str, frozenset[str]]
    children: dict[str, frozenset[str]]
    ancestors: dict[str, frozenset[str]]

    def ancestor_ids(self, node_id: str) -> tuple[str, ...]:
        available = self.ancestors.get(node_id, frozenset())
        return tuple(current for current in self.ordered_node_ids if current in available)

    def descendant_ids(self, start_ids: set[str]) -> frozenset[str]:
        reachable: set[str] = set()
        stack = list(start_ids)
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(self.children.get(current, frozenset()))
        return frozenset(reachable)


def compile_execution_plan(graph: dict[str, Any]) -> ExecutionPlan:
    if "edges" in graph:
        raise ExecutionPlanError("旧 graph.edges 契约不再支持，请使用 graph.execution_edges")
    nodes = graph.get("nodes")
    execution_edges = graph.get("execution_edges")
    if not isinstance(nodes, list):
        raise ExecutionPlanError("graph.nodes 必须是数组")
    if not isinstance(execution_edges, list):
        raise ExecutionPlanError("graph.execution_edges 必须是数组")

    nodes_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or not node_id:
            raise ExecutionPlanError("节点缺少 id")
        if node_id in nodes_by_id:
            raise ExecutionPlanError(f"节点 id 重复：{node_id}")
        nodes_by_id[node_id] = node

    predecessors: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    children: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    indegree = {node_id: 0 for node_id in nodes_by_id}
    for edge in execution_edges:
        if not isinstance(edge, dict):
            raise ExecutionPlanError("执行连线格式非法")
        source = edge.get("source")
        target = edge.get("target")
        if source not in nodes_by_id or target not in nodes_by_id:
            raise ExecutionPlanError("执行连线引用了不存在的节点")
        if source == target:
            raise ExecutionPlanError("节点不能连接到自身")
        if target in children[source]:
            continue
        children[source].add(target)
        predecessors[target].add(source)
        indegree[target] += 1

    queue = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    ordered_node_ids: list[str] = []
    while queue:
        node_id = queue.pop(0)
        ordered_node_ids.append(node_id)
        for child in sorted(children[node_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
                queue.sort()
    if len(ordered_node_ids) != len(nodes_by_id):
        raise ExecutionPlanError("graph 存在环路，无法拓扑排序")

    ancestors: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    for node_id in ordered_node_ids:
        for child in children[node_id]:
            ancestors[child].add(node_id)
            ancestors[child].update(ancestors[node_id])

    return ExecutionPlan(
        ordered_node_ids=tuple(ordered_node_ids),
        nodes_by_id=nodes_by_id,
        predecessors={node_id: frozenset(value) for node_id, value in predecessors.items()},
        children={node_id: frozenset(value) for node_id, value in children.items()},
        ancestors={node_id: frozenset(value) for node_id, value in ancestors.items()},
    )
