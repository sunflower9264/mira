"""管理员账号管理。

约束：
- 全局只有一个管理员，固定 id 为 ``ADMIN_USER_ID``。
- 管理员的来源是 ``backend/.env`` 的 ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``，由
  ``scripts/init_admin.py`` 在项目初始化阶段读取并 upsert 到数据库；FastAPI
  lifespan 不再做 admin upsert。
- ``SettingsRow`` / ``Skill`` 等全局表都挂在 ``ADMIN_USER_ID`` 这一个 owner_id 下，
  普通用户的 ``GET /api/settings`` 也读这一行，写操作仅 admin 可调。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SettingsRow, User
from app.services.auth import hash_password
from app.services.prompts import seed_prompt_templates
from app.services.settings import default_agents
from app.utils import dumps, now_utc

ADMIN_USER_ID = "user_admin"

ADMIN_PASSWORD_PLACEHOLDER = "change-me"
ADMIN_PASSWORD_MIN_LENGTH = 6


class AdminCredentialsError(ValueError):
    """``.env`` 中的 admin 凭据无效（缺失、过短或还是占位符）。"""


def validate_admin_credentials(username: str, password: str) -> None:
    cleaned_username = (username or "").strip()
    if not cleaned_username:
        raise AdminCredentialsError("ADMIN_USERNAME 不能为空")
    if len(cleaned_username) > 32:
        raise AdminCredentialsError("ADMIN_USERNAME 长度需为 1-32")
    if not password:
        raise AdminCredentialsError("ADMIN_PASSWORD 不能为空")
    if password == ADMIN_PASSWORD_PLACEHOLDER:
        raise AdminCredentialsError(
            "ADMIN_PASSWORD 仍为占位符 'change-me'，请编辑 backend/.env 设置真实密码"
        )
    if len(password) < ADMIN_PASSWORD_MIN_LENGTH:
        raise AdminCredentialsError(
            f"ADMIN_PASSWORD 至少 {ADMIN_PASSWORD_MIN_LENGTH} 位"
        )


async def upsert_admin(db: AsyncSession, username: str, password: str) -> User:
    """按固定 id upsert 管理员，并兜底创建全局 ``SettingsRow``。"""
    validate_admin_credentials(username, password)
    cleaned_username = username.strip()
    password_hash = hash_password(password)
    # 兜底：username 被另一个 id 占用时把那个用户改名让出 username。
    # 不删除冲突用户，避免破坏 apps/runs 等未级联的外键关系（开发期可以接受残留）。
    conflict = (
        await db.execute(
            select(User).where(User.username == cleaned_username, User.id != ADMIN_USER_ID)
        )
    ).scalar_one_or_none()
    if conflict is not None:
        conflict.username = f"{conflict.id}_renamed"
        await db.flush()
    user = await db.get(User, ADMIN_USER_ID)
    if user is None:
        user = User(
            id=ADMIN_USER_ID,
            username=cleaned_username,
            password_hash=password_hash,
            is_admin=True,
        )
        db.add(user)
    else:
        user.username = cleaned_username
        user.password_hash = password_hash
        user.is_admin = True
    settings_row = await db.get(SettingsRow, ADMIN_USER_ID)
    if settings_row is None:
        settings_row = SettingsRow(
            owner_id=ADMIN_USER_ID,
            agents_json=dumps(default_agents()),
            skills_json=dumps([]),
            mcp_servers_json=dumps([]),
            updated_at=now_utc(),
        )
        db.add(settings_row)
    await seed_prompt_templates(db, commit=False)
    await db.commit()
    await db.refresh(user)
    return user
