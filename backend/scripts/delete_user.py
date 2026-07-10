from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import App, AppVersion, Run, SettingsRow, Skill, Step, StepLog, User
from app.services.runtime_paths import data_dir, runtime_dir


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == args.username))).scalar_one_or_none()
        if not user:
            print("user not found")
            return
        paths = [
            data_dir() / user.id,
            runtime_dir() / "homes" / user.id,
            runtime_dir() / "workspaces" / user.id,
        ]
        app_ids = (await db.execute(select(App.id).where(App.owner_id == user.id))).scalars().all()
        run_ids = (await db.execute(select(Run.id).where(Run.owner_id == user.id))).scalars().all()
        step_ids = (await db.execute(select(Step.id).where(Step.run_id.in_(run_ids)))).scalars().all() if run_ids else []
        skill_ids = (await db.execute(select(Skill.id).where(Skill.owner_id == user.id))).scalars().all()
        settings_count = 1 if await db.get(SettingsRow, user.id) else 0
        version_count = (
            len((await db.execute(select(AppVersion.id).where(AppVersion.app_id.in_(app_ids)))).scalars().all())
            if app_ids
            else 0
        )
        log_count = (
            len((await db.execute(select(StepLog.id).where(StepLog.step_id.in_(step_ids)))).scalars().all())
            if step_ids
            else 0
        )
        print("Will delete:")
        for path in paths:
            print(f"  {path}")
        print("DB rows:")
        print(f"  users: 1")
        print(f"  apps: {len(app_ids)}")
        print(f"  app_versions: {version_count}")
        print(f"  runs: {len(run_ids)}")
        print(f"  steps: {len(step_ids)}")
        print(f"  step_logs: {log_count}")
        print(f"  settings: {settings_count}")
        print(f"  skills: {len(skill_ids)}")
        if args.dry_run or not args.apply:
            print("dry-run only; pass --apply to execute")
            return
        confirmation = input(f"Type username '{args.username}' to confirm deletion: ")
        if confirmation != args.username:
            print("aborted")
            return
        if step_ids:
            await db.execute(delete(StepLog).where(StepLog.step_id.in_(step_ids)))
        if run_ids:
            await db.execute(delete(Step).where(Step.run_id.in_(run_ids)))
            await db.execute(delete(Run).where(Run.id.in_(run_ids)))
        if app_ids:
            await db.execute(delete(AppVersion).where(AppVersion.app_id.in_(app_ids)))
            await db.execute(delete(App).where(App.id.in_(app_ids)))
        await db.execute(delete(SettingsRow).where(SettingsRow.owner_id == user.id))
        await db.execute(delete(Skill).where(Skill.owner_id == user.id))
        await db.delete(user)
        await db.commit()
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
