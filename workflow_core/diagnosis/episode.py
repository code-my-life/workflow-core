"""The episode of a card: the runs and events a diagnosis examines (ADR 0012).

Ported from factory-live's `DiagnosisContext` at commit d47f235 (packages/domain/src/diagnosis/context.ts): the runs
since the last reset (creation, unblocking, previous block or give-up, review asked or given, reassignment, forced
status, manual reclaim) up to the event being told, the block, the give-up or the loop, else the last failure.
"""

from __future__ import annotations

from workflow_core.model.facts import CardFacts, EventFact, RunFact

# What opens a new episode. The runtime sometimes restarts a blocked card without an "unblocked" event: the previous
# block is enough.
BOUNDARIES = frozenset(
    {
        "created",
        "unblocked",
        "blocked",
        "gave_up",
        "review_requested",
        "changes_requested",
        "completed",
        "promoted",
        "assigned",
        "status",
    }
)
# The events that tell a failure, when no event is named.
FAILURES = ("blocked", "gave_up", "block_loop_detected", "timed_out", "crashed")


def last_failure(events: list[EventFact]) -> EventFact | None:
    return next((event for event in reversed(events) if event.kind in FAILURES), None)


def profile_name(run: RunFact) -> str:
    # A run without a profile is shown as "?", as factory-live reads it.
    return (run.profile or "").strip() or "?"


class Episode:
    """The facts of one episode, ready for the rules. Times are Unix seconds."""

    def __init__(self, facts: CardFacts, now: float, anchor: EventFact | None = None) -> None:
        self.facts = facts
        self.card = facts.card
        events = sorted(facts.events, key=lambda event: event.id)
        # The event told: the one the primary cause must touch.
        self.anchor = anchor if anchor is not None else last_failure(events)
        self.until: float = self.anchor.created_at if self.anchor is not None else now
        start = self.card.created_at
        for event in events:
            if event.created_at > self.until:
                break
            if self.anchor is not None and event.id >= self.anchor.id:
                break
            if event.kind in BOUNDARIES or (event.kind == "reclaimed" and event.payload.get("manual") is True):
                start = max(start, event.created_at)
        self.start = start
        self.runs = sorted(
            (run for run in facts.runs if start - 1 <= run.started_at <= self.until), key=lambda run: run.id
        )
        self.events = [event for event in events if start <= event.created_at <= self.until]
        self.blocking_run_id = (
            self.anchor.run_id
            if self.anchor is not None and self.anchor.run_id is not None
            else (self.runs[-1].id if self.runs else None)
        )

    @property
    def profile(self) -> str | None:
        """The agent whose settings count: the last run's, else the card's assignee."""
        return profile_name(self.runs[-1]) if self.runs else self.card.assignee

    def run_of(self, run_id: int | None) -> RunFact | None:
        return next((run for run in self.runs if run.id == run_id), None) if run_id is not None else None

    def events_of(self, kind: str) -> list[EventFact]:
        return [event for event in self.events if event.kind == kind]
