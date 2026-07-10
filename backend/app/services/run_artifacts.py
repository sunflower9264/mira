from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, Run, Step
from app.schemas import RunArtifactOut, RunArtifactsOut
from app.services.artifacts import signed_run_artifact_download_url
from app.services.apps import should_redact_app_source
from app.services.runtime_paths import run_workspace
from app.utils import loads

RUN_ARTIFACT_LIMIT = 200


async def list_run_artifacts(db: AsyncSession, run_id: str, user_id: str) -> RunArtifactsOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    app = await db.get(App, run.app_id)
    redact_source = should_redact_app_source(app, user_id)

    graph = loads(run.graph_json, {"nodes": [], "edges": []}) or {"nodes": [], "edges": []}
    nodes_by_id = _nodes_by_id(graph)
    steps = (
        await db.execute(select(Step).where(Step.run_id == run.id).order_by(Step.ordering.asc(), Step.id.asc()))
    ).scalars().all()
    artifacts: dict[str, RunArtifactOut] = {}
    truncated = False

    for step in steps:
        node = nodes_by_id.get(step.node_id)
        if not _is_artifact_contract_generate(node):
            continue
        output = loads(step.output_json, None) if step.output_json else None
        for item in _artifact_items(output):
            artifact = _artifact_from_contract_item(run, step.node_id, node, item, redact_source=redact_source)
            if artifact is None:
                continue
            if _add_artifact(artifacts, artifact):
                continue
            truncated = True
            break
        if truncated:
            break

    if not truncated:
        for artifact in _workspace_artifacts(run):
            if _add_artifact(artifacts, artifact):
                continue
            truncated = True
            break

    return RunArtifactsOut(artifacts=list(artifacts.values()), truncated=truncated)


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
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _artifact_from_contract_item(
    run: Run,
    node_id: str,
    node: dict[str, Any],
    item: dict[str, Any],
    *,
    redact_source: bool,
) -> RunArtifactOut | None:
    name = str(item.get("name") or "").strip()
    path_value = item.get("path")
    path_text = str(path_value).strip() if isinstance(path_value, str) else ""

    resolved = _resolve_workspace_artifact(run, path_text) if path_text else None
    if resolved is not None:
        path, relative = resolved
        url = signed_run_artifact_download_url(run, relative)
        return _artifact_out(
            name=name or path.name,
            path=relative,
            size=path.stat().st_size,
            download_url=url,
            source_node_id=None if redact_source else node_id,
            source_node_title=None if redact_source else str(node.get("title") or node_id),
            source_kind="artifact_contract",
            mime=_guess_mime(path.name),
        )

    return None


def _workspace_artifacts(run: Run) -> list[RunArtifactOut]:
    workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
    if not workspace.exists():
        return []
    artifacts: list[RunArtifactOut] = []
    for path in sorted((item for item in workspace.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        try:
            relative = path.resolve().relative_to(workspace).as_posix()
        except ValueError:
            continue
        artifacts.append(
            _artifact_out(
                name=path.name,
                path=relative,
                size=path.stat().st_size,
                download_url=signed_run_artifact_download_url(run, relative),
                source_node_id=None,
                source_node_title=None,
                source_kind="workspace_file",
                mime=_guess_mime(path.name),
            )
        )
    return artifacts


def _resolve_workspace_artifact(run: Run, path_text: str) -> tuple[Path, str] | None:
    if not path_text or "\\" in path_text:
        return None
    workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
    candidate = Path(path_text)
    path = candidate.resolve() if candidate.is_absolute() else (workspace / path_text).resolve()
    try:
        relative = path.relative_to(workspace).as_posix()
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path, relative


def _add_artifact(artifacts: dict[str, RunArtifactOut], artifact: RunArtifactOut) -> bool:
    key = artifact.path or artifact.download_url or artifact.id
    existing = artifacts.get(key)
    if existing is not None:
        if existing.source_node_id is None and artifact.source_node_id is not None:
            artifacts[key] = artifact
        return True
    if len(artifacts) >= RUN_ARTIFACT_LIMIT:
        return False
    artifacts[key] = artifact
    return True


def _artifact_out(
    *,
    name: str,
    path: str | None,
    size: int | None,
    download_url: str,
    source_node_id: str | None,
    source_node_title: str | None,
    source_kind: str,
    mime: str | None,
) -> RunArtifactOut:
    stable = download_url.split("download_token=", 1)[0].rstrip("?&")
    return RunArtifactOut(
        id=stable or path or name,
        name=name,
        path=path,
        size=size,
        download_url=download_url,
        source_node_id=source_node_id,
        source_node_title=source_node_title,
        source_kind=source_kind,  # type: ignore[arg-type]
        mime=mime,
    )


def _guess_mime(name: str) -> str | None:
    mime, _encoding = mimetypes.guess_type(name)
    return mime
