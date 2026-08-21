from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import App, AppVersion, Run, RunEvent, Step, StepLog
from app.services.runtime_paths import run_workspace_path, runtime_dir
from app.services.uploads import delete_upload
from app.utils import loads

CONFIRMATION = "DELETE WORKFLOW RUNS"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Workflow run history before migration 0019.")
    parser.add_argument("--apply", action="store_true", help="perform the deletion; default is dry-run")
    args = parser.parse_args()

    async with SessionLocal() as db:
        runs = list(
            (
                await db.execute(
                    select(Run.id, Run.owner_id, Run.app_id, Run.inputs_json)
                )
            ).mappings().all()
        )
        steps = list(
            (
                await db.execute(
                    select(Step.id, Step.run_id, Step.input_json)
                )
            ).mappings().all()
        )
        step_ids = [step["id"] for step in steps]
        event_count = int(await db.scalar(select(func.count()).select_from(RunEvent)) or 0)
        log_count = (
            int(
                await db.scalar(
                    select(func.count()).select_from(StepLog).where(StepLog.step_id.in_(step_ids))
                )
                or 0
            )
            if step_ids
            else 0
        )
        protected_uploads = await _protected_upload_ids(db)
        run_uploads = _run_upload_ids(runs, steps) - protected_uploads
        workspaces = [
            run_workspace_path(run["owner_id"], run["app_id"], run["id"])
            for run in runs
        ]
        scoped_homes = _scoped_home_paths(workspaces)

        print("Workflow run reset plan:")
        print(f"  runs: {len(runs)}")
        print(f"  steps: {len(steps)}")
        print(f"  step_logs: {log_count}")
        print(f"  run_events_present: {event_count}")
        print(f"  run_uploads: {len(run_uploads)}")
        print(f"  run_workspaces: {len(workspaces)}")
        print(f"  scoped_homes: {len(scoped_homes)}")
        if not args.apply:
            print("dry-run only; pass --apply to execute")
            return
        confirmation = input(f"Type '{CONFIRMATION}' to confirm: ")
        if confirmation != CONFIRMATION:
            print("aborted")
            return

        if step_ids:
            await db.execute(delete(StepLog).where(StepLog.step_id.in_(step_ids)))
        await db.execute(delete(RunEvent))
        await db.execute(delete(Step))
        await db.execute(delete(Run))
        await db.commit()

    for workspace in workspaces:
        shutil.rmtree(workspace, ignore_errors=True)
    for home in scoped_homes:
        shutil.rmtree(home, ignore_errors=True)
    for owner_id, upload_id in run_uploads:
        delete_upload(owner_id, upload_id)
    print("Workflow run reset complete")


async def _protected_upload_ids(db) -> set[tuple[str, str]]:
    protected: set[tuple[str, str]] = set()
    apps = list(
        (
            await db.execute(
                select(App.id, App.owner_id, App.graph_json, App.cover)
            )
        ).mappings().all()
    )
    owners_by_app = {app["id"]: app["owner_id"] for app in apps}
    for app in apps:
        protected.update(
            (app["owner_id"], upload_id)
            for upload_id in _upload_ids(loads(app["graph_json"], {}))
        )
        if isinstance(app["cover"], str) and app["cover"].startswith("upl_"):
            protected.add((app["owner_id"], app["cover"]))
    versions = list(
        (
            await db.execute(
                select(AppVersion.app_id, AppVersion.graph_json)
            )
        ).mappings().all()
    )
    for version in versions:
        owner_id = owners_by_app.get(version["app_id"])
        if owner_id:
            protected.update(
                (owner_id, upload_id)
                for upload_id in _upload_ids(loads(version["graph_json"], {}))
            )
    return protected


def _run_upload_ids(
    runs: Sequence[Mapping[str, Any]],
    steps: Sequence[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    owner_by_run = {run["id"]: run["owner_id"] for run in runs}
    found = {
        (run["owner_id"], upload_id)
        for run in runs
        for upload_id in _upload_ids(loads(run["inputs_json"], {}))
    }
    for step in steps:
        owner_id = owner_by_run.get(step["run_id"])
        if owner_id:
            found.update(
                (owner_id, upload_id)
                for upload_id in _upload_ids(loads(step["input_json"], {}))
            )
    return found


def _upload_ids(value: Any) -> set[str]:
    if isinstance(value, list):
        return {upload_id for item in value for upload_id in _upload_ids(item)}
    if not isinstance(value, dict):
        return set()
    found: set[str] = set()
    upload_id = value.get("id")
    if isinstance(upload_id, str) and upload_id.startswith("upl_"):
        found.add(upload_id)
    for nested in value.values():
        found.update(_upload_ids(nested))
    return found


def _scoped_home_paths(workspaces: list[Path]) -> set[Path]:
    scoped_root = runtime_dir() / "homes" / "_scoped"
    paths: set[Path] = set()
    for workspace in workspaces:
        candidates = [workspace]
        nodes = workspace / "nodes"
        if nodes.is_dir():
            candidates.extend(path for path in nodes.glob("*/attempt-*/work") if path.is_dir())
        for candidate in candidates:
            digest = hashlib.sha256(str(candidate.resolve()).encode("utf-8")).hexdigest()[:20]
            paths.add(scoped_root / digest)
    return paths


if __name__ == "__main__":
    asyncio.run(main())
