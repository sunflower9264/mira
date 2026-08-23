from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.services.artifacts import file_sha256
from app.services.output_contracts import artifact_output_for_storage, output_contract_for_node
from app.utils import new_id


OUTPUT_ENVELOPE_VERSION = 1
OUTPUT_ENVELOPE_KEY = "_mira_output_version"


class WorkflowDataIntegrityError(ValueError):
    pass


def build_output_envelope(
    node: dict[str, Any],
    output: Any,
    *,
    step_workspace: Path,
    run_workspace: Path,
    run_id: str,
    node_id: str,
    step_id: str,
) -> dict[str, Any]:
    holder = _holder(run_id=run_id, node_id=node_id, step_id=step_id)
    node_title = str(node.get("title") or node_id)
    contract = output_contract_for_node(node)
    artifacts: list[dict[str, Any]] = []
    value = output
    if isinstance(contract, dict) and contract.get("type") == "artifact":
        value = None
        stored = artifact_output_for_storage(
            node,
            output,
            workspace=step_workspace,
            artifact_root=run_workspace,
        )
        if not isinstance(stored, list):
            raise ValueError("artifact manifest 不是数组")
        for index, item in enumerate(stored, start=1):
            artifact_id = new_id("artifact")
            source = _resolve_declared_artifact(run_workspace, str(item.get("path") or ""))
            if source is None:
                raise ValueError(f"artifact 提交源文件不存在：{item.get('path')}")
            target_relative = (
                Path("artifacts")
                / _safe_segment(node_id)
                / artifact_id
                / _safe_filename(source.name)
            )
            target = (run_workspace / target_relative).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(target.stat().st_mode & ~0o222)
            artifacts.append(
                {
                    **item,
                    "path": target_relative.as_posix(),
                    "artifact_id": artifact_id,
                    "holder": holder,
                    "origin": {
                        **holder,
                        "artifact_id": artifact_id,
                        "node_title": node_title,
                    },
                    "reused_from": None,
                    "output_port": "artifacts",
                    "ordinal": index,
                }
            )
    return {
        OUTPUT_ENVELOPE_KEY: OUTPUT_ENVELOPE_VERSION,
        "meta": {
            "holder": holder,
            "origin": {**holder, "node_title": node_title},
            "reused_from": None,
        },
        "value": value,
        "artifacts": artifacts,
    }


def is_output_envelope(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(OUTPUT_ENVELOPE_KEY) == OUTPUT_ENVELOPE_VERSION
        and isinstance(value.get("meta"), dict)
        and isinstance(value.get("artifacts"), list)
        and "value" in value
    )


def visible_output(value: Any) -> Any:
    if not is_output_envelope(value):
        return value
    artifacts = value.get("artifacts")
    return artifacts if artifacts else value.get("value")


def workflow_data_prompt() -> str:
    return "\n".join(
        [
            "# Workflow 数据 Interface",
            "整个应用的一次运行由同一个 RunAgent 推进；顺序节点延续同一会话，并共享同一个可写 `/workspace`。",
            "上游已经完成的分析、决策和文件都保留在当前会话与 workspace 中；不要等待额外的显式结果注入。",
            "应用输入和素材在 Agent 首次启动前写入 `/workspace/.mira/run-context/`；需要时直接读取该目录，附件副本位于 `/workspace/inputs/`。",
            "`/mnt/inputs` 只用于当前 ask_user 恢复时新增的附件。",
            "可以在 `/workspace` 中维护跨节点工作文件；需要出现在节点正式结果或用户下载区的内容仍必须满足当前节点的强输出契约。",
            "节点最终回复只提交当前节点契约要求的结果，不要用隐藏 sidecar、handoff 或 manifest 文件代替正式节点输出。",
            "业务验收完成但结论不通过时，仍须返回符合契约的 failed/blocked 业务结果；只有工具、程序或契约无法执行时才属于节点执行失败。",
        ]
    )


def artifact_items(value: Any) -> list[dict[str, Any]]:
    if not is_output_envelope(value):
        return []
    return [item for item in value.get("artifacts", []) if isinstance(item, dict)]


def copy_reused_output_envelope(
    value: Any,
    *,
    source_workspace: Path,
    target_workspace: Path,
    target_run_id: str,
    target_node_id: str,
    target_step_id: str,
) -> Any:
    if not is_output_envelope(value):
        return value
    source_meta = value["meta"]
    source_holder = _lineage_record(source_meta.get("holder"), require_artifact=False)
    origin = _lineage_record(source_meta.get("origin"), require_artifact=False)
    if source_holder is None or origin is None:
        raise ValueError("复用 OutputEnvelope 缺少 lineage")
    target_holder = _holder(
        run_id=target_run_id,
        node_id=target_node_id,
        step_id=target_step_id,
    )
    copied_artifacts: list[dict[str, Any]] = []
    for item in artifact_items(value):
        path_text = item.get("path")
        if not isinstance(path_text, str):
            raise ValueError("复用 artifact 缺少 path")
        source = _resolve_declared_artifact(source_workspace, path_text)
        if source is None:
            raise ValueError(f"复用 artifact 不存在：{path_text}")
        expected_sha256 = item.get("sha256")
        if not isinstance(expected_sha256, str) or file_sha256(source) != expected_sha256:
            raise ValueError(f"复用 artifact 完整性校验失败：{path_text}")
        source_artifact_id = item.get("artifact_id")
        source_artifact_holder = _lineage_record(item.get("holder"), require_artifact=False)
        artifact_origin = _lineage_record(item.get("origin"), require_artifact=True)
        if (
            not isinstance(source_artifact_id, str)
            or not source_artifact_id
            or source_artifact_holder is None
            or artifact_origin is None
        ):
            raise ValueError("复用 artifact 缺少 lineage")
        artifact_id = new_id("artifact")
        target_relative = (
            Path("artifacts")
            / _safe_segment(target_node_id)
            / artifact_id
            / _safe_filename(source.name)
        )
        target = (target_workspace / target_relative).resolve()
        try:
            target.relative_to(target_workspace.resolve())
        except ValueError as exc:
            raise ValueError(f"复用 artifact 路径越界：{path_text}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_artifacts.append(
            {
                **item,
                "path": target_relative.as_posix(),
                "artifact_id": artifact_id,
                "holder": target_holder,
                "origin": artifact_origin,
                "reused_from": {
                    **source_artifact_holder,
                    "artifact_id": source_artifact_id,
                },
            }
        )
    return {
        OUTPUT_ENVELOPE_KEY: OUTPUT_ENVELOPE_VERSION,
        "meta": {
            "holder": target_holder,
            "origin": origin,
            "reused_from": source_holder,
        },
        "value": value.get("value"),
        "artifacts": copied_artifacts,
    }


def _holder(*, run_id: str, node_id: str, step_id: str) -> dict[str, str]:
    return {"run_id": run_id, "node_id": node_id, "step_id": step_id}


def _lineage_record(value: Any, *, require_artifact: bool) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    required = {"run_id", "node_id", "step_id"}
    if require_artifact:
        required.add("artifact_id")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        return None
    return {
        key: item
        for key, item in value.items()
        if key in {"run_id", "node_id", "step_id", "artifact_id", "node_title"}
        and isinstance(item, str)
    }


def _resolve_declared_artifact(workspace: Path, path_text: str) -> Path | None:
    if not path_text or path_text.startswith("/") or "\\" in path_text:
        return None
    root = workspace.resolve()
    path = (root / path_text).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _safe_segment(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("._")
    return safe or "node"


def _safe_filename(value: str) -> str:
    name = Path(value).name
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in name).strip(".")
    return safe or "artifact"
