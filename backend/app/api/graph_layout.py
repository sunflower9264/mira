from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import GraphLayoutBeautifyOut
from app.schemas.requests import GraphLayoutBeautifyIn
from app.services.apps import get_owned_app_or_404
from app.services.graph_layout import beautify_graph_layout

router = APIRouter(tags=["graph-layout"])


@router.post("/graph-layout/beautify", response_model=GraphLayoutBeautifyOut)
async def graph_layout_beautify_endpoint(
    payload: GraphLayoutBeautifyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GraphLayoutBeautifyOut:
    await get_owned_app_or_404(db, payload.app_id, user.id)
    graph = await beautify_graph_layout(db, user.id, payload.graph, payload.node_sizes)
    return GraphLayoutBeautifyOut(graph=graph)
