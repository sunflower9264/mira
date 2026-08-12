from __future__ import annotations

import json
import mimetypes
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, ValidationError, SchemaError

from app.services.artifacts import file_sha256
from app.services.office_documents import (
    OFFICE_VALIDATION_TIMEOUT_SECONDS,
    OfficeValidationUnavailable,
    validate_office_documents,
)
from app.services.text_integrity import (
    UNICODE_REPLACEMENT_ERROR,
    contains_unicode_replacement,
    validate_artifact_text_integrity,
)


OutputContractType = Literal["json", "html", "artifact"]
CONTRACT_TYPES = {"json", "html", "artifact"}
ARTIFACT_KINDS = {"image", "code", "html", "markdown", "csv", "excel", "docx", "ppt", "pdf", "archive", "zip", "file"}
CONTRACT_KEYS = {
    "type",
    "json_schema",
    "artifact_kind",
    "max_count",
    "validate_office_documents",
}
JSON_SCHEMA_KEYS = {
    "$schema",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "description",
    "title",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "format",
}
ARTIFACT_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".svg"},
    "code": {".zip", ".tar", ".gz", ".tgz"},
    "html": {".html", ".htm"},
    "markdown": {".md", ".markdown"},
    "csv": {".csv"},
    "excel": {".xlsx", ".xls"},
    "docx": {".docx"},
    "ppt": {".pptx", ".ppt"},
    "pdf": {".pdf"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"},
    "zip": {".zip"},
}
ARTIFACT_MANIFEST_VERSION = 1
ARTIFACT_RESERVED_TOP_LEVEL_DIRS = {".uploads"}
OFFICE_VALIDATION_ARTIFACT_KINDS = {"docx", "excel", "ppt", "zip", "file"}
_FENCED_RE = re.compile(r"^```(?:json|JSON|markdown|md|text)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class ContractValidationResult:
    ok: bool
    output: Any = None
    error: str | None = None
    repairable: bool = True


def output_contract_for_node(node: dict[str, Any]) -> dict[str, Any] | None:
    contract = node.get("output_contract")
    if not isinstance(contract, dict):
        return None
    output_type = contract.get("type")
    if output_type not in CONTRACT_TYPES:
        return None
    return contract


def normalize_output_contract_config(contract: Any) -> Any:
    if not isinstance(contract, dict):
        return contract
    output_type = contract.get("type")
    if output_type == "json":
        return {
            key: value
            for key, value in contract.items()
            if key not in {"artifact_kind", "max_count", "validate_office_documents"}
        }
    if output_type == "html":
        return {
            key: value
            for key, value in contract.items()
            if key not in {"json_schema", "artifact_kind", "max_count", "validate_office_documents"}
        }
    if output_type == "artifact":
        return {key: value for key, value in contract.items() if key != "json_schema"}
    return contract


def validate_output_contract_config(node: dict[str, Any]) -> str | None:
    node_type = node.get("type")
    contract = node.get("output_contract")
    if contract is None:
        return None
    label = node.get("title") or node.get("id") or "?"
    if node_type != "generate":
        return f"节点「{label}」只有 generate 支持 output_contract"
    if not isinstance(contract, dict):
        return f"节点「{label}」output_contract 必须是对象"
    output_type = contract.get("type")
    if output_type not in CONTRACT_TYPES:
        return f"节点「{label}」output_contract.type 无效"
    unknown_keys = sorted(str(key) for key in contract.keys() if key not in CONTRACT_KEYS)
    if unknown_keys:
        return f"节点「{label}」output_contract 包含不支持的字段：{', '.join(unknown_keys)}"
    if output_type == "json":
        schema = contract.get("json_schema")
        if not isinstance(schema, dict):
            return f"节点「{label}」json 输出契约必须包含 json_schema 对象"
        schema_error = validate_strict_json_schema(schema)
        if schema_error:
            return f"节点「{label}」json_schema 无效：{schema_error}"
    elif "json_schema" in contract:
        return f"节点「{label}」只有 json 输出契约支持 json_schema"
    artifact_kind = contract.get("artifact_kind")
    if output_type == "artifact":
        if artifact_kind not in ARTIFACT_KINDS:
            return f"节点「{label}」artifact 输出契约必须包含有效 artifact_kind"
        max_count = contract.get("max_count")
        if max_count is not None and (
            not isinstance(max_count, int) or isinstance(max_count, bool) or max_count < 1 or max_count > 50
        ):
            return f"节点「{label}」output_contract.max_count 必须是 1-50 的整数"
        validate_office = contract.get("validate_office_documents")
        if validate_office is not None and not isinstance(validate_office, bool):
            return f"节点「{label}」output_contract.validate_office_documents 必须是 bool"
        if validate_office is True and artifact_kind not in OFFICE_VALIDATION_ARTIFACT_KINDS:
            supported = ", ".join(sorted(OFFICE_VALIDATION_ARTIFACT_KINDS))
            return f"节点「{label}」validate_office_documents 仅支持 artifact_kind：{supported}"
    elif artifact_kind is not None:
        return f"节点「{label}」只有 artifact 输出契约支持 artifact_kind"
    elif "max_count" in contract:
        return f"节点「{label}」只有 artifact 输出契约支持 max_count"
    elif "validate_office_documents" in contract:
        return f"节点「{label}」只有 artifact 输出契约支持 validate_office_documents"
    return None


def validate_strict_json_schema(schema: dict[str, Any]) -> str | None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return exc.message
    if schema.get("type") != "object":
        return "根 schema.type 必须是 object"
    return _validate_schema_subset(schema, path="$", root=True)


def schema_for_contract(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("type") == "output":
        return html_output_schema()
    if node.get("type") != "generate":
        return None
    contract = output_contract_for_node(node)
    if contract is None:
        return None
    output_type = contract["type"]
    if output_type == "json":
        schema = contract.get("json_schema")
        return schema if isinstance(schema, dict) else None
    if output_type == "html":
        return html_output_schema()
    if output_type == "artifact":
        return artifact_output_schema(contract)
    return None


def html_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"html": {"type": "string", "minLength": 1}},
        "required": ["html"],
    }


