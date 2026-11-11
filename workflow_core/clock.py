"""The core's one way to write the time: ISO 8601, UTC, to the second."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
