from __future__ import annotations

import hashlib
import json
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

from .runtime_paths import skills_data_dir
from .serializers import skill_to_config


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


def parse_skill_metadata(zip_path: Path, fallback_name: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            skill_md = next((name for name in names if name.endswith("SKILL.md")), None)
            if skill_md:
                text = zf.read(skill_md).decode("utf-8", errors="ignore")
                meta = _parse_front_matter(text)
                if meta.get("name"):
                    return meta["name"], meta.get("description", "")
            for manifest in ("manifest.json", "package.json"):
                match = next((name for name in names if name.endswith(manifest)), None)
                if match:
                    data = json.loads(zf.read(match).decode("utf-8"))
                    if data.get("name"):
                        return data["name"], data.get("description", "")
    except Exception:
        return fallback_name, ""
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
    if not zipfile.is_zipfile(path):
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="无效的 Skill zip")
    fallback_name = Path(filename).stem
    name, description = parse_skill_metadata(path, fallback_name)
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
        enabled=True,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def read_skill_markdown(db: AsyncSession, skill_id: str) -> SkillMarkdownOut:
    from app.services.admin import ADMIN_USER_ID

    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id, Skill.owner_id == ADMIN_USER_ID))
    ).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="未找到该 Skill")
    try:
        with zipfile.ZipFile(skill.archive_path) as zf:
            skill_md = next((name for name in zf.namelist() if name.endswith("SKILL.md")), None)
            if not skill_md:
                raise HTTPException(status_code=404, detail="未找到 SKILL.md")
            content = zf.read(skill_md).decode("utf-8", errors="replace")
            return SkillMarkdownOut(path=skill_md, content=content)
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


async def skill_config_for_upload(db: AsyncSession, archive: UploadFile):
    return skill_to_config(await save_archive(db, archive))
