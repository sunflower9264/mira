from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal, create_all, reconfigure_database
from app.main import app
from app.runtime.factory import set_runtime_override
from app.services import apps as apps_service
from app.services import prompts as prompts_service
from app.services.admin import upsert_admin
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime


TEST_ADMIN_USERNAME = "test-admin"
TEST_ADMIN_PASSWORD = "admin-test-secret"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "mira.sqlite"
    data_dir = tmp_path / "data"
    runtime_dir = tmp_path / "runtime"
    prompt_seed_dir = tmp_path / "seeds" / "prompts"
    shutil.copytree(prompts_service.PROMPT_SEED_DIR, prompt_seed_dir)
    monkeypatch.setattr(prompts_service, "PROMPT_SEED_DIR", prompt_seed_dir)
    monkeypatch.setattr(
        apps_service,
        "GALLERY_SEED_PATH",
        Path(__file__).parent / "fixtures" / "gallery.json",
    )
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-mira-backend-tests-32-bytes")
    monkeypatch.setenv("CODEX_CONFIG_SECRET", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    monkeypatch.setenv("ADMIN_USERNAME", TEST_ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))
    get_settings.cache_clear()
    asyncio.run(reconfigure_database(get_settings().database_url))

    async def _ensure_admin() -> None:
        await create_all()
        async with SessionLocal() as db:
            await upsert_admin(db, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)

    asyncio.run(_ensure_admin())
    set_runtime_override(MockRuntime())
    with TestClient(app) as c:
        yield c
    set_runtime_override(None)


@pytest.fixture
def auth_client(client):
    """默认登录管理员账号（绝大多数已有用例需要写权限）。"""
    response = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def configure_codex(auth_client):
    def _enable() -> None:
        response = auth_client.put(
            "/api/settings/codex/config",
            json={
                "content": "\n",
                "auth_content": "{}\n",
                "supported_models": ["test-model"],
            },
        )
        assert response.status_code == 200, response.text

    return _enable


@pytest.fixture
def user_client(client):
    """创建并登录一个普通用户，用于校验 admin-only 端点的 403。"""
    user = create_regular_user()
    client.headers.update({"Authorization": f"Bearer {user['token']}"})
    return client
