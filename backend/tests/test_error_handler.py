from app.main import app
from fastapi.testclient import TestClient


@app.get("/__test__/boom")
async def boom():
    raise RuntimeError("boom")


def test_unhandled_exception_returns_generic_detail():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test__/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "服务器内部错误"
    assert isinstance(body["request_id"], str)
