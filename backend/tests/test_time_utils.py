from __future__ import annotations

from datetime import UTC, datetime

from app.utils import iso


def test_iso_renders_display_timezone_for_utc_datetime():
    assert iso(datetime(2026, 7, 4, 1, 30, 0, tzinfo=UTC)) == "2026-07-04T09:30:00+08:00"


def test_iso_treats_naive_datetime_as_utc():
    assert iso(datetime(2026, 7, 4, 1, 30, 0)) == "2026-07-04T09:30:00+08:00"
