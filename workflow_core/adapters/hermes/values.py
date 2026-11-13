"""The small readers of loose values the adapter shares: a JSON or YAML document read from Hermès holds anything, and
each reader keeps only the shape it expects, else gives its empty value."""

from __future__ import annotations

from typing import Any


def as_record(value: Any) -> dict:
    """An object, else an empty one."""
    return value if isinstance(value, dict) else {}


def as_strings(value: Any) -> list[str]:
    """The strings of a list, else an empty list."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def as_text(value: Any) -> str | None:
    """A non-blank string, trimmed, else `None`."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def as_number(value: object) -> float:
    """A number that is not a boolean, as a float, else 0."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def dotted(value: Any, key: str) -> Any:
    """The value at a dotted path (`plugins.enabled`), else `None`."""
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value
