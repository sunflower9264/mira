from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import signal
import stat
import sys
import tarfile
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
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        return None


def _write_project_archive(
    path: Path,
    *,
    with_package_lock: bool,
    project_scripts: dict[str, str] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "project/package.json",
            json.dumps({"scripts": project_scripts or {"dev": "vite"}}),
        )
        if with_package_lock:
            zf.writestr("project/package-lock.json", json.dumps({"lockfileVersion": 3}))


def _run_successful_capture(
    tmp_path: Path,
    monkeypatch,
    *,
    with_package_lock: bool,
    routes: list[dict[str, str]] | None = None,
    min_screenshots: int | None = None,
    viewports: list[dict[str, int | str]] | None = None,
    failed_routes: set[str] | None = None,
    browser_failure_output: str = "browser capture failed",
    route_statuses: dict[str, int] | None = None,
    project_scripts: dict[str, str] | None = None,
    expected_exit_code: int = 0,
    preexisting_archive: bool = False,
):  # noqa: ANN001, ANN202
    archive = tmp_path / "project.zip"
    out_dir = tmp_path / "screenshots"
    archive_out = tmp_path / "screenshots.zip"
    _write_project_archive(
        archive,
        with_package_lock=with_package_lock,
        project_scripts=project_scripts,
    )
    if preexisting_archive:
        with zipfile.ZipFile(archive_out, "w") as zf:
            zf.writestr("stale.txt", "stale screenshot artifact")

    commands: list[list[str]] = []
    events: list[tuple[str, list[str]]] = []
    chromium_profiles: list[Path] = []
    http_urls: list[str] = []

    def fake_run(command: list[str], **_kwargs):  # noqa: ANN003, ANN202
        commands.append(command)
        events.append(("run", command))
        if command[0] == capture_screenshots.CHROMIUM_BINARY:
            url = command[-1]
            if failed_routes and any(url.endswith(route) for route in failed_routes):
                return SimpleNamespace(returncode=1, stdout=browser_failure_output)
            profile = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir=")))
            screenshot = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--screenshot=")))
            chromium_profiles.append(profile)
            (profile / "Default").mkdir(parents=True, exist_ok=True)
            (profile / "Default" / "Preferences").write_text("{}", encoding="utf-8")
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout="")

    def fake_urlopen(url: str, *_args, **_kwargs):  # noqa: ANN003, ANN202
        http_urls.append(url)
        for route, status in (route_statuses or {}).items():
            if url.endswith(route):
                raise urllib.error.HTTPError(url, status, "route unavailable", hdrs=None, fp=None)
        return _ReadyResponse()

    def fake_popen(command: list[str], *_args, **_kwargs):  # noqa: ANN003, ANN202
        events.append(("popen", command))
        return _ExitedProcess()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(capture_screenshots.subprocess, "run", fake_run)
    monkeypatch.setattr(capture_screenshots.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(capture_screenshots.urllib.request, "urlopen", fake_urlopen)
    argv = [
        str(CAPTURE_SCRIPT),
        "--archive",
        str(archive),
        "--out-dir",
        str(out_dir),
        "--archive-out",
        str(archive_out),
    ]
    if routes is not None:
        argv.extend(["--routes-json", json.dumps(routes, ensure_ascii=False)])
    if min_screenshots is not None:
        argv.extend(["--min-screenshots", str(min_screenshots)])
    if viewports is not None:
        argv.extend(["--viewports-json", json.dumps(viewports)])
    monkeypatch.setattr(sys, "argv", argv)

    assert capture_screenshots.main() == expected_exit_code
    members: set[str] = set()
    if archive_out.is_file():
        with zipfile.ZipFile(archive_out) as zf:
            members = set(zf.namelist())
    manifest = json.loads((out_dir / "screenshot-manifest.json").read_text(encoding="utf-8"))
    log = (out_dir / "capture-log.txt").read_text(encoding="utf-8")
    return SimpleNamespace(
        commands=commands,
        events=events,
        chromium_profiles=chromium_profiles,
        http_urls=http_urls,
        members=members,
        manifest=manifest,
        log=log,
        archive_out=archive_out,
        out_dir=out_dir,
    )


def test_capture_keeps_chromium_profile_out_of_screenshot_archive(tmp_path, monkeypatch) -> None:
    result = _run_successful_capture(tmp_path, monkeypatch, with_package_lock=False)

    chromium_command = next(
        command for command in result.commands if command[0] == capture_screenshots.CHROMIUM_BINARY
    )
    assert chromium_command[0] == "/usr/bin/chromium"
    assert result.members == {
        "screenshots/SP-01-home.png",
        "screenshots/capture-log.txt",
        "screenshots/screenshot-manifest.json",
    }
    assert len(result.chromium_profiles) == 1
    assert result.out_dir not in result.chromium_profiles[0].parents
    assert result.manifest["ok"] is True
    assert result.manifest["failures"] == []
    assert result.manifest["screenshots"][0]["sha256"] == hashlib.sha256(b"\x89PNG\r\n\x1a\n").hexdigest()


def test_stop_process_kills_lingering_server_process_group(monkeypatch) -> None:
    signals: list[int] = []

    def fake_killpg(process_group: int, sent_signal: int) -> None:
        assert process_group == _ParentExitsWithLingeringChildren.pid
        signals.append(sent_signal)

    monkeypatch.setattr(capture_screenshots.os, "killpg", fake_killpg)

    capture_screenshots._stop_process(_ParentExitsWithLingeringChildren(), lambda _message: None)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_capture_manifest_and_log_redact_runtime_absolute_paths(tmp_path, monkeypatch) -> None:
    result = _run_successful_capture(tmp_path, monkeypatch, with_package_lock=True)

    manifest_text = json.dumps(result.manifest, ensure_ascii=False)
    assert result.manifest["archive"] == "project.zip"
    assert str(tmp_path) not in manifest_text
    assert str(tmp_path) not in result.log
    assert all(str(profile) not in result.log for profile in result.chromium_profiles)
    assert "project root: project" in result.log
    assert "<workspace>" in result.log
    assert "<runtime-temp>" in result.log


def test_capture_redacts_common_runtime_paths_without_changing_urls(tmp_path, monkeypatch) -> None:
    diagnostic_url = "http://127.0.0.1:3210/tmp/health"
    browser_output = " ".join(
        [
            "/home/mira/runtime/workspaces/app/index.js",
            "/usr/local/lib/node_modules/pkg/index.js",
            "/opt/mira/capture_screenshots.py",
            "/tmp/mira-chromium-crash/Crashpad",
            diagnostic_url,
        ]
    )
    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=[
            {"route": "/success", "page_name": "成功页"},
            {"route": "/failure", "page_name": "失败页"},
        ],
        failed_routes={"/failure"},
        browser_failure_output=browser_output,
        expected_exit_code=1,
    )

    manifest_text = json.dumps(result.manifest, ensure_ascii=False)
    for leaked_path in (
        "/home/mira/runtime/workspaces/app/index.js",
        "/usr/local/lib/node_modules/pkg/index.js",
        "/opt/mira/capture_screenshots.py",
        "/tmp/mira-chromium-crash/Crashpad",
    ):
        assert leaked_path not in manifest_text
        assert leaked_path not in result.log
    for redacted_path in (
        "<runtime-home>/runtime/workspaces/app/index.js",
        "<system>/lib/node_modules/pkg/index.js",
        "<mira-runtime>/capture_screenshots.py",
        "<tmp>/mira-chromium-crash/Crashpad",
    ):
        assert redacted_path in manifest_text
        assert redacted_path in result.log
    assert diagnostic_url in manifest_text
    assert diagnostic_url in result.log


