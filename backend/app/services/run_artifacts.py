from __future__ import annotations

import asyncio
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, Run, Step
from app.schemas import RunArtifactOut, RunArtifactsOut
from app.services.artifacts import (
    file_sha256,
    signed_run_artifact_download_url,
)
from app.services.apps import should_redact_app_source
from app.services.output_contracts import ARTIFACT_MANIFEST_VERSION, ARTIFACT_RESERVED_TOP_LEVEL_DIRS
from app.services.runtime_paths import run_workspace
from app.services.workflow_data import artifact_items
from app.utils import loads

RUN_ARTIFACT_LIMIT = 200
ArtifactIntegrity = Literal["verified", "modified"]
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class RunArtifactCatalogEntry:
    artifact_id: str
    file_path: Path
    relative_path: str
    name: str
    size: int
    sha256: str
    integrity: ArtifactIntegrity
    origin_run_id: str
    origin_artifact_id: str
    origin_node_id: str
    origin_node_title: str
    reused_from_run_id: str | None
    reused_from_artifact_id: str | None
    mime: str | None


@dataclass(frozen=True)
class _DeclaredArtifactManifest:
    step_id: str
    node_id: str
    node: dict[str, Any]
    contract: dict[str, Any]
    item: dict[str, Any]


async def list_run_artifacts(db: AsyncSession, run_id: str, user_id: str) -> RunArtifactsOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    app = await db.get(App, run.app_id)
    redact_source = should_redact_app_source(app, user_id)
    catalog, truncated = await catalog_run_artifacts(db, run, limit=RUN_ARTIFACT_LIMIT)
    return RunArtifactsOut(
        artifacts=[_artifact_out(run, entry, redact_source=redact_source) for entry in catalog],
        truncated=truncated,
    )


async def catalog_run_artifacts(
    db: AsyncSession,
    run: Run,
    *,
    node_id: str | None = None,
    limit: int | None = RUN_ARTIFACT_LIMIT,
) -> tuple[list[RunArtifactCatalogEntry], bool]:
    catalog: dict[str, RunArtifactCatalogEntry] = {}
    invalid_versioned_paths: set[str] = set()
    truncated = False

    manifests = await _declared_artifact_manifests(db, run, node_id=node_id)
    for manifest in manifests:
        entry = await asyncio.to_thread(
            _catalog_entry,
            run,
            manifest.step_id,
            manifest.node_id,
            manifest.node,
            manifest.contract,
            manifest.item,
        )
        if entry is None:
            invalid_path = _versioned_manifest_relative_path(run, manifest.item)
            if invalid_path is not None:
                invalid_versioned_paths.add(invalid_path)
                catalog.pop(invalid_path, None)
            continue
        if entry.relative_path in invalid_versioned_paths:
            continue
        existing = catalog.get(entry.relative_path)
        if existing is not None:
            if existing.integrity == "modified":
                continue
            if entry.integrity == "modified":
                catalog[entry.relative_path] = entry
            continue
        if limit is not None and len(catalog) >= limit:
            truncated = True
            continue
        catalog[entry.relative_path] = entry

    return list(catalog.values()), truncated


async def validate_run_artifact_integrity(db: AsyncSession, run: Run) -> str | None:
    manifests = await _declared_artifact_manifests(db, run)
    for manifest in manifests:
        entry = await asyncio.to_thread(
            _catalog_entry,
            run,
            manifest.step_id,
            manifest.node_id,
            manifest.node,
            manifest.contract,
            manifest.item,
        )
        node_title = str(manifest.node.get("title") or manifest.node_id)
        if entry is None:
            path = manifest.item.get("path")
            label = path.strip() if isinstance(path, str) and path.strip() else "未知路径"
            return f"节点「{node_title}」声明的 artifact 文件缺失或 manifest 无效：{label}"
        if entry.integrity == "modified":
            return f"节点「{node_title}」声明的 artifact 已被修改：{entry.relative_path}"
    return None


async def find_run_artifact(
    db: AsyncSession,
    run: Run,
    relative_path: str,
) -> RunArtifactCatalogEntry | None:
    catalog, _truncated = await catalog_run_artifacts(db, run, limit=None)
    return next((entry for entry in catalog if entry.relative_path == relative_path), None)


async def _declared_artifact_manifests(
    db: AsyncSession,
    run: Run,
    *,
    node_id: str | None = None,
) -> list[_DeclaredArtifactManifest]:
    graph = loads(run.graph_json, {"nodes": [], "edges": []}) or {"nodes": [], "edges": []}
    nodes_by_id = _nodes_by_id(graph)
    query = (
        select(Step.id, Step.node_id, Step.output_json)
        .where(Step.run_id == run.id, Step.status == "success")
        .order_by(Step.ordering.asc(), Step.id.asc())
    )
    if node_id is not None:
        query = query.where(Step.node_id == node_id)
    rows = (await db.execute(query)).all()
    manifests: list[_DeclaredArtifactManifest] = []
    for step_id, step_node_id, output_json in rows:
        node = nodes_by_id.get(step_node_id)
        if not _is_artifact_contract_generate(node):
            continue
        contract = node["output_contract"]
        output = loads(output_json, None) if output_json else None
        manifests.extend(
            _DeclaredArtifactManifest(
                step_id=step_id,
                node_id=step_node_id,
                node=node,
                contract=contract,
                item=item,
            )
            for item in _artifact_items(output)
        )
    return manifests


