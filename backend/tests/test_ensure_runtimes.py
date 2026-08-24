from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import ensure_runtimes


class _FakeImages:
    def __init__(self, image):  # noqa: ANN001
        self.image = image

    def get(self, _name: str):  # noqa: ANN201
        return self.image


class _FakeApi:
    def __init__(self) -> None:
        self.build_calls: list[dict] = []

    def build(self, **kwargs):  # noqa: ANN003, ANN201
        self.build_calls.append(kwargs)
        return []


class _FakeClient:
    def __init__(self, image) -> None:  # noqa: ANN001
        self.images = _FakeImages(image)
        self.api = _FakeApi()

    def ping(self) -> None:
        return None


def _image_with_digest(digest: str):  # noqa: ANN202
    return SimpleNamespace(attrs={"Config": {"Labels": {ensure_runtimes.RUNTIME_DEFINITION_LABEL: digest}}})


def test_runtime_definition_includes_browser_policy_files() -> None:
    relative_paths = {
        path.relative_to(ensure_runtimes.BACKEND_ROOT).as_posix()
        for path in ensure_runtimes._runtime_definition_files()
    }

    assert "runtime/playwright-cli.config.json" in relative_paths
    assert "runtime/scripts/mira-browser" in relative_paths


def test_current_runtime_image_is_not_rebuilt(monkeypatch) -> None:
    client = _FakeClient(_image_with_digest("current"))
    monkeypatch.setattr(ensure_runtimes, "_runtime_definition_digest", lambda: "current")
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))

    assert ensure_runtimes.main() == 0
    assert client.api.build_calls == []


def test_stale_runtime_image_is_rebuilt_with_definition_digest(monkeypatch) -> None:
    client = _FakeClient(_image_with_digest("old"))
    monkeypatch.setattr(ensure_runtimes, "_runtime_definition_digest", lambda: "current")
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))

    assert ensure_runtimes.main() == 0
    assert client.api.build_calls == [
        {
            "path": str(ensure_runtimes.BACKEND_ROOT),
            "dockerfile": "runtime/Dockerfile",
            "tag": ensure_runtimes.DEFAULT_IMAGE,
            "rm": True,
            "decode": True,
            "pull": True,
            "buildargs": {"RUNTIME_DEFINITION_SHA256": "current"},
        }
    ]
