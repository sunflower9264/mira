from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import User
from app.runtime.factory import get_runtime
from app.services import runtime_config, skills_install
from app.services.runtime_paths import run_workspace
from sqlalchemy import select


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user")
    parser.add_argument("--username")
    parser.add_argument("--app", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--session-id")
    args = parser.parse_args()

    async with SessionLocal() as db:
        user_id = args.user
        if args.username:
            user = (await db.execute(select(User).where(User.username == args.username))).scalar_one_or_none()
            if not user:
                raise SystemExit(f"User not found: {args.username}")
            user_id = user.id
        if not user_id:
            raise SystemExit("Pass --user <user_id> or --username <username>")
        await runtime_config.write_configs(db)
        await skills_install.sync_user_skills(db)

    async def on_chunk(chunk):
        print(chunk.model_dump(mode="json"))

    result = await get_runtime(user_id).execute(
        prompt=args.prompt,
        session_id=args.session_id,
        allowed_tools=None,
        model=None,
        reasoning_effort="low",
        cwd=run_workspace(user_id, args.app, f"_smoke_{args.node}"),
        on_chunk=on_chunk,
        cancel_event=asyncio.Event(),
    )
    print(result.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
