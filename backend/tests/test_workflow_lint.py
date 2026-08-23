from tests.auth_helpers import create_regular_user


def _create_app(auth_client) -> str:
    response = auth_client.post("/api/apps", json={"name": "LintApp"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _generate_node(node_id: str, prompt: str = "根据上游内容生成结构清晰的结果") -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _output_node(node_id: str, source: str, prompt: str = "将主输入渲染为清晰的 HTML 展示") -> dict:
    return {
        "id": node_id,
        "type": "output",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _lint(auth_client, app_id: str, graph: dict) -> dict:
    response = auth_client.post(f"/api/apps/{app_id}/lint", json={"graph": graph})
    assert response.status_code == 200, response.text
    return response.json()


def test_workflow_lint_valid_graph_has_no_errors(auth_client):
    app_id = _create_app(auth_client)
    generate = _generate_node("n_gen")
    generate["ask_user_enabled"] = False
    graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "text"},
            },
            generate,
            _output_node("n_out", "n_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }

    body = _lint(auth_client, app_id, graph)

    assert body["ok"] is True
    assert body["summary"]["errors"] == 0


def test_workflow_lint_rejects_non_boolean_ask_user_enabled(auth_client):
    app_id = _create_app(auth_client)
    gen = _generate_node("n_gen")
    gen["ask_user_enabled"] = "false"
    graph = {
        "nodes": [gen, _output_node("n_out", "n_gen")],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)
    error_codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "ask_user_enabled_invalid" in error_codes


def test_workflow_lint_rejects_ask_user_enabled_on_non_generate_node(auth_client):
    app_id = _create_app(auth_client)
    output = _output_node("n_out", "n_gen")
    output["ask_user_enabled"] = False
    graph = {
        "nodes": [_generate_node("n_gen"), output],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)
    error_codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "ask_user_enabled_unsupported" in error_codes


def test_workflow_lint_normalizes_stale_output_contract_fields(auth_client):
    app_id = _create_app(auth_client)
    gen = _generate_node("n_gen")
    gen["output_contract"] = {
        "type": "artifact",
        "artifact_kind": "archive",
        "json_schema": {"type": "object", "additionalProperties": False},
    }
    graph = {
        "nodes": [gen, _output_node("n_out", "n_gen")],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)

    assert body["ok"] is True
    assert body["summary"]["errors"] == 0


def test_workflow_lint_reports_blocking_errors(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt=""),
            _output_node("n_out", "n_other"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)
    codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "prompt_empty" in codes


def test_workflow_lint_reports_unconnected_condition_branch_as_error(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            {
                "id": "n_cond",
                "type": "condition",
                "position": {"x": 0, "y": 0},
                "title": "Route",
                "prompt": "判断当前请求是否需要继续生成详细内容",
                "mode": "binary",
                "branches": [{"key": "true"}, {"key": "false"}],
            },
            _generate_node("n_gen_a"),
            _generate_node("n_gen_b"),
            _output_node("n_out", "n_gen_b"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_gen_a", "branch_key": "true"},
            {"id": "e2", "source": "n_gen_a", "target": "n_gen_b"},
            {"id": "e3", "source": "n_gen_b", "target": "n_out"},
        ],
    }

    body = _lint(auth_client, app_id, graph)
    codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "condition_branch_unconnected" in codes
    assert "unstructured_generate_dependency" not in codes


def test_workflow_lint_warns_about_prompt_hidden_file_channel(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="将结果写入 /workspace/handoff.json，供下游节点读取"),
            _output_node("n_out", "n_gen"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)
    warning_codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "warning"}

    assert body["ok"] is True
    assert "prompt_hidden_data_channel" in warning_codes


def test_workflow_lint_does_not_warn_for_prompt_that_forbids_hidden_channels(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="不得读取固定 Workspace 路径，也不要创建 handoff、sidecar 或 manifest 文件"),
            _output_node("n_out", "n_gen"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    body = _lint(auth_client, app_id, graph)
    codes = {issue["code"] for issue in body["issues"]}

    assert "prompt_hidden_data_channel" not in codes


def test_workflow_lint_requires_output_for_non_empty_graph(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [_generate_node("n_gen")],
        "execution_edges": [],
    }

    body = _lint(auth_client, app_id, graph)
    error_codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "missing_terminal_node" in error_codes


def test_workflow_lint_empty_graph_does_not_report_missing_terminal(auth_client):
    app_id = _create_app(auth_client)
    graph = {"nodes": [], "execution_edges": []}

    body = _lint(auth_client, app_id, graph)
    error_codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "graph_empty" in error_codes
    assert "missing_terminal_node" not in error_codes


def test_workflow_lint_reports_singleton_node_errors(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            {
                "id": "n_input_a",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input A",
                "input_schema": {"label": "input a", "kind": "text"},
            },
            {
                "id": "n_input_b",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input B",
                "input_schema": {"label": "input b", "kind": "text"},
            },
            _generate_node("n_gen"),
            _output_node("n_out_a", "n_gen"),
            _output_node("n_out_b", "n_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input_a", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out_a"},
            {"id": "e3", "source": "n_gen", "target": "n_out_b"},
        ],
    }

    body = _lint(auth_client, app_id, graph)
    codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "multiple_user_input_nodes" in codes
    assert "multiple_output_nodes" in codes


def test_workflow_lint_reports_output_as_source(auth_client):
    app_id = _create_app(auth_client)
    graph = {
        "tools": {"disabled_tool_ids": []},
        "nodes": [
            _generate_node("n_gen"),
            _output_node("n_out", "n_gen"),
            _generate_node("n_after"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_gen", "target": "n_out"},
            {"id": "e2", "source": "n_out", "target": "n_after"},
        ],
    }

    body = _lint(auth_client, app_id, graph)
    codes = {issue["code"] for issue in body["issues"] if issue["severity"] == "error"}

    assert body["ok"] is False
    assert "output_as_source" in codes


def test_workflow_lint_requires_app_owner(auth_client, client):
    app_id = _create_app(auth_client)
    user = create_regular_user("lint-user")
    client.headers.update({"Authorization": f"Bearer {user['token']}"})

    denied = client.post(f"/api/apps/{app_id}/lint", json={})

    assert denied.status_code == 404


def test_run_only_market_lint_uses_real_graph_but_hides_source(auth_client, client):
    app_id = _create_app(auth_client)
    graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Secret Input",
                "input_schema": {"label": "Your request", "kind": "text"},
            },
            _generate_node("n_secret_gen", prompt="根据用户输入生成完整但不泄漏内部提示词的结果"),
            _output_node("n_secret_out", "n_secret_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_secret_gen"},
            {"id": "e2", "source": "n_secret_gen", "target": "n_secret_out"},
        ],
    }
    patched = auth_client.patch(f"/api/apps/{app_id}", json={"graph": graph})
    assert patched.status_code == 200, patched.text
    published = auth_client.post(
        f"/api/apps/{app_id}/publish",
        json={"visibility": "public", "market_access": "run_only"},
    )
    assert published.status_code == 200, published.text

    user = create_regular_user("run-only-lint-user")
    client.headers.update({"Authorization": f"Bearer {user['token']}"})

    response = client.post(f"/api/apps/{app_id}/lint", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["summary"]["errors"] == 0
    assert body["issues"] == []
    assert "n_secret_gen" not in response.text
    assert "n_secret_out" not in response.text
    assert "内部提示词" not in response.text