@pytest.mark.parametrize(
    ("with_package_lock", "expected_command"),
    [
        (True, ["npm", "ci", "--no-audit", "--no-fund"]),
        (False, ["npm", "install", "--no-audit", "--no-fund"]),
    ],
)
def test_capture_selects_npm_install_command_from_package_lock(
    tmp_path,
    monkeypatch,
    with_package_lock: bool,
    expected_command: list[str],
) -> None:
    result = _run_successful_capture(tmp_path, monkeypatch, with_package_lock=with_package_lock)

    npm_install = next(command for command in result.commands if command[0] == "npm")
    assert npm_install == expected_command


def test_capture_runs_declared_database_setup_before_starting_server(tmp_path, monkeypatch) -> None:
    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        project_scripts={
            "dev": "next dev",
            "db:init": "node scripts/db-init.mjs",
            "db:seed": "node scripts/db-seed.mjs",
        },
    )

    assert result.events[:4] == [
        ("run", ["npm", "ci", "--no-audit", "--no-fund"]),
        ("run", ["npm", "run", "db:init"]),
        ("run", ["npm", "run", "db:seed"]),
        ("popen", ["npm", "run", "dev", "--", "-p", "3210", "-H", "127.0.0.1"]),
    ]


def test_capture_stops_when_declared_database_setup_fails(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "project.zip"
    out_dir = tmp_path / "screenshots"
    archive_out = tmp_path / "screenshots.zip"
    _write_project_archive(
        archive,
        with_package_lock=True,
        project_scripts={
            "dev": "next dev",
            "db:init": "node scripts/db-init.mjs",
            "db:seed": "node scripts/db-seed.mjs",
        },
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):  # noqa: ANN003, ANN202
        commands.append(command)
        return SimpleNamespace(returncode=1 if command == ["npm", "run", "db:init"] else 0, stdout="")

    def unexpected_popen(*_args, **_kwargs):  # noqa: ANN003, ANN202
        raise AssertionError("dev server must not start after db:init fails")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(capture_screenshots.subprocess, "run", fake_run)
    monkeypatch.setattr(capture_screenshots.subprocess, "Popen", unexpected_popen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_SCRIPT),
            "--archive",
            str(archive),
            "--out-dir",
            str(out_dir),
            "--archive-out",
            str(archive_out),
        ],
    )

    assert capture_screenshots.main() == 1
    assert commands == [
        ["npm", "ci", "--no-audit", "--no-fund"],
        ["npm", "run", "db:init"],
    ]
    manifest = json.loads((out_dir / "screenshot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert "npm run db:init" in manifest["failures"][0]["message"]


def test_capture_returns_nonzero_when_minimum_screenshot_count_is_not_met(tmp_path, monkeypatch) -> None:
    routes = [{"route": f"/page-{index}", "page_name": f"页面 {index}"} for index in range(1, 5)]

    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=routes,
        min_screenshots=5,
        expected_exit_code=1,
    )

    assert not result.archive_out.exists()
    assert len(result.manifest["screenshots"]) == 4
    assert result.manifest["ok"] is False
    assert result.manifest["failures"] == [
        {
            "stage": "minimum_screenshots",
            "message": "captured 4 screenshot(s); minimum required is 5",
        }
    ]


def test_capture_succeeds_at_explicit_minimum_with_no_failures(tmp_path, monkeypatch) -> None:
    routes = [{"route": f"/page-{index}", "page_name": f"页面 {index}"} for index in range(1, 6)]

    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=routes,
        min_screenshots=5,
    )

    assert len(result.manifest["screenshots"]) == 5
    assert result.manifest["failures"] == []
    assert result.manifest["ok"] is True


