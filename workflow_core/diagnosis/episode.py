"""The episode of a card: the runs, events, tool results and journal lines a diagnosis examines (ADR 0012).

Ported from factory-live's `DiagnosisContext` at commit d47f235 (packages/domain/src/diagnosis/context.ts): the runs
since the last reset (creation, unblocking, previous block or give-up, review asked or given, reassignment, forced
status, manual reclaim) up to the event being told, the block, the give-up or the loop, else the last failure; their
sessions' tool results classified, each with the call that produced it; and the journal of the same window.
"""

from __future__ import annotations

from dataclasses import dataclass

from workflow_core.config import Workflow
from workflow_core.model.facts import (
    DiagnosisInput,
    EventFact,
    LogFact,
    RunFact,
    RuntimeTexts,
    SessionFacts,
    ToolCallFact,
    ToolOutcome,
    ToolResultFact,
)

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
FAILED = frozenset({"crashed", "blocked", "gave_up", "timed_out", "rate_limited", "spawn_failed"})


def last_failure(events: list[EventFact]) -> EventFact | None:
    return next((event for event in reversed(events) if event.kind in FAILURES), None)


def profile_name(run: RunFact) -> str:
    # A run without a profile is shown as "?", as factory-live reads it.
    return (run.profile or "").strip() or "?"


@dataclass(frozen=True)
class Observation:
    """A classified tool result, with the call that produced it and the run where it happened."""

    run_id: int | None
    session_id: str
    result: ToolResultFact
    call: ToolCallFact | None
    outcome: ToolOutcome


def observations_of(sessions: list[SessionFacts], texts: RuntimeTexts, workflow: Workflow) -> list[Observation]:
    items: list[Observation] = []
    for session in sessions:
        # In time order, whatever the reading order: the diagnosis does not depend on it.
        calls = sorted(session.calls, key=lambda call: call.at)
        results = sorted(session.results, key=lambda result: result.at)
        by_id = {call.call_id: call for call in calls if call.call_id is not None}
        for result in results:
            # Without a call id, the latest call of the same tool before the result.
            call = by_id.get(result.call_id) if result.call_id else None
            if call is None:
                call = next(
                    (item for item in reversed(calls) if item.tool == result.tool and item.at <= result.at), None
                )
            outcome = texts.classify_tool_result(result.content, workflow.guard_suffixes, workflow.pr_acceptance)
            items.append(Observation(session.run_id, session.id, result, call, outcome))
    return sorted(items, key=lambda item: item.result.at)


class Episode:
    """The facts of one episode, ready for the rules. Times are Unix seconds."""

    def __init__(self, input: DiagnosisInput, texts: RuntimeTexts, workflow: Workflow) -> None:
        self.input = input
        self.texts = texts
        self.terms = texts.terms
        self.workflow = workflow
        self.card = input.card
        events = sorted(input.events, key=lambda event: event.id)
        # The event told: the one the primary cause must touch.
        self.anchor = input.anchor if input.anchor is not None else last_failure(events)
        self.until: float = self.anchor.created_at if self.anchor is not None else input.now
        start: float = self.card.created_at
        for event in events:
            if event.created_at > self.until:
                break
            if self.anchor is not None and event.id >= self.anchor.id:
                break
            if event.kind in BOUNDARIES or (event.kind == "reclaimed" and event.payload.get("manual") is True):
                start = max(start, event.created_at)
        self.start = start
        self.runs = sorted(
            (run for run in input.runs if start - 1 <= run.started_at <= self.until), key=lambda run: run.id
        )
        self.events = [event for event in input.events if start <= event.created_at <= self.until]
        self.blocking_run_id: int | None = (
            self.anchor.run_id
            if self.anchor is not None and self.anchor.run_id is not None
            else (self.runs[-1].id if self.runs else None)
        )
        run_ids = {run.id for run in self.runs}
        every = observations_of(input.sessions or [], texts, workflow)
        self.observations = [
            item
            for item in every
            if start - 1 <= item.result.at <= self.until + 1 and (item.run_id is None or item.run_id in run_ids)
        ]
        # Everything after the episode: it proves a cause fixed.
        self.later_observations = [item for item in every if item.result.at > self.until + 1]
        self.logs: list[LogFact] = sorted(
            (fact for fact in input.logs or [] if start - 1 <= fact.at <= self.until + 1), key=lambda fact: fact.at
        )

    @property
    def profile(self) -> str | None:
        """The agent whose settings count: the one of its configuration, else the last run's, else the assignee."""
        if self.input.profile is not None:
            return self.input.profile.profile
        return profile_name(self.runs[-1]) if self.runs else self.card.assignee

    @property
    def block_kind(self) -> str | None:
        """The type of the block told, as the runtime noted it on the event; the card's otherwise."""
        noted = self.anchor.payload.get("kind") if self.anchor is not None and self.anchor.kind == "blocked" else None
        return noted if isinstance(noted, str) and noted else self.card.block_kind

    def run_of(self, run_id: int | None) -> RunFact | None:
        return next((run for run in self.runs if run.id == run_id), None) if run_id is not None else None

    def observations_of_run(self, run_id: int) -> list[Observation]:
        return [item for item in self.observations if item.run_id == run_id]

    def failed_runs(self) -> list[RunFact]:
        return [run for run in self.runs if run.outcome is not None and run.outcome in FAILED]

    def events_of(self, kind: str) -> list[EventFact]:
        return [event for event in self.events if event.kind == kind]
