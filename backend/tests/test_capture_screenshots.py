from __future__ import annotations

import hashlib
import importlib.util
import json
import signal
import sys
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = BACKEND_ROOT / "runtime" / "scripts" / "capture_screenshots.py"
RUNTIME_DOCKERFILE = BACKEND_ROOT / "runtime" / "Dockerfile"


def _load_capture_module():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("mira_capture_screenshots", CAPTURE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture_screenshots = _load_capture_module()


class _ExitedProcess:
    pid = 1234

    def poll(self) -> int:
        return 0


class _ParentExitsWithLingeringChildren:
    pid = 4321

    def poll(self) -> None:
        return None

    def wait(self, *, timeout: int) -> int:
        assert timeout == 10
        return 0

    def terminate(self) -> None:
        raise AssertionError("process-group signalling should be used")


class _ReadyResponse:
    status = 200

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        return None


def _write_project(path: Path, *, with_node_modules: bool = True, scripts: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True)
    (path / "package.json").write_text(
        json.dumps({"scripts": scripts or {"dev": "vite"}}), encoding="utf-8"
    )
    if with_node_modules:
        (path / "node_modules").mkdir()


def _run_capture(tmp_path: Path, monkeypatch, *, project: Path | None = None, expected_exit_code: int = 0,
                 routes: list[dict[str, str]] | None = None, min_screenshots: int = 1,
                 failed_routes: set[str] | None = None, route_statuses: dict[str, int] | None = None,
                 scripts: dict[str, str] | None = None, with_node_modules: bool = True,
                 viewports: list[dict[str, int | str]] | None = None,
                 browser_failure_output: str = "browser capture failed",
                 failed_commands: set[tuple[str, ...]] | None = None,
                 preexisting_archive: bool = False):
    project = project or (tmp_path / "project")
    if not project.exists():
        _write_project(project, with_node_modules=with_node_modules, scripts=scripts)
    out_dir = tmp_path / "screenshots"
    archive_out = tmp_path / "screenshots.zip"
    if preexisting_archive:
        with zipfile.ZipFile(archive_out, "w") as archive:
            archive.writestr("stale.txt", "stale")
    commands: list[list[str]] = []
    events: list[tuple[str, list[str]]] = []
    profiles: list[Path] = []
    http_urls: list[str] = []

    def fake_run(command: list[str], **_kwargs):  # noqa: ANN003, ANN202
        commands.append(command)
        events.append(("run", command))
        if tuple(command) in (failed_commands or set()):
            return SimpleNamespace(returncode=1, stdout="setup failed")
        if command[0] == capture_screenshots.CHROMIUM_BINARY:
            url = command[-1]
            if failed_routes and any(url.endswith(route) for route in failed_routes):
                return SimpleNamespace(returncode=1, stdout=browser_failure_output)
            profile = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir=")))
            screenshot = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--screenshot=")))
            profiles.append(profile)
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout="")

    def fake_urlopen(url: str, *_args, **_kwargs):  # noqa: ANN003, ANN202
        http_urls.append(url)
        for route, status in (route_statuses or {}).items():
            if url.endswith(route):
                raise urllib.error.HTTPError(url, status, "route unavailable", hdrs=None, fp=None)
        return _ReadyResponse()

    def fake_popen(command: list[str], *_args, **_kwargs):
        events.append(("popen", command))
        return _ExitedProcess()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(capture_screenshots.subprocess, "run", fake_run)
    monkeypatch.setattr(capture_screenshots.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(capture_screenshots.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_SCRIPT), "--project-dir", str(project),
            "--out-dir", str(out_dir), "--zip-out", str(archive_out),
            "--min-screenshots", str(min_screenshots),
            *( ["--routes-json", json.dumps(routes, ensure_ascii=False)] if routes else []),
            *( ["--viewports-json", json.dumps(viewports)] if viewports else []),
        ],
    )
    assert capture_screenshots.main() == expected_exit_code
    members = set()
    if archive_out.is_file():
        with zipfile.ZipFile(archive_out) as archive:
            members = set(archive.namelist())
    manifest = json.loads((out_dir / "screenshot-manifest.json").read_text(encoding="utf-8"))
    return SimpleNamespace(
        commands=commands, events=events, profiles=profiles, http_urls=http_urls,
        members=members, manifest=manifest, archive_out=archive_out, out_dir=out_dir,
        log=(out_dir / "capture-log.txt").read_text(encoding="utf-8"),
    )


def test_capture_reuses_existing_node_modules_and_writes_only_screenshot_archive(tmp_path, monkeypatch):
    result = _run_capture(tmp_path, monkeypatch)
    assert not any(command and command[0] == "npm" and command[1] in {"ci", "install"} for command in result.commands)
    assert result.members == {
        "screenshots/SP-01-home.png", "screenshots/capture-log.txt", "screenshots/screenshot-manifest.json"
    }
    assert result.manifest["ok"] is True
    assert result.manifest["project_dir"] == "project"
    assert result.manifest["screenshots"][0]["sha256"] == hashlib.sha256(b"\x89PNG\r\n\x1a\n").hexdigest()
    assert result.profiles and all(not profile.exists() for profile in result.profiles)


