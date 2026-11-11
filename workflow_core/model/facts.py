"""The facts of one card that the diagnosis reads, and the port through which an adapter serves them (ADR 0012).

The rules of `workflow_core.diagnosis` only see these facts: what the runtime's bases hold, as plain values. What the
diagnosis writes out of the runtime in clear (the name of a setting, later configuration keys and commands to copy)
comes from the adapter too, in `RuntimeTerms`: the rules never name the runtime (ADR 0001).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class CardDetail(BaseModel):
    """A card with every field the diagnosis reads. Dates are Unix seconds, `None` when absent."""

    id: str
    title: str
    body: str | None
    status: str
    assignee: str | None
    created_at: int
    started_at: int | None
    completed_at: int | None
    block_kind: str | None
    current_run_id: int | None
    workspace_path: str | None
    skills: list[str]
    last_heartbeat_at: int | None
    branch_name: str | None
    consecutive_failures: int
    block_recurrences: int
    last_failure_error: str | None
    max_runtime_seconds: int | None
    idempotency_key: str | None
    model_override: str | None
    provider_override: str | None


class RunFact(BaseModel):
    """One run of the card, with its error text and its metadata (a JSON object, empty when unreadable)."""

    id: int
    profile: str | None
    status: str
    outcome: str | None
    started_at: int
    ended_at: int | None
    summary: str | None
    metadata: dict[str, Any]
    error: str | None


class EventFact(BaseModel):
    """One event of the card's life (created, blocked, timed out...), its payload a JSON object, empty when
    unreadable."""

    id: int
    run_id: int | None
    kind: str
    payload: dict[str, Any]
    created_at: int


class CardFacts(BaseModel):
    """A card, its runs and its events, read together in one short read transaction; runs and events by id."""

    card: CardDetail
    runs: list[RunFact]
    events: list[EventFact]


class RuntimeTerms(BaseModel):
    """The runtime's own words that a diagnosis quotes, given by the adapter."""

    # The card's setting that bounds the duration of a run.
    runtime_limit_key: str


class CardFactsReader(Protocol):
    """Reads the facts of one card of a named board. A card absent from the board is `None`; a board that cannot
    be read raises `BoardUnavailableError`."""

    terms: RuntimeTerms

    def read_card_facts(self, board: str, card: str) -> CardFacts | None: ...
