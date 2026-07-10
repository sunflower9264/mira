"""Ensure the Docker sandbox image used by Claude/Codex runtimes exists.

The backend no longer installs Claude/Codex CLIs on the host. Real Agent calls
run inside the image built from ``runtime/Dockerfile``. Build failures are
reported as warnings and do not block backend startup; Settings status checks
surface the actionable runtime error.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
DEFAULT_IMAGE = "mira-agent-runtime:latest"
RUNTIME_DEFINITION_LABEL = "mira.runtime.definition-sha256"


def _image_name() -> str:
    return os.environ.get("RUNTIME_SANDBOX_IMAGE", DEFAULT_IMAGE).strip() or DEFAULT_IMAGE


def _runtime_definition_files() -> tuple[Path, ...]:
    return (
        BACKEND_ROOT / "runtime" / "Dockerfile",
        BACKEND_ROOT / "scripts" / "ask_user_mcp_server.py",
        BACKEND_ROOT / "runtime" / "scripts" / "capture_screenshots.py",
    )


def _runtime_definition_digest() -> str:
    digest = hashlib.sha256()
    for path in _runtime_definition_files():
        relative = path.relative_to(BACKEND_ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _image_matches_definition(image, expected_digest: str) -> bool:  # noqa: ANN001
    labels = image.attrs.get("Config", {}).get("Labels") or {}
    return labels.get(RUNTIME_DEFINITION_LABEL) == expected_digest


def main() -> int:
    image = _image_name()
    dockerfile = BACKEND_ROOT / "runtime" / "Dockerfile"
    if not dockerfile.exists():
        print(f"[ensure-runtimes] runtime Dockerfile missing: {dockerfile}", file=sys.stderr)
        return 0
    try:
        import docker
    except ImportError:
        print("[ensure-runtimes] docker Python SDK not installed; cannot check runtime image.", file=sys.stderr)
        return 0
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"[ensure-runtimes] Docker daemon unavailable: {exc}", file=sys.stderr)
        return 0
    expected_digest = _runtime_definition_digest()
    try:
        existing_image = client.images.get(image)
    except Exception:
        existing_image = None
    if existing_image is not None and _image_matches_definition(existing_image, expected_digest):
        print(f"[ensure-runtimes] Runtime sandbox image {image} is current, skip.")
        return 0
    if existing_image is not None:
        print(f"[ensure-runtimes] Runtime sandbox image {image} is stale, rebuild.")
    print(f"[ensure-runtimes] Building runtime sandbox image {image} from {dockerfile}")
    try:
        for chunk in client.api.build(
            path=str(BACKEND_ROOT),
            dockerfile="runtime/Dockerfile",
            tag=image,
            rm=True,
            decode=True,
            pull=True,
            buildargs={"RUNTIME_DEFINITION_SHA256": expected_digest},
        ):
            text = chunk.get("stream") or chunk.get("error")
            if text:
                print(text, end="" if text.endswith("\n") else "\n")
            if chunk.get("error"):
                return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[ensure-runtimes] Runtime sandbox image build failed: {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
