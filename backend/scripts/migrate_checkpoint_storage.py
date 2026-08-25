from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import Run, RunWorkspaceCheckpoint
from app.services.runtime_paths import compact_run_scoped_home, run_workspace_path
from app.services.workspace_tree import WorkspaceTree, scan_tree, tree_hash


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy directory checkpoints to CAS v2.")
    parser.add_argument("--run-id", action="append", help="run id to migrate; may be repeated")
    parser.add_argument("--all", action="store_true", help="migrate every terminal run")
    parser.add_argument("--apply", action="store_true", help="apply; default is dry-run")
    parser.add_argument("--drop-legacy", action="store_true", help="delete verified legacy-tree backups")
    args = parser.parse_args()
    if not args.all and not args.run_id:
        parser.error("provide --run-id or --all")

    async with SessionLocal() as db:
        query = select(Run).where(Run.status.in_({"success", "failed", "cancelled"}))
        if not args.all:
            query = query.where(Run.id.in_(set(args.run_id or [])))
        runs = (await db.execute(query.order_by(Run.id))).scalars().all()
        if args.run_id and len(runs) != len(set(args.run_id)):
            found = {run.id for run in runs}
            missing = sorted(set(args.run_id) - found)
            raise SystemExit(f"terminal run not found: {missing}")
        migrated = 0
        dropped = 0
        for run in runs:
            checkpoints = (
                await db.execute(
                    select(RunWorkspaceCheckpoint)
                    .where(RunWorkspaceCheckpoint.run_id == run.id)
                    .order_by(RunWorkspaceCheckpoint.created_at, RunWorkspaceCheckpoint.id)
                )
            ).scalars().all()
            tree = WorkspaceTree(
                run_workspace_path(run.owner_id, run.app_id, run.id),
                initialize=args.apply,
            )
            print(f"run {run.id}: checkpoints={len(checkpoints)}")
            for checkpoint in checkpoints:
                snapshot = tree.checkpoint_snapshot(checkpoint.id)
                actual = tree_hash(snapshot)
                if actual != checkpoint.tree_hash:
                    raise SystemExit(f"hash mismatch: {checkpoint.id}")
                files = scan_tree(snapshot)
                logical = sum(entry.size for entry in files.values() if entry.kind == "file")
                kind = "cas_v2" if (snapshot / "manifest.json").is_file() else "tree_v1"
                print(f"  {checkpoint.id}: {kind}, files={len(files)}, logical_bytes={logical}")
                if not args.apply:
                    continue
                if args.drop_legacy:
                    dropped += int(
                        tree.drop_legacy_checkpoint(checkpoint.id, expected_hash=checkpoint.tree_hash)
                    )
                else:
                    migrated += int(
                        tree.migrate_legacy_checkpoint(
                            checkpoint.id,
                            expected_hash=checkpoint.tree_hash,
                            keep_legacy=True,
                        )
                    )
            if args.apply and not args.drop_legacy:
                compact_run_scoped_home(run.id)
        print(
            f"done: runs={len(runs)}, migrated={migrated}, dropped_legacy={dropped}, "
            f"mode={'apply' if args.apply else 'dry-run'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