def _nodes_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        node["id"]: node
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }


def _is_artifact_contract_generate(node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict) or node.get("type") != "generate":
        return False
    contract = node.get("output_contract")
    return isinstance(contract, dict) and contract.get("type") == "artifact"


def _artifact_items(value: Any) -> list[dict[str, Any]]:
    return artifact_items(value)


def _catalog_entry(
    run: Run,
    step_id: str,
    node_id: str,
    node: dict[str, Any],
    contract: dict[str, Any],
    item: dict[str, Any],
) -> RunArtifactCatalogEntry | None:
    holder = _lineage_record(item.get("holder"), require_artifact=False)
    if holder != {"run_id": run.id, "node_id": node_id, "step_id": step_id}:
        return None
    artifact_id = item.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        return None
    origin = _lineage_record(item.get("origin"), require_artifact=True)
    if origin is None:
        return None
    reused_from_value = item.get("reused_from")
    reused_from = (
        None
        if reused_from_value is None
        else _lineage_record(reused_from_value, require_artifact=True)
    )
    if reused_from_value is not None and reused_from is None:
        return None
    manifest_version = item.get("manifest_version")
    if (
        not isinstance(manifest_version, int)
        or isinstance(manifest_version, bool)
        or manifest_version != ARTIFACT_MANIFEST_VERSION
    ):
        return None

    path_value = item.get("path")
    path_text = path_value.strip() if isinstance(path_value, str) else ""
    if not path_text or "\\" in path_text or "\ufffd" in path_text:
        return None
    resolved = _resolve_workspace_artifact(run, path_text, allow_absolute=False)
    if resolved is None:
        return None
    path, relative = resolved

    name_value = item.get("name")
    name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else path.name
    if "\ufffd" in name:
        return None
    try:
        current_size = path.stat().st_size
        current_sha256 = file_sha256(path)
    except OSError:
        return None

    size_value = item.get("size")
    sha256_value = item.get("sha256")
    artifact_kind = item.get("artifact_kind")
    if (
        not isinstance(size_value, int)
        or isinstance(size_value, bool)
        or size_value < 0
        or not isinstance(sha256_value, str)
        or _SHA256_RE.fullmatch(sha256_value) is None
        or artifact_kind != contract.get("artifact_kind")
    ):
        return None
    size = size_value
    sha256 = sha256_value.lower()
    integrity: ArtifactIntegrity = "verified" if current_size == size and current_sha256 == sha256 else "modified"

    return RunArtifactCatalogEntry(
        artifact_id=artifact_id,
        file_path=path,
        relative_path=relative,
        name=name,
        size=size,
        sha256=sha256,
        integrity=integrity,
        origin_run_id=origin["run_id"],
        origin_artifact_id=origin["artifact_id"],
        origin_node_id=origin["node_id"],
        origin_node_title=origin.get("node_title") or origin["node_id"],
        reused_from_run_id=reused_from["run_id"] if reused_from is not None else None,
        reused_from_artifact_id=reused_from["artifact_id"] if reused_from is not None else None,
        mime=_guess_mime(path.name),
    )


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


def _resolve_workspace_artifact(
    run: Run,
    path_text: str,
    *,
    allow_absolute: bool,
) -> tuple[Path, str] | None:
    resolved = _workspace_artifact_location(run, path_text, allow_absolute=allow_absolute)
    if resolved is None:
        return None
    path, relative_path = resolved
    if not path.is_file():
        return None
    return path, relative_path


def _workspace_artifact_location(
    run: Run,
    path_text: str,
    *,
    allow_absolute: bool,
) -> tuple[Path, str] | None:
    try:
        workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
        candidate = Path(path_text)
        if candidate.is_absolute() and not allow_absolute:
            return None
        path = candidate.resolve() if candidate.is_absolute() else (workspace / path_text).resolve()
        relative_path = path.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    if not relative_path.parts or relative_path.parts[0] in ARTIFACT_RESERVED_TOP_LEVEL_DIRS:
        return None
    return path, relative_path.as_posix()


def _versioned_manifest_relative_path(run: Run, item: dict[str, Any]) -> str | None:
    path_value = item.get("path")
    path_text = path_value.strip() if isinstance(path_value, str) else ""
    if not path_text or "\\" in path_text or "\ufffd" in path_text:
        return None
    resolved = _workspace_artifact_location(run, path_text, allow_absolute=True)
    return resolved[1] if resolved is not None else None


def _artifact_out(
    run: Run,
    entry: RunArtifactCatalogEntry,
    *,
    redact_source: bool,
) -> RunArtifactOut:
    download_url = signed_run_artifact_download_url(run, entry.relative_path, entry.sha256)
    return RunArtifactOut(
        id=entry.artifact_id,
        name=entry.name,
        size=entry.size,
        sha256=entry.sha256,
        integrity=entry.integrity,
        download_url=download_url,
        origin_run_id=None if redact_source else entry.origin_run_id,
        origin_artifact_id=None if redact_source else entry.origin_artifact_id,
        origin_node_id=None if redact_source else entry.origin_node_id,
        origin_node_title=None if redact_source else entry.origin_node_title,
        reused_from_run_id=None if redact_source else entry.reused_from_run_id,
        reused_from_artifact_id=None if redact_source else entry.reused_from_artifact_id,
        mime=entry.mime,
    )


def _guess_mime(name: str) -> str | None:
    mime, _encoding = mimetypes.guess_type(name)
    return mime
