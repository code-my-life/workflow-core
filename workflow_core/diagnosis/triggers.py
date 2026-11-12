"""Which cards get a diagnosis, on every declared board: the six kinds of interventions factory-live explains.

Ported from `interventionsOf` at commit d47f235 (packages/domain/src/interventions.ts), for the kinds its diagnosis
service diagnoses (`DIAGNOSED`, apps/server/src/application/diagnosis.ts), and from the service's choice of the agent
to examine (`compute`). The rules name no board and no agent: a workflow's own conventions come from its entry in
`config/workflows.toml`.
"""

from __future__ import annotations

import re

from workflow_core.config import Workflow
from workflow_core.diagnosis.episode import profile_name
from workflow_core.model.facts import CardFacts, EventFact
from workflow_core.model.triggers import Trigger, TriggerKind

# The events after which a card held by the active pull request guard counts again from zero.
RESET_HOLD = frozenset({"claimed", "unblocked", "promoted", "created", "status", "assigned"})
BLOCK_KINDS: frozenset[str] = frozenset({"needs_input", "capability", "transient"})


def _last(events: list[EventFact], kind: str) -> EventFact | None:
    return next((event for event in reversed(events) if event.kind == kind), None)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _held_since(events: list[EventFact]) -> int | None:
    since = None
    for event in events:
        if event.kind in RESET_HOLD:
            since = None
        elif event.kind == "respawn_guarded" and event.payload.get("reason") == "active_pr" and since is None:
            since = event.created_at
    return since


def ready_since(facts: CardFacts) -> int:
    """When a held card became ready: its last event that counts the hold again from zero, else its creation."""
    resets = [event.created_at for event in facts.events if event.kind in RESET_HOLD]
    return max(resets) if resets else facts.card.created_at


def _matches(pattern: str | None, text: str) -> bool:
    return pattern is not None and re.search(pattern, text, re.IGNORECASE) is not None


def _blocked(
    facts: CardFacts, events: list[EventFact], workflow: Workflow
) -> tuple[TriggerKind, EventFact | None, int] | None:
    card = facts.card
    blocked = _last(events, "blocked")
    gave_up = _last(events, "gave_up")
    # A card of the workflow's approval kind waits for a pull request to be approved, or reports a red main: neither
    # is diagnosed. This comes first, as in factory-live.
    if _matches(workflow.approval_card_title, card.title.strip()):
        if card.block_kind == "needs_input":
            return None
        if _matches(workflow.main_red_reason, _text(blocked.payload.get("reason")) if blocked else ""):
            return None
    if gave_up is not None and (blocked is None or gave_up.id > blocked.id):
        return "gave_up", gave_up, gave_up.created_at
    opened = blocked.created_at if blocked is not None else card.created_at
    if card.block_kind in BLOCK_KINDS:
        return card.block_kind, blocked, opened
    if card.block_kind == "dependency":
        return None
    # A block without a type counts as an awaited answer.
    return "needs_input", blocked, opened


def _agent(facts: CardFacts, event: EventFact | None) -> str | None:
    runs = sorted(facts.runs, key=lambda run: run.id)
    if event is not None and event.run_id:
        told = next((run for run in runs if run.id == event.run_id), None)
        if told is not None:
            return profile_name(told)
    return profile_name(runs[-1]) if runs else facts.card.assignee


def trigger_of(facts: CardFacts, workflow: Workflow, now: float) -> Trigger | None:
    """The trigger of one card, or `None` when the card waits for nothing the diagnosis explains."""
    card = facts.card
    events = sorted(facts.events, key=lambda event: event.id)
    found: tuple[TriggerKind, EventFact | None, int] | None = None
    if card.status == "blocked":
        found = _blocked(facts, events, workflow)
    elif card.status == "triage":
        loop = _last(events, "block_loop_detected")
        if loop is not None:
            found = ("triage_loop", loop, loop.created_at)
    elif card.status == "ready":
        since = _held_since(events)
        if since is not None and now - since >= workflow.held_after_minutes * 60:
            found = ("held_active_pr", None, since)
    if found is None:
        return None
    kind, event, opened = found
    return Trigger(
        id=f"{kind}:{card.id}:{event.id if event is not None else opened}",
        kind=kind,
        card=card.id,
        event=event.id if event is not None else None,
        opened_at=opened,
        agent=_agent(facts, event),
    )


def triggers_of(cards: list[CardFacts], workflow: Workflow, now: float) -> list[Trigger]:
    """The triggers of a board's cards, oldest first."""
    found = [trigger for facts in cards if (trigger := trigger_of(facts, workflow, now)) is not None]
    return sorted(found, key=lambda trigger: trigger.opened_at)
