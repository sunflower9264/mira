from __future__ import annotations

import uuid

from app.config import get_settings
from app.services.artifacts import signed_upload_download_url
from app.services.uploads import resolve_upload
from tests.auth_helpers import create_regular_user


def _create_user() -> dict[str, str]:
    return create_regular_user(f"upl_{uuid.uuid4().hex[:10]}")


def test_upload_success_writes_blob_and_meta(client):
    user = _create_user()
    token = user["token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/uploads",
        headers=headers,
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("upl_")
    assert body["name"] == "hello.txt"
    assert body["mime"] == "text/plain"
    assert body["size"] == len(b"hello world")
    assert body["created_at"]

    ref = resolve_upload(user["id"], body["id"])
    assert ref is not None
    assert ref.path.read_bytes() == b"hello world"
    assert ref.name == "hello.txt"
    assert ref.mime == "text/plain"
    assert ref.size == len(b"hello world")


def test_upload_rejects_empty_body(client):
    token = _create_user()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/uploads",
        headers=headers,
        files={"file": ("empty.bin", b"", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "未提供文件"


def test_upload_rejects_oversize(client, monkeypatch):
    token = _create_user()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 8)

    response = client.post(
        "/api/uploads",
        headers=headers,
        files={"file": ("big.bin", b"0123456789", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "文件超出大小限制"


def test_upload_missing_file_field_returns_422(client):
    token = _create_user()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/api/uploads", headers=headers)
    assert response.status_code == 422


def test_upload_requires_auth(client):
    response = client.post(
        "/api/uploads",
        files={"file": ("hello.txt", b"x", "text/plain")},
    )
    assert response.status_code == 401


def test_upload_isolated_across_users(client):
    user_a = _create_user()
    user_b = _create_user()
    token_a = user_a["token"]
    token_b = user_b["token"]

    response = client.post(
        "/api/uploads",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("secret.txt", b"only-for-a", "text/plain")},
    )
    assert response.status_code == 200
    upload_id = response.json()["id"]

    # 当前用户能解析到自己的 upload；其他用户拿不到。
    assert resolve_upload(user_a["id"], upload_id) is not None
    assert resolve_upload(user_b["id"], upload_id) is None

    allowed = client.get(f"/api/uploads/{upload_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert allowed.status_code == 200
    assert allowed.content == b"only-for-a"
    assert allowed.headers["content-type"].startswith("text/plain")

    denied = client.get(f"/api/uploads/{upload_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert denied.status_code == 404
    assert denied.json()["detail"] == "附件不存在"


def test_upload_signed_download_url_works_without_auth_header(client):
    user = _create_user()
    token = user["token"]
    response = client.post(
        "/api/uploads",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("signed.txt", b"signed-download", "text/plain")},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["id"]
    url = signed_upload_download_url(user["id"], upload_id)

    allowed = client.get(url)
    assert allowed.status_code == 200
    assert allowed.content == b"signed-download"

    denied = client.get(f"/api/uploads/{upload_id}?download_token=bad")
    assert denied.status_code == 401


def test_resolve_upload_rejects_path_traversal(client):
    user_id = _create_user()["id"]

    assert resolve_upload(user_id, "../escape") is None
    assert resolve_upload(user_id, "..\\escape") is None
    assert resolve_upload(user_id, "") is None
    assert resolve_upload(user_id, "upl_does_not_exist") is None
