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


class BoardUnavailableError(Exception):
    """A board whose base cannot be read: a named error, never an empty list."""

    def __init__(self, board: str, reason: str) -> None:
        super().__init__(f'board "{board}" cannot be read: {reason}')
        self.board = board


class BoardReader(Protocol):
    """Reads the cards of a board. Every call names its board: a reader has no current board."""

    def read_cards(self, board: str) -> list[Card]: ...
