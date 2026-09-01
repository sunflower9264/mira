from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.services.execution_plan import ExecutionPlanError, compile_execution_plan
from app.services.graph_validation import DEFAULT_BRANCH_KEY
from app.services.output_contracts import validate_output_contract_config
from app.services.template import contains_template_token


WorkflowLintSeverity = Literal["error", "warning", "info"]

PROMPT_NODE_TYPES = {"generate", "condition", "output"}
NODE_TYPES = {"user_input", "generate", "output", "asset", "condition"}
TERMINAL_NODE_TYPES = {"output"}
GENERIC_PROMPTS = {
    "ok",
    "hi",
    "test",
    "生成",
    "输出",
    "整理输出",
    "请生成",
    "请输出",
    "渲染",
}


@dataclass(frozen=True)
class WorkflowLintIssue:
    severity: WorkflowLintSeverity
    code: str
    title: str
    detail: str
    node_id: str | None = None
    edge_id: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "suggestion": self.suggestion,
        }


def lint_workflow(
    graph: dict[str, Any],
    *,
    enabled_tool_ids: set[str] | None = None,
) -> dict[str, Any]:
    issues: list[WorkflowLintIssue] = []
    enabled_tool_ids = enabled_tool_ids or set()

    if not isinstance(graph, dict):
        issues.append(_issue("error", "graph_invalid", "工作流格式非法", "graph 必须是对象"))
        return _result(issues)

    nodes = graph.get("nodes")
    edges = graph.get("execution_edges")
    if not isinstance(nodes, list):
        issues.append(_issue("error", "nodes_invalid", "节点列表非法", "graph.nodes 必须是数组"))
        nodes = []
    if not isinstance(edges, list):
        issues.append(_issue("error", "edges_invalid", "连线列表非法", "graph.execution_edges 必须是数组"))
        edges = []
    if not nodes:
        issues.append(
            _issue(
                "error",
                "graph_empty",
                "工作流没有节点",
                "至少需要添加一个可运行节点。",
                suggestion="添加 user_input、generate 和 output 节点组成最小工作流。",
            )
        )

    tools = graph.get("tools")
    disabled_tool_ids = tools.get("disabled_tool_ids") if isinstance(tools, dict) else None
    if isinstance(disabled_tool_ids, list):
        unknown_tool_ids = sorted({
            item for item in disabled_tool_ids if isinstance(item, str) and item and item not in enabled_tool_ids
        })
        if unknown_tool_ids:
            issues.append(
                _issue(
                    "warning",
                    "tool_exclusion_stale",
                    "Tools 排除项已失效",
                    f"以下 Tools 当前不存在或未启用，将被忽略：{', '.join(unknown_tool_ids[:5])}",
                    suggestion="重新打开 Tools 下拉保存当前选择。",
                )
            )

    by_id: dict[str, dict[str, Any]] = {}
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    duplicate_ids: set[str] = set()

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            issues.append(_issue("error", "node_invalid", "节点格式非法", f"第 {index + 1} 个节点必须是对象"))
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id:
            issues.append(_issue("error", "node_missing_id", "节点缺少 id", f"第 {index + 1} 个节点缺少有效 id"))
            continue
        if node_id in by_id:
            duplicate_ids.add(node_id)
            issues.append(_issue("error", "node_duplicate_id", "节点 id 重复", f"节点 id 重复：{node_id}", node_id=node_id))
            continue
        if node_type not in NODE_TYPES:
            issues.append(_issue("error", "node_unknown_type", "节点类型未知", f"节点「{_label(node)}」类型无效：{node_type}", node_id=node_id))
        by_id[node_id] = node
        incoming[node_id] = set()
        outgoing[node_id] = set()

    edge_ids: set[str] = set()
    normal_edges: set[tuple[str, str]] = set()
    condition_edges: set[tuple[str, str]] = set()
    condition_connected_handles: dict[str, set[str]] = {}

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            issues.append(_issue("error", "edge_invalid", "连线格式非法", f"第 {index + 1} 条连线必须是对象"))
            continue
        edge_id = edge.get("id")
        source_id = edge.get("source")
        target_id = edge.get("target")
        if not isinstance(edge_id, str) or not edge_id:
            issues.append(_issue("error", "edge_missing_id", "连线缺少 id", f"第 {index + 1} 条连线缺少有效 id"))
        elif edge_id in edge_ids:
            issues.append(_issue("error", "edge_duplicate_id", "连线 id 重复", f"连线 id 重复：{edge_id}", edge_id=edge_id))
        else:
            edge_ids.add(edge_id)

        current_edge_id = edge_id if isinstance(edge_id, str) else None
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            issues.append(_issue("error", "edge_missing_endpoint", "连线缺少端点", "连线必须包含 source 和 target", edge_id=current_edge_id))
            continue
        if source_id not in by_id or target_id not in by_id:
            issues.append(
                _issue(
                    "error",
                    "edge_missing_node",
                    "连线引用不存在的节点",
                    f"连线引用了不存在的节点：{source_id} -> {target_id}",
                    edge_id=current_edge_id,
                )
            )
            continue
        if source_id == target_id:
            issues.append(_issue("error", "edge_self_loop", "节点不能连接到自身", f"节点「{_label(by_id[source_id])}」连接到了自身", node_id=source_id, edge_id=current_edge_id))
            continue

        source = by_id[source_id]
        target = by_id[target_id]
        source_type = source.get("type")
        target_type = target.get("type")
        if source_type in TERMINAL_NODE_TYPES:
            code = f"{source_type}_as_source"
            issues.append(_issue("error", code, f"{source_type} 不能作为连线起点", f"{source_type} 节点「{_label(source)}」不能连接到下游", node_id=source_id, edge_id=current_edge_id))
        if target_type in {"user_input", "asset"}:
            issues.append(_issue("error", "source_only_target", "起点类节点不能作为连线终点", f"{target_type} 节点「{_label(target)}」不能接收上游连线", node_id=target_id, edge_id=current_edge_id))

        if source_type == "condition":
            handle = edge.get("branch_key")
            handles = _condition_handles(source, issues)
            if not isinstance(handle, str) or not handle:
                issues.append(_issue("error", "condition_edge_missing_handle", "condition 出边缺少分支", f"condition 节点「{_label(source)}」的出边必须设置 branch_key", node_id=source_id, edge_id=current_edge_id))
            elif handle not in handles:
                issues.append(_issue("error", "condition_edge_invalid_handle", "condition 出边分支无效", f"分支 handle 不存在：{handle}", node_id=source_id, edge_id=current_edge_id))
            else:
                key = (source_id, handle)
                if key in condition_edges:
                    issues.append(_issue("error", "condition_edge_duplicate_handle", "condition 分支重复连接", f"分支「{handle}」最多只能连接一条出边", node_id=source_id, edge_id=current_edge_id))
                condition_edges.add(key)
                condition_connected_handles.setdefault(source_id, set()).add(handle)
        else:
            if "branch_key" in edge:
                issues.append(_issue("error", "non_condition_handle", "非 condition 连线不应有分支", "只有 condition 出边可以设置 branch_key", edge_id=current_edge_id))
            key = (source_id, target_id)
            if key in normal_edges:
                issues.append(_issue("error", "edge_duplicate", "连线重复", f"重复连线：{source_id} -> {target_id}", edge_id=current_edge_id))
            normal_edges.add(key)

        incoming[target_id].add(source_id)
        outgoing[source_id].add(target_id)

    for node_id, node in by_id.items():
        node_type = node.get("type")
        contract_error = validate_output_contract_config(node)
        if contract_error:
            issues.append(_issue("error", "output_contract_invalid", "输出契约配置非法", contract_error, node_id=node_id))

        if node_type in PROMPT_NODE_TYPES:
            prompt = node.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                issues.append(_issue("error", "prompt_empty", "提示词为空", f"节点「{_label(node)}」必须填写提示词", node_id=node_id))
            else:
                cleaned = " ".join(prompt.strip().split())
                if contains_template_token(prompt):
                    issues.append(_issue("error", "prompt_template_token", "提示词包含变量占位符", f"节点「{_label(node)}」提示词不能包含变量占位符", node_id=node_id))
                if _is_generic_prompt(cleaned):
                    issues.append(
                        _issue(
                            "warning",
                            "prompt_too_generic",
                            "提示词过于简单",
                            f"节点「{_label(node)}」的提示词可能不足以稳定指导 Agent。",
                            node_id=node_id,
                            suggestion="补充任务目标、输出格式、约束和判断标准。",
                        )
                    )
                if _prompt_uses_hidden_data_channel(prompt):
                    issues.append(
                        _issue(
                            "warning",
                            "prompt_hidden_data_channel",
                            "提示词包含隐式文件通道",
                            f"节点「{_label(node)}」疑似通过固定 Workspace 路径或 handoff 文件传递数据。",
                            node_id=node_id,
                            suggestion="改为正式节点输出；后续节点会延续同一 RunAgent 会话与 workspace。",
                        )
                    )

        if node_type == "output" and not incoming.get(node_id):
            issues.append(_issue("error", "output_no_input", "output 没有上游输入", f"output 节点「{_label(node)}」没有接入任何上游节点", node_id=node_id))

        if node_type == "condition":
            handles = _condition_handles(node, issues)
            connected = condition_connected_handles.get(node_id, set())
            for handle in sorted(handles - {DEFAULT_BRANCH_KEY}):
                if handle not in connected:
                    issues.append(
                        _issue(
                            "error",
                            "condition_branch_unconnected",
                            "condition 分支未连接",
                            f"condition 节点「{_label(node)}」的分支「{handle}」没有下游连线。",
                            node_id=node_id,
                            suggestion="连接该分支，或删除不会使用的分支。",
                        )
                    )

    user_inputs = [node for node in by_id.values() if node.get("type") == "user_input"]
    if len(user_inputs) > 1:
        issues.append(
            _issue(
                "error",
                "multiple_user_input_nodes",
                "工作流有多个 user_input 节点",
                "一个工作流只能有一个用户输入节点。",
                suggestion="删除多余的 user_input，或把多个输入要求合并到一个输入节点。",
            )
        )

    outputs = [node for node in by_id.values() if node.get("type") == "output"]
    if by_id and not outputs:
        issues.append(
            _issue(
                "error",
                "missing_terminal_node",
                "工作流缺少终点节点",
                "至少需要添加一个输出节点。",
                suggestion="添加输出节点展示最终结果。",
            )
        )
    if len(outputs) > 1:
        issues.append(
            _issue(
                "error",
                "multiple_output_nodes",
                "工作流有多个 output 节点",
                "一个工作流只能有一个最终输出节点。",
                suggestion="删除多余的 output，或把多个结果合并到一个 output HTML 展示中。",
            )
        )
    terminal_ids = {
        node["id"]
        for node in outputs
        if isinstance(node.get("id"), str)
    }
    execution_plan = None
    if not duplicate_ids:
        try:
            execution_plan = compile_execution_plan(
                {"nodes": list(by_id.values()), "execution_edges": [edge for edge in edges if isinstance(edge, dict)]}
            )
        except ExecutionPlanError as exc:
            issues.append(_issue("error", "graph_topology_invalid", "工作流拓扑非法", str(exc)))

    if terminal_ids and execution_plan is not None:
        can_reach_terminal = set(terminal_ids)
        for output_id in terminal_ids:
            can_reach_terminal.update(execution_plan.ancestor_ids(output_id))
        for node_id, node in by_id.items():
            if node.get("type") in TERMINAL_NODE_TYPES:
                continue
            if node_id not in can_reach_terminal:
                issues.append(
                    _issue(
                        "error",
                        "node_not_reaching_terminal",
                        "节点不会影响最终结果",
                        f"节点「{_label(node)}」无法到达任何 output 节点。",
                        node_id=node_id,
                        suggestion="将它接入主流程，或删除不需要的节点。",
                    )
                )

    for node_id, node in by_id.items():
        if not incoming.get(node_id) and not outgoing.get(node_id) and len(by_id) > 1:
            issues.append(
                _issue(
                    "warning",
                    "node_isolated",
                    "节点未连接",
                    f"节点「{_label(node)}」没有任何连线。",
                    node_id=node_id,
                    suggestion="连接到工作流，或删除孤立节点。",
                )
            )

    return _result(_dedupe(issues))