def artifact_output_schema(contract: dict[str, Any]) -> dict[str, Any]:
    max_count = contract.get("max_count") if isinstance(contract.get("max_count"), int) else 10
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "artifacts": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "name": {"type": "string", "minLength": 1},
                    },
                    "required": ["path", "name"],
                },
            }
        },
        "required": ["artifacts"],
    }


def contract_prompt_suffix(node: dict[str, Any]) -> str:
    if node.get("type") == "output":
        return "\n".join(
            [
                "# 输出契约",
                '最终回复必须是 JSON 对象，形状为 {"html":"..."}。',
                "html 字段内放可由 iframe srcDoc 直接渲染的静态 HTML；不要返回 Markdown、解释文字或额外字段。",
            ]
        )
    if node.get("type") != "generate":
        return ""
    contract = output_contract_for_node(node)
    if contract is None:
        return ""
    output_type = contract["type"]
    lines = [
        "# 输出契约",
        f"本 generate 节点必须输出 `{output_type}` 契约结果。请严格遵守，不要输出额外解释。",
    ]
    if output_type == "json":
        lines.append("最终回复必须是严格符合后端 JSON Schema 的 JSON 对象。")
    elif output_type == "html":
        lines.append('最终回复必须是 JSON 对象，形状为 {"html":"..."}；html 字段内放静态 HTML。')
    elif output_type == "artifact":
        artifact_kind = _artifact_kind_label(contract.get("artifact_kind"))
        lines.append(
            f'请生成{artifact_kind}到当前工作目录内，并最终只返回 JSON 对象，形状为 '
            '{"artifacts":[{"path":"相对路径或工作区内路径","name":"显示名"}]}。'
        )
        hint = _artifact_kind_hint(contract.get("artifact_kind"))
        if hint:
            lines.append(hint)
        if contract.get("validate_office_documents") is True:
            lines.append("产物必须包含 Office 文档，且每个文档都必须能被实际打开并转换出至少一页 PDF。")
    return "\n".join(lines)


