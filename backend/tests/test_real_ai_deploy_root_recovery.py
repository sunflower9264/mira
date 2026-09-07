from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("MIRA_RUN_REAL_AI_DEPLOY_RECOVERY_TEST") != "1",
    reason="set MIRA_RUN_REAL_AI_DEPLOY_RECOVERY_TEST=1 to call the deployed real Codex runtime",
)

BASE_URL = os.getenv("MIRA_DEPLOY_BASE_URL", "").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("MIRA_REAL_AI_TIMEOUT", "600"))
INTERNAL_PROMPT_TERMS = (
    "output_contract",
    "json_schema",
    "success_field",
    "success_value",
    "failure_kind",
    "agent_session_id",
    "runtime_policy",
    "session_scope",
    "step.delta",
    "run_events",
    "自动修复机制",
    "根因恢复 1/1",
)

PACKAGE_JSON = b'''{
  "name": "order-pricing-e2e",
  "private": true,
  "type": "module",
  "scripts": {"test": "node --test"}
}
'''
BUGGY_SOURCE = b'''export function calculateTotal(subtotalCents, discountCents) {
  if (!Number.isInteger(subtotalCents) || !Number.isInteger(discountCents)) {
    throw new TypeError("amounts must be integer cents");
  }
  if (subtotalCents < 0 || discountCents < 0 || discountCents > subtotalCents) {
    throw new RangeError("invalid order amounts");
  }
  return subtotalCents + discountCents;
}
'''
TEST_SOURCE = b'''import test from "node:test";
import assert from "node:assert/strict";
import { calculateTotal } from "../src/calculateTotal.js";

test("subtracts a discount from the subtotal", () => {
  assert.equal(calculateTotal(10000, 1000), 9000);
});

test("rejects a discount larger than the subtotal", () => {
  assert.throws(() => calculateTotal(1000, 1001), RangeError);
});
'''
README = b'''# Order Pricing E2E

Run `npm test`. Monetary values use integer cents.
'''


