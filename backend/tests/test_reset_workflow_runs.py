from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_reset_workflow_runs_supports_pre_0019_schema(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "legacy.sqlite"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
        "DATA_DIR": str(tmp_path / "data"),
        "RUNTIME_DIR": str(tmp_path / "runtime"),
        "JWT_SECRET": "test-secret-for-mira-backend-tests-32-bytes",
        "AGENT_CONFIG_SECRET": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        "ADMIN_USERNAME": "test-admin",
        "ADMIN_PASSWORD": "admin-test-secret",
    }

    upgrade_0018 = _run(
        [sys.executable, "-m", "alembic", "upgrade", "0018_run_history_indexes"],
        backend_root,
        env,
    )
    assert upgrade_0018.returncode == 0, upgrade_0018.stderr
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runs (id, app_id, owner_id, status, inputs_json, graph_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("run_legacy", "app_legacy", "user_legacy", "success", "{}", "{}"),
        )

    dry_run = _run([sys.executable, "scripts/reset_workflow_runs.py"], backend_root, env)
    assert dry_run.returncode == 0, dry_run.stderr
    assert "runs: 1" in dry_run.stdout
    assert "dry-run only" in dry_run.stdout

    applied = _run(
        [sys.executable, "scripts/reset_workflow_runs.py", "--apply"],
        backend_root,
        env,
        input_text="DELETE WORKFLOW RUNS\n",
    )
    assert applied.returncode == 0, applied.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (0,)

    upgrade_head = _run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        backend_root,
        env,
    )
    assert upgrade_head.returncode == 0, upgrade_head.stderr


def _run(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )
