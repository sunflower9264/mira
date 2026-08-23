from __future__ import annotations

from copy import deepcopy
import time


def _generate_node(node_id: str, *, prompt: str) -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _condition_node(*, mode: str, branches: list[dict], prompt: str) -> dict:
    return {
        "id": "n_cond",
        "type": "condition",
        "position": {"x": 0, "y": 0},
        "title": "cond",
        "prompt": prompt,
        "mode": mode,
        "branches": branches,
    }


def _ensure_output(graph: dict) -> dict:
    if any(node.get("type") == "output" for node in graph.get("nodes", []) if isinstance(node, dict)):
        return graph
    next_graph = deepcopy(graph)
    nodes = next_graph.setdefault("nodes", [])
    outgoing = {
        edge.get("source")
        for edge in next_graph.get("execution_edges", [])
        if isinstance(edge, dict) and isinstance(edge.get("source"), str)
    }
    sources = [
        node["id"]
        for node in nodes
        if isinstance(node, dict)
        and isinstance(node.get("id"), str)
        and node["id"] not in outgoing
    ]
    source = sources[0] if sources else ""
    nodes.append(
        {
            "id": "n_auto_out",
            "type": "output",
            "position": {"x": 400, "y": 0},
            "title": "Output",
            "prompt": "render [[respond:<section>ok</section>]]",
        }
    )
    for index, source_id in enumerate(sources, start=1):
        next_graph.setdefault("execution_edges", []).append(
            {"id": f"e_auto_out_{index}", "source": source_id, "target": "n_auto_out"}
        )
    return next_graph


def _build_app(auth_client, *, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "CondApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _ensure_output(graph)})
    assert response.status_code == 200, response.text
    return created["id"]


def _wait_for_terminal(auth_client, run_id: str, *, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"success", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_condition_binary_selects_true_branch(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_cond"]["output"] == "true"
    assert by_id["n_cond"]["input"]["condition_result"]["chosen_branch"] == "true"
    assert by_id["n_cond"]["input"]["condition_result"]["unchosen_branches"] == ["false"]
    assert by_id["n_cond"]["input"]["condition_result"]["forced"] is False
    assert by_id["n_yes"]["status"] == "success"
    assert by_id["n_yes"]["output"] == "YES"
    assert by_id["n_no"]["status"] == "skipped"


def test_condition_rejects_non_exact_branch_key(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true。]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
            _generate_node("n_merge", prompt="汇总 [[respond:MERGED]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e3", "source": "n_yes", "target": "n_merge"},
            {"id": "e4", "source": "n_no", "target": "n_merge"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "failed"
    assert final["failure_kind"] == "routing"
    assert "branch key" in final["error"]


def test_run_rejects_condition_branch_that_cannot_reach_output(auth_client, configure_codex):
    configure_codex()
    created = auth_client.post("/api/apps", json={"name": "Invalid condition route"}).json()
    graph = {
        "nodes": [
            _condition_node(
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 400, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
            },
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e3", "source": "n_yes", "target": "n_out"},
        ],
    }
    saved = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert saved.status_code == 200, saved.text

    started = auth_client.post("/api/runs", json={"app_id": created["id"], "inputs": {}})

    assert started.status_code == 400
    assert "false" in started.json()["detail"]
    assert "output" in started.json()["detail"]


def test_condition_cases_unmatched_falls_back_to_default(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="cases",
                branches=[{"key": "apple", "label": "苹果"}, {"key": "banana", "label": "香蕉"}],
                prompt="选水果 [[respond:__default__]]",
            ),
            _generate_node("n_apple", prompt="苹果 [[respond:APPLE]]"),
            _generate_node("n_banana", prompt="香蕉 [[respond:BANANA]]"),
            _generate_node("n_default", prompt="默认 [[respond:DEFAULT]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_apple", "branch_key": "apple"},
            {"id": "e2", "source": "n_cond", "target": "n_banana", "branch_key": "banana"},
            {
                "id": "e3",
                "source": "n_cond",
                "target": "n_default",
                "branch_key": "__default__",
            },
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_cond"]["output"] == "__default__"
    assert by_id["n_apple"]["status"] == "skipped"
    assert by_id["n_banana"]["status"] == "skipped"
    assert by_id["n_default"]["status"] == "success"
    assert by_id["n_default"]["output"] == "DEFAULT"
    condition_prompt = by_id["n_cond"]["input"]["prompt"]
    assert '"key": "__default__"' in condition_prompt
    assert '"label": "其它：以上分支均不匹配"' in condition_prompt


def test_condition_cases_exact_match_runs_only_selected(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="cases",
                branches=[
                    {"key": "route_a", "label": "送入苹果流程"},
                    {"key": "route_b", "label": "送入香蕉流程"},
                ],
                prompt="选水果 [[respond:route_b]]",
            ),
            _generate_node("n_apple", prompt="苹果 [[respond:APPLE]]"),
            _generate_node("n_banana", prompt="香蕉 [[respond:BANANA]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_apple", "branch_key": "route_a"},
            {"id": "e2", "source": "n_cond", "target": "n_banana", "branch_key": "route_b"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_cond"]["output"] == "route_b"
    assert by_id["n_apple"]["status"] == "skipped"
    assert by_id["n_banana"]["status"] == "success"
    condition_prompt = by_id["n_cond"]["input"]["prompt"]
    assert '"key": "route_a"' in condition_prompt
    assert '"label": "送入苹果流程"' in condition_prompt
    assert '"key": "route_b"' in condition_prompt
    assert '"label": "送入香蕉流程"' in condition_prompt
    assert "根据每项 `label` 的业务含义判断" in condition_prompt


def test_condition_downstream_chain_is_fully_skipped(auth_client, configure_codex):
    """未选分支的整条下游（含传递闭包）都应该被 skip。"""

    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="二选一 [[respond:true]]",
            ),
            _generate_node("n_true_a", prompt="A [[respond:A]]"),
            _generate_node("n_false_a", prompt="FA [[respond:FA]]"),
            _generate_node("n_false_b", prompt="FB [[respond:FB]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_true_a", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_false_a", "branch_key": "false"},
            {"id": "e3", "source": "n_false_a", "target": "n_false_b"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_cond"]["output"] == "true"
    assert by_id["n_true_a"]["status"] == "success"
    assert by_id["n_false_a"]["status"] == "skipped"
    assert by_id["n_false_b"]["status"] == "skipped"


def test_condition_merge_node_runs_when_reachable_from_selected_branch(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="二选一 [[respond:true]]",
            ),
            _generate_node("n_true_a", prompt="A [[respond:A]]"),
            _generate_node("n_false_a", prompt="FA [[respond:FA]]"),
            _generate_node("n_merge", prompt="汇总 [[respond:MERGE]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_true_a", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_false_a", "branch_key": "false"},
            {"id": "e3", "source": "n_true_a", "target": "n_merge"},
            {"id": "e4", "source": "n_false_a", "target": "n_merge"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_true_a"]["status"] == "success"
    assert by_id["n_false_a"]["status"] == "skipped"
    assert by_id["n_merge"]["status"] == "success"
    assert by_id["n_merge"]["output"] == "MERGE"
