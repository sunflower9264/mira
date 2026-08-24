from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import get_settings


POLICY_VERSION = "skill-python-wheels-v1"
MANIFEST_VERSION = 1
PYTHON_ABI_POLICY = "cpython-3.12"
BUILD_TIMEOUT_SECONDS = 300
MAX_REQUIREMENTS_BYTES = 256 * 1024
MAX_DEPENDENCY_FILES = 100_000
MAX_DEPENDENCY_BYTES = 512 * 1024 * 1024
_HASH_OPTION_RE = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}\Z")
_PACKAGE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:\[[A-Za-z0-9,._-]+\])?"
    r"(?:\s*(?:(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)"
    r"(?:\s*,\s*(?:(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+))*)?"
    r"(?:\s*;\s*[A-Za-z0-9_.'\"()\s<>=!~-]+)?"
    r"\Z"
)


class SkillDependencyError(RuntimeError):
    """Raised when a Skill dependency layer cannot be built or verified."""


@dataclass(frozen=True)
class SkillRequirements:
    filename: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class SkillDependencyLayer:
    cache_key: str
    deps_path: Path
    manifest_path: Path


def read_skill_requirements(archive_path: Path, *, skill_root: str = "") -> SkillRequirements | None:
    """Read the dependency manifest adjacent to the canonical ``SKILL.md``.

    ``requirements.lock`` wins over ``requirements.txt``. Dependency manifests
    elsewhere in the archive are deliberately ignored.
    """

    root = _canonical_skill_root(skill_root)
    candidates = [root / "requirements.lock", root / "requirements.txt"]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            by_name: dict[str, list[zipfile.ZipInfo]] = {}
            for member in archive.infolist():
                normalized = _canonical_archive_name(member.filename)
                by_name.setdefault(normalized, []).append(member)
            for candidate in candidates:
                name = candidate.as_posix()
                matches = by_name.get(name, [])
                if len(matches) > 1:
                    raise SkillDependencyError(f"依赖清单存在规范化重名成员: {name}")
                if not matches:
                    continue
                member = matches[0]
                if member.is_dir():
                    raise SkillDependencyError(f"依赖清单不是普通文件: {name}")
                if member.file_size > MAX_REQUIREMENTS_BYTES:
                    raise SkillDependencyError("Skill 依赖清单不能超过 256 KiB")
                content = archive.read(member)
                validate_requirements(content)
                return SkillRequirements(
                    filename=member.filename,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
    except SkillDependencyError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SkillDependencyError(f"无法读取 Skill 依赖清单: {exc}") from exc
    return None


def validate_requirements(content: bytes) -> None:
    """Accept package-index requirements only; reject executable pip features."""

    if len(content) > MAX_REQUIREMENTS_BYTES:
        raise SkillDependencyError("Skill 依赖清单不能超过 256 KiB")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SkillDependencyError("Skill 依赖清单必须是 UTF-8") from exc
    if "\x00" in text or "\ufffd" in text:
        raise SkillDependencyError("Skill 依赖清单包含无效字符")

    continuing = False
    requirement_count = 0
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if continuing and _HASH_OPTION_RE.fullmatch(line.removesuffix("\\").strip()):
            continuing = line.endswith("\\")
            continue
        continuing = line.endswith("\\")
        if continuing:
            line = line[:-1].rstrip()
        if not line:
            raise SkillDependencyError(f"依赖清单第 {line_number} 行无效")
        lowered = line.lower()
        if (
            line.startswith("-")
            or "://" in line
            or " @ " in line
            or lowered.startswith(("git+", "hg+", "svn+", "bzr+", "file:"))
            or line.startswith((".", "/", "~", "\\"))
            or "/" in line
            or "\\" in line
            or "--" in line
            or re.match(r"^[A-Za-z]:", line)
        ):
            raise SkillDependencyError(
                f"依赖清单第 {line_number} 行包含不允许的 URL、路径或 pip 选项"
            )
        if not _PACKAGE_RE.match(line):
            raise SkillDependencyError(f"依赖清单第 {line_number} 行不是合法的包要求")
        requirement_count += 1
    if continuing:
        raise SkillDependencyError("依赖清单末尾存在未完成的续行")
    if requirement_count == 0:
        raise SkillDependencyError("Skill 依赖清单不能为空")


def build_dependency_layer(
    archive_path: Path,
    *,
    cache_root: Path,
    skill_root: str = "",
    client=None,  # noqa: ANN001 - docker SDK client is optional at import time
) -> SkillDependencyLayer | None:
    """Build or reuse an isolated, content-addressed Python dependency layer."""

    requirements = read_skill_requirements(archive_path, skill_root=skill_root)
    if requirements is None:
        return None
    docker_client, owns_client = _docker_client(client)
    try:
        image = docker_client.images.get(get_settings().runtime_sandbox_image)
        image_identity = _image_identity(image)
        cache_key = _cache_key(requirements, image_identity)
        cache_root = cache_root.resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        lock_dir = cache_root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{cache_key}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            cached = _try_cached_layer(cache_root, cache_key)
            if cached is not None:
                return cached
            return _build_locked(
                docker_client,
                requirements=requirements,
                image_identity=image_identity,
                cache_root=cache_root,
                cache_key=cache_key,
            )
    except SkillDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001 - Docker SDK has backend-specific exceptions
        raise SkillDependencyError(f"Skill 依赖构建失败: {exc}") from exc
    finally:
        if owns_client:
            try:
                docker_client.close()
            except Exception:  # noqa: BLE001
                pass


def validate_dependency_layer(cache_root: Path, cache_key: str) -> Path:
    """Fail closed unless a cache entry and its content manifest are intact."""

    if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
        raise SkillDependencyError("Skill 依赖缓存键无效")
    entry = cache_root.resolve() / cache_key
    deps_path = entry / ".deps"
    manifest_path = entry / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillDependencyError("Skill 依赖缓存 manifest 无效") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_VERSION
        or manifest.get("policy_version") != POLICY_VERSION
        or manifest.get("cache_key") != cache_key
        or not isinstance(manifest.get("packages"), list)
    ):
        raise SkillDependencyError("Skill 依赖缓存 manifest 与当前策略不匹配")
    scan = _scan_dependency_tree(deps_path)
    packages = _installed_packages(deps_path)
    if (
        manifest.get("file_count") != scan["file_count"]
        or manifest.get("total_bytes") != scan["total_bytes"]
        or manifest.get("tree_sha256") != scan["tree_sha256"]
        or manifest.get("packages") != packages
    ):
        raise SkillDependencyError("Skill 依赖缓存内容已损坏或被修改")
    return deps_path


