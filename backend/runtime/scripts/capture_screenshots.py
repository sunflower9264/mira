#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a generated web app and capture browser screenshots.")
    parser.add_argument("--archive", required=True, help="Source code archive path (.zip/.tar/.tar.gz/.tgz).")
    parser.add_argument("--routes-json", default="", help="JSON array of routes or route objects.")
    parser.add_argument("--out-dir", default="screenshots", help="Directory for screenshots and metadata.")
    parser.add_argument("--archive-out", default="", help="Output zip path. Defaults to <out-dir>.zip.")
    parser.add_argument("--port", type=int, default=3210)
    parser.add_argument("--max-routes", type=int, default=10)
    parser.add_argument("--install-timeout", type=int, default=240)
    parser.add_argument("--startup-timeout", type=int, default=90)
    args = parser.parse_args()

    cwd = Path.cwd()
    archive = _resolve_path(cwd, args.archive)
    out_dir = _resolve_output_dir(cwd, args.out_dir)
    archive_out = _resolve_path(cwd, args.archive_out) if args.archive_out else out_dir.with_suffix(".zip")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "capture-log.txt"
    manifest_path = out_dir / "screenshot-manifest.json"

    log: list[str] = []
    manifest: dict[str, Any] = {
        "ok": False,
        "archive": str(archive),
        "project_root": "",
        "base_url": f"http://127.0.0.1:{args.port}",
        "screenshots": [],
        "failures": [],
        "warnings": [],
    }

    def note(message: str) -> None:
        log.append(message)
        print(message, flush=True)

    server: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="mira-capture-") as temp:
        temp_dir = Path(temp)
        try:
            if not archive.exists():
                raise RuntimeError(f"archive not found: {archive}")
            note(f"extracting {archive}")
            _extract_archive(archive, temp_dir)
            project_root = _find_project_root(temp_dir)
            manifest["project_root"] = str(project_root.relative_to(temp_dir))
            note(f"project root: {project_root}")

            routes = _route_items(args.routes_json, project_root, args.max_routes)
            if not routes:
                routes = [{"route": "/", "page_name": "首页"}]
            note("routes: " + ", ".join(item["route"] for item in routes))

            _run(
                ["npm", "install", "--no-audit", "--no-fund"],
                cwd=project_root,
                timeout=args.install_timeout,
                note=note,
                env=_node_env(project_root, args.port),
            )
            server = _start_dev_server(project_root, args.port, note)
            _wait_for_url(f"http://127.0.0.1:{args.port}/", args.startup_timeout, note)

            for index, item in enumerate(routes, start=1):
                _capture_route(index, item, args.port, out_dir, manifest, note)

            manifest["ok"] = bool(manifest["screenshots"])
            if not manifest["ok"]:
                manifest["warnings"].append("No screenshots were captured successfully.")
        except Exception as exc:  # noqa: BLE001
            manifest["failures"].append({"stage": "capture", "message": str(exc)})
            note(f"capture failed: {exc}")
        finally:
            if server is not None:
                _stop_process(server, note)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
            _zip_dir(out_dir, archive_out)
            note(f"wrote archive: {archive_out}")

    return 0


def _resolve_path(cwd: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (cwd / path).resolve()


def _resolve_output_dir(cwd: Path, value: str) -> Path:
    path = _resolve_path(cwd, value)
    if path == cwd or cwd not in path.parents:
        raise RuntimeError("out-dir must be inside the current workspace")
    return path


def _extract_archive(archive: Path, target: Path) -> None:
    suffixes = "".join(archive.suffixes).lower()
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                _ensure_safe_member(target, member.filename)
            zf.extractall(target)
        return
    if suffixes.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                _ensure_safe_member(target, member.name)
            tf.extractall(target)
        return
    raise RuntimeError(f"unsupported archive type: {archive.name}")


def _ensure_safe_member(root: Path, name: str) -> None:
    resolved = (root / name).resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"unsafe archive member path: {name}")


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


def _capture_route(index: int, item: dict[str, str], port: int, out_dir: Path, manifest: dict[str, Any], note) -> None:
    route = _normalize_route(item["route"])
    url = f"http://127.0.0.1:{port}{route}"
    filename = f"SP-{index:02d}-{_slug(route)}.png"
    output = out_dir / filename
    chromium_home = out_dir / ".chromium"
    chromium_home.mkdir(parents=True, exist_ok=True)
    command = [
        "chromium",
        "--headless=new",
        "--no-sandbox",
        "--disable-crash-reporter",
        "--disable-crashpad",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--window-size=1440,1000",
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
                "ref": f"SP-{index:02d}",
                "page_name": item["page_name"],
                "route": route,
                "path": str(output.relative_to(out_dir.parent)),
                "name": filename,
                "source": "chromium headless capture",
            }
        )
        note(f"captured {route} -> {output}")
        return
    manifest["failures"].append({"route": route, "message": (result.stdout or "").strip()[-1000:]})
    note(f"failed to capture {route}: {(result.stdout or '').strip()[-1000:]}")


def _slug(route: str) -> str:
    value = route.strip("/") or "home"
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return value[:80] or "page"


def _stop_process(process: subprocess.Popen[str], note) -> None:
    if process.poll() is not None:
        return
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


def _zip_dir(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source.parent))


if __name__ == "__main__":
    sys.exit(main())
