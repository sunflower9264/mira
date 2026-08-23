from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.graph_validation import (
    GraphValidationError,
    sanitize_prompt_template_tokens,
    validate_executable_graph,
    validate_graph_structure,
    validate_prompt_nodes,
)
from app.services.output_contracts import normalize_output_contract_config
from app.services.tools import strip_runtime_tools_snapshot
from app.services.uploads import resolve_upload


def clean_graph_input(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        raw = {"nodes": [], "execution_edges": []}
    if not isinstance(raw, dict):
        raise GraphValidationError("graph 必须是对象")
    graph = sanitize_prompt_template_tokens(strip_runtime_tools_snapshot(deepcopy(raw)))
    _normalize_node_output_contracts(graph)
    return graph


def _normalize_node_output_contracts(graph: dict[str, Any]) -> None:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "generate":
            continue
        if "output_contract" in node:
            node["output_contract"] = normalize_output_contract_config(node.get("output_contract"))


def prepare_structural_graph(raw: dict[str, Any] | None) -> dict[str, Any]:
    graph = clean_graph_input(raw)
    validate_graph_structure(graph)
    return graph


def prepare_prompt_valid_graph(raw: dict[str, Any] | None, owner_id: str | None = None) -> dict[str, Any]:
    graph = prepare_structural_graph(raw)
    validate_prompt_nodes(graph)
    validate_asset_upload_ownership(graph, owner_id)
    return graph


def prepare_executable_graph(raw: dict[str, Any] | None, owner_id: str | None = None) -> dict[str, Any]:
    graph = clean_graph_input(raw)
    validate_executable_graph(graph)
    validate_prompt_nodes(graph)
    validate_asset_upload_ownership(graph, owner_id)
    return graph


def prepare_planning_graph(raw: dict[str, Any] | None) -> dict[str, Any]:
    return clean_graph_input(raw)


def validate_asset_upload_ownership(graph: dict[str, Any], owner_id: str | None) -> None:
    if not owner_id:
        return
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "asset":
            continue
        asset_kind = node.get("asset_kind")
        if asset_kind == "file":
            uploads = node.get("uploads")
            if not isinstance(uploads, list):
                continue
            for upload in uploads:
                upload_id = upload.get("id") if isinstance(upload, dict) else None
                _ensure_owned_upload(owner_id, upload_id)
        elif asset_kind == "drawing":
            upload = node.get("upload")
            if upload is None:
                continue
            upload_id = upload.get("id") if isinstance(upload, dict) else None
            _ensure_owned_upload(owner_id, upload_id)


def _ensure_owned_upload(owner_id: str, upload_id: Any) -> None:
    if not isinstance(upload_id, str) or not upload_id.strip() or resolve_upload(owner_id, upload_id) is None:
        raise GraphValidationError("素材上传文件不存在")
