"""The triggers of the diagnosis: the cards that wait for the owner, of the six kinds that get a diagnosis (ADR 0012).

Ported from factory-live's interventions at commit d47f235 (packages/domain/src/interventions.ts), for the six kinds
its diagnosis service explains. Titles, questions, severities, notifications and actions stay with factory-live.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TriggerKind = Literal["needs_input", "capability", "transient", "gave_up", "triage_loop", "held_active_pr"]


class Trigger(BaseModel):
    # Stable from one computation to the next: `<kind>:<card>:<event id, or opening time>`.
    id: str
    kind: TriggerKind
    card: str
    # The event told (the block, the give-up, the loop); `None` when none is.
    event: int | None
    # Unix seconds.
    opened_at: int | float
    # The agent the diagnosis examines: the told event's run's, else the card's last run's, else its assignee.
    agent: str | None