def _issue(
    severity: WorkflowLintSeverity,
    code: str,
    title: str,
    detail: str,
    *,
    node_id: str | None = None,
    edge_id: str | None = None,
    suggestion: str | None = None,
) -> WorkflowLintIssue:
    return WorkflowLintIssue(severity, code, title, detail, node_id=node_id, edge_id=edge_id, suggestion=suggestion)


def _result(issues: list[WorkflowLintIssue]) -> dict[str, Any]:
    ordered = sorted(issues, key=lambda item: ({"error": 0, "warning": 1, "info": 2}[item.severity], item.code, item.node_id or "", item.edge_id or ""))
    counts = {
        "errors": sum(1 for item in ordered if item.severity == "error"),
        "warnings": sum(1 for item in ordered if item.severity == "warning"),
        "infos": sum(1 for item in ordered if item.severity == "info"),
    }
    return {
        "ok": counts["errors"] == 0,
        "summary": counts,
        "issues": [item.to_dict() for item in ordered],
    }


def _dedupe(issues: list[WorkflowLintIssue]) -> list[WorkflowLintIssue]:
    seen: set[tuple[Any, ...]] = set()
    result: list[WorkflowLintIssue] = []
    for issue in issues:
        key = (issue.severity, issue.code, issue.node_id, issue.edge_id, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _label(node: dict[str, Any]) -> str:
    return str(node.get("title") or node.get("id") or "?")


def _is_generic_prompt(prompt: str) -> bool:
    normalized = prompt.strip().lower()
    return len(normalized) < 8 or normalized in GENERIC_PROMPTS


_HIDDEN_CHANNEL_ACTION_RE = re.compile(
    r"(?:写入|写到|保存到|读取|加载|查找|依赖|传给|交给|供.{0,8}读取|write|save|read|load|find|consume|depend)"
    r".{0,40}(?:/workspace(?:/|\b)|\bhandoff\b|\bsidecar\b|\bmanifest\b)",
    re.IGNORECASE,
)
_OFFICIAL_WORKSPACE_INPUT_PATH_RE = re.compile(
    r"/workspace/(?:\.mira/run-context|inputs)(?:/|\b)",
    re.IGNORECASE,
)
_HIDDEN_CHANNEL_NEGATIONS = ("不得", "禁止", "不要", "不可", "不能", "严禁", "avoid", "do not", "must not", "never")


def _prompt_uses_hidden_data_channel(prompt: str) -> bool:
    for line in prompt.splitlines() or [prompt]:
        lowered = line.lower()
        if any(negation in lowered for negation in _HIDDEN_CHANNEL_NEGATIONS):
            continue
        candidate = _OFFICIAL_WORKSPACE_INPUT_PATH_RE.sub("", line)
        if _HIDDEN_CHANNEL_ACTION_RE.search(candidate):
            return True
    return False


def _condition_handles(node: dict[str, Any], issues: list[WorkflowLintIssue]) -> set[str]:
    node_id = node.get("id") if isinstance(node.get("id"), str) else None
    mode = node.get("mode")
    branches = node.get("branches")
    if mode not in {"binary", "cases"}:
        issues.append(_issue("error", "condition_mode_invalid", "condition 模式非法", f"节点「{_label(node)}」mode 只能是 binary 或 cases", node_id=node_id))
        return set()
    if not isinstance(branches, list) or len(branches) < 2:
        issues.append(_issue("error", "condition_branches_invalid", "condition 分支不足", f"节点「{_label(node)}」至少需要 2 个分支", node_id=node_id))
        return set()
    keys: list[str] = []
    for branch in branches:
        key = branch.get("key") if isinstance(branch, dict) else None
        if not isinstance(key, str) or not key:
            issues.append(_issue("error", "condition_branch_key_invalid", "condition 分支 key 非法", f"节点「{_label(node)}」存在空分支 key", node_id=node_id))
            continue
        if key == DEFAULT_BRANCH_KEY:
            issues.append(_issue("error", "condition_branch_key_reserved", "condition 分支 key 保留", f"保留 key {DEFAULT_BRANCH_KEY} 不能用作自定义分支", node_id=node_id))
            continue
        keys.append(key)
    if len(set(keys)) != len(keys):
        issues.append(_issue("error", "condition_branch_key_duplicate", "condition 分支 key 重复", f"节点「{_label(node)}」存在重复分支 key", node_id=node_id))
    if mode == "binary" and set(keys) != {"true", "false"}:
        issues.append(_issue("error", "condition_binary_keys_invalid", "binary 分支非法", f"节点「{_label(node)}」binary 模式只能使用 true / false 分支", node_id=node_id))
    return set(keys) | ({DEFAULT_BRANCH_KEY} if mode == "cases" else set())
