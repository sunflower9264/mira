from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import NlCompileOut
from app.schemas.requests import NlCompileIn, NlCompileRefineIn, NlCompileResumeIn
from app.services.apps import get_owned_app_or_404
from app.services.nlcompile import (
    active_compile_for_app,
    apply_compile,
    cancel_compile,
    compile_graph,
    refine_compile,
    resume_compile,
)

router = APIRouter(tags=["nlcompile"])


@router.post("/nlcompile", response_model=NlCompileOut)
async def nlcompile_endpoint(
    payload: NlCompileIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NlCompileOut:
    # 校验 app_id 归属当前用户；不存在或越权统一 404，与 apps 接口一致。
    await get_owned_app_or_404(db, payload.app_id, user.id)
    result = await compile_graph(db, user.id, payload.app_id, payload.instruction, payload.current_graph, payload.compile_id)
    return result


@router.get("/apps/{app_id}/nlcompile/active", response_model=NlCompileOut)
async def nlcompile_active_endpoint(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NlCompileOut | Response:
    await get_owned_app_or_404(db, app_id, user.id)
    result = await active_compile_for_app(db, user.id, app_id)
    if result is None:
        return Response(status_code=204)
    return result


@router.post("/nlcompile/{compile_id}/resume", response_model=NlCompileOut)
async def nlcompile_resume_endpoint(
    compile_id: str,
    payload: NlCompileResumeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NlCompileOut:
    return await resume_compile(db, user.id, compile_id, payload)


@router.post("/nlcompile/{compile_id}/refine", response_model=NlCompileOut)
async def nlcompile_refine_endpoint(
    compile_id: str,
    payload: NlCompileRefineIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NlCompileOut:
    return await refine_compile(db, user.id, compile_id, payload)


@router.post("/nlcompile/{compile_id}/apply", response_model=NlCompileOut)
async def nlcompile_apply_endpoint(
    compile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NlCompileOut:
    return await apply_compile(db, user.id, compile_id)


@router.post("/nlcompile/{compile_id}/cancel", status_code=204)
async def nlcompile_cancel_endpoint(
    compile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await cancel_compile(db, user.id, compile_id)
