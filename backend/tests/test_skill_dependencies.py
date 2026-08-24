from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import skill_dependencies
from app.services.skill_dependencies import SkillDependencyError


class _FakeContainer:
    def __init__(self, kwargs: dict, *, fail: bool = False) -> None:
        self.kwargs = kwargs
        self.fail = fail
        self.started = False
        self.killed = False
        self.removed = False

    def start(self) -> None:
        self.started = True
        if self.fail:
            return
        command = self.kwargs["command"]
        volumes = self.kwargs["volumes"]
        if "download" in command:
            wheels = _host_mount(volumes, "/wheels")
            (wheels / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
        elif "install" in command:
            output = _host_mount(volumes, "/out") / ".deps" / "demo"
            output.mkdir(parents=True)
            (output / "__init__.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
            metadata = output.parent / "demo-1.0.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
                encoding="utf-8",
            )

    def wait(self, *, timeout: int) -> dict[str, int]:
        assert timeout == skill_dependencies.BUILD_TIMEOUT_SECONDS
        return {"StatusCode": 1 if self.fail else 0}

    def logs(self, **_kwargs) -> bytes:
        return b"simulated pip failure"

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class _FakeContainers:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.created: list[_FakeContainer] = []
        self.fail_at = fail_at

    def create(self, **kwargs) -> _FakeContainer:
        container = _FakeContainer(kwargs, fail=len(self.created) + 1 == self.fail_at)
        self.created.append(container)
        return container


class _FakeImages:
    def __init__(self, image_id: str = "sha256:image-a") -> None:
        self.image_id = image_id

    def get(self, _name: str):  # noqa: ANN201
        return SimpleNamespace(
            id=self.image_id,
            attrs={"Id": self.image_id, "Os": "linux", "Architecture": "amd64"},
        )


class _FakeClient:
    def __init__(self, *, image_id: str = "sha256:image-a", fail_at: int | None = None) -> None:
        self.images = _FakeImages(image_id)
        self.containers = _FakeContainers(fail_at=fail_at)


def _host_mount(volumes: dict[str, dict[str, str]], container_path: str) -> Path:
    for host, mount in volumes.items():
        if mount["bind"] == container_path:
            return Path(host)
    raise AssertionError(f"missing mount: {container_path}")


def _skill_zip(path: Path, requirements: bytes, *, nested: bool = False, lock: bytes | None = None) -> None:
    root = "nested/" if nested else ""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{root}SKILL.md", "---\nname: demo\n---\n")
        archive.writestr(f"{root}requirements.txt", requirements)
        if lock is not None:
            archive.writestr(f"{root}requirements.lock", lock)


def test_build_uses_two_hardened_containers_and_publishes_verified_layer(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    _skill_zip(archive, b"demo>=1,<2\n", nested=True)
    client = _FakeClient()

    layer = skill_dependencies.build_dependency_layer(
        archive,
        cache_root=tmp_path / "cache",
        skill_root="nested",
        client=client,
    )

    assert layer is not None
    assert layer.deps_path == tmp_path / "cache" / layer.cache_key / ".deps"
    assert skill_dependencies.validate_dependency_layer(tmp_path / "cache", layer.cache_key) == layer.deps_path
    assert len(client.containers.created) == 2
    download, install = client.containers.created
    assert "download" in download.kwargs["command"]
    assert download.kwargs["network_disabled"] is False
    assert install.kwargs["network_disabled"] is True
    assert "install" in install.kwargs["command"]
    assert "--no-index" in install.kwargs["command"]
    assert "--only-binary=:all:" in download.kwargs["command"]
    for container in client.containers.created:
        kwargs = container.kwargs
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges:true"]
        assert kwargs["read_only"] is True
        assert kwargs["tmpfs"]["/tmp"].startswith("rw,noexec,nosuid,nodev")
        assert kwargs["user"] == f"{os.getuid()}:{os.getgid()}"
        assert kwargs["pids_limit"] > 0
        assert kwargs["mem_limit"]
        assert "extra_hosts" not in kwargs
        assert "/var/run/docker.sock" not in kwargs["volumes"]
        assert all(mount["bind"] not in {"/home/mira", "/workspace", "/mnt/inputs"} for mount in kwargs["volumes"].values())
        assert set(kwargs["environment"]) == {
            "HOME",
            "PIP_CONFIG_FILE",
            "PIP_DISABLE_PIP_VERSION_CHECK",
            "PYTHONDONTWRITEBYTECODE",
        }
        assert container.started and container.removed

    manifest = json.loads(layer.manifest_path.read_text(encoding="utf-8"))
    assert manifest["cache_key"] == layer.cache_key
    assert manifest["runtime_image"]["id"] == "sha256:image-a"
    assert manifest["requirements"]["sha256"]
    assert manifest["wheels"][0]["sha256"]
    assert manifest["packages"] == [{"name": "demo", "version": "1.0"}]
    assert manifest["tree_sha256"]


def test_requirements_lock_takes_precedence(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    _skill_zip(archive, b"ignored==1\n", lock=b"locked==2\n")

    requirements = skill_dependencies.read_skill_requirements(archive)

    assert requirements is not None
    assert requirements.filename == "requirements.lock"
    assert requirements.content == b"locked==2\n"


def test_cache_hit_does_not_start_new_containers(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    _skill_zip(archive, b"demo==1\n")
    cache_root = tmp_path / "cache"
    first_client = _FakeClient()
    first = skill_dependencies.build_dependency_layer(archive, cache_root=cache_root, client=first_client)
    second_client = _FakeClient()

    second = skill_dependencies.build_dependency_layer(archive, cache_root=cache_root, client=second_client)

    assert first is not None and second is not None
    assert second.cache_key == first.cache_key
    assert second_client.containers.created == []


def test_cache_key_changes_with_requirements_or_actual_image_id(tmp_path: Path) -> None:
    first_archive = tmp_path / "first.zip"
    second_archive = tmp_path / "second.zip"
    _skill_zip(first_archive, b"demo==1\n")
    _skill_zip(second_archive, b"demo==2\n")
    cache_root = tmp_path / "cache"

    first = skill_dependencies.build_dependency_layer(first_archive, cache_root=cache_root, client=_FakeClient())
    changed_requirements = skill_dependencies.build_dependency_layer(
        second_archive, cache_root=cache_root, client=_FakeClient()
    )
    changed_image = skill_dependencies.build_dependency_layer(
        first_archive,
        cache_root=cache_root,
        client=_FakeClient(image_id="sha256:image-b"),
    )

    assert first is not None and changed_requirements is not None and changed_image is not None
    assert len({first.cache_key, changed_requirements.cache_key, changed_image.cache_key}) == 3


def test_failed_build_removes_partial_cache_and_containers(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    _skill_zip(archive, b"demo==1\n")
    cache_root = tmp_path / "cache"
    client = _FakeClient(fail_at=2)

    with pytest.raises(SkillDependencyError, match="pip 容器退出码"):
        skill_dependencies.build_dependency_layer(archive, cache_root=cache_root, client=client)

    assert all(container.removed for container in client.containers.created)
    assert client.containers.created[-1].killed
    assert not [path for path in cache_root.iterdir() if path.is_dir() and path.name != ".locks"]


@pytest.mark.parametrize(
    "requirement",
    [
        b"https://example.com/demo.whl\n",
        b"demo @ https://example.com/demo.whl\n",
        b"git+https://example.com/demo.git\n",
        b"-e ./demo\n",
        b"-r more.txt\n",
        b"--index-url https://example.com/simple\n",
        b"demo==1 --index-url https://example.com/simple\n",
        b"../demo\n",
        b"/tmp/demo.whl\n",
        b"demo/pkg==1\n",
    ],
)
def test_rejects_url_vcs_options_and_local_paths(requirement: bytes) -> None:
    with pytest.raises(SkillDependencyError, match="不允许"):
        skill_dependencies.validate_requirements(requirement)


def test_accepts_hash_locked_requirements() -> None:
    skill_dependencies.validate_requirements(
        b"demo==1.0 \\\n    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )


def test_validate_layer_detects_same_size_tampering(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    _skill_zip(archive, b"demo==1\n")
    layer = skill_dependencies.build_dependency_layer(
        archive, cache_root=tmp_path / "cache", client=_FakeClient()
    )
    assert layer is not None
    package = layer.deps_path / "demo" / "__init__.py"
    original = package.read_bytes()
    package.write_bytes(b"x" * len(original))

    with pytest.raises(SkillDependencyError, match="被修改"):
        skill_dependencies.validate_dependency_layer(tmp_path / "cache", layer.cache_key)


def test_no_requirements_returns_none_without_docker(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as skill_zip:
        skill_zip.writestr("SKILL.md", "---\nname: demo\n---\n")
    archive.write_bytes(buffer.getvalue())

    assert skill_dependencies.build_dependency_layer(
        archive, cache_root=tmp_path / "cache", client=object()
    ) is None


def test_rejects_absolute_zip_member_even_when_name_would_match(tmp_path: Path) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as skill_zip:
        skill_zip.writestr("SKILL.md", "---\nname: demo\n---\n")
        skill_zip.writestr("/requirements.txt", "demo==1\n")

    with pytest.raises(SkillDependencyError, match="路径不安全"):
        skill_dependencies.read_skill_requirements(archive)
