from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.auth import create_access_token, hash_password
from app.utils import new_id


def create_regular_user(username: str | None = None, password: str = "secret123") -> dict[str, str]:
    username = username or f"user_{uuid.uuid4().hex[:10]}"

    async def _create() -> dict[str, str]:
        async with SessionLocal() as db:
            exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            assert exists is None
            user = User(
                id=new_id("user"),
                username=username,
                password_hash=hash_password(password),
                is_admin=False,
            )
            db.add(user)
            await db.commit()
            return {
                "id": user.id,
                "username": user.username,
                "token": create_access_token(user.id),
            }

    return asyncio.run(_create())
