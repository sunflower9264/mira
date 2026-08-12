from __future__ import annotations

import json
from pathlib import Path

from app.services.graph_validation import validate_graph_structure, validate_prompt_nodes
from app.services.workflow_lint import lint_workflow


GRAPH_PATH = Path(__file__).resolve().parents[2] / "docs" / "reviews" / "code-and-copyright-workflow-v15.graph.json"


def _graph() -> dict:
    return json.loads(GRAPH_PATH.read_text(encoding="utf-8"))


def _assert_array_bounds(schema: dict, path: str = "$") -> None:
    if schema.get("type") == "array":
        assert isinstance(schema.get("minItems"), int), path
        assert isinstance(schema.get("maxItems"), int), path
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            if isinstance(value, dict):
                _assert_array_bounds(value, f"{path}.properties.{key}")
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_array_bounds(items, f"{path}.items")


def _assert_string_max_lengths(schema: dict, path: str = "$") -> None:
    if schema.get("type") == "string":
        assert isinstance(schema.get("maxLength"), int), path
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            if isinstance(value, dict):
                _assert_string_max_lengths(value, f"{path}.properties.{key}")
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_string_max_lengths(items, f"{path}.items")


def test_code_copyright_v15_graph_is_structurally_valid() -> None:
    graph = _graph()

    validate_graph_structure(graph)
    validate_prompt_nodes(graph)

    assert len(graph["nodes"]) == 13
    assert len(graph["edges"]) == 26
    assert len([node for node in graph["nodes"] if node.get("ask_user_enabled") is True]) == 2
    assert len([node for node in graph["nodes"] if node.get("ask_user_enabled") is False]) == 8
    candidate_node = next(node for node in graph["nodes"] if node.get("id") == "n_candidate_directions")
    candidates_schema = candidate_node["output_contract"]["json_schema"]["properties"]["candidates"]
    assert candidates_schema["minItems"] == 3
    assert candidates_schema["maxItems"] == 3
    assert any(
        edge.get("source") == "n_direction_visual_choice" and edge.get("target") == "n_source_package"
        for edge in graph["edges"]
    )


def test_code_copyright_v15_graph_lints_without_errors() -> None:
    graph = _graph()
    enabled_tools = {
        "skill:skill_e4d67ee394094da1bd732e73889fb39e",
        "skill:skill_f9be4e9b44734f9d97afe00f6fd9a2bd",
        "skill:skill_a7bb0e6025b042ebac04bd3c7a095876",
        "skill:skill_99c60286382a4e75829fc03fcd56f276",
        "skill:skill_6efab84ed1d94d31a54bda773f440189",
        "skill:skill_477bf1e362254679b881a956821f6362",
        "skill:skill_92e828b5ecd14770a3897e2f1805d8d9",
        "skill:skill_b737bd8e189b42c9b02846c061162d46",
        "skill:skill_a855a8f68a924d7d9f73133b55b00eac",
    }

    result = lint_workflow(graph, enabled_agents={"codex"}, enabled_tool_ids=enabled_tools)

    assert result["summary"]["errors"] == 0, result["issues"]
    assert set(graph["tools"]["disabled_tool_ids"]) == {
        "skill:skill_99c60286382a4e75829fc03fcd56f276",
        "skill:skill_477bf1e362254679b881a956821f6362",
        "skill:skill_92e828b5ecd14770a3897e2f1805d8d9",
        "skill:skill_b737bd8e189b42c9b02846c061162d46",
        "skill:skill_a855a8f68a924d7d9f73133b55b00eac",
    }


def test_code_copyright_v15_json_arrays_have_explicit_bounds() -> None:
    graph = _graph()

    for node in graph["nodes"]:
        contract = node.get("output_contract")
        if not isinstance(contract, dict) or contract.get("type") != "json":
            continue
        _assert_array_bounds(contract["json_schema"], node["id"])
        _assert_string_max_lengths(contract["json_schema"], node["id"])


def test_code_copyright_v15_document_spec_is_compact_and_template_independent() -> None:
    graph = _graph()
    node = next(node for node in graph["nodes"] if node.get("id") == "n_document_spec")
    schema = node["output_contract"]["json_schema"]

    assert node["reasoning_effort"] == "medium"
    assert "不读取或生成文件" in node["prompt"]
    assert not any(
        edge.get("source") == "n_copyright_templates" and edge.get("target") == "n_document_spec"
        for edge in graph["edges"]
    )
    assert set(schema["properties"]) == {"placeholder_fields", "materials"}
    materials = schema["properties"]["materials"]
    assert set(materials["properties"]) == {
        "application_form",
        "feature_summary",
        "source_code",
        "user_manual",
        "design_document",
        "test_matrix",
        "environment_sheet",
        "account_text",
    }
    assert set(materials["required"]) == set(materials["properties"])


def test_code_copyright_v15_artifacts_are_single_declared_deliverables() -> None:
    graph = _graph()
    artifact_nodes = [
        node
        for node in graph["nodes"]
        if isinstance(node.get("output_contract"), dict) and node["output_contract"].get("type") == "artifact"
    ]

    assert {node["id"] for node in artifact_nodes} == {
        "n_source_package",
        "n_preview_screenshots",
        "n_document_package",
    }
    assert all(node["output_contract"].get("max_count") == 1 for node in artifact_nodes)
    assert all(node["output_contract"].get("artifact_kind") == "zip" for node in artifact_nodes)
    office_validated = [
        node["id"] for node in artifact_nodes if node["output_contract"].get("validate_office_documents") is True
    ]
    assert office_validated == ["n_document_package"]


def test_code_copyright_v15_artifact_prompts_use_fixed_zip_filenames() -> None:
    graph = _graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
    expected_filenames = {
        "n_source_package": "product-source.zip",
        "n_preview_screenshots": "product-preview-screenshots.zip",
        "n_document_package": "software-copyright-package.zip",
    }

    for node_id, filename in expected_filenames.items():
        assert filename in nodes[node_id]["prompt"]
    assert "--min-screenshots 5" in nodes["n_preview_screenshots"]["prompt"]


def test_code_copyright_v15_document_package_uses_exact_top_level_filenames() -> None:
    graph = _graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
    required_filenames = {
        "01_申请表.docx",
        "02_功能特点.docx",
        "03_源代码.docx",
        "04_软件说明书.docx",
        "05_设计文档.docx",
        "06_产品测试功能表.docx",
        "07_非嵌入式软件环境表.docx",
        "08_账号.txt",
        "package-manifest.json",
        "生成报告.md",
    }

    for node_id in ("n_document_package", "n_package_review"):
        assert all(filename in nodes[node_id]["prompt"] for filename in required_filenames)
        assert "libreoffice --headless" not in nodes[node_id]["prompt"]
        assert "pdfinfo" not in nodes[node_id]["prompt"]

    inventory = nodes["n_package_review"]["output_contract"]["json_schema"]["properties"]["document_inventory"]
    assert inventory["type"] == "object"
    assert inventory["additionalProperties"] is False
    assert set(inventory["properties"]) == required_filenames
    assert set(inventory["required"]) == required_filenames


def test_code_copyright_v15_artifact_integrity_contract_has_all_states() -> None:
    graph = _graph()
    review_node = next(node for node in graph["nodes"] if node.get("id") == "n_package_review")
    deliverables = review_node["output_contract"]["json_schema"]["properties"]["deliverables"]
    integrity = deliverables["items"]["properties"]["integrity"]

    assert integrity["enum"] == ["verified", "modified", "legacy_unverified"]
