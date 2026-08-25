from __future__ import annotations

import logging
import shutil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run
from app.services.runtime_paths import run_workspaces_root_path, runtime_dir

logger = logging.getLogger(__name__)


async def cleanup_orphan_run_workspaces(db: AsyncSession) -> int:
    root = run_workspaces_root_path()
    if not root.is_dir():
        return 0
    rows = (await db.execute(select(Run.owner_id, Run.app_id, Run.id))).all()
    live = {(owner_id, app_id, run_id) for owner_id, app_id, run_id in rows}
    removed = 0
    for user_dir in root.iterdir():
        if not user_dir.is_dir() or user_dir.is_symlink():
            continue
        for app_dir in user_dir.iterdir():
            if app_dir.name.startswith("_") or not app_dir.is_dir() or app_dir.is_symlink():
                continue
            for run_dir in app_dir.iterdir():
                if not run_dir.is_dir() or run_dir.is_symlink():
                    continue
                if (user_dir.name, app_dir.name, run_dir.name) in live:
                    continue
                try:
                    shutil.rmtree(run_dir)
                    removed += 1
                except OSError:
                    logger.warning("failed to remove orphan run workspace: %s", run_dir, exc_info=True)
    return removed


async def cleanup_orphan_run_homes(db: AsyncSession) -> int:
    root = runtime_dir() / "homes" / "_scoped"
    if not root.is_dir():
        return 0
    live_run_ids = set((await db.execute(select(Run.id))).scalars().all())
    removed = 0
    for scope_dir in root.iterdir():
        if not scope_dir.is_dir() or scope_dir.is_symlink():
            continue
        marker = scope_dir / ".mira-scope"
        try:
            scope = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not scope.startswith("run:") or scope.removeprefix("run:") in live_run_ids:
            continue
        try:
            shutil.rmtree(scope_dir)
            removed += 1
        except OSError:
            logger.warning("failed to remove orphan run HOME: %s", scope_dir, exc_info=True)
    return removed


async def cleanup_orphan_run_wiki_snapshots(db: AsyncSession) -> int:
    root = runtime_dir() / "wiki-snapshots" / "runs"
    if not root.is_dir():
        return 0
    live_run_ids = set((await db.execute(select(Run.id))).scalars().all())
    removed = 0
    for snapshot in root.iterdir():
        if not snapshot.is_dir() or snapshot.is_symlink() or snapshot.name in live_run_ids:
            continue
        try:
            shutil.rmtree(snapshot)
            removed += 1
        except OSError:
            logger.warning("failed to remove orphan Run Wiki snapshot: %s", snapshot, exc_info=True)
    return removed
