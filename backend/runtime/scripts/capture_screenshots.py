#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from typing import Any


MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_EXPANDED_BYTES = 1024 * 1024 * 1024
CHROMIUM_BINARY = "/usr/bin/chromium"
URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+")
RUNTIME_PATH_REDACTIONS = (
    ("/home/mira", "<runtime-home>"),
    ("/usr/local", "<system>"),
    ("/opt/mira", "<mira-runtime>"),
    ("/var/tmp", "<tmp>"),
    ("/tmp", "<tmp>"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a generated web app and capture browser screenshots.")
    parser.add_argument("--archive", required=True, help="Source code archive path (.zip/.tar/.tar.gz/.tgz).")
    parser.add_argument("--routes-json", default="", help="JSON array of routes or route objects.")
    parser.add_argument("--out-dir", default="screenshots", help="Directory for screenshots and metadata.")
    parser.add_argument("--archive-out", default="", help="Output zip path. Defaults to <out-dir>.zip.")
    parser.add_argument("--port", type=int, default=3210)
    parser.add_argument("--max-routes", type=int, default=10)
    parser.add_argument("--min-screenshots", type=int, default=1)
    parser.add_argument(
        "--viewports-json",
        default="",
        help='JSON array such as [{"name":"desktop","width":1440,"height":1000}].',
    )
    parser.add_argument("--install-timeout", type=int, default=240)
    parser.add_argument("--startup-timeout", type=int, default=90)
    args = parser.parse_args()
    if args.min_screenshots < 1:
        parser.error("--min-screenshots must be at least 1")

    cwd = Path.cwd()
    archive = _resolve_path(cwd, args.archive)
    out_dir = _resolve_output_dir(cwd, args.out_dir)
    archive_out = _resolve_path(cwd, args.archive_out) if args.archive_out else out_dir.with_suffix(".zip")
    if archive_out == archive:
        raise RuntimeError("archive-out must differ from the source archive")
    archive_out.unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "capture-log.txt"
    manifest_path = out_dir / "screenshot-manifest.json"
    path_redactions = [(str(cwd.resolve()), "<workspace>")]
    if archive != cwd and cwd not in archive.parents:
        path_redactions.append((str(archive), "<archive>"))
    if archive_out != cwd and cwd not in archive_out.parents:
        path_redactions.append((str(archive_out), "<archive-out>"))

    log: list[str] = []
    manifest: dict[str, Any] = {
        "ok": False,
        "archive": _display_path(archive, cwd, placeholder="<archive>"),
        "project_root": "",
        "base_url": f"http://127.0.0.1:{args.port}",
        "screenshots": [],
        "failures": [],
        "warnings": [],
    }

    def note(message: str) -> None:
        safe_message = _redact_paths(message, path_redactions)
        log.append(safe_message)
        print(safe_message, flush=True)

    server: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="mira-capture-") as temp:
        temp_dir = Path(temp)
        path_redactions.append((str(temp_dir), "<runtime-temp>"))
        try:
            if not archive.exists():
                raise RuntimeError(f"archive not found: {archive}")
            note(f"extracting {archive}")
            _extract_archive(archive, temp_dir)
            project_root = _find_project_root(temp_dir)
            manifest["project_root"] = str(project_root.relative_to(temp_dir))
            note(f"project root: {manifest['project_root']}")

            routes = _route_items(args.routes_json, project_root, args.max_routes)
            if not routes:
                routes = [{"route": "/", "page_name": "首页"}]
            note("routes: " + ", ".join(item["route"] for item in routes))

            install_command = "ci" if (project_root / "package-lock.json").is_file() else "install"
            _run(
                ["npm", install_command, "--no-audit", "--no-fund"],
                cwd=project_root,
                timeout=args.install_timeout,
                note=note,
                env=_node_env(project_root, args.port),
            )
            package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts") if isinstance(package, dict) else None
            for script_name in ("db:init", "db:seed"):
                if isinstance(scripts, dict) and isinstance(scripts.get(script_name), str):
                    _run(
                        ["npm", "run", script_name],
                        cwd=project_root,
                        timeout=args.install_timeout,
                        note=note,
                        env=_node_env(project_root, args.port),
                    )
            server = _start_dev_server(project_root, args.port, note)
            _wait_for_url(f"http://127.0.0.1:{args.port}/", args.startup_timeout, note)

            viewports = _viewport_items(args.viewports_json)
            manifest["viewports"] = viewports
            for index, item in enumerate(routes, start=1):
                for viewport in viewports:
                    _capture_route(
                        index,
                        item,
                        viewport,
                        include_viewport_name=len(viewports) > 1,
                        port=args.port,
                        out_dir=out_dir,
                        manifest=manifest,
                        note=note,
                        path_redactions=path_redactions,
                    )

            screenshot_count = len(manifest["screenshots"])
            if screenshot_count < args.min_screenshots:
                message = f"captured {screenshot_count} screenshot(s); minimum required is {args.min_screenshots}"
                manifest["failures"].append({"stage": "minimum_screenshots", "message": message})
                note(message)
            manifest["ok"] = screenshot_count >= args.min_screenshots and not manifest["failures"]
        except Exception as exc:  # noqa: BLE001
            message = _redact_paths(str(exc), path_redactions)
            manifest["failures"].append({"stage": "capture", "message": message})
            note(f"capture failed: {message}")
        finally:
            if server is not None:
                _stop_process(server, note)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if manifest["ok"]:
                log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
                _zip_dir(out_dir, archive_out)
                note(f"wrote archive: {archive_out}")
            else:
                archive_out.unlink(missing_ok=True)
                note("capture failed; archive output was not written")
                log_path.write_text("\n".join(log) + "\n", encoding="utf-8")

    return 0 if manifest["ok"] else 1


def _resolve_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (cwd / path).resolve()


def _resolve_output_dir(cwd: Path, value: str) -> Path:
    path = _resolve_path(cwd, value)
    if path == cwd or cwd not in path.parents:
        raise RuntimeError("out-dir must be inside the current workspace")
    return path


def _display_path(path: Path, root: Path, *, placeholder: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return placeholder


def _redact_paths(message: str, redactions: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    offset = 0
    for match in URL_PATTERN.finditer(message):
        chunks.append(_redact_path_segment(message[offset : match.start()], redactions))
        chunks.append(match.group(0))
        offset = match.end()
    chunks.append(_redact_path_segment(message[offset:], redactions))
    return "".join(chunks)


def _redact_path_segment(message: str, redactions: list[tuple[str, str]]) -> str:
    safe_message = message
    for value, placeholder in sorted(redactions, key=lambda item: len(item[0]), reverse=True):
        if value and value != os.sep:
            safe_message = safe_message.replace(value, placeholder)
    for root, placeholder in RUNTIME_PATH_REDACTIONS:
        safe_message = re.sub(
            rf"(?<![\w/]){re.escape(root)}(?=/|$|[\s'\"`),.:;\]])",
            placeholder,
            safe_message,
        )
    return safe_message


def _extract_archive(archive: Path, target: Path) -> None:
    suffixes = "".join(archive.suffixes).lower()
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            _ensure_archive_limits(len(members), [member.file_size for member in members if not member.is_dir()])
            normalized_names: set[str] = set()
            validated_members: list[tuple[zipfile.ZipInfo, str]] = []
            for member in members:
                normalized_name = _ensure_safe_member(target, member.filename)
                if normalized_name in normalized_names:
                    raise RuntimeError(f"duplicate archive member path: {normalized_name}")
                normalized_names.add(normalized_name)
                _ensure_safe_zip_member_type(member)
                validated_members.append((member, normalized_name))
            for member, normalized_name in validated_members:
                destination = target / normalized_name
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                except (EOFError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
                    destination.unlink(missing_ok=True)
                    raise RuntimeError(f"invalid ZIP member data: {normalized_name}") from exc
        return
    if suffixes.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive) as tf:
            members = tf.getmembers()
            _ensure_archive_limits(len(members), [member.size for member in members if member.isfile()])
            for member in members:
                _ensure_safe_member(target, member.name)
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"unsafe archive member type: {member.name}")
            tf.extractall(target)
        return
    raise RuntimeError(f"unsupported archive type: {archive.name}")


def _ensure_safe_member(root: Path, name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized) or ".." in Path(normalized).parts:
        raise RuntimeError(f"unsafe archive member path: {name}")
    normalized = Path(normalized).as_posix()
    resolved = (root / normalized).resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"unsafe archive member path: {name}")
    return normalized


def _ensure_safe_zip_member_type(member: zipfile.ZipInfo) -> None:
    if member.is_dir():
        return
    file_type = stat.S_IFMT(member.external_attr >> 16)
    if file_type not in (0, stat.S_IFREG):
        raise RuntimeError(f"unsafe archive member type: {member.filename}")


def _ensure_archive_limits(member_count: int, expanded_sizes: list[int]) -> None:
    if member_count > MAX_ARCHIVE_MEMBERS:
        raise RuntimeError(f"archive member count exceeds limit: {member_count} > {MAX_ARCHIVE_MEMBERS}")
    if any(size < 0 for size in expanded_sizes):
        raise RuntimeError("archive expanded size is invalid")
    expanded_size = sum(expanded_sizes)
    if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
        raise RuntimeError(
            f"archive expanded size exceeds limit: {expanded_size} > {MAX_ARCHIVE_EXPANDED_BYTES}"
        )


def _find_project_root(root: Path) -> Path:
    candidates: list[Path] = []
    for package in root.rglob("package.json"):
        if "node_modules" in package.parts:
            continue
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except Exception:
            continue
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict) and isinstance(scripts.get("dev"), str):
            candidates.append(package.parent)
    if not candidates:
        raise RuntimeError("no package.json with a dev script found")
    return sorted(candidates, key=lambda path: len(path.parts))[0]


