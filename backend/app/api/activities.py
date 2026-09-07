from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas.activities import AiActivityOut
from app.services.ai_activities import list_ai_activities

router = APIRouter(tags=["activities"])


@router.get("/ai-activities", response_model=list[AiActivityOut])
async def get_ai_activities(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AiActivityOut]:
    return await list_ai_activities(db, user.id)
