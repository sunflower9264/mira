from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings


def now_utc() -> datetime:
    return datetime.now(UTC)


def display_now() -> datetime:
    return now_utc().astimezone(display_timezone())


def display_timezone() -> ZoneInfo:
    name = get_settings().display_timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(display_timezone()).isoformat()


def new_id(prefix: str | None = None) -> str:
    value = uuid.uuid4().hex
    return f"{prefix}_{value}" if prefix else value


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    return json.loads(raw)
