from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Skill
from app.schemas import RuntimeSkillConfig

from .runtime_paths import claude_home, codex_home


MD5_MARKER = ".mira_skill_md5"


async def sync_global_skills(db: AsyncSession) -> None:
    """清理共享 fake HOME 的 Skills。

    Skills 是 App run 级 Tool，不能继续全局暴露给所有 Agent 调用。
    具体 run 会通过 ``sync_runtime_skills`` 同步到 scoped HOME。
    """
    _sync_skill_dir([], claude_home() / ".claude" / "skills")
    _sync_skill_dir([], codex_home() / ".agents" / "skills")


async def sync_user_skills(db: AsyncSession) -> None:
    """兼容历史调用点；实际同步的是全局 Skills。"""
    await sync_global_skills(db)


def _sync_skill_dir(skills: list[Skill], skills_dir: Path) -> None:
    active_ids = {skill.id for skill in skills}
    skills_dir.mkdir(parents=True, exist_ok=True)
    for child in skills_dir.iterdir():
        marker = child / MD5_MARKER
        if child.is_dir() and child.name not in active_ids and (marker.exists() or child.name.startswith("skill_")):
            shutil.rmtree(child, ignore_errors=True)
    for skill in skills:
        target = skills_dir / skill.id
        marker = target / MD5_MARKER
        if (
            skill.archive_md5
            and marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == skill.archive_md5
        ):
            continue
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(skill.archive_path) as zf:
            _safe_extract(zf, target)
        if skill.archive_md5:
            marker.write_text(skill.archive_md5, encoding="utf-8")


def sync_runtime_skills(skills: list[RuntimeSkillConfig], skills_dir: Path) -> None:
    _sync_skill_dir(skills, skills_dir)  # type: ignore[arg-type]


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for member in zf.infolist():
        destination = (target / member.filename).resolve()
        if destination != root and root not in destination.parents:
            continue
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as source, destination.open("wb") as dest:
            shutil.copyfileobj(source, dest)
