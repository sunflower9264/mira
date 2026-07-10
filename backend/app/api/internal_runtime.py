from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header

from app.runtime.ask_user_bridge import handle_internal_ask_user

router = APIRouter(prefix="/internal/runtime", tags=["internal-runtime"])


@router.post("/ask-user/{session_id}")
async def ask_user_callback(
    session_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
):
    return await handle_internal_ask_user(session_id, authorization, payload)