def contract_repair_description(node: dict[str, Any]) -> str:
    return contract_prompt_suffix(node).replace("# 输出契约", "输出契约").strip()


def validate_contract_output(
    node: dict[str, Any],
    text: str,
    *,
    workspace: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ContractValidationResult:
    if node.get("type") == "output":
        return _validate_html_output(text)
    if node.get("type") != "generate":
        return ContractValidationResult(ok=True, output=text)
    contract = output_contract_for_node(node)
    if contract is None:
        if not text.strip():
            return ContractValidationResult(ok=False, error="输出不能为空")
        if contains_unicode_replacement(text):
            return ContractValidationResult(ok=False, error=UNICODE_REPLACEMENT_ERROR)
        return ContractValidationResult(ok=True, output=text)
    output_type = contract["type"]
    if output_type == "json":
        parsed = _parse_json_output(text)
        if not parsed.ok:
            return parsed
        if contains_unicode_replacement(parsed.output):
            return ContractValidationResult(ok=False, error=UNICODE_REPLACEMENT_ERROR)
        try:
            Draft202012Validator(contract["json_schema"]).validate(parsed.output)
        except ValidationError as exc:
            return ContractValidationResult(ok=False, error=f"JSON Schema 校验失败：{exc.message}")
        return ContractValidationResult(ok=True, output=parsed.output)
    if output_type == "html":
        return _validate_html_output(text)
    if output_type == "artifact":
        return _validate_artifact_output(contract, text, workspace=workspace, cancelled=cancelled)
    return ContractValidationResult(ok=True, output=text)


def artifact_output_for_storage(
    node: dict[str, Any],
    output: Any,
    *,
    workspace: Path,
) -> Any:
    contract = output_contract_for_node(node)
    if not isinstance(contract, dict) or contract.get("type") != "artifact":
        return output
    if not isinstance(output, list):
        raise ValueError("artifact 校验结果不是数组")

    workspace_resolved = workspace.resolve()
    artifact_kind = str(contract.get("artifact_kind") or "file")
    manifest: list[dict[str, Any]] = []
    for index, item in enumerate(output, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个 artifact 校验结果不是对象")
        path_text = item.get("path")
        if not isinstance(path_text, str) or not path_text.strip():
            raise ValueError(f"第 {index} 个 artifact 校验结果缺少 path")
        resolved = _resolve_workspace_path(workspace_resolved, path_text)
        if resolved is None or not resolved.is_file():
            raise ValueError(f"第 {index} 个 artifact 文件不存在")
        relative = resolved.relative_to(workspace_resolved).as_posix()
        if _is_reserved_artifact_path(relative):
            raise ValueError(f"第 {index} 个 artifact 位于上传暂存目录")
        name = item.get("name")
        display_name = name.strip() if isinstance(name, str) and name.strip() else resolved.name
        manifest.append(
            {
                "path": relative,
                "name": display_name,
                "size": resolved.stat().st_size,
                "sha256": file_sha256(resolved),
                "artifact_kind": artifact_kind,
                "manifest_version": ARTIFACT_MANIFEST_VERSION,
            }
        )
    return manifest


def _validate_schema_subset(schema: dict[str, Any], *, path: str, root: bool = False) -> str | None:
    unknown = sorted(str(key) for key in schema.keys() if key not in JSON_SCHEMA_KEYS)
    if unknown:
        return f"{path} 包含不支持的字段：{', '.join(unknown)}"
    if any(key in schema for key in ("$ref", "oneOf", "anyOf", "allOf", "not")):
        return f"{path} 不支持 $ref/oneOf/anyOf/allOf/not"
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        return f"{path}.type 无效"
    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            return f"{path}.additionalProperties 必须是 false"
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            return f"{path}.properties 必须是非空对象"
        required = schema.get("required")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            return f"{path}.required 必须是字符串数组"
        property_keys = set(properties.keys())
        required_keys = set(required)
        if root and required_keys != property_keys:
            return f"{path}.required 必须包含所有 properties 字段"
        if not required_keys.issubset(property_keys):
            return f"{path}.required 包含 properties 中不存在的字段"
        for key, value in properties.items():
            if not isinstance(value, dict):
                return f"{path}.properties.{key} 必须是对象"
            nested_error = _validate_schema_subset(value, path=f"{path}.properties.{key}")
            if nested_error:
                return nested_error
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return f"{path}.items 必须是对象"
        nested_error = _validate_schema_subset(items, path=f"{path}.items")
        if nested_error:
            return nested_error
    return None


def _validate_html_output(text: str) -> ContractValidationResult:
    parsed = _parse_json_output(text)
    if not parsed.ok or not isinstance(parsed.output, dict):
        return ContractValidationResult(ok=False, error='HTML 输出必须是 JSON 对象：{"html":"..."}')
    html = parsed.output.get("html")
    if not isinstance(html, str) or not html.strip():
        return ContractValidationResult(ok=False, error="HTML 输出必须包含非空 html 字符串")
    if contains_unicode_replacement(html):
        return ContractValidationResult(ok=False, error=UNICODE_REPLACEMENT_ERROR)
    return ContractValidationResult(ok=True, output=html)


def _validate_artifact_output(
    contract: dict[str, Any],
    text: str,
    *,
    workspace: Path | None,
    cancelled: Callable[[], bool] | None,
) -> ContractValidationResult:
    office_deadline = (
        time.monotonic() + OFFICE_VALIDATION_TIMEOUT_SECONDS
        if contract.get("validate_office_documents") is True
        else None
    )
    if workspace is None:
        return ContractValidationResult(ok=False, error="artifact 输出缺少工作区上下文")
    parsed = _parse_json_output(text)
    if not parsed.ok or not isinstance(parsed.output, dict):
        return ContractValidationResult(ok=False, error='artifact 输出必须是 JSON 对象：{"artifacts":[...]}')
    artifacts = parsed.output.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ContractValidationResult(ok=False, error="artifact 输出必须包含非空 artifacts 数组")
    max_count = contract.get("max_count")
    if not isinstance(max_count, int) or isinstance(max_count, bool):
        max_count = 10
    if len(artifacts) > max_count:
        return ContractValidationResult(ok=False, error=f"artifact 数量超过 max_count={max_count}")
    normalized: list[dict[str, str]] = []
    artifact_kind = str(contract.get("artifact_kind") or "file")
    workspace_resolved = workspace.resolve()
    seen_paths: set[str] = set()
    for index, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact 必须是对象")
        path_text = item.get("path")
        if not isinstance(path_text, str) or not path_text.strip():
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact 必须包含非空 path")
        if "\ufffd" in path_text:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact.path 包含损坏字符 U+FFFD")
        if "download_url" in item:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact 不允许返回 download_url")
        resolved = _resolve_workspace_path(workspace_resolved, path_text)
        if resolved is None:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact.path 不在运行工作区内")
        if not resolved.exists() or not resolved.is_file():
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact.path 文件不存在")
        relative = resolved.relative_to(workspace_resolved).as_posix()
        if _is_reserved_artifact_path(relative):
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact 位于上传暂存目录")
        if relative in seen_paths:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact.path 与前面的产物重复")
        seen_paths.add(relative)
        try:
            file_error = _validate_artifact_file(
                resolved,
                artifact_kind,
                validate_office=contract.get("validate_office_documents") is True,
                cancelled=cancelled,
                office_deadline=office_deadline,
            )
        except OfficeValidationUnavailable as exc:
            return ContractValidationResult(ok=False, error=str(exc), repairable=False)
        if file_error:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact 文件无效：{file_error}")
        name = item.get("name")
        display_name = name.strip() if isinstance(name, str) and name.strip() else resolved.name
        if "\ufffd" in display_name:
            return ContractValidationResult(ok=False, error=f"第 {index} 个 artifact.name 包含损坏字符 U+FFFD")
        normalized.append({"path": str(resolved), "name": display_name})
    return ContractValidationResult(ok=True, output=normalized)


def _validate_artifact_file(
    path: Path,
    artifact_kind: str,
    *,
    validate_office: bool = False,
    cancelled: Callable[[], bool] | None = None,
    office_deadline: float | None = None,
) -> str | None:
    extensions = ARTIFACT_EXTENSIONS.get(artifact_kind)
    suffix = path.suffix.lower()
    if extensions is not None and suffix not in extensions:
        return f"扩展名 {suffix or '(none)'} 不符合 {artifact_kind}"
    integrity_error = validate_artifact_text_integrity(path)
    if integrity_error:
        return integrity_error
    if validate_office:
        office_error = validate_office_documents(path, cancelled=cancelled, deadline=office_deadline)
        if office_error:
            return office_error
    if artifact_kind == "html":
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"HTML 无法按严格 UTF-8 读取：{exc}"
        result = _validate_html_output(json.dumps({"html": text}, ensure_ascii=False))
        return None if result.ok else result.error
    if artifact_kind == "csv":
        try:
            sample = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            return f"CSV 无法按严格 UTF-8 读取：{exc}"
        if not sample:
            return "CSV 为空"
        column_count = len(sample[0].split(","))
        if column_count < 1:
            return "CSV 表头无效"
    if artifact_kind == "pdf" and path.read_bytes()[:4] != b"%PDF":
        return "PDF 文件头无效"
    mime, _encoding = mimetypes.guess_type(path.name)
    if artifact_kind == "image" and suffix != ".svg" and (mime is None or not mime.startswith("image/")):
        return "MIME 类型不是图片"
    return None


def _resolve_workspace_path(workspace: Path, path_text: str) -> Path | None:
    candidate = Path(path_text.strip())
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve()
        workspace_resolved = workspace.resolve()
        resolved.relative_to(workspace_resolved)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _is_reserved_artifact_path(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    first_part = parts[0] if parts else ""
    return first_part in ARTIFACT_RESERVED_TOP_LEVEL_DIRS


def _parse_json_output(text: str) -> ContractValidationResult:
    cleaned = _strip_fenced(text.strip())
    try:
        return ContractValidationResult(ok=True, output=json.loads(cleaned))
    except json.JSONDecodeError as exc:
        return ContractValidationResult(ok=False, error=f"JSON 解析失败：{exc.msg}")


def _strip_fenced(text: str) -> str:
    match = _FENCED_RE.match(text)
    return match.group(1).strip() if match else text


def _artifact_kind_label(value: Any) -> str:
    labels = {
        "image": "图片文件产物",
        "code": "代码包产物",
        "html": "HTML 文件产物",
        "markdown": "Markdown 文件产物",
        "csv": "CSV 文件产物",
        "excel": "Excel 文件产物",
        "docx": "DOCX 文档产物",
        "ppt": "PPT 演示文稿产物",
        "pdf": "PDF 文件产物",
        "archive": "压缩包产物",
        "zip": "ZIP 压缩包产物",
        "file": "文件产物",
    }
    return labels.get(str(value), "文件产物")


def _artifact_kind_hint(value: Any) -> str:
    hints = {
        "image": "图片产物应优先生成 png、jpg、jpeg、webp 或 svg 文件。",
        "code": "代码产物应优先生成可下载的 zip 或 tar 项目包，而不是只返回代码片段。",
        "html": "HTML 产物应写入 .html 文件，并返回该文件路径。",
        "markdown": "Markdown 产物应写入 .md 文件，并返回该文件路径。",
        "csv": "CSV 产物应写入 .csv 文件，并返回该文件路径。",
        "excel": "Excel 产物应优先生成 .xlsx 文件，并返回该文件路径。",
        "docx": "DOCX 产物应生成 .docx 文件，并返回该文件路径。",
        "ppt": "PPT 产物应优先生成 .pptx 文件，并返回该文件路径。",
        "pdf": "PDF 产物应生成 .pdf 文件，并返回该文件路径。",
        "archive": "压缩包产物应优先生成 .zip 或 .tar 文件，并返回该文件路径。",
        "zip": "ZIP 压缩包产物应生成 .zip 文件，并返回该文件路径。",
    }
    return hints.get(str(value), "")
