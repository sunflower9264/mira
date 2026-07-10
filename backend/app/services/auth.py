from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.utils import now_utc

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    return pwd_context.verify(plain, password_hash)


def create_access_token(sub: str) -> str:
    settings = get_settings()
    now = now_utc()
    payload: dict[str, Any] = {
        "sub": sub,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("invalid token") from exc
