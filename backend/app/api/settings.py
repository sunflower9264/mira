from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    CodexConfigSaveIn,
    CodexSetupState,
    InstructionSaveIn,
    McpServerConfig,
    PromptTemplateSaveIn,
    SkillMarkdownOut,
    SkillUpdateIn,
)
from app.runtime.factory import get_runtime
from app.services import instructions, prompts, runtime_config, skills_install
from app.services.codex_config import (
    codex_setup_completed,
    read_codex_config,
    save_codex_config,
)
from app.services.runtime_paths import runtime_dir
from app.services.serializers import skill_to_config
from app.services.settings import (
    add_mcp_server,
    delete_mcp_server,
    delete_skill_entry,
    normalize_supported_models,
    save_supported_models,
    settings_out,
    update_mcp_server,
    update_skill_enabled,
)
from app.services.skills import read_skill_markdown, save_archive
from app.utils import now_utc

router = APIRouter(tags=["settings"])

SMOKE_STATUS_TIMEOUT_SEC = 45


@router.get("/settings")
async def get_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 任意登录用户可读全局配置（StepTab / AppLaunchView 依赖此数据）。
    return await settings_out(db, reveal_keys=user.is_admin)


@router.patch("/settings/skills/{skill_id}")
async def patch_settings_skill(
    skill_id: str,
    payload: SkillUpdateIn,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    settings = await update_skill_enabled(
        db,
        skill_id,
        enabled=payload.enabled,
        planning_enabled=payload.planning_enabled,
    )
    await skills_install.sync_global_skills(db)
    return settings


@router.delete("/settings/skills/{skill_id}", status_code=204)
async def delete_settings_skill(
    skill_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await delete_skill_entry(db, skill_id)
    await skills_install.sync_global_skills(db)
    return None


@router.get("/settings/skills/{skill_id}/skill-md", response_model=SkillMarkdownOut)
async def get_settings_skill_markdown(
    skill_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await read_skill_markdown(db, skill_id)


@router.post("/settings/mcp")
async def post_settings_mcp(
    payload: McpServerConfig,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    settings = await add_mcp_server(db, payload)
    await runtime_config.write_configs(db)
    return settings


@router.put("/settings/mcp/{server_id}")
async def put_settings_mcp_item(
    server_id: str,
    payload: McpServerConfig,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    settings = await update_mcp_server(db, server_id, payload)
    await runtime_config.write_configs(db)
    return settings


@router.delete("/settings/mcp/{server_id}", status_code=204)
async def delete_settings_mcp_item(
    server_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await delete_mcp_server(db, server_id)
    await runtime_config.write_configs(db)
    return None


@router.get("/settings/codex/config")
async def get_codex_config(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await read_codex_config(db)


@router.get("/settings/codex/setup-state", response_model=CodexSetupState)
async def get_codex_setup_state(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return CodexSetupState(completed=await codex_setup_completed(db))


@router.put("/settings/codex/config")
async def put_codex_config(
    payload: CodexConfigSaveIn,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    supported_models = normalize_supported_models(payload.supported_models)
    try:
        await save_codex_config(db, payload.content, payload.auth_content, commit=False)
        await save_supported_models(
            db,
            supported_models,
            commit=False,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await runtime_config.write_configs(db)
    return {
        **(await read_codex_config(db)).model_dump(mode="json"),
        "settings": (await settings_out(db, reveal_keys=True)).model_dump(mode="json"),
    }


@router.post("/settings/codex/status")
async def refresh_codex_status(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    # 先把 DB 中的配置正文同步成 runtime 文件，再做 CLI / 配置文件存在性检查；
    # App Server + 配置文件就绪后，无条件执行一次真实短调用，由 smoke 结果填 runnable。
    await runtime_config.write_configs(db)
    runtime = get_runtime()
    status = await runtime.detect_status()
    if not status.installed:
        return status.model_copy(update={"runnable": False})

    async def on_chunk(_chunk):
        return None

    cwd = runtime_dir() / "status_checks" / admin.id / "codex"
    cwd.mkdir(parents=True, exist_ok=True)
    smoke_prompt = await prompts.get_prompt_content(db, "status_smoke")
    try:
        result = await asyncio.wait_for(
            runtime.execute(
                prompt=smoke_prompt,
                session_id=None,
                model=None,
                reasoning_effort="low",
                cwd=cwd,
                on_chunk=on_chunk,
                cancel_event=asyncio.Event(),
                runtime_policy="execute",
            ),
            timeout=SMOKE_STATUS_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return status.model_copy(
            update={
                "runnable": False,
                "error": "Codex 可用性检测超时",
                "checked_at": now_utc(),
            }
        )
    except Exception as exc:
        return status.model_copy(
            update={
                "runnable": False,
                "error": str(exc) or "Codex 可用性检测失败",
                "checked_at": now_utc(),
            }
        )
    if result.finished_with != "done":
        return status.model_copy(
            update={
                "runnable": False,
                "error": result.error or "Codex 可用性检测失败",
                "checked_at": now_utc(),
            }
        )
    return status.model_copy(update={"runnable": True, "error": None, "checked_at": now_utc()})


@router.get("/settings/prompts")
async def list_prompts(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await prompts.list_prompt_templates(db)


@router.put("/settings/prompts/{prompt_key}")
async def put_prompt(
    prompt_key: str,
    payload: PromptTemplateSaveIn,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await prompts.save_prompt_template(db, prompt_key, payload.content)


@router.get("/settings/instructions")
async def get_instruction(
    admin: User = Depends(get_current_admin),
):
    return instructions.read_instruction_file()


@router.put("/settings/instructions")
async def put_instruction(
    payload: InstructionSaveIn,
    admin: User = Depends(get_current_admin),
):
    return instructions.write_instruction_file(payload.content)


@router.post("/skills/parse")
async def parse_skill(
    archive: UploadFile,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    skill = await save_archive(db, archive)
    await skills_install.sync_global_skills(db)
    return skill_to_config(skill)
