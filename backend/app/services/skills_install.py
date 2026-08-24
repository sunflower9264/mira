from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.runtime.sandbox import CONTAINER_HOME, DockerBindMount
from app.schemas import RuntimeSkillConfig

from .runtime_paths import codex_home, skill_dependency_cache_dir


MD5_MARKER = ".mira_skill_md5"
MAX_SKILL_ARCHIVE_MEMBERS = 10_000
MAX_SKILL_ARCHIVE_EXPANDED_BYTES = 1 << 30


class SkillArchiveError(ValueError):
    pass


async def sync_global_skills(db: AsyncSession) -> None:
    """清理共享 fake HOME 的 Skills。

    Skills 是 App run 级 Tool，不能继续全局暴露给所有 Agent 调用。
    具体 run 会通过 ``sync_runtime_skills`` 同步到 scoped HOME。
    """
    _sync_skill_dir([], codex_home() / ".agents" / "skills")


async def sync_user_skills(db: AsyncSession) -> None:
    """兼容历史调用点；实际同步的是全局 Skills。"""
    await sync_global_skills(db)


def inspect_skill_archive(zip_path: Path) -> str:
    """Validate a Skill archive and return its canonical SKILL.md parent.

    The root Skill uses an empty string. Nested roots use normalized POSIX paths.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _, skill_root = _inspect_open_archive(zf)
    except SkillArchiveError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SkillArchiveError("无效的 Skill zip") from exc
    return skill_root


def sync_runtime_skills(
    skills: list[RuntimeSkillConfig],
    skills_dir: Path,
) -> tuple[DockerBindMount, ...]:
    _sync_skill_dir(skills, skills_dir)
    mounts: list[DockerBindMount] = []
    for skill in skills:
        skill_root = _validated_relative_root(skill.skill_root)
        target = skills_dir / skill.id
        if not _directory_chain_is_safe(target, skill_root):
            raise SkillArchiveError(f"Skill {skill.id} 的安装目录已被修改")
        mountpoint = target.joinpath(*skill_root.parts) / ".deps"
        # The runtime HOME is writable. Always clear a prior unmounted .deps so
        # only the managed read-only layer is visible inside the container.
        if mountpoint.exists():
            if mountpoint.is_symlink() or not mountpoint.is_dir():
                mountpoint.unlink()
            else:
                shutil.rmtree(mountpoint)
        mountpoint.mkdir(parents=True)
        if not skill.dependency_key:
            continue
        dependency_path = _validated_dependency_path(skill.dependency_key)
        container_target = (
            CONTAINER_HOME
            / ".agents"
            / "skills"
            / skill.id
            / Path(*skill_root.parts)
            / ".deps"
        )
        mounts.append(
            DockerBindMount(
                source=dependency_path,
                target=container_target,
                read_only=True,
            )
        )
    return tuple(mounts)


def _sync_skill_dir(skills: list[RuntimeSkillConfig], skills_dir: Path) -> None:
    active_ids = {skill.id for skill in skills}
    skills_dir.mkdir(parents=True, exist_ok=True)
    if skills_dir.is_symlink():
        raise SkillArchiveError("Skill 安装目录不能是符号链接")
    for child in skills_dir.iterdir():
        marker = child / MD5_MARKER
        if child.name not in active_ids and (marker.exists() or child.name.startswith("skill_")):
            _remove_path(child)
    for skill in skills:
        if not _safe_component(skill.id):
            raise SkillArchiveError("Skill id 非法")
        target = skills_dir / skill.id
        expected_root = _validated_relative_root(skill.skill_root).as_posix()
        if expected_root == ".":
            expected_root = ""
        if _installed_skill_matches(target, skill, expected_root):
            continue
        archive_path = Path(skill.archive_path)
        if skill.archive_md5 and _file_md5(archive_path) != skill.archive_md5:
            raise SkillArchiveError(f"Skill {skill.id} 压缩包完整性校验失败")
        temp = Path(tempfile.mkdtemp(prefix=f".{skill.id}-", dir=skills_dir))
        try:
            with zipfile.ZipFile(archive_path) as zf:
                members, actual_root = _inspect_open_archive(zf)
                if actual_root != expected_root:
                    raise SkillArchiveError(f"Skill {skill.id} 的 SKILL.md 根目录与记录不一致")
                _extract_members(zf, members, temp)
            marker_payload = {"archive_md5": skill.archive_md5, "skill_root": expected_root}
            (temp / MD5_MARKER).write_text(
                json.dumps(marker_payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            _remove_path(target)
            temp.replace(target)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise


def _installed_skill_matches(target: Path, skill: RuntimeSkillConfig, skill_root: str) -> bool:
    if target.is_symlink() or not target.is_dir():
        return False
    marker = target / MD5_MARKER
    if not skill.archive_md5 or marker.is_symlink() or not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload != {"archive_md5": skill.archive_md5, "skill_root": skill_root}:
        return False
    if _file_md5(Path(skill.archive_path)) != skill.archive_md5:
        raise SkillArchiveError(f"Skill {skill.id} 压缩包完整性校验失败")
    root_parts = PurePosixPath(skill_root)
    if not _directory_chain_is_safe(target, root_parts):
        return False
    root = target / Path(*root_parts.parts)
    skill_md = root / "SKILL.md"
    return not skill_md.is_symlink() and skill_md.is_file()


def _inspect_open_archive(zf: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath]], str]:
    infos = zf.infolist()
    if len(infos) > MAX_SKILL_ARCHIVE_MEMBERS:
        raise SkillArchiveError(
            f"Skill 压缩包成员数超过限制：{len(infos)} > {MAX_SKILL_ARCHIVE_MEMBERS}"
        )
    expanded_size = sum(info.file_size for info in infos if not info.is_dir())
    if expanded_size < 0 or expanded_size > MAX_SKILL_ARCHIVE_EXPANDED_BYTES:
        raise SkillArchiveError("Skill 压缩包展开大小超过限制")

    members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    skill_files: list[PurePosixPath] = []
    for info in infos:
        member_path = _normalized_member_path(info.filename)
        normalized = member_path.as_posix()
        if normalized in seen:
            raise SkillArchiveError(f"Skill 压缩包存在规范化重名成员：{normalized}")
        seen.add(normalized)
        _validate_member_type(info)
        members.append((info, member_path))
        if not info.is_dir() and member_path.name == "SKILL.md":
            skill_files.append(member_path)
        if not info.is_dir():
            try:
                with zf.open(info) as source:
                    while source.read(1024 * 1024):
                        pass
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise SkillArchiveError(f"Skill 压缩包成员损坏：{normalized}") from exc

    if len(skill_files) != 1:
        raise SkillArchiveError("Skill 压缩包必须且只能包含一个 SKILL.md")
    skill_root_path = skill_files[0].parent
    managed_deps = skill_root_path / ".deps"
    for _, member_path in members:
        if member_path == managed_deps or managed_deps in member_path.parents:
            raise SkillArchiveError("Skill 压缩包不能包含托管的 .deps 目录")
    skill_root = "" if skill_root_path == PurePosixPath(".") else skill_root_path.as_posix()
    return members, skill_root


def _extract_members(
    zf: zipfile.ZipFile,
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]],
    target: Path,
) -> None:
    root = target.resolve()
    for info, member_path in members:
        destination = (target / Path(*member_path.parts)).resolve()
        if destination != root and root not in destination.parents:
            raise SkillArchiveError(f"Skill 压缩包成员路径非法：{member_path.as_posix()}")
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(info) as source, destination.open("wb") as dest:
                shutil.copyfileobj(source, dest)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise SkillArchiveError(f"Skill 压缩包成员损坏：{member_path.as_posix()}") from exc


def _normalized_member_path(name: str) -> PurePosixPath:
    if not name or "\x00" in name:
        raise SkillArchiveError("Skill 压缩包成员路径非法")
    normalized_name = name.replace("\\", "/")
    path = PurePosixPath(normalized_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillArchiveError(f"Skill 压缩包成员路径非法：{name}")
    return path


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if info.is_dir():
        if kind not in {0, stat.S_IFDIR}:
            raise SkillArchiveError(f"Skill 压缩包成员类型非法：{info.filename}")
        return
    if kind not in {0, stat.S_IFREG}:
        raise SkillArchiveError(f"Skill 压缩包成员类型非法：{info.filename}")


def _validated_relative_root(value: str) -> PurePosixPath:
    if not value:
        return PurePosixPath(".")
    path = _normalized_member_path(value)
    if path.as_posix() != value:
        raise SkillArchiveError("Skill root 必须是规范化 POSIX 相对路径")
    if path.name in {"SKILL.md", ".deps"}:
        raise SkillArchiveError("Skill root 非法")
    return path


def _safe_component(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def _validated_dependency_path(dependency_key: str) -> Path:
    if len(dependency_key) != 64 or any(char not in "0123456789abcdef" for char in dependency_key):
        raise RuntimeError("Skill 依赖缓存 key 非法")
    from .skill_dependencies import validate_dependency_layer

    return validate_dependency_layer(skill_dependency_cache_dir(), dependency_key)


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SkillArchiveError(f"Skill 压缩包不可读：{path.name}") from exc
    return digest.hexdigest()


def _directory_chain_is_safe(target: Path, relative: PurePosixPath) -> bool:
    current = target
    if current.is_symlink() or not current.is_dir():
        return False
    for part in relative.parts:
        if part == ".":
            continue
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return False
    return True


def _remove_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
