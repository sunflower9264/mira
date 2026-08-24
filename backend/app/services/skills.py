from __future__ import annotations

import asyncio
import hashlib
import shutil
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Skill
from app.schemas import SkillMarkdownOut
from app.utils import new_id

from .runtime_paths import skill_dependency_cache_dir, skills_data_dir
from .skill_dependencies import SkillDependencyError, build_dependency_layer
from .skills_install import SkillArchiveError, inspect_skill_archive


def _parse_front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def parse_skill_metadata(zip_path: Path, fallback_name: str, *, skill_root: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            skill_md = f"{skill_root}/SKILL.md" if skill_root else "SKILL.md"
            text = zf.read(skill_md).decode("utf-8", errors="strict")
            meta = _parse_front_matter(text)
            if meta.get("name"):
                return meta["name"], meta.get("description", "")
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise SkillArchiveError("SKILL.md 必须是有效的 UTF-8 文本") from exc
    return fallback_name, ""


async def save_archive(db: AsyncSession, archive: UploadFile) -> Skill:
    from app.services.admin import ADMIN_USER_ID

    filename = archive.filename or "skill.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="只接受 .zip 压缩包")
    content = await archive.read()
    max_size = get_settings().max_skill_size_bytes
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="Skill 压缩包不能超过 10 MB")
    skill_id = new_id("skill")
    folder = skills_data_dir(ADMIN_USER_ID) / skill_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "raw.zip"
    path.write_bytes(content)
    try:
        skill_root = inspect_skill_archive(path)
        fallback_name = Path(filename).stem
        name, description = parse_skill_metadata(path, fallback_name, skill_root=skill_root)
        layer = await asyncio.to_thread(
            build_dependency_layer,
            path,
            cache_root=skill_dependency_cache_dir(),
            skill_root=skill_root,
        )
    except SkillArchiveError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillDependencyError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    archive_md5 = hashlib.md5(content).hexdigest()
    skill = Skill(
        id=skill_id,
        owner_id=ADMIN_USER_ID,
        name=name,
        description=description,
        archive_name=filename,
        archive_size=len(content),
        archive_path=str(path.resolve()),
        archive_md5=archive_md5,
        skill_root=skill_root,
        dependency_status="ready" if layer is not None else "not_required",
        dependency_key=layer.cache_key if layer is not None else "",
        dependency_error="",
        enabled=True,
    )
    db.add(skill)
    try:
        await db.commit()
        await db.refresh(skill)
    except Exception:
        await db.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return skill


async def reconcile_skill_dependencies(db: AsyncSession) -> None:
    """Rebuild or reuse every stored Skill layer for the current runtime image."""

    from app.services.admin import ADMIN_USER_ID

    skills = list(
        (await db.execute(select(Skill).where(Skill.owner_id == ADMIN_USER_ID))).scalars().all()
    )
    for skill in skills:
        try:
            skill_root = inspect_skill_archive(Path(skill.archive_path))
            layer = await asyncio.to_thread(
                build_dependency_layer,
                Path(skill.archive_path),
                cache_root=skill_dependency_cache_dir(),
                skill_root=skill_root,
            )
        except (SkillArchiveError, SkillDependencyError) as exc:
            skill.dependency_status = "failed"
            skill.dependency_key = ""
            skill.dependency_error = str(exc)[-4_000:]
            skill.enabled = False
            skill.planning_enabled = False
            continue
        skill.skill_root = skill_root
        skill.dependency_status = "ready" if layer is not None else "not_required"
        skill.dependency_key = layer.cache_key if layer is not None else ""
        skill.dependency_error = ""
    await db.commit()


async def read_skill_markdown(db: AsyncSession, skill_id: str) -> SkillMarkdownOut:
    from app.services.admin import ADMIN_USER_ID

    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id, Skill.owner_id == ADMIN_USER_ID))
    ).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="未找到该 Skill")
    try:
        with zipfile.ZipFile(skill.archive_path) as zf:
            skill_md = f"{skill.skill_root}/SKILL.md" if skill.skill_root else "SKILL.md"
            content = zf.read(skill_md).decode("utf-8", errors="replace")
            return SkillMarkdownOut(path=skill_md, content=content)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到 SKILL.md")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 Skill zip")


async def delete_skill(db: AsyncSession, skill_id: str) -> None:
    from app.services.admin import ADMIN_USER_ID

    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id, Skill.owner_id == ADMIN_USER_ID))
    ).scalar_one_or_none()
    if not skill:
        return
    shutil.rmtree(Path(skill.archive_path).parent, ignore_errors=True)
    await db.delete(skill)
    await db.commit()
