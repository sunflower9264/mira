from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, RunAgentBranch, RunAgentOperation, RunWorkspaceCheckpoint, Step
from app.runtime.base import AgentChunk
from app.runtime.factory import get_runtime
from app.services.run_hub import RunChannel
from app.services.runtime_paths import run_workspace
from app.services.tools import RuntimeToolConfig
from app.services.workspace_tree import TreeEntry, WorkspaceTree, remove_tree, scan_tree, tree_hash
from app.utils import dumps, loads, new_id, now_utc


MERGE_RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paths": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "result_sha256": {"type": ["string", "null"]},
                    "deleted": {"type": "boolean"},
                },
                "required": ["path", "sources", "result_sha256", "deleted"],
            },
        },
    },
    "required": ["paths"],
}


class RunAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class BranchLease:
    id: str
    workspace: Path
    session_id: str | None
    fork_session: bool
    pre_checkpoint_id: str | None


class RunAgent:
    """Run-scoped owner of session lineage, writable worktrees and checkpoints."""

    def __init__(
        self,
        db: AsyncSession,
        run: Run,
        channel: RunChannel,
        *,
        runtime_tools: RuntimeToolConfig | None,
    ) -> None:
        self.db = db
        self.run = run
        self.channel = channel
        self.runtime_tools = runtime_tools
        self.run_root = run_workspace(run.owner_id, run.app_id, run.id)
        self.workspaces = WorkspaceTree(self.run_root)

    async def ensure_root(self) -> RunAgentBranch:
        existing = (
            await self.db.execute(
                select(RunAgentBranch)
                .where(RunAgentBranch.run_id == self.run.id, RunAgentBranch.parent_branch_id.is_(None))
                .order_by(RunAgentBranch.created_at.asc(), RunAgentBranch.id.asc())
            )
        ).scalars().first()
        if existing is not None:
            return existing
        branch_id = new_id("branch")
        workspace = await asyncio.to_thread(self.workspaces.create_empty_branch, branch_id)
        branch = RunAgentBranch(
            id=branch_id,
            run_id=self.run.id,
            parent_branch_id=None,
            fork_node_id=None,
            base_checkpoint_id=None,
            provider_session_id=None,
            fork_from_session_id=None,
            workspace_relpath=workspace.relative_to(self.run_root).as_posix(),
            state="active",
        )
        self.db.add(branch)
        await self.db.flush()
        checkpoint = await self.checkpoint(branch, step=None, node_id=None, kind="run_root")
        branch.base_checkpoint_id = checkpoint.id
        await self.db.commit()
        return branch

    async def lease(self, branch_id: str) -> BranchLease:
        branch = await self._branch(branch_id)
        checkpoint = await self.latest_checkpoint(branch.id)
        session_id = branch.provider_session_id or branch.fork_from_session_id
        return BranchLease(
            id=branch.id,
            workspace=self._workspace(branch),
            session_id=session_id,
            fork_session=branch.provider_session_id is None and branch.fork_from_session_id is not None,
            pre_checkpoint_id=checkpoint.id if checkpoint is not None else branch.base_checkpoint_id,
        )

    async def fork(self, parent_branch_id: str, *, fork_node_id: str | None) -> RunAgentBranch:
        parent = await self._branch(parent_branch_id)
        checkpoint = await self.latest_checkpoint(parent.id)
        if checkpoint is None:
            raise RunAgentError(f"branch {parent.id} 没有可 fork 的 checkpoint")
        branch_id = new_id("branch")
        workspace = await asyncio.to_thread(self.workspaces.fork_branch, checkpoint.id, branch_id)
        child = RunAgentBranch(
            id=branch_id,
            run_id=self.run.id,
            parent_branch_id=parent.id,
            fork_node_id=fork_node_id,
            base_checkpoint_id=checkpoint.id,
            provider_session_id=None,
            fork_from_session_id=parent.provider_session_id or parent.fork_from_session_id,
            workspace_relpath=workspace.relative_to(self.run_root).as_posix(),
            state="active",
        )
        self.db.add(child)
        await self.db.flush()
        return child

    async def close_fanout_parent(self, branch_id: str) -> None:
        branch = await self._branch(branch_id)
        branch.state = "forked"
        branch.closed_at = now_utc()
        await self.db.commit()
        await asyncio.to_thread(self.workspaces.discard_branch, branch.id)

    async def record_session(self, branch_id: str, session_id: str | None) -> None:
        if not session_id:
            return
        branch = await self._branch(branch_id)
        branch.provider_session_id = session_id
        branch.fork_from_session_id = None
        await self.db.commit()

    async def checkpoint(
        self,
        branch: RunAgentBranch,
        *,
        step: Step | None,
        node_id: str | None,
        kind: str = "post_node",
        output: Any = None,
    ) -> RunWorkspaceCheckpoint:
        checkpoint_id = new_id("checkpoint")
        snapshot, digest = await asyncio.to_thread(
            self.workspaces.create_checkpoint, branch.id, checkpoint_id
        )
        output_digest = None
        if output is not None:
            output_digest = hashlib.sha256(dumps(output).encode("utf-8")).hexdigest()
        checkpoint = RunWorkspaceCheckpoint(
            id=checkpoint_id,
            run_id=self.run.id,
            step_id=step.id if step is not None else None,
            node_id=node_id,
            branch_id=branch.id,
            kind=kind,
            snapshot_relpath=snapshot.relative_to(self.run_root).as_posix(),
            tree_hash=digest,
            provider_session_id=branch.provider_session_id or branch.fork_from_session_id,
            output_digest=output_digest,
        )
        self.db.add(checkpoint)
        if step is not None:
            step.post_checkpoint_id = checkpoint.id
        await self.db.commit()
        return checkpoint

    async def latest_checkpoint(self, branch_id: str) -> RunWorkspaceCheckpoint | None:
        return (
            await self.db.execute(
                select(RunWorkspaceCheckpoint)
                .where(
                    RunWorkspaceCheckpoint.run_id == self.run.id,
                    RunWorkspaceCheckpoint.branch_id == branch_id,
                )
                .order_by(RunWorkspaceCheckpoint.created_at.desc(), RunWorkspaceCheckpoint.id.desc())
            )
        ).scalars().first()

    async def join(self, source_branch_ids: set[str], *, node_id: str) -> RunAgentBranch:
        if len(source_branch_ids) < 2:
            return await self._branch(next(iter(source_branch_ids)))
        sources = [await self._branch(branch_id) for branch_id in sorted(source_branch_ids)]
        common = await self._nearest_common_ancestor(sources)
        base_checkpoint_id = await self._common_fork_checkpoint(common.id, sources)
        coordinator = await self._fork_from_checkpoint(
            common,
            base_checkpoint_id,
            fork_node_id=node_id,
        )
        operation = RunAgentOperation(
            id=new_id("operation"),
            run_id=self.run.id,
            step_id=None,
            branch_id=coordinator.id,
            kind="join",
            status="running",
            provider_session_id=coordinator.fork_from_session_id,
            request_json="{}",
        )
        self.db.add(operation)
        await self.db.commit()
        try:
            receipt = await self._run_join_agent(
                common=common,
                coordinator=coordinator,
                sources=sources,
                base_checkpoint_id=base_checkpoint_id,
                node_id=node_id,
            )
            operation.status = "success"
            operation.provider_session_id = coordinator.provider_session_id
            operation.result_json = dumps(receipt)
            operation.finished_at = now_utc()
            merge_checkpoint = await self.checkpoint(
                coordinator,
                step=None,
                node_id=node_id,
                kind="post_join",
                output=receipt,
            )
            coordinator.base_checkpoint_id = merge_checkpoint.id
            coordinator.state = "active"
            for source in sources:
                source.state = "consumed"
                source.closed_at = now_utc()
            await self.db.commit()
            for source in sources:
                await asyncio.to_thread(self.workspaces.discard_branch, source.id)
            return coordinator
        except Exception as exc:
            operation.status = "failed"
            operation.result_json = dumps({"error": str(exc)})
            operation.finished_at = now_utc()
            coordinator.state = "failed"
            coordinator.closed_at = now_utc()
            await self.db.commit()
            raise

    async def _run_join_agent(
        self,
        *,
        common: RunAgentBranch,
        coordinator: RunAgentBranch,
        sources: list[RunAgentBranch],
        base_checkpoint_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        workspace = self._workspace(coordinator)
        staging = workspace / ".mira" / "merge"
        branches_dir = staging / "branches"
        contexts_dir = staging / "contexts"
        base_snapshot = self.workspaces.checkpoint_snapshot(base_checkpoint_id)
        base_tree = await asyncio.to_thread(scan_tree, base_snapshot)
        staging.mkdir(parents=True, exist_ok=False)
        shutil.copytree(base_snapshot, staging / "base", symlinks=True)
        manifests: dict[str, list[dict[str, Any]]] = {}
        for source in sources:
            snapshot = await self.latest_checkpoint(source.id)
            source_snapshot = (
                (self.run_root / snapshot.snapshot_relpath).resolve()
                if snapshot is not None
                else self._workspace(source)
            )
            shutil.copytree(source_snapshot, branches_dir / source.id, symlinks=True)
            manifests[source.id] = [
                change.as_dict()
                for change in await asyncio.to_thread(
                    self.workspaces.diff, base_checkpoint_id, source.id
                )
            ]
            contexts_dir.mkdir(parents=True, exist_ok=True)
            (contexts_dir / f"{source.id}.json").write_text(
                json.dumps(await self._branch_context(source.id), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifests, ensure_ascii=False, indent=2), encoding="utf-8")
        expected = _expected_receipt_paths(manifests)
        evidence_hash = await asyncio.to_thread(tree_hash, staging)
        prompt = _join_prompt(node_id=node_id, source_ids=[item.id for item in sources])
        runtime = get_runtime()
        chunks: list[str] = []

        async def on_chunk(chunk: AgentChunk) -> None:
            if chunk.type == "session":
                session_id = _session_id_from_chunk(chunk)
                if session_id:
                    coordinator.provider_session_id = session_id
                    coordinator.fork_from_session_id = None
                    await self.db.commit()
                return
            if chunk.type == "text" and chunk.text:
                chunks.append(chunk.text)
            await self.channel.publish(
                "step.delta",
                {"node_id": node_id, "chunk": chunk.model_dump(exclude_none=True)},
            )

        base_checkpoint = await self.db.get(RunWorkspaceCheckpoint, base_checkpoint_id)
        if base_checkpoint is None:
            raise RunAgentError("join base checkpoint 不存在")
        session_id = base_checkpoint.provider_session_id
        result = await runtime.execute(
            prompt=prompt,
            session_id=session_id,
            model=None,
            reasoning_effort=None,
            cwd=workspace,
            on_chunk=on_chunk,
            cancel_event=self.channel.cancel_event,
            on_ask_user=None,
            runtime_tools=self.runtime_tools,
            runtime_policy="execute",
            output_schema=MERGE_RECEIPT_SCHEMA,
            session_scope=f"run:{self.run.id}",
            fork_session=bool(session_id),
        )
        if result.finished_with == "cancelled":
            raise RunAgentError("join 已取消")
        if result.finished_with != "done":
            raise RunAgentError(result.error or "join Agent 执行失败")
        coordinator.provider_session_id = result.session_id or session_id
        coordinator.fork_from_session_id = None
        await self.db.commit()
        raw = result.total_text or "".join(chunks)
        try:
            receipt = loads(raw, None)
        except Exception as exc:
            raise RunAgentError("join receipt 不是合法 JSON") from exc
        if not isinstance(receipt, dict):
            raise RunAgentError("join receipt 必须是对象")
        if await asyncio.to_thread(tree_hash, staging) != evidence_hash:
            raise RunAgentError("join Agent 修改了只读合并证据")
        remove_tree(staging)
        _validate_merge_receipt(receipt, expected, workspace, base_tree=base_tree)
        return receipt

    async def _branch_context(self, branch_id: str) -> dict[str, Any]:
        steps = (
            await self.db.execute(
                select(Step)
                .where(Step.run_id == self.run.id, Step.branch_id == branch_id)
                .order_by(Step.ordering.asc(), Step.id.asc())
            )
        ).scalars().all()
        return {
            "branch_id": branch_id,
            "steps": [
                {
                    "node_id": step.node_id,
                    "status": step.status,
                    "input": loads(step.input_json, None),
                    "output": loads(step.output_json, None),
                }
                for step in steps
            ],
        }

    async def _fork_from_checkpoint(
        self,
        parent: RunAgentBranch,
        checkpoint_id: str,
        *,
        fork_node_id: str,
    ) -> RunAgentBranch:
        branch_id = new_id("branch")
        workspace = await asyncio.to_thread(self.workspaces.fork_branch, checkpoint_id, branch_id)
        branch = RunAgentBranch(
            id=branch_id,
            run_id=self.run.id,
            parent_branch_id=parent.id,
            fork_node_id=fork_node_id,
            base_checkpoint_id=checkpoint_id,
            provider_session_id=None,
            fork_from_session_id=parent.provider_session_id or parent.fork_from_session_id,
            workspace_relpath=workspace.relative_to(self.run_root).as_posix(),
            state="joining",
        )
        self.db.add(branch)
        await self.db.commit()
        return branch

    async def _nearest_common_ancestor(self, branches: list[RunAgentBranch]) -> RunAgentBranch:
        chains = [await self._ancestor_chain(branch) for branch in branches]
        common_ids = set(chains[0])
        for chain in chains[1:]:
            common_ids.intersection_update(chain)
        if not common_ids:
            raise RunAgentError("fan-in branches 没有共同父 branch")
        first_chain = chains[0]
        common_id = next(branch_id for branch_id in first_chain if branch_id in common_ids)
        return await self._branch(common_id)

    async def _ancestor_chain(self, branch: RunAgentBranch) -> list[str]:
        chain: list[str] = []
        current: RunAgentBranch | None = branch
        while current is not None:
            chain.append(current.id)
            current = await self._branch(current.parent_branch_id) if current.parent_branch_id else None
        return chain

    async def _common_fork_checkpoint(
        self,
        common_branch_id: str,
        branches: list[RunAgentBranch],
    ) -> str:
        checkpoint_ids: set[str] = set()
        for branch in branches:
            current = branch
            if current.id == common_branch_id:
                continue
            while current.parent_branch_id != common_branch_id:
                if current.parent_branch_id is None:
                    raise RunAgentError("branch lineage 与共同父不一致")
                current = await self._branch(current.parent_branch_id)
            if not current.base_checkpoint_id:
                raise RunAgentError("fan-out branch 缺少 base checkpoint")
            checkpoint_ids.add(current.base_checkpoint_id)
        if len(checkpoint_ids) != 1:
            raise RunAgentError("fan-in branches 不是从同一 workspace checkpoint 分叉")
        return next(iter(checkpoint_ids))

    async def _branch(self, branch_id: str | None) -> RunAgentBranch:
        if not branch_id:
            raise RunAgentError("branch_id 缺失")
        branch = await self.db.get(RunAgentBranch, branch_id)
        if branch is None or branch.run_id != self.run.id:
            raise RunAgentError(f"run branch 不存在：{branch_id}")
        await self.db.refresh(branch)
        return branch

    def _workspace(self, branch: RunAgentBranch) -> Path:
        path = (self.run_root / branch.workspace_relpath).resolve()
        try:
            path.relative_to(self.run_root)
        except ValueError as exc:
            raise RunAgentError("branch workspace 路径越界") from exc
        return path


def _expected_receipt_paths(
    manifests: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    expected: dict[str, dict[str, dict[str, Any]]] = {}
    for branch_id, changes in manifests.items():
        for change in changes:
            path = change.get("path")
            if isinstance(path, str):
                expected.setdefault(path, {})[branch_id] = change
    return expected


def _session_id_from_chunk(chunk: AgentChunk) -> str | None:
    raw = chunk.raw
    if not isinstance(raw, dict):
        return None
    thread = raw.get("thread")
    if isinstance(thread, dict) and isinstance(thread.get("id"), str):
        return thread["id"]
    return None


def _validate_merge_receipt(
    receipt: dict[str, Any],
    expected: dict[str, dict[str, dict[str, Any]]],
    workspace: Path,
    *,
    base_tree: dict[str, TreeEntry],
) -> None:
    paths = receipt.get("paths")
    if not isinstance(paths, list):
        raise RunAgentError("join receipt.paths 必须是数组")
    received: dict[str, dict[str, Any]] = {}
    for item in paths:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RunAgentError("join receipt path 项非法")
        path = item["path"]
        if path in received:
            raise RunAgentError(f"join receipt path 重复：{path}")
        received[path] = item
    if set(received) != set(expected):
        missing = sorted(set(expected) - set(received))
        extra = sorted(set(received) - set(expected))
        raise RunAgentError(f"join receipt 覆盖不完整：missing={missing}, extra={extra}")
    result_tree = scan_tree(workspace)
    unexpected = sorted(set(result_tree) - set(base_tree) - set(expected))
    if unexpected:
        raise RunAgentError(f"join Agent 新增了未声明路径：{unexpected}")
    for path in sorted(set(base_tree) - set(expected)):
        if result_tree.get(path) != base_tree[path]:
            raise RunAgentError(f"join Agent 改写了未声明的 base 路径：{path}")
    for path, source_changes in expected.items():
        item = received[path]
        received_sources = item.get("sources")
        if not isinstance(received_sources, list) or set(received_sources) != set(source_changes):
            raise RunAgentError(f"join receipt sources 不完整：{path}")
        deleted = item.get("deleted") is True
        entry = result_tree.get(path)
        if deleted:
            if entry is not None or item.get("result_sha256") is not None:
                raise RunAgentError(f"join receipt 删除状态与 workspace 不一致：{path}")
        elif entry is None or item.get("result_sha256") != entry.sha256:
            raise RunAgentError(f"join receipt hash 与 workspace 不一致：{path}")
        if len(source_changes) == 1:
            source_change = next(iter(source_changes.values()))
            source_deleted = source_change.get("kind") == "deleted"
            if source_deleted != deleted:
                raise RunAgentError(f"join Agent 改写了无冲突路径的删除状态：{path}")
            if not deleted and item.get("result_sha256") != source_change.get("sha256"):
                raise RunAgentError(f"join Agent 改写了无冲突路径内容：{path}")


def _join_prompt(*, node_id: str, source_ids: list[str]) -> str:
    return "\n".join(
        [
            "你是 Mira RunAgent 的 fan-in 合并协调 Agent。",
            f"当前 join 节点：{node_id}",
            f"待合并 branches：{', '.join(source_ids)}",
            "当前 /workspace 已从最近共同父 checkpoint 创建。",
            "完整 branch snapshots、contexts/*.json 节点输入输出和 diff manifest 位于 /workspace/.mira/merge。",
            "请逐一读取 manifest 和所有 branch-context.json，由你完整判断并把每个 branch 的变更合并到 /workspace。",
            "后端不会替你自动选择主分支或自动套用文件变更；冲突也必须由你基于全部 branch 上下文解决。",
            "不要修改 .mira/merge 下的证据文件。完成后返回严格 JSON receipt，paths 必须逐项覆盖 manifest 中出现的每个路径。",
            "每项包含 path、所有改动该路径的 sources、最终 result_sha256；若最终删除则 deleted=true 且 result_sha256=null。",
        ]
    )
