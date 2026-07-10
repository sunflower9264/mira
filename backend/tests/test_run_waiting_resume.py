"""阶段 4：runtime waiting / resume 测试矩阵（spec §7）。"""

from __future__ import annotations

from copy import deepcopy
import json
import time
from typing import Any

from tests.auth_helpers import create_regular_user


def _generate_node(node_id: str, *, prompt: str) -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _ensure_output(graph: dict) -> dict:
    if any(node.get("type") == "output" for node in graph.get("nodes", []) if isinstance(node, dict)):
        return graph
    next_graph = deepcopy(graph)
    nodes = next_graph.setdefault("nodes", [])
    source = next(
        (node.get("id") for node in reversed(nodes) if isinstance(node, dict) and isinstance(node.get("id"), str)),
        "",
    )
    nodes.append(
        {
            "id": "n_auto_out",
            "type": "output",
            "position": {"x": 200, "y": 0},
            "title": "Output",
            "prompt": "render [[respond:<section>ok</section>]]",
            "source_node_id": source,
        }
    )
    if source:
        next_graph.setdefault("edges", []).append({"id": "e_auto_out", "source": source, "target": "n_auto_out"})
    return next_graph


def _build_app(auth_client, *, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "WaitingApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _ensure_output(graph)})
    assert response.status_code == 200, response.text
    return created["id"]


def _decision_group(
    group_id: str,
    *,
    single: bool,
    options: list[str],
    label: str = "请选择",
    placeholder: str | None = None,
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "id": group_id,
        "type": "single" if single else "multi",
        "label": label,
        "options": [_decision_option(option, recommended=index == 0) for index, option in enumerate(options)],
    }
    if placeholder:
        group["placeholder"] = placeholder
    return group


def _decision_option(label: str, *, recommended: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "description": f"选择 {label} 会按该方向继续。",
        "recommended": recommended,
    }


def _option_labels(group: dict[str, Any]) -> list[str]:
    return [option["label"] for option in group["options"]]


def _ask_user_prompt(
    *,
    groups: list[dict[str, Any]] | None = None,
    single: bool = True,
    options: list[str] | None = None,
    extras: str = "",
    placeholder: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "context": {"title": "确认运行选择", "summary": "继续运行前需要你选择一个处理方向。"},
        "groups": groups
        or [_decision_group("choice", single=single, options=options or ["A", "B", "C"], placeholder=placeholder)],
    }
    return f"[[ask_user:{json.dumps(payload, ensure_ascii=False)}]] [[respond:RESULT]] {extras}".strip()


def _start_run(auth_client, app_id: str, inputs: dict | None = None) -> str:
    response = auth_client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": inputs or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _wait_for_status(auth_client, run_id: str, expected: set[str], *, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        last = body
        if body["status"] in expected:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not enter {expected}: last={last}")


def _find_waiting_step(run_body: dict, node_id: str) -> dict:
    for step in run_body["steps"]:
        if step["node_id"] == node_id:
            return step
    raise AssertionError(f"step for {node_id} not found")


def _tool_use_id_for(auth_client, run_id: str, node_id: str) -> str:
    body = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
    step = _find_waiting_step(body, node_id)
    ask = step["input"]["ask_user"]
    return ask["tool_use_id"]


# --- spec §7 矩阵 ------------------------------------------------------------


def test_single_ask_user_emit_step_waiting_and_resume_success(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    body = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
    step = _find_waiting_step(body, "n_gen")
    assert step["status"] == "waiting_for_user"
    ask = step["input"]["ask_user"]
    assert ask["groups"][0]["type"] == "single"
    assert _option_labels(ask["groups"][0]) == ["A", "B", "C", "以上都不是"]
    tool_use_id = ask["tool_use_id"]

    # resume
    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["A"]}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success", final
    step = _find_waiting_step(final, "n_gen")
    assert step["status"] == "success"
    # resume payload 落到 step.input
    assert step["input"]["resume"]["answers"] == [{"group_id": "choice", "selected": ["A"]}]
    # mock 把 ask 结果拼到 LLM 输出尾巴
    assert "answers=choice=A" in step["output"]


def test_single_ask_user_can_resume_with_none_option(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    body = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
    step = _find_waiting_step(body, "n_gen")
    ask = step["input"]["ask_user"]
    assert _option_labels(ask["groups"][0]) == ["A", "B", "C", "以上都不是"]

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": ask["tool_use_id"],
            "answers": [{"group_id": "choice", "selected": ["以上都不是"]}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success", final
    step = _find_waiting_step(final, "n_gen")
    assert step["input"]["resume"]["answers"] == [{"group_id": "choice", "selected": ["以上都不是"]}]
    assert "answers=choice=以上都不是" in step["output"]


def test_multi_ask_user_with_text_and_attachments(auth_client, enable_claude_agent):
    enable_claude_agent()
    # 准备一个 upload，用于 attachments
    upload_resp = auth_client.post(
        "/api/uploads",
        files={"file": ("hint.txt", b"hello", "text/plain")},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    upload_id = upload_resp.json()["id"]

    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node(
                "n_gen",
                prompt=_ask_user_prompt(single=False, options=["X", "Y", "Z"]),
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["X", "Z"]}],
            "text": "and please tighten the tone",
            "attachments": [{"id": upload_id, "name": "hint.txt"}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success"
    step = _find_waiting_step(final, "n_gen")
    resume = step["input"]["resume"]
    assert resume["answers"] == [{"group_id": "choice", "selected": ["X", "Z"]}]
    assert resume["text"] == "and please tighten the tone"
    assert resume["attachments"][0]["id"] == upload_id
    assert resume["attachments"][0]["download_url"].startswith(f"/api/uploads/{upload_id}?download_token=")
    assert "answers=choice=X|Z" in step["output"]


def test_multi_group_ask_user_answers_all_groups(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node(
                "n_gen",
                prompt=_ask_user_prompt(
                    groups=[
                        _decision_group("intent", single=True, options=["写作", "翻译", "总结"], label="选择用途"),
                        _decision_group("tone", single=False, options=["正式", "简洁", "活泼"], label="选择语气"),
                    ],
                ),
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    body = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
    ask = _find_waiting_step(body, "n_gen")["input"]["ask_user"]
    assert [group["id"] for group in ask["groups"]] == ["intent", "tone"]

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": ask["tool_use_id"],
            "answers": [
                {"group_id": "intent", "selected": ["写作"]},
                {"group_id": "tone", "selected": ["正式", "简洁"]},
            ],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success"
    step = _find_waiting_step(final, "n_gen")
    assert "answers=intent=写作,tone=正式|简洁" in step["output"]


def test_ask_user_can_resume_with_text_instead_of_answers(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node(
                "n_gen",
                prompt=_ask_user_prompt(single=True, options=["A", "B", "C"]),
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [],
            "text": "use my own answer",
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success"
    step = _find_waiting_step(final, "n_gen")
    resume = step["input"]["resume"]
    assert resume["answers"] == []
    assert resume["text"] == "use my own answer"
    assert "text=use my own answer" in step["output"]


def test_ask_user_can_resume_with_attachment_instead_of_answers(auth_client, enable_claude_agent):
    enable_claude_agent()
    upload_resp = auth_client.post(
        "/api/uploads",
        files={"file": ("choice.txt", b"answer", "text/plain")},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    upload_id = upload_resp.json()["id"]
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node(
                "n_gen",
                prompt=_ask_user_prompt(single=True, options=["A", "B", "C"]),
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [],
            "attachments": [{"id": upload_id, "name": "choice.txt"}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "success"
    step = _find_waiting_step(final, "n_gen")
    resume = step["input"]["resume"]
    assert resume["answers"] == []
    assert resume["attachments"][0]["id"] == upload_id
    assert "attachments=choice.txt" in step["output"]


def test_multi_group_missing_answer_returns_400(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node(
                "n_gen",
                prompt=_ask_user_prompt(
                    groups=[
                        _decision_group("intent", single=True, options=["写作", "翻译", "总结"]),
                        _decision_group("tone", single=False, options=["正式", "简洁", "活泼"]),
                    ],
                ),
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "intent", "selected": ["写作"]}],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "回答不完整"
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_resume_selected_not_in_options_returns_400(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["yes", "no", "later"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["maybe"]}],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "选项不合法"
    # 仍处于 waiting，不影响后续 resume
    body = auth_client.get(f"/api/runs/{run_id}").json()
    assert body["status"] == "waiting_for_user"
    # cleanup：cancel
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_multi_ask_user_none_option_is_mutually_exclusive(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=False, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["A", "以上都不是"]}],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "选项不合法"
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_resume_wrong_node_id_returns_409(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_wrong",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["A"]}],
        },
    )
    assert response.status_code == 409
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_resume_wrong_tool_use_id_returns_409(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    _tool_use_id_for(auth_client, run_id, "n_gen")  # 确认进入 waiting

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": "toolu_wrong",
            "answers": [{"group_id": "choice", "selected": ["A"]}],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "ask_user 已失效，请重新发起运行"
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_resume_empty_payload_returns_400(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "必须至少提供一项输入"
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})


def test_cancel_during_waiting(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 204
    final = _wait_for_status(auth_client, run_id, {"cancelled"})
    assert final["status"] == "cancelled"
    step = _find_waiting_step(final, "n_gen")
    assert step["status"] == "cancelled"


def test_ask_user_protocol_error_does_not_emit_waiting(auth_client, enable_claude_agent):
    """spec §1.2 + §7：options 数量不足时不应 emit step.waiting，run 应直接失败。"""

    enable_claude_agent()
    bad_prompt = (
        '[[ask_user:{"context":{"title":"确认运行选择","summary":"继续运行前需要你选择一个处理方向。"},"groups":[{"id":"choice","type":"single","label":"x",'
        '"options":[{"label":"only-one","description":"只给一个选项。","recommended":true}]}]}]] [[respond:LATE]]'
    )
    graph = {
        "agent": "claude",
        "nodes": [_generate_node("n_gen", prompt=bad_prompt)],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "failed"
    step = _find_waiting_step(final, "n_gen")
    assert step["status"] == "failed"
    assert "ask_user" in (step["error"] or "")
    # 协议错误不应给 step.input 写 ask_user 字段，因为我们根本没 emit step.waiting。
    assert (step["input"] or {}).get("ask_user") is None


def test_ask_user_protocol_error_for_too_many_options(auth_client, enable_claude_agent):
    enable_claude_agent()
    bad_prompt = (
        '[[ask_user:{"context":{"title":"确认运行选择","summary":"继续运行前需要你选择一个处理方向。"},"groups":[{"id":"choice","type":"single","label":"x",'
        '"options":['
        '{"label":"A","description":"选 A。","recommended":true},'
        '{"label":"B","description":"选 B。","recommended":false},'
        '{"label":"C","description":"选 C。","recommended":false},'
        '{"label":"D","description":"选 D。","recommended":false}'
        ']}]}]] [[respond:LATE]]'
    )
    graph = {
        "agent": "claude",
        "nodes": [_generate_node("n_gen", prompt=bad_prompt)],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    final = _wait_for_status(auth_client, run_id, {"success", "failed", "cancelled"})
    assert final["status"] == "failed"
    step = _find_waiting_step(final, "n_gen")
    assert step["status"] == "failed"
    assert "ask_user.groups.options 数量必须在 2-3 之间" in (step["error"] or "")
    assert (step["input"] or {}).get("ask_user") is None


def test_resume_with_foreign_attachment_returns_404(auth_client, enable_claude_agent):
    """spec §7：attachments 包含他人 upload id → 404。"""

    enable_claude_agent()
    # 另一个用户上传一个文件：手动切到他的 token，再切回 admin。
    admin_token = auth_client.headers["Authorization"]
    user_token = f"Bearer {create_regular_user()['token']}"
    auth_client.headers["Authorization"] = user_token
    other_resp = auth_client.post(
        "/api/uploads",
        files={"file": ("other.txt", b"secret", "text/plain")},
    )
    assert other_resp.status_code == 200
    foreign_id = other_resp.json()["id"]
    auth_client.headers["Authorization"] = admin_token

    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt=_ask_user_prompt(single=True, options=["A", "B", "C"])),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run_id = _start_run(auth_client, app_id)
    tool_use_id = _tool_use_id_for(auth_client, run_id, "n_gen")

    response = auth_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "tool_use_id": tool_use_id,
            "answers": [{"group_id": "choice", "selected": ["A"]}],
            "attachments": [{"id": foreign_id}],
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "附件不存在"
    auth_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(auth_client, run_id, {"cancelled"})