def test_deployed_real_codex_root_recovery_and_prompt_assistant(tmp_path: Path) -> None:
    username = os.getenv("MIRA_DEPLOY_USERNAME")
    password = os.getenv("MIRA_DEPLOY_PASSWORD")
    if not BASE_URL or not username or not password:
        pytest.skip("set MIRA_DEPLOY_BASE_URL, MIRA_DEPLOY_USERNAME and MIRA_DEPLOY_PASSWORD")

    fixture_zip = tmp_path / "order-pricing-buggy.zip"
    _write_fixture(fixture_zip)

    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})

        runtime_status = client.post("/api/settings/codex/status")
        runtime_status.raise_for_status()
        runtime_body = runtime_status.json()
        assert runtime_body["installed"] is True
        assert runtime_body["runnable"] is True, runtime_body.get("error")

        settings = client.get("/api/settings")
        settings.raise_for_status()
        disabled_tool_ids = [
            str(tool["id"])
            for tool in settings.json().get("tools", [])
            if isinstance(tool, dict) and isinstance(tool.get("id"), str)
        ]

        upload = client.post(
            "/api/uploads",
            files={"file": (fixture_zip.name, fixture_zip.read_bytes(), "application/zip")},
        )
        upload.raise_for_status()
        upload_body = upload.json()

        graph = _graph(disabled_tool_ids)
        app_name = f"E2E-真实Codex-根因恢复-{time.strftime('%Y%m%d-%H%M%S')}"
        created = client.post(
            "/api/apps",
            json={"name": app_name, "description": "真实 Codex App Server 根因恢复与提示词助手验收"},
        )
        created.raise_for_status()
        app_id = str(created.json()["id"])
        print(f"E2E_APP_ID={app_id}")
        patched = client.patch(f"/api/apps/{app_id}", json={"graph": graph})
        patched.raise_for_status()

        assistant = client.post(
            "/api/prompt-assistant/generate",
            json={
                "app_id": app_id,
                "graph": graph,
                "node_id": "n_gate",
                "user_request": (
                    "把当前提示词整理成清晰的工程验收说明：解压订单金额项目，"
                    "只检查当前源码而不修改，运行 npm test，并如实总结测试结果。"
                ),
            },
        )
        assistant.raise_for_status()
        assistant_body = _finish_prompt_assistant(client, assistant.json())
        generated_prompt = str(assistant_body["prompt"])
        assert assistant_body["output_contract"] is None
        leaked = [term for term in INTERNAL_PROMPT_TERMS if term.casefold() in generated_prompt.casefold()]
        assert not leaked, leaked

        run = client.post(
            "/api/runs",
            json={
                "app_id": app_id,
                "wiki_mode": "without",
                "inputs": {
                    "n_input": {
                        "value": "验收订单金额计算项目并交付修复后的可运行源码包。",
                        "attachments": [{"id": upload_body["id"], "name": fixture_zip.name}],
                    }
                },
            },
        )
        run.raise_for_status()
        run_id = str(run.json()["run_id"])
        print(f"E2E_RUN_ID={run_id}")
        final = _wait_for_terminal(client, run_id)
        assert final["status"] == "success", _debug_run(final)

        steps = {step["node_id"]: step for step in final["steps"]}
        gate = steps["n_gate"]
        gate_logs = [str(log.get("text") or "") for log in gate.get("logs", [])]
        assert sum("开始根因恢复 1/1" in text for text in gate_logs) == 1, gate_logs
        assert sum("根因恢复后复验通过" in text for text in gate_logs) == 1, gate_logs
        assert any("业务门禁未通过" in text and 'status="failed"' in text for text in gate_logs), gate_logs
        assert gate["output"]["status"] == "passed", gate
        assert steps["n_package"]["status"] == "success"
        assert steps["n_out"]["status"] == "success"

        artifacts = client.get(f"/api/runs/{run_id}/artifacts")
        artifacts.raise_for_status()
        candidates = [item for item in artifacts.json()["artifacts"] if str(item.get("name", "")).endswith(".zip")]
        assert len(candidates) == 1, artifacts.json()
        artifact = candidates[0]
        assert artifact["integrity"] == "verified"
        downloaded = client.get(str(artifact["download_url"]))
        downloaded.raise_for_status()
        archive_bytes = downloaded.content
        assert hashlib.sha256(archive_bytes).hexdigest() == artifact["sha256"]

        repaired_zip = tmp_path / "repaired-order-pricing.zip"
        repaired_zip.write_bytes(archive_bytes)
        extracted = tmp_path / "repaired"
        _safe_extract(repaired_zip, extracted)
        project_root = _project_root(extracted)
        assert (project_root / "test/calculateTotal.test.js").read_bytes() == TEST_SOURCE
        assert (project_root / "package.json").read_bytes() == PACKAGE_JSON
        assert (project_root / "src/calculateTotal.js").read_bytes() != BUGGY_SOURCE

        test_result = subprocess.run(
            ["npm", "test"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert test_result.returncode == 0, test_result.stdout + test_result.stderr
        module_uri = (project_root / "src/calculateTotal.js").resolve().as_uri()
        black_box = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                (
                    f'import {{ calculateTotal }} from "{module_uri}"; '
                    "if (calculateTotal(10000, 1000) !== 9000) process.exit(1);"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert black_box.returncode == 0, black_box.stdout + black_box.stderr

        current_graph = client.get(f"/api/apps/{app_id}").json()["graph"]
        current_gate = next(node for node in current_graph["nodes"] if node["id"] == "n_gate")
        current_gate["prompt"] = generated_prompt
        applied = client.patch(f"/api/apps/{app_id}", json={"graph": current_graph})
        applied.raise_for_status()
        applied_gate = next(node for node in applied.json()["graph"]["nodes"] if node["id"] == "n_gate")
        assert applied_gate["output_contract"]["success_field"] == "status"
        assert applied_gate["output_contract"]["success_value"] == "passed"

        print(
            "E2E_EVIDENCE="
            + json.dumps(
                {
                    "app_name": app_name,
                    "app_id": app_id,
                    "run_id": run_id,
                    "runtime_identity": runtime_body.get("identity"),
                    "prompt_assistant_status": assistant_body["status"],
                    "recovery_start_count": sum("开始根因恢复 1/1" in text for text in gate_logs),
                    "run_status": final["status"],
                    "artifact_name": artifact["name"],
                    "artifact_sha256": artifact["sha256"],
                    "independent_npm_test": "passed",
                    "black_box_total": 9000,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def _write_fixture(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("order-pricing/package.json", PACKAGE_JSON)
        archive.writestr("order-pricing/src/calculateTotal.js", BUGGY_SOURCE)
        archive.writestr("order-pricing/test/calculateTotal.test.js", TEST_SOURCE)
        archive.writestr("order-pricing/README.md", README)


def _graph(disabled_tool_ids: list[str]) -> dict[str, Any]:
    return {
        "tools": {"disabled_tool_ids": disabled_tool_ids},
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "待验收项目",
                "input_schema": {"label": "项目 ZIP", "kind": "file", "required": True},
            },
            {
                "id": "n_gate",
                "type": "generate",
                "position": {"x": 260, "y": 0},
                "title": "真实测试验收",
                "prompt": (
                    "找到上游输入附件中的订单金额项目 ZIP，把它解压到当前工作区的 project 目录；"
                    "如果 project 已存在则复用。首次验收只检查当前状态，不编辑、删除或重新生成源码与测试。"
                    "在项目目录运行 npm test，根据命令的真实退出码和测试报告返回验收结果；"
                    "全部测试通过时 status 为 passed，否则为 failed。信息已经完整，不需要向用户提问。"
                ),
                "output_contract": {
                    "type": "json",
                    "success_field": "status",
                    "success_value": "passed",
                    "json_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "status": {"type": "string", "enum": ["passed", "failed"]},
                            "summary": {"type": "string", "minLength": 1},
                            "command": {"type": "string", "minLength": 1},
                            "tests_passed": {"type": "integer", "minimum": 0},
                            "tests_failed": {"type": "integer", "minimum": 0},
                        },
                        "required": ["status", "summary", "command", "tests_passed", "tests_failed"],
                    },
                },
            },
            {
                "id": "n_package",
                "type": "generate",
                "position": {"x": 520, "y": 0},
                "title": "打包可运行源码",
                "prompt": (
                    "确认 project 中现有测试仍然通过，不修改源码或测试；"
                    "把完整项目打包为 repaired-order-pricing.zip，必须包含 package.json、src、test 和 README.md。"
                    "信息已经完整，不需要向用户提问。"
                ),
                "output_contract": {"type": "artifact", "artifact_kind": "code", "max_count": 1},
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 780, "y": 0},
                "title": "验收结果",
                "prompt": "用简洁中文 HTML 展示测试结论和可下载源码包名称。",
            },
        ],
        "execution_edges": [
            {"id": "e_input_gate", "source": "n_input", "target": "n_gate"},
            {"id": "e_gate_package", "source": "n_gate", "target": "n_package"},
            {"id": "e_package_out", "source": "n_package", "target": "n_out"},
        ],
    }


def _finish_prompt_assistant(client: httpx.Client, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("status") != "waiting_for_user":
        assert body.get("status") == "completed", body
        return body
    answers = []
    for group in body["request"]["groups"]:
        options = [option for option in group["options"] if option.get("label") != "以上都不是"]
        selected = next((option for option in options if option.get("recommended")), options[0])
        answers.append({"group_id": group["id"], "selected": [selected["label"]]})
    resumed = client.post(
        f"/api/prompt-assistant/{body['generation_id']}/resume",
        json={"answers": answers},
    )
    resumed.raise_for_status()
    result = resumed.json()
    assert result.get("status") == "completed", result
    return result


def _wait_for_terminal(client: httpx.Client, run_id: str) -> dict[str, Any]:
    deadline = time.time() + TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"success", "failed", "cancelled"}:
            return last
        if last["status"] == "waiting_for_user":
            raise AssertionError(f"fully specified test unexpectedly requested user input: {_debug_run(last)}")
        time.sleep(1)
    raise AssertionError(f"run did not finish within {TIMEOUT_SECONDS}s: {_debug_run(last)}")


def _safe_extract(path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            assert not member.is_absolute() and ".." not in member.parts, info.filename
        archive.extractall(target)


def _project_root(extracted: Path) -> Path:
    packages = list(extracted.rglob("package.json"))
    assert len(packages) == 1, packages
    return packages[0].parent


def _debug_run(body: dict[str, Any] | None) -> dict[str, Any] | None:
    if body is None:
        return None
    return {
        "id": body.get("id"),
        "status": body.get("status"),
        "error": body.get("error"),
        "steps": [
            {
                "node_id": step.get("node_id"),
                "status": step.get("status"),
                "output": step.get("output"),
                "error": step.get("error"),
                "logs": step.get("logs"),
            }
            for step in body.get("steps", [])
        ],
    }
