"""Persistent run event storage used by SSE replay and recovery."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunEvent
from app.utils import dumps, loads, now_utc


async def append_run_event(db: AsyncSession, run_id: str, event: str, data: dict[str, Any]) -> RunEvent:
    row = RunEvent(
        run_id=run_id,
        event=event,
        data_json=dumps(data),
        created_at=now_utc(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def iter_run_events(
    db: AsyncSession,
    run_id: str,
    *,
    after_id: int | None = None,
) -> AsyncIterator[RunEvent]:
    query = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id.asc())
    if after_id is not None:
        query = query.where(RunEvent.id > after_id)
    rows = (await db.execute(query)).scalars().all()
    for row in rows:
        yield row


def event_to_sse_frame(
    row: RunEvent,
    transform: Callable[[int, str, dict[str, Any]], tuple[str, dict[str, Any]] | None] | None = None,
) -> str | None:
    payload = loads(row.data_json, {}) or {}
    event = row.event
    if transform:
        transformed = transform(row.id, row.event, payload)
        if transformed is None:
            return None
        event, payload = transformed
    return f"id: {row.id}\nevent: {event}\ndata: {dumps(payload)}\n\n"
