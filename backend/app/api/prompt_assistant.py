from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import PromptAssistantGenerateOut
from app.schemas.requests import PromptAssistantGenerateIn, PromptAssistantResumeIn
from app.services.apps import get_owned_app_or_404
from app.services.prompt_assistant import (
    active_prompt_assistant_for_app,
    cancel_prompt_assistant,
    generate_prompt_assistant,
    resume_prompt_assistant,
)

router = APIRouter(tags=["prompt-assistant"])


@router.post("/prompt-assistant/generate", response_model=PromptAssistantGenerateOut)
async def prompt_assistant_generate_endpoint(
    payload: PromptAssistantGenerateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptAssistantGenerateOut:
    app = await get_owned_app_or_404(db, payload.app_id, user.id)
    return await generate_prompt_assistant(db, user.id, app, payload)


@router.post("/prompt-assistant/{generation_id}/cancel", status_code=204)
async def prompt_assistant_cancel_endpoint(
    generation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await cancel_prompt_assistant(db, user.id, generation_id)


@router.post("/prompt-assistant/{generation_id}/resume", response_model=PromptAssistantGenerateOut)
async def prompt_assistant_resume_endpoint(
    generation_id: str,
    payload: PromptAssistantResumeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptAssistantGenerateOut:
    return await resume_prompt_assistant(db, user.id, generation_id, payload)


@router.get("/apps/{app_id}/prompt-assistant/active", response_model=PromptAssistantGenerateOut)
async def prompt_assistant_active_endpoint(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptAssistantGenerateOut | Response:
    await get_owned_app_or_404(db, app_id, user.id)
    result = await active_prompt_assistant_for_app(db, user.id, app_id)
    if result is None:
        return Response(status_code=204)
    return result
