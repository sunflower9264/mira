from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptTemplate
from app.schemas import PromptTemplateOut
from app.utils import dumps, iso, loads, now_utc


PROMPT_SEED_DIR = Path(__file__).resolve().parents[2] / "seeds" / "prompts"
LEGACY_PROMPT_SEED_KEYS = {"prompt_helper"}


@dataclass(frozen=True)
class SeedPrompt:
    key: str
    name: str
    description: str
    content: str
    variables: list[str]


def render_prompt(content: str, variables: dict[str, object]) -> str:
    return Template(content).safe_substitute({key: str(value) for key, value in variables.items()})


def load_seed_prompts(seed_dir: Path | None = None) -> list[SeedPrompt]:
    seed_dir = seed_dir or PROMPT_SEED_DIR
    if not seed_dir.exists():
        return []
    return [_read_seed_prompt(path) for path in sorted(seed_dir.glob("*.md"))]


def _read_seed_prompt(path: Path) -> SeedPrompt:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    metadata: dict[str, object] = {}
    content = raw
    if raw.startswith("---\n"):
        _, frontmatter, body = raw.split("---\n", 2)
        metadata = _parse_frontmatter(frontmatter)
        content = body.lstrip("\n")
    key = str(metadata.get("key") or path.stem).strip()
    name = str(metadata.get("name") or key).strip()
    description = str(metadata.get("description") or "").strip()
    variables = metadata.get("variables")
    if not isinstance(variables, list):
        variables = []
    return SeedPrompt(
        key=key,
        name=name,
        description=description,
        content=content.rstrip("\n"),
        variables=[str(item).strip() for item in variables if str(item).strip()],
    )


def _parse_frontmatter(text: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list_key: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if current_list_key and stripped.startswith("- "):
            items = data.setdefault(current_list_key, [])
            if isinstance(items, list):
                items.append(stripped[2:].strip())
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "[]":
            data[key] = []
        elif value:
            data[key] = value
        else:
            data[key] = []
            current_list_key = key
    return data


def _seed_path_by_key(key: str, seed_dir: Path | None = None) -> Path:
    seed_dir = seed_dir or PROMPT_SEED_DIR
    if not seed_dir.exists():
        raise HTTPException(status_code=404, detail="未找到 Prompt seed 目录")
    for path in sorted(seed_dir.glob("*.md")):
        if _read_seed_prompt(path).key == key:
            return path
    raise HTTPException(status_code=404, detail="未找到该 Prompt seed")


def _write_seed_prompt_content(key: str, content: str, seed_dir: Path | None = None) -> None:
    path = _seed_path_by_key(key, seed_dir)
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise HTTPException(status_code=500, detail="Prompt seed 格式无效")
    parts = raw.split("---\n", 2)
    if len(parts) != 3 or parts[0] != "":
        raise HTTPException(status_code=500, detail="Prompt seed 格式无效")
    _, frontmatter, _body = parts
    next_content = content.rstrip("\n")
    path.write_text(f"---\n{frontmatter}---\n{next_content}\n", encoding="utf-8", newline="\n")


async def seed_prompt_templates(db: AsyncSession, *, commit: bool = True) -> None:
    seeds = load_seed_prompts()
    if not seeds:
        return
    existing_rows = {
        row.key: row
        for row in (await db.execute(select(PromptTemplate))).scalars().all()
    }
    for seed in seeds:
        row = existing_rows.get(seed.key)
        if row is not None:
            _apply_seed_prompt(row, seed)
            continue
        db.add(
            PromptTemplate(
                key=seed.key,
                name=seed.name,
                description=seed.description,
                content=seed.content,
                variables_json=dumps(seed.variables),
                updated_at=now_utc(),
            )
        )
    for key in LEGACY_PROMPT_SEED_KEYS:
        row = existing_rows.get(key)
        if row is not None:
            await db.delete(row)
    if commit:
        await db.commit()
    else:
        await db.flush()


def _apply_seed_prompt(row: PromptTemplate, seed: SeedPrompt) -> None:
    row.name = seed.name
    row.description = seed.description
    row.content = seed.content
    row.variables_json = dumps(seed.variables)
    row.updated_at = now_utc()


async def list_prompt_templates(db: AsyncSession) -> list[PromptTemplateOut]:
    rows = (await db.execute(select(PromptTemplate).order_by(PromptTemplate.key))).scalars().all()
    return [_prompt_out(row) for row in rows]


async def get_prompt_template(db: AsyncSession, key: str) -> PromptTemplateOut:
    row = await db.get(PromptTemplate, key)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该 Prompt")
    return _prompt_out(row)


async def get_prompt_content(db: AsyncSession, key: str) -> str:
    return (await get_prompt_template(db, key)).content


async def save_prompt_template(db: AsyncSession, key: str, content: str) -> PromptTemplateOut:
    row = await db.get(PromptTemplate, key)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该 Prompt")
    _write_seed_prompt_content(key, content)
    row.content = content
    row.updated_at = now_utc()
    await db.commit()
    await db.refresh(row)
    return _prompt_out(row)


def _prompt_out(row: PromptTemplate) -> PromptTemplateOut:
    return PromptTemplateOut(
        key=row.key,
        name=row.name,
        description=row.description,
        content=row.content,
        variables=loads(row.variables_json, []),
        updated_at=iso(row.updated_at),
    )
