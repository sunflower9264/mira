def test_health_and_docs_are_public(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    docs = client.get("/api/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
