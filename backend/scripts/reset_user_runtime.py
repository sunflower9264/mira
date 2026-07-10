from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import User
from app.services.runtime_paths import runtime_dir


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
    paths = [runtime_dir() / "homes" / user.id, runtime_dir() / "workspaces" / user.id]
    for path in paths:
        print(path)
    if args.dry_run or not args.apply:
        print("dry-run only; pass --apply to execute")
        return
    confirmation = input(f"Type username '{args.username}' to confirm runtime reset: ")
    if confirmation != args.username:
        print("aborted")
        return
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