def test_capture_supports_desktop_and_mobile_viewports(tmp_path, monkeypatch) -> None:
    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=[{"route": "/dashboard", "page_name": "仪表盘"}],
        viewports=[
            {"name": "desktop", "width": 1440, "height": 1000},
            {"name": "mobile", "width": 390, "height": 844},
        ],
        min_screenshots=2,
    )

    chromium = [command for command in result.commands if command[0] == capture_screenshots.CHROMIUM_BINARY]
    assert [next(arg for arg in command if arg.startswith("--window-size=")) for command in chromium] == [
        "--window-size=1440,1000",
        "--window-size=390,844",
    ]
    assert {item["name"] for item in result.manifest["screenshots"]} == {
        "SP-01-desktop-dashboard.png",
        "SP-01-mobile-dashboard.png",
    }
    assert result.manifest["viewports"] == [
        {"name": "desktop", "width": 1440, "height": 1000},
        {"name": "mobile", "width": 390, "height": 844},
    ]


def test_capture_returns_nonzero_when_any_route_fails_even_if_minimum_is_met(tmp_path, monkeypatch) -> None:
    routes = [
        {"route": "/success", "page_name": "成功页"},
        {"route": "/failure", "page_name": "失败页"},
    ]

    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=routes,
        min_screenshots=1,
        failed_routes={"/failure"},
        expected_exit_code=1,
    )

    assert len(result.manifest["screenshots"]) == 1
    assert result.manifest["failures"] == [{"route": "/failure", "message": "browser capture failed"}]
    assert result.manifest["ok"] is False


