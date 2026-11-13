"""The interventions of a board: each trigger of the diagnosis with its severity, notification, title, question and
actions (ADR 0012).

Ported from factory-live's `interventionsOf` at commit d47f235 (packages/domain/src/interventions.ts), for the six
kinds of triggers (workflow_core.diagnosis.triggers). The links to an issue, a pull request or a dashboard stay with
factory-live; the commands to copy are written by the runtime's adapter (`RuntimeTexts`); the agents they name come
from the workflow's entry (`default_agent`, `relay_agent`), and the gesture from its declared gestures.
"""

from __future__ import annotations

import math

from workflow_core.clock import iso_ms
from workflow_core.config import Workflow
from workflow_core.diagnosis.triggers import triggers_of
from workflow_core.model.facts import CardFacts, EventFact, RuntimeTexts
from workflow_core.model.interventions import (
    Action,
    CodedText,
    CopyAction,
    GestureAction,
    Intervention,
    InterventionText,
    QuotedText,
)
from workflow_core.model.triggers import Trigger

# The gesture that unblocks a card, when the workflow declares it (`/<key> unblock`).
UNBLOCK = "unblock"


def _code(name: str, **params) -> CodedText:
    return CodedText(code=name, params=params)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _last(events: list[EventFact], kind: str) -> EventFact | None:
    return next((event for event in reversed(events) if event.kind == kind), None)


def _first_line(reason: str, fallback: CodedText) -> InterventionText:
    line = reason.split("\n")[0].strip()
    return QuotedText(quote=line) if line else fallback


def _or_code(reason: str, fallback: CodedText) -> InterventionText:
    return QuotedText(quote=reason) if reason else fallback


def _js_round(value: float) -> int:
    """Math.round, as factory-live rounds: halves go up."""
    return math.floor(value + 0.5)


def build(trigger: Trigger, facts: CardFacts, workflow: Workflow, now: float, texts: RuntimeTexts) -> Intervention:
    """The intervention of one trigger of the workflow's board."""
    card = facts.card
    events = sorted(facts.events, key=lambda event: event.id)
    blocked = _last(events, "blocked")
    reason = _text(blocked.payload.get("reason")) if blocked is not None else ""
    declared = {gesture.name for gesture in workflow.gestures}
    unblock: list[Action] = [GestureAction(gesture=UNBLOCK)] if UNBLOCK in declared else []
    show = CopyAction(label=_code("intervention.action.showCard"), command=texts.show_command(workflow.board, card.id))

    kind = trigger.kind
    push = True
    if kind == "gave_up":
        gave_up = next((event for event in events if event.id == trigger.event), None)
        payload = gave_up.payload if gave_up is not None else {}
        failures = payload.get("failures")
        failures = int(failures) if isinstance(failures, int | float) and not isinstance(failures, bool) else 0
        severity = "critical"
        title: InterventionText = _code("intervention.title.gaveUp", failures=failures or None)
        error = _text(payload.get("error"))
        question: InterventionText = (
            QuotedText(quote=error or reason) if (error or reason) else _code("intervention.question.gaveUp")
        )
        actions: list[Action] = [*unblock, show]
    elif kind == "triage_loop":
        severity = "critical"
        title = _code("intervention.title.triageLoop")
        question = _code("intervention.question.triageLoop")
        actions = [show]
    elif kind == "held_active_pr":
        severity = "warning"
        title = _code("intervention.title.held", minutes=_js_round((now - trigger.opened_at) / 60))
        question = _code("intervention.question.held")
        owner = card.assignee or workflow.default_agent
        relay = workflow.relay_agent
        actions = (
            [
                CopyAction(
                    label=_code("intervention.action.relay", owner=owner),
                    command=texts.relay_command(workflow.board, card.id, relay, owner),
                )
            ]
            if owner and relay
            else []
        )
    elif card.block_kind == "capability":
        severity = "critical"
        title = _first_line(reason, _code("intervention.title.capability"))
        question = _or_code(reason, _code("intervention.question.capability"))
        actions = [*unblock, show]
    elif card.block_kind == "transient":
        severity = "warning"
        title = _first_line(reason, _code("intervention.title.transient"))
        question = _or_code(reason, _code("intervention.question.transient"))
        agent = workflow.default_agent
        reassign = (
            [
                CopyAction(
                    label=_code("intervention.action.reassignCoder"),
                    command=texts.reassign_unblock_command(workflow.board, card.id, agent),
                )
            ]
            if agent
            else []
        )
        actions = [*unblock, *reassign]
    elif card.block_kind == "needs_input":
        severity = "critical"
        title = _first_line(reason, _code("intervention.title.needsInput"))
        question = _or_code(reason, _code("intervention.question.needsInput"))
        actions = [*unblock, show]
    else:
        # A block without a type: visible, not notified, since it is no question of the agent.
        severity = "warning"
        push = False
        title = _first_line(reason, _code("intervention.title.blocked"))
        question = _or_code(reason, _code("intervention.question.blocked"))
        actions = [*unblock, show]

    return Intervention(
        id=trigger.id,
        kind=kind,
        item=card.id,
        event=trigger.event,
        opened_at=iso_ms(trigger.opened_at),
        agent=trigger.agent,
        severity=severity,
        push=push,
        title=title,
        question=question,
        actions=actions,
    )


def interventions_of(cards: list[CardFacts], workflow: Workflow, now: float, texts: RuntimeTexts) -> list[Intervention]:
    """The open interventions of a board's cards, by opening time."""
    by_card = {facts.card.id: facts for facts in cards}
    return [
        build(trigger, by_card[trigger.card], workflow, now, texts) for trigger in triggers_of(cards, workflow, now)
    ]
