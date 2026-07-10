from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import User
from app.schemas.requests import AuthIn
from app.services.auth import create_access_token, verify_password

from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(payload: AuthIn, db: AsyncSession = Depends(get_db)):
    username = payload.username.strip()
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": create_access_token(user.id),
        "user": {"username": user.username, "is_admin": user.is_admin},
    }


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "is_admin": user.is_admin}