def test_capture_failure_removes_stale_archive_output(tmp_path, monkeypatch) -> None:
    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=[{"route": "/failure", "page_name": "失败页"}],
        failed_routes={"/failure"},
        expected_exit_code=1,
        preexisting_archive=True,
    )

    assert result.manifest["ok"] is False
    assert not result.archive_out.exists()


@pytest.mark.parametrize("status", [404, 500])
def test_capture_rejects_routes_with_http_error_status_before_chromium(tmp_path, monkeypatch, status: int) -> None:
    routes = [
        {"route": "/success", "page_name": "成功页"},
        {"route": "/unavailable", "page_name": "不可用页"},
    ]

    result = _run_successful_capture(
        tmp_path,
        monkeypatch,
        with_package_lock=True,
        routes=routes,
        min_screenshots=1,
        route_statuses={"/unavailable": status},
        expected_exit_code=1,
    )

    chromium_urls = [
        command[-1]
        for command in result.commands
        if command[0] == capture_screenshots.CHROMIUM_BINARY
    ]
    assert chromium_urls == ["http://127.0.0.1:3210/success"]
    assert "http://127.0.0.1:3210/unavailable" in result.http_urls
    assert result.manifest["screenshots"][0]["route"] == "/success"
    assert result.manifest["failures"] == [
        {
            "route": "/unavailable",
            "message": f"route returned HTTP {status}: http://127.0.0.1:3210/unavailable",
        }
    ]
    assert result.manifest["ok"] is False