def _route_items(routes_json: str, project_root: Path, max_routes: int) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    if routes_json.strip():
        try:
            parsed = json.loads(routes_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"routes-json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("routes-json must be an array")
        for item in parsed:
            if isinstance(item, str):
                routes.append({"route": _normalize_route(item), "page_name": _route_label(item)})
            elif isinstance(item, dict):
                route = _normalize_route(str(item.get("route") or item.get("path") or ""))
                if route:
                    routes.append({"route": route, "page_name": str(item.get("page_name") or item.get("name") or _route_label(route))})
    routes.extend(_infer_routes(project_root))
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in routes:
        route = _normalize_route(item["route"])
        if not route or route in seen or "[" in route or "]" in route:
            continue
        seen.add(route)
        unique.append({"route": route, "page_name": item["page_name"] or _route_label(route)})
        if len(unique) >= max_routes:
            break
    return unique


def _infer_routes(project_root: Path) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    app_dir = project_root / "app"
    if app_dir.exists():
        for page in sorted(app_dir.rglob("page.*")):
            if "node_modules" in page.parts:
                continue
            rel = page.parent.relative_to(app_dir)
            route = "/" if str(rel) == "." else "/" + "/".join(rel.parts)
            routes.append({"route": route, "page_name": _route_label(route)})
    pages_dir = project_root / "pages"
    if pages_dir.exists():
        for page in sorted(pages_dir.rglob("*.*")):
            if page.name.startswith("_") or "api" in page.relative_to(pages_dir).parts:
                continue
            rel = page.relative_to(pages_dir).with_suffix("")
            route = "/" if rel.name == "index" and len(rel.parts) == 1 else "/" + "/".join(part for part in rel.parts if part != "index")
            routes.append({"route": route, "page_name": _route_label(route)})
    return routes


def _normalize_route(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        value = "/" + value.split("/", 3)[3] if "/" in value[8:] else "/"
    if not value.startswith("/"):
        value = "/" + value
    return re.sub(r"/+", "/", value)


def _route_label(route: str) -> str:
    route = _normalize_route(route)
    if route == "/":
        return "首页"
    return route.strip("/").replace("-", " ").replace("_", " ") or route


def _viewport_items(value: str) -> list[dict[str, Any]]:
    if not value.strip():
        return [{"name": "desktop", "width": 1440, "height": 1000}]
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"viewports-json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not 1 <= len(parsed) <= 4:
        raise RuntimeError("viewports-json must contain 1-4 viewport objects")
    viewports: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            raise RuntimeError("each viewport must be an object")
        name = str(item.get("name") or "").strip().lower()
        width = item.get("width")
        height = item.get("height")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name) or name in names:
            raise RuntimeError(f"viewport name is invalid or duplicated: {name or '(empty)'}")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not 320 <= width <= 3840
            or not isinstance(height, int)
            or isinstance(height, bool)
            or not 320 <= height <= 2160
        ):
            raise RuntimeError(f"viewport dimensions are invalid: {name}")
        names.add(name)
        viewports.append({"name": name, "width": width, "height": height})
    return viewports


