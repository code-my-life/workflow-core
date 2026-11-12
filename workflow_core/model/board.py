"""A board's cards, as the core serves them, and the port through which an adapter reads them (ADR 0003)."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class Card(BaseModel):
    """One card of a board. Dates are Unix seconds, `None` when absent."""

    id: str
    title: str
    body: str | None
    status: str
    assignee: str | None
    created_at: int
    started_at: int | None
    completed_at: int | None
    block_kind: str | None


class Run(BaseModel):
    """One run of a card: who worked on it, how it ended. Dates are Unix seconds."""

    id: int
    profile: str | None
    status: str
    outcome: str | None
    started_at: int
    ended_at: int | None
    summary: str | None
    # The run's metadata, a JSON object (branch, pull request...); empty when absent or unreadable.
    metadata: dict[str, Any]


class CardEvent(BaseModel):
    """One event of a card's life (created, claimed, blocked...): its payload a JSON object, empty when absent or
    unreadable. Dates are Unix seconds."""

    id: int
    run_id: int | None
    kind: str
    payload: dict[str, Any]
    created_at: int


class Comment(BaseModel):
    id: int
    author: str
    body: str
    created_at: int


class Item(Card):
    """What a workflow moves forward, with its full state. At this stage, each card is its own item (ADR 0016)."""

    # The key the intake set (it ties the card to its issue when the title does not), and the card's working folder.
    idempotency_key: str | None
    workspace_path: str | None
    # The run in progress, and the last sign of life of its agent (Unix seconds).
    current_run_id: int | None
    last_heartbeat_at: int | None
    model_override: str | None
    provider_override: str | None
    skills: list[str]
    runs: list[Run]
    # The ids of the cards this one waits for, and of the cards waiting for it.
    parents: list[str]
    children: list[str]
    comments: list[Comment]
    # The card's events, by id.
    events: list[CardEvent]


class BoardUnavailableError(Exception):
    """A board whose base cannot be read: a named error, never an empty list."""

    def __init__(self, board: str, reason: str) -> None:
        super().__init__(f'board "{board}" cannot be read: {reason}')
        self.board = board


class BoardReader(Protocol):
    """Reads the cards of a board. Every call names its board: a reader has no current board."""

    def read_cards(self, board: str) -> list[Card]: ...

    def read_items(self, board: str) -> list[Item]: ...