def test_capture_fails_without_node_modules(tmp_path, monkeypatch):
    result = _run_capture(tmp_path, monkeypatch, with_node_modules=False, expected_exit_code=1)
    assert result.manifest["failures"] == [{
        "stage": "capture", "message": "project-dir requires an existing node_modules directory"
    }]
    assert not any(event[0] == "popen" for event in result.events)


def test_capture_runs_database_setup_before_server(tmp_path, monkeypatch):
    result = _run_capture(
        tmp_path, monkeypatch,
        scripts={"dev": "next dev", "db:init": "node scripts/init.mjs", "db:seed": "node scripts/seed.mjs"},
    )
    assert result.events[:3] == [
        ("run", ["npm", "run", "db:init"]),
        ("run", ["npm", "run", "db:seed"]),
        ("popen", ["npm", "run", "dev", "--", "-p", "3210", "-H", "127.0.0.1"]),
    ]


def test_capture_stops_when_database_setup_fails(tmp_path, monkeypatch):
    result = _run_capture(
        tmp_path, monkeypatch,
        scripts={"dev": "vite", "db:init": "node scripts/init.mjs"},
        failed_commands={("npm", "run", "db:init")}, expected_exit_code=1,
    )
    assert result.manifest["ok"] is False
    assert "npm run db:init" in result.manifest["failures"][0]["message"]
    assert not any(event[0] == "popen" for event in result.events)


def test_capture_rejects_project_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    linked = workspace / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="project-dir must be inside"):
        capture_screenshots._resolve_project_dir(workspace, str(outside))
    with pytest.raises(RuntimeError, match="project-dir must be inside"):
        capture_screenshots._resolve_project_dir(workspace, str(linked))


@pytest.mark.parametrize("legacy_flag", ["--archive", "--archive-out", "--skip-install"])
def test_capture_rejects_removed_legacy_flags(tmp_path, monkeypatch, legacy_flag: str):
    monkeypatch.setattr(sys, "argv", [str(CAPTURE_SCRIPT), legacy_flag, str(tmp_path / "project")])
    with pytest.raises(SystemExit, match="2"):
        capture_screenshots.main()


def test_capture_removes_stale_output_when_route_fails(tmp_path, monkeypatch):
    result = _run_capture(
        tmp_path, monkeypatch, routes=[{"route": "/ok", "page_name": "ok"}, {"route": "/bad", "page_name": "bad"}],
        failed_routes={"/bad"}, expected_exit_code=1, preexisting_archive=True,
    )
    assert result.manifest["ok"] is False
    assert not result.archive_out.exists()


def test_capture_removes_stale_output_when_screenshot_threshold_is_not_met(tmp_path, monkeypatch):
    result = _run_capture(tmp_path, monkeypatch, min_screenshots=2, expected_exit_code=1)
    assert result.manifest["ok"] is False
    assert not result.archive_out.exists()


def test_capture_supports_multiple_viewports(tmp_path, monkeypatch):
    result = _run_capture(
        tmp_path, monkeypatch,
        routes=[{"route": "/dashboard", "page_name": "仪表盘"}], min_screenshots=2,
        viewports=[{"name": "desktop", "width": 1440, "height": 1000}, {"name": "mobile", "width": 390, "height": 844}],
    )
    assert len(result.manifest["screenshots"]) == 2
    assert {item["name"] for item in result.manifest["screenshots"]} == {
        "SP-01-desktop-dashboard.png", "SP-01-mobile-dashboard.png"
    }


@pytest.mark.parametrize("status", [404, 500])
def test_capture_checks_http_status_before_chromium(tmp_path, monkeypatch, status: int):
    result = _run_capture(
        tmp_path, monkeypatch,
        routes=[{"route": "/ok", "page_name": "ok"}, {"route": "/bad", "page_name": "bad"}],
        route_statuses={"/bad": status}, expected_exit_code=1,
    )
    assert [command[-1] for command in result.commands if command[0] == capture_screenshots.CHROMIUM_BINARY] == [
        "http://127.0.0.1:3210/ok"
    ]


def test_capture_redacts_paths_and_keeps_urls(tmp_path, monkeypatch):
    diagnostic_url = "http://127.0.0.1:3210/tmp/health"
    output = " /home/mira/runtime/workspaces/app/index.js " + diagnostic_url
    result = _run_capture(
        tmp_path, monkeypatch,
        routes=[{"route": "/ok", "page_name": "ok"}, {"route": "/bad", "page_name": "bad"}],
        failed_routes={"/bad"}, browser_failure_output=output, expected_exit_code=1,
    )
    text = json.dumps(result.manifest, ensure_ascii=False) + result.log
    assert "/home/mira/runtime/workspaces/app/index.js" not in text
    assert "<runtime-home>/runtime/workspaces/app/index.js" in text
    assert diagnostic_url in text


def test_stop_process_kills_lingering_process_group(monkeypatch):
    signals: list[int] = []
    monkeypatch.setattr(capture_screenshots.os, "killpg", lambda _pid, sent: signals.append(sent))
    capture_screenshots._stop_process(_ParentExitsWithLingeringChildren(), lambda _message: None)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_runtime_image_pins_codex_version():
    assert "ARG CODEX_VERSION=0.147.0" in RUNTIME_DOCKERFILE.read_text(encoding="utf-8")
