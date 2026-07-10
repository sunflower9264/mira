from __future__ import annotations

import json
import re
from typing import Any


def parse_structured_json_object(text: str, *, label: str) -> dict[str, Any]:
    raw = text.strip()
    parse_error: json.JSONDecodeError | None = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        parse_error = exc
        data = _extract_json_object(raw)
    if data is None:
        raise ValueError(f"{label} 不是有效 JSON 对象") from parse_error
    if not isinstance(data, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return data


def _extract_json_object(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", text):
        try:
            data, end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            candidates.append((end, data))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


_STRING_ARRAY_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
}


_NON_EMPTY_STRING_ARRAY_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {"type": "string", "minLength": 1},
}


NL_COMPILE_PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plan": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "goal_summary": {"type": "string", "minLength": 1},
                "assumptions": _STRING_ARRAY_SCHEMA,
                "data_flow": _STRING_ARRAY_SCHEMA,
                "implementation_steps": _NON_EMPTY_STRING_ARRAY_SCHEMA,
                "graph_changes": _NON_EMPTY_STRING_ARRAY_SCHEMA,
                "expected_inputs": _NON_EMPTY_STRING_ARRAY_SCHEMA,
                "expected_outputs": _NON_EMPTY_STRING_ARRAY_SCHEMA,
                "acceptance_criteria": _NON_EMPTY_STRING_ARRAY_SCHEMA,
            },
            "required": [
                "goal_summary",
                "assumptions",
                "data_flow",
                "implementation_steps",
                "graph_changes",
                "expected_inputs",
                "expected_outputs",
                "acceptance_criteria",
            ],
        }
    },
    "required": ["plan"],
}


NL_COMPILE_PATCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "patches": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["add_node", "remove_node", "update_node", "add_edge", "remove_edge"],
                    },
                    "id": {"type": ["string", "null"]},
                    "node_json": {"type": ["string", "null"]},
                    "patch_json": {"type": ["string", "null"]},
                    "edge_id": {"type": ["string", "null"]},
                    "edge_source": {"type": ["string", "null"]},
                    "edge_target": {"type": ["string", "null"]},
                    "edge_source_handle": {"type": ["string", "null"]},
                },
                "required": [
                    "op",
                    "id",
                    "node_json",
                    "patch_json",
                    "edge_id",
                    "edge_source",
                    "edge_target",
                    "edge_source_handle",
                ],
            },
        }
    },
    "required": ["patches"],
}


PROMPT_ASSISTANT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prompt": {"type": "string", "minLength": 1},
        "output_contract_json": {"type": ["string", "null"]},
    },
    "required": ["prompt", "output_contract_json"],
}