@pytest.mark.parametrize(
    ("case", "suffix", "expected_message"),
    [
        ("zip_traversal", ".zip", "unsafe archive member path"),
        ("zip_symlink", ".zip", "unsafe archive member type"),
        ("zip_special", ".zip", "unsafe archive member type"),
        ("tar_traversal", ".tar", "unsafe archive member path"),
        ("tar_symlink", ".tar", "unsafe archive member type"),
        ("tar_hardlink", ".tar", "unsafe archive member type"),
        ("tar_special", ".tar", "unsafe archive member type"),
    ],
)
def test_capture_rejects_unsafe_archive_members(
    tmp_path,
    monkeypatch,
    case: str,
    suffix: str,
    expected_message: str,
) -> None:
    archive = tmp_path / f"unsafe{suffix}"
    _write_unsafe_archive(archive, case)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_SCRIPT),
            "--archive",
            str(archive),
            "--out-dir",
            str(tmp_path / "screenshots"),
            "--archive-out",
            str(tmp_path / "screenshots.zip"),
        ],
    )

    assert capture_screenshots.main() == 1
    manifest = json.loads((tmp_path / "screenshots" / "screenshot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert expected_message in manifest["failures"][0]["message"]


def _write_unsafe_archive(path: Path, case: str) -> None:
    if case.startswith("zip_"):
        with zipfile.ZipFile(path, "w") as zf:
            if case == "zip_traversal":
                zf.writestr("../escape.txt", "escape")
                return
            info = zipfile.ZipInfo("unsafe-entry")
            info.create_system = 3
            file_type = stat.S_IFLNK if case == "zip_symlink" else stat.S_IFIFO
            info.external_attr = (file_type | 0o644) << 16
            zf.writestr(info, "../escape" if case == "zip_symlink" else "")
        return

    with tarfile.open(path, "w") as tf:
        if case == "tar_traversal":
            data = b"escape"
            info = tarfile.TarInfo("../escape.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            return
        if case == "tar_hardlink":
            data = b"target"
            target = tarfile.TarInfo("target.txt")
            target.size = len(data)
            tf.addfile(target, io.BytesIO(data))
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.LNKTYPE
            info.linkname = "target.txt"
        elif case == "tar_symlink":
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.SYMTYPE
            info.linkname = "../escape"
        else:
            info = tarfile.TarInfo("unsafe-entry")
            info.type = tarfile.FIFOTYPE
        tf.addfile(info)


@pytest.mark.parametrize("duplicate_name", ["project/./package.json", "project\\package.json"])
def test_capture_rejects_zip_members_with_same_normalized_path(
    tmp_path,
    monkeypatch,
    duplicate_name: str,
) -> None:
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project/package.json", json.dumps({"scripts": {"dev": "vite"}}))
        zf.writestr(duplicate_name, "duplicate")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_SCRIPT),
            "--archive",
            str(archive),
            "--out-dir",
            str(tmp_path / "screenshots"),
            "--archive-out",
            str(tmp_path / "screenshots.zip"),
        ],
    )

    assert capture_screenshots.main() == 1
    manifest = json.loads((tmp_path / "screenshots" / "screenshot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["failures"] == [
        {
            "stage": "capture",
            "message": "duplicate archive member path: project/package.json",
        }
    ]


def test_capture_reports_corrupt_zip_member_data_consistently(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project/package.json", json.dumps({"scripts": {"dev": "vite"}}))
    with zipfile.ZipFile(archive) as zf:
        member = zf.getinfo("project/package.json")
    payload = bytearray(archive.read_bytes())
    name_length = int.from_bytes(payload[member.header_offset + 26 : member.header_offset + 28], "little")
    extra_length = int.from_bytes(payload[member.header_offset + 28 : member.header_offset + 30], "little")
    data_offset = member.header_offset + 30 + name_length + extra_length
    payload[data_offset] = 0xFF
    archive.write_bytes(payload)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_SCRIPT),
            "--archive",
            str(archive),
            "--out-dir",
            str(tmp_path / "screenshots"),
            "--archive-out",
            str(tmp_path / "screenshots.zip"),
        ],
    )

    assert capture_screenshots.main() == 1
    manifest = json.loads((tmp_path / "screenshots" / "screenshot-manifest.json").read_text(encoding="utf-8"))
    assert manifest["failures"] == [
        {
            "stage": "capture",
            "message": "invalid ZIP member data: project/package.json",
        }
    ]


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_capture_rejects_archive_member_count_over_limit(tmp_path, monkeypatch, suffix: str) -> None:
    archive = tmp_path / f"too-many{suffix}"
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("first.txt", "first")
            zf.writestr("second.txt", "second")
    else:
        with tarfile.open(archive, "w") as tf:
            for name in ("first.txt", "second.txt"):
                data = name.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    monkeypatch.setattr(capture_screenshots, "MAX_ARCHIVE_MEMBERS", 1, raising=False)

    with pytest.raises(RuntimeError, match="member count"):
        capture_screenshots._extract_archive(archive, tmp_path / "extract")


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_capture_rejects_archive_expanded_size_over_limit(tmp_path, monkeypatch, suffix: str) -> None:
    archive = tmp_path / f"too-large{suffix}"
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("payload.bin", b"12345")
    else:
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo("payload.bin")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"12345"))

    monkeypatch.setattr(capture_screenshots, "MAX_ARCHIVE_EXPANDED_BYTES", 4, raising=False)

    with pytest.raises(RuntimeError, match="expanded size"):
        capture_screenshots._extract_archive(archive, tmp_path / "extract")


def test_runtime_image_pins_codex_0_147_0() -> None:
    dockerfile = RUNTIME_DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG CODEX_VERSION=0.147.0" in dockerfile
    assert "libreoffice-writer" not in dockerfile
    assert "poppler-utils" not in dockerfile