def _node_env(project_root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PORT": str(port),
            "HOST": "127.0.0.1",
            "HOSTNAME": "127.0.0.1",
            "BROWSER": "none",
            "npm_config_cache": str(project_root / ".npm-cache"),
        }
    )
    return env


def _run(command: list[str], *, cwd: Path, timeout: int, note, env: dict[str, str] | None = None) -> None:
    note("$ " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.stdout:
        note(result.stdout[-4000:])
    if result.returncode != 0:
        raise RuntimeError(f"command failed with exit code {result.returncode}: {' '.join(command)}")


def _start_dev_server(project_root: Path, port: int, note) -> subprocess.Popen[str]:
    package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    dev = str(package.get("scripts", {}).get("dev") or "")
    if "next" in dev:
        args = ["npm", "run", "dev", "--", "-p", str(port), "-H", "127.0.0.1"]
    elif "vite" in dev:
        args = ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)]
    else:
        args = ["npm", "run", "dev"]
    env = _node_env(project_root, port)
    note("$ " + " ".join(args))
    return subprocess.Popen(
        args,
        cwd=project_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _wait_for_url(url: str, timeout: int, note) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    note(f"server ready: {url}")
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"server did not become ready at {url}: {last_error}")


def _check_route_url(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"route request failed: {url}: {exc}") from exc
    if status >= 400:
        raise RuntimeError(f"route returned HTTP {status}: {url}")
    return status


