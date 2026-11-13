"""The port through which the core writes to the runtime's boards (ADR 0001, amended), and its named error.

Only the runtime's adapter implements it; the engine sees nothing else. Every call names its board: a writer has no
current board (ADR 0003).
"""

from __future__ import annotations

from typing import Protocol


class BoardWriteError(Exception):
    """A write that failed: the command without its values, its exit code (None past its delay) and its error
    output."""

    def __init__(self, command: str, code: int | None, stderr: str) -> None:
        ending = "délai dépassé" if code is None else f"code {code}"
        output = stderr.strip() or "(sortie d'erreur vide)"
        super().__init__(f"écriture « {command} » en échec ({ending}) : {output}")
        self.command = command
        self.code = code
        self.stderr = stderr


class BoardWriter(Protocol):
    """Writes to the runtime's boards by name."""

    def has_board(self, board: str) -> bool:
        """Whether the board's base exists."""
        ...

    def create_board(self, board: str) -> None: ...

    def create_root_card(self, board: str, *, title: str, lane: str, item: str, issue: str | None) -> str:
        """Create the root card of an item, assigned to a lane, with the item's id as its idempotency key and its
        issue's address as its body; return the card's id."""
        ...

    def assign(self, board: str, card: str, lane: str) -> None:
        """Assign the card to another lane or profile."""
        ...
