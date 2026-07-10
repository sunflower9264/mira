from datetime import UTC, datetime
import subprocess
import sys
import uuid
from pathlib import Path

from app.services.auth import create_access_token
from tests.auth_helpers import create_regular_user


def test_login_me(client):
    create_regular_user("alice_auth_case")
    login = client.post("/api/auth/login", json={"username": "alice_auth_case", "password": "secret123"})
    assert login.status_code == 200
    token = login.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice_auth_case"


def test_register_endpoint_is_not_available(client):
    response = client.post("/api/auth/register", json={"username": "dupe_auth_case", "password": "secret123"})
    assert response.status_code == 404


def test_wrong_password(client):
    create_regular_user("bob_auth_case")
    response = client.post("/api/auth/login", json={"username": "bob_auth_case", "password": "badpass"})
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_invalid_token(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert response.status_code == 401
    assert response.json()["detail"] == "登录已失效"


def test_access_tokens_are_unique_for_same_admin(monkeypatch):
    fixed_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr("app.services.auth.now_utc", lambda: fixed_now)

    first = create_access_token("user_admin")
    second = create_access_token("user_admin")

    assert first != second


def test_create_user_script_creates_login_user(client):
    username = f"script_user_{uuid.uuid4().hex[:10]}"
    backend_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/create_user.py",
            "--username",
            username,
            "--password",
            "secret123",
        ],
        cwd=backend_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    login = client.post("/api/auth/login", json={"username": username, "password": "secret123"})
    assert login.status_code == 200
    assert login.json()["user"] == {"username": username, "is_admin": False}