def _build_locked(
    client,  # noqa: ANN001
    *,
    requirements: SkillRequirements,
    image_identity: dict[str, str],
    cache_root: Path,
    cache_key: str,
) -> SkillDependencyLayer:
    entry = cache_root / cache_key
    if entry.exists():
        shutil.rmtree(entry, ignore_errors=True)
    temp_path = Path(tempfile.mkdtemp(prefix=f".build-{cache_key[:12]}-", dir=cache_root))
    try:
        input_dir = temp_path / "input"
        wheels_dir = temp_path / "wheels"
        publish_dir = temp_path / "publish"
        input_dir.mkdir()
        wheels_dir.mkdir()
        publish_dir.mkdir()
        requirements_path = input_dir / "requirements.txt"
        requirements_path.write_bytes(requirements.content)

        _run_container(
            client,
            image=get_settings().runtime_sandbox_image,
            command=[
                "python",
                "-m",
                "pip",
                "download",
                "--isolated",
                "--disable-pip-version-check",
                "--only-binary=:all:",
                "--dest",
                "/wheels",
                "--requirement",
                "/input/requirements.txt",
            ],
            volumes={
                str(input_dir): {"bind": "/input", "mode": "ro"},
                str(wheels_dir): {"bind": "/wheels", "mode": "rw"},
            },
            network_disabled=False,
        )
        wheels = _wheel_manifest(wheels_dir)
        if not wheels:
            raise SkillDependencyError("pip download 未产生 wheel 文件")
        _run_container(
            client,
            image=get_settings().runtime_sandbox_image,
            command=[
                "python",
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--no-index",
                "--only-binary=:all:",
                "--find-links=/wheels",
                "--target",
                "/out/.deps",
                "--requirement",
                "/input/requirements.txt",
            ],
            volumes={
                str(input_dir): {"bind": "/input", "mode": "ro"},
                str(wheels_dir): {"bind": "/wheels", "mode": "ro"},
                str(publish_dir): {"bind": "/out", "mode": "rw"},
            },
            network_disabled=True,
        )
        scan = _scan_dependency_tree(publish_dir / ".deps")
        packages = _installed_packages(publish_dir / ".deps")
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "policy_version": POLICY_VERSION,
            "cache_key": cache_key,
            "python_abi_policy": PYTHON_ABI_POLICY,
            "runtime_image": image_identity,
            "requirements": {
                "filename": PurePosixPath(requirements.filename).name,
                "sha256": requirements.sha256,
            },
            "wheels": wheels,
            "packages": packages,
            **scan,
        }
        (publish_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(publish_dir, entry)
        return SkillDependencyLayer(
            cache_key=cache_key,
            deps_path=validate_dependency_layer(cache_root, cache_key),
            manifest_path=entry / "manifest.json",
        )
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def _run_container(
    client,  # noqa: ANN001
    *,
    image: str,
    command: list[str],
    volumes: dict[str, dict[str, str]],
    network_disabled: bool,
) -> None:
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "image": image,
        "command": command,
        "detach": True,
        "init": True,
        "working_dir": "/input",
        "user": _container_user(),
        "environment": {
            "HOME": "/tmp/home",
            "PIP_CONFIG_FILE": "/dev/null",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "volumes": volumes,
        "read_only": True,
        "tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=256m"},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": settings.runtime_container_memory,
        "pids_limit": settings.runtime_container_pids_limit,
        "network_disabled": network_disabled,
        "labels": {"mira.runtime": "skill-dependency-builder"},
    }
    if settings.runtime_container_cpus > 0:
        kwargs["nano_cpus"] = int(settings.runtime_container_cpus * 1_000_000_000)
    container = None
    completed = False
    try:
        container = client.containers.create(**kwargs)
        container.start()
        result = container.wait(timeout=BUILD_TIMEOUT_SECONDS)
        status = int(result.get("StatusCode") or 0)
        if status != 0:
            logs = container.logs(stdout=True, stderr=True, tail=100)
            if isinstance(logs, bytes):
                logs = logs.decode("utf-8", errors="replace")
            message = str(logs).strip()[-4_000:]
            raise SkillDependencyError(f"pip 容器退出码 {status}: {message}")
        completed = True
    except SkillDependencyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SkillDependencyError(f"pip 容器执行失败或超时: {exc}") from exc
    finally:
        if container is not None:
            if not completed:
                try:
                    container.kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001
                pass


