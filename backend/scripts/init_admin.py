"""初始化管理员账号。

读取 ``backend/.env`` 中的 ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``，校验合法后
upsert 到 ``users`` 表（id 固定 ``user_admin``，``is_admin=True``），并兜底创建
全局 ``settings`` 行。

幂等：管理员已存在则同步 username / password_hash / is_admin。校验失败时返回
非 0 退出码，让 ``scripts/dev.py`` 中断启动并提示运维填写 ``.env``。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
# 子进程默认 cwd 取决于调用方；强制切到 backend 根目录，确保 .env / SQLite 相对路径解析一致。
os.chdir(BACKEND_ROOT)

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, create_all  # noqa: E402
from app.services import runtime_config  # noqa: E402
from app.services.admin import AdminCredentialsError, upsert_admin  # noqa: E402


async def _run() -> int:
    settings = get_settings()
    try:
        await create_all()
    except Exception as exc:  # noqa: BLE001
        print(f"[init_admin] failed to ensure database schema: {exc}", file=sys.stderr)
        return 1
    async with SessionLocal() as db:
        try:
            user = await upsert_admin(db, settings.admin_username, settings.admin_password)
            await runtime_config.write_configs(db)
        except AdminCredentialsError as exc:
            print(f"[init_admin] {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001
            print(f"[init_admin] unexpected error: {exc}", file=sys.stderr)
            return 1
    print(f"[init_admin] admin ready: id={user.id} username={user.username}")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