def _capture_route(
    index: int,
    item: dict[str, str],
    viewport: dict[str, Any],
    *,
    include_viewport_name: bool,
    port: int,
    out_dir: Path,
    manifest: dict[str, Any],
    note,
    path_redactions: list[tuple[str, str]],
) -> None:
    route = _normalize_route(item["route"])
    url = f"http://127.0.0.1:{port}{route}"
    viewport_name = str(viewport["name"])
    viewport_suffix = f"-{viewport_name}" if include_viewport_name else ""
    filename = f"SP-{index:02d}{viewport_suffix}-{_slug(route)}.png"
    output = out_dir / filename
    try:
        status = _check_route_url(url)
        note(f"route ready: {url} (HTTP {status})")
    except RuntimeError as exc:
        message = _redact_paths(str(exc), path_redactions)
        manifest["failures"].append({"route": route, "message": message})
        note(f"failed to capture {route}: {message}")
        return
    with tempfile.TemporaryDirectory(prefix="mira-chromium-") as chromium_temp:
        chromium_home = Path(chromium_temp)
        path_redactions.append((str(chromium_home), "<runtime-temp>"))
        command = [
            CHROMIUM_BINARY,
            "--headless=new",
            "--no-sandbox",
            "--disable-crash-reporter",
            "--disable-crashpad",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            f"--window-size={viewport['width']},{viewport['height']}",
            "--virtual-time-budget=3000",
            f"--user-data-dir={chromium_home}",
            f"--screenshot={output}",
            url,
        ]
        note("$ " + " ".join(command))
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(chromium_home),
                "XDG_CONFIG_HOME": str(chromium_home / "config"),
                "XDG_CACHE_HOME": str(chromium_home / "cache"),
            }
        )
        result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
        manifest["screenshots"].append(
            {
                "ref": f"SP-{index:02d}{viewport_suffix}",
                "page_name": item["page_name"],
                "route": route,
                "viewport": viewport,
                "path": str(output.relative_to(out_dir.parent)),
                "name": filename,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "source": "chromium headless capture",
            }
        )
        note(f"captured {route} -> {output}")
        return
    message = _redact_paths((result.stdout or "").strip()[-1000:], path_redactions)
    manifest["failures"].append({"route": route, "message": message})
    note(f"failed to capture {route}: {message}")


def _slug(route: str) -> str:
    value = route.strip("/") or "home"
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return value[:80] or "page"


def _stop_process(process: subprocess.Popen[str], note) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                process.kill()
            process.wait(timeout=5)
            return

    # npm may exit before its Next/Vite children finish graceful shutdown. Stop
    # any remaining writers before TemporaryDirectory removes the project tree.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _zip_dir(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() != ".png" and path.name not in {"screenshot-manifest.json", "capture-log.txt"}:
                continue
            zf.write(path, path.relative_to(source.parent))


if __name__ == "__main__":
    sys.exit(main())