def _try_cached_layer(cache_root: Path, cache_key: str) -> SkillDependencyLayer | None:
    try:
        deps_path = validate_dependency_layer(cache_root, cache_key)
    except SkillDependencyError:
        return None
    return SkillDependencyLayer(
        cache_key=cache_key,
        deps_path=deps_path,
        manifest_path=cache_root / cache_key / "manifest.json",
    )


def _scan_dependency_tree(root: Path) -> dict[str, int | str]:
    if not root.is_dir() or root.is_symlink():
        raise SkillDependencyError("Skill 依赖目录不存在或不是普通目录")
    count = 0
    total = 0
    digest = hashlib.sha256()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise SkillDependencyError(f"无法扫描 Skill 依赖目录: {exc}") from exc
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SkillDependencyError(f"无法读取 Skill 依赖文件: {relative}") from exc
            if stat.S_ISLNK(info.st_mode):
                raise SkillDependencyError(f"Skill 依赖层不允许链接: {relative}")
            if stat.S_ISDIR(info.st_mode):
                stack.append(Path(entry.path))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SkillDependencyError(f"Skill 依赖层包含特殊文件: {relative}")
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise SkillDependencyError(f"Skill 依赖层包含 setuid/setgid 文件: {relative}")
            count += 1
            total += info.st_size
            if count > MAX_DEPENDENCY_FILES:
                raise SkillDependencyError("Skill 依赖层文件数量超过限制")
            if total > MAX_DEPENDENCY_BYTES:
                raise SkillDependencyError("Skill 依赖层大小超过 512 MiB")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(info.st_size).encode("ascii"))
            digest.update(b"\0")
            with Path(entry.path).open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
    if count == 0:
        raise SkillDependencyError("Skill 依赖层为空")
    return {"file_count": count, "total_bytes": total, "tree_sha256": digest.hexdigest()}


