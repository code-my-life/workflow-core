"""The decision of a workflow's command (ADR 0005, Conventions of the architecture): what becomes of an item."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Decision(BaseModel):
    """`advance` moves the item to the station `to`, or to the next one without it; `wait` keeps it where it is;
    `fail` stops it with its reason."""

    decision: Literal["advance", "wait", "fail"]
    reason: str
    to: str | None = None

    @classmethod
    def fail(cls, reason: str) -> Decision:
        return cls(decision="fail", reason=reason)
