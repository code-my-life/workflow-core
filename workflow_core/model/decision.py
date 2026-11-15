"""The decision of a workflow's command (ADR 0005, Conventions of the architecture): what becomes of an item."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, model_validator

# An item's local id: no space, `/` nor `:`, since `<key>:<id>` is the idempotency key of its root card.
LOCAL_ID = re.compile(r"[^\s/:]+")


class Decision(BaseModel):
    """`advance` moves the item to the station `to`, or to the next one without it; `wait` keeps it where it is;
    `fail` stops it with its reason."""

    decision: Literal["advance", "wait", "fail"]
    reason: str
    to: str | None = None

    @classmethod
    def fail(cls, reason: str) -> Decision:
        return cls(decision="fail", reason=reason)


class IntakeDecision(BaseModel):
    """The decision of a workflow's intake command on an issue: `accept` makes it the item `<key>:<id>`, `refuse` leaves
    it, with its reason. Anything else is not a decision."""

    decision: Literal["accept", "refuse"]
    id: str | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _id_or_reason(self) -> IntakeDecision:
        if self.decision == "accept" and (self.id is None or not LOCAL_ID.fullmatch(self.id)):
            raise ValueError("an accepted issue needs an id without space, '/' nor ':'")
        if self.decision == "refuse" and not self.reason.strip():
            raise ValueError("a refused issue needs its reason")
        return self
