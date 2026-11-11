"""A board's cards, as the core serves them, and the port through which an adapter reads them (ADR 0003)."""

from __future__ import annotations

from typing import Protocol

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


class Comment(BaseModel):
    id: int
    author: str
    body: str
    created_at: int


class Item(Card):
    """What a workflow moves forward, with its full state. At this stage, each card is its own item (ADR 0016)."""

    runs: list[Run]
    # The ids of the cards this one waits for, and of the cards waiting for it.
    parents: list[str]
    children: list[str]
    comments: list[Comment]


class BoardUnavailableError(Exception):
    """A board whose base cannot be read: a named error, never an empty list."""

    def __init__(self, board: str, reason: str) -> None:
        super().__init__(f'board "{board}" cannot be read: {reason}')
        self.board = board


class BoardReader(Protocol):
    """Reads the cards of a board. Every call names its board: a reader has no current board."""

    def read_cards(self, board: str) -> list[Card]: ...

    def read_items(self, board: str) -> list[Item]: ...