def _wheel_manifest(wheels_dir: Path) -> list[dict[str, str | int]]:
    result: list[dict[str, str | int]] = []
    for path in sorted(wheels_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".whl":
            raise SkillDependencyError(f"pip download 产生了非 wheel 文件: {path.name}")
        size = path.stat().st_size
        if size > MAX_DEPENDENCY_BYTES:
            raise SkillDependencyError("单个 wheel 超过 512 MiB")
        result.append({"name": path.name, "size": size, "sha256": _file_sha256(path)})
    return result


def _installed_packages(deps_path: Path) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for metadata_path in sorted(deps_path.glob("*.dist-info/METADATA"), key=lambda path: path.as_posix()):
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise SkillDependencyError(f"安装包 metadata 类型非法: {metadata_path.name}")
        if metadata_path.stat().st_size > 4 * 1024 * 1024:
            raise SkillDependencyError(f"安装包 metadata 过大: {metadata_path.parent.name}")
        try:
            metadata = BytesParser(policy=compat32).parsebytes(metadata_path.read_bytes(), headersonly=True)
        except (OSError, ValueError) as exc:
            raise SkillDependencyError(f"无法读取安装包 metadata: {metadata_path.parent.name}") from exc
        name = metadata.get("Name", "").strip()
        version = metadata.get("Version", "").strip()
        if not name or not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name) or "\n" in version:
            raise SkillDependencyError(f"安装包 metadata 缺少合法 Name/Version: {metadata_path.parent.name}")
        packages.append({"name": name, "version": version})
    if not packages:
        raise SkillDependencyError("Skill 依赖层没有可识别的安装包 metadata")
    return packages


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_key(requirements: SkillRequirements, image_identity: dict[str, str]) -> str:
    payload = {
        "policy_version": POLICY_VERSION,
        "python_abi_policy": PYTHON_ABI_POLICY,
        "runtime_image": image_identity,
        "requirements_sha256": requirements.sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _image_identity(image) -> dict[str, str]:  # noqa: ANN001
    attrs = getattr(image, "attrs", {}) or {}
    image_id = getattr(image, "id", "") or attrs.get("Id", "")
    os_name = attrs.get("Os", "")
    architecture = attrs.get("Architecture", "")
    if not all(isinstance(value, str) and value for value in (image_id, os_name, architecture)):
        raise SkillDependencyError("无法确定 runtime 镜像的 ID、OS 或架构")
    return {"id": image_id, "os": os_name, "architecture": architecture}


def _docker_client(client):  # noqa: ANN001, ANN202
    if client is not None:
        return client, False
    try:
        import docker
    except ImportError as exc:
        raise SkillDependencyError("缺少 docker Python SDK") from exc
    try:
        return docker.from_env(), True
    except Exception as exc:  # noqa: BLE001
        raise SkillDependencyError(f"Docker daemon 不可用: {exc}") from exc


def _container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"


def _canonical_skill_root(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized) if normalized else PurePosixPath()
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillDependencyError("canonical Skill 根路径无效")
    return path


def _canonical_archive_name(value: str) -> str:
    if not value or value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise SkillDependencyError(f"Skill ZIP 成员路径不安全: {value}")
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillDependencyError(f"Skill ZIP 成员路径不安全: {value}")
    return path.as_posix()
