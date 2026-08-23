from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    AgentConfigSaveIn,
    AgentSetupState,
    InstructionSaveIn,
    McpServerConfig,
    PromptTemplateSaveIn,
    SkillMarkdownOut,
    SkillUpdateIn,
)
from app.runtime.factory import get_runtime
from app.services import instructions, prompts, runtime_config, skills_install
from app.services.agent_config import (
    agent_setup_completed,
    read_config_file,
    validate_content,
    write_config_file,
)
from app.services.runtime_paths import runtime_dir
from app.services.serializers import skill_to_config
from app.services.settings import (
    add_mcp_server,
    delete_mcp_server,
    delete_skill_entry,
    normalize_supported_models,
    save_agent_config_metadata,
    settings_out,
    update_mcp_server,
    update_skill_enabled,
)
from app.services.skills import read_skill_markdown, save_archive
from app.utils import now_utc

router = APIRouter(tags=["settings"])

# GET 路由支持虚拟 id "codex-auth"，方便前端单独读取 auth.json 正文；
# PUT 路由不再接受 "codex-auth"，auth.json 保存合并到 codex 的 PUT
# （payload.auth_content），以便和 config.toml / enabled 一次性同步。
AgentConfigGetKind = Literal["claude-code", "codex", "codex-auth"]
AgentConfigPutKind = Literal["claude-code", "codex"]
InstructionProvider = Literal["claude-code", "codex"]
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


@router.get("/settings/agents/{agent_id}/config")
async def get_agent_config(
    agent_id: AgentConfigGetKind,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return await read_config_file(db, agent_id)


@router.get("/settings/agents/setup-state", response_model=AgentSetupState)
async def get_agent_setup_state(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    return AgentSetupState(completed=await agent_setup_completed(db))


@router.put("/settings/agents/{agent_id}/config")
async def put_agent_config(
    agent_id: AgentConfigPutKind,
    payload: AgentConfigSaveIn,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.auth_content is not None and agent_id != "codex":
        raise HTTPException(status_code=400, detail="auth_content 只能与 codex 配置一起保存")
    supported_models = normalize_supported_models(payload.supported_models)
    # 先 validate 再写入，避免 codex 配置合法 / auth.json 非法时出现半成功。
    if agent_id == "codex" and payload.auth_content is not None:
        validate_content("codex-auth", payload.auth_content)
    auth_saved = None
    settings = None
    try:
        await write_config_file(db, agent_id, payload.content, commit=False)
        if agent_id == "codex" and payload.auth_content is not None:
            auth_saved = await write_config_file(db, "codex-auth", payload.auth_content, commit=False)
        settings = await save_agent_config_metadata(
            db,
            agent_id,
            enabled=payload.enabled,
            supported_models=supported_models,
            commit=False,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await runtime_config.write_configs(db)
    saved = await read_config_file(db, agent_id)
    payload_out = saved.model_dump(mode="json")
    if auth_saved is not None:
        auth_saved = await read_config_file(db, "codex-auth")
    if auth_saved is not None:
        payload_out["auth"] = auth_saved.model_dump(mode="json")
    if settings is not None:
        settings = await settings_out(db, reveal_keys=True)
        payload_out["settings"] = settings.model_dump(mode="json")
    return payload_out


@router.post("/settings/agents/{agent_id}/status")
async def refresh_agent_status(
    agent_id: AgentConfigPutKind,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    # 先把 DB 中的配置正文同步成 runtime 文件，再做 CLI / 配置文件存在性检查；
    # CLI + 配置文件就绪后，无条件执行一次真实短调用，由 smoke 结果填 runnable。
    await runtime_config.write_configs(db)
    runtime_kind = "codex" if agent_id == "codex" else "claude"
    runtime = get_runtime(runtime_kind, admin.id)
    status = await runtime.detect_status()
    if not status.installed:
        return status.model_copy(update={"runnable": False})

    async def on_chunk(_chunk):
        return None

    cwd = runtime_dir() / "status_checks" / admin.id / runtime_kind
    cwd.mkdir(parents=True, exist_ok=True)
    smoke_prompt = await prompts.get_prompt_content(db, "status_smoke")
    try:
        result = await asyncio.wait_for(
            runtime.execute(
                prompt=smoke_prompt,
                session_id=None,
                allowed_tools=None,
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
                "error": "Agent 可用性检测超时",
                "checked_at": now_utc(),
            }
        )
    except Exception as exc:
        return status.model_copy(
            update={
                "runnable": False,
                "error": str(exc) or "Agent 可用性检测失败",
                "checked_at": now_utc(),
            }
        )
    if result.finished_with != "done":
        return status.model_copy(
            update={
                "runnable": False,
                "error": result.error or "Agent 可用性检测失败",
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


@router.get("/settings/instructions/{provider}")
async def get_instruction(
    provider: InstructionProvider,
    admin: User = Depends(get_current_admin),
):
    return instructions.read_instruction_file(provider)


@router.put("/settings/instructions/{provider}")
async def put_instruction(
    provider: InstructionProvider,
    payload: InstructionSaveIn,
    admin: User = Depends(get_current_admin),
):
    return instructions.write_instruction_file(provider, payload.content)


@router.post("/skills/parse")
async def parse_skill(
    archive: UploadFile,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    skill = await save_archive(db, archive)
    await skills_install.sync_global_skills(db)
    return skill_to_config(skill)
