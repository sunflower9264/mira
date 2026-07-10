"""Create a regular Mira user.

This script intentionally creates only non-admin users. The admin account is
still managed by ``scripts/init_admin.py`` from ``backend/.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import select

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app.models import User  # noqa: E402
from app.services.auth import hash_password  # noqa: E402
from app.utils import new_id  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a regular Mira user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", help="Password for non-interactive use")
    return parser.parse_args()


def _read_password(args: argparse.Namespace) -> str:
    if args.password is not None:
        return args.password
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise ValueError("passwords do not match")
    return first


async def _run() -> int:
    args = _parse_args()
    username = args.username.strip()
    if not username or len(username) > 32:
        print("[create_user] username length must be 1-32", file=sys.stderr)
        return 2
    settings = get_settings()
    if username == settings.admin_username.strip():
        print("[create_user] username is reserved for admin", file=sys.stderr)
        return 2

    password = _read_password(args)
    if len(password) < 6:
        print("[create_user] password must be at least 6 characters", file=sys.stderr)
        return 2

    await create_all()
    async with SessionLocal() as db:
        exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if exists:
            print("[create_user] username already exists", file=sys.stderr)
            return 2
        user = User(
            id=new_id("user"),
            username=username,
            password_hash=hash_password(password),
            is_admin=False,
        )
        db.add(user)
        await db.commit()
    print(f"[create_user] user created: id={user.id} username={user.username}")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except ValueError as exc:
        print(f"[create_user] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[create_user] unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
