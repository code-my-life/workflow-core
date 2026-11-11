"""The core's ways to write the time: ISO 8601, UTC, to the second, or to the millisecond for the diagnosis."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def iso_ms(seconds: float) -> str:
    """Unix seconds as ISO 8601 UTC to the millisecond, `Z` suffix (`2026-10-29T09:47:00.000Z`), rounded half up to
    the millisecond as JavaScript's `toISOString` writes it."""
    millis = math.floor(seconds * 1000 + 0.5)
    moment = datetime.fromtimestamp(millis // 1000, UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{millis % 1000:03d}Z"
