from datetime import datetime


def _user_input_graph() -> dict:
    return {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "textarea"},
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
                "source_node_id": "n_input",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_input", "target": "n_out"}],
    }


def _no_user_input_graph() -> dict:
    return {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "asset_kind": "text",
                "content": "input",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
                "source_node_id": "n_asset",
            }
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }


def test_run_name_defaults_to_user_input_value(auth_client, enable_claude_agent):
    enable_claude_agent()
    app = auth_client.post("/api/apps", json={"name": "History Names"}).json()
    patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": _user_input_graph()})
    assert patched.status_code == 200, patched.text

    created = auth_client.post(
        "/api/runs",
        json={"app_id": app["id"], "inputs": {"n_input": {"value": "  很长的用户输入   会被折叠空白  ", "attachments": []}}},
    )
    assert created.status_code == 200, created.text

    run = auth_client.get(f"/api/runs/{created.json()['run_id']}").json()
    assert run["name"] == "很长的用户输入 会被折叠空白"


def test_run_name_falls_back_to_app_name_and_can_be_renamed(auth_client, enable_claude_agent, monkeypatch):
    enable_claude_agent()
    monkeypatch.setattr("app.services.runs.display_now", lambda: datetime.fromisoformat("2026-07-04T09:30:00+08:00"))
    app = auth_client.post("/api/apps", json={"name": "No Input App"}).json()
    patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": _no_user_input_graph()})
    assert patched.status_code == 200, patched.text

    created = auth_client.post("/api/runs", json={"app_id": app["id"], "inputs": {}})
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    run = auth_client.get(f"/api/runs/{run_id}").json()
    assert run["name"] == "No Input App · 07-04 09:30"

    renamed = auth_client.patch(f"/api/runs/{run_id}", json={"name": "  手动命名  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "手动命名"

    listed = auth_client.get(f"/api/apps/{app['id']}/runs").json()
    assert listed[0]["name"] == "手动命名"


def test_run_rename_rejects_empty_name(auth_client, enable_claude_agent):
    enable_claude_agent()
    app = auth_client.post("/api/apps", json={"name": "Reject Empty"}).json()
    patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": _no_user_input_graph()})
    assert patched.status_code == 200, patched.text
    created = auth_client.post("/api/runs", json={"app_id": app["id"], "inputs": {}})
    assert created.status_code == 200, created.text

    response = auth_client.patch(f"/api/runs/{created.json()['run_id']}", json={"name": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "运行记录名称不能为空"
