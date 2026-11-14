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


class BoardWriteRefusedError(BoardWriteError):
    """A write the writer refused before running anything: the observer mode, or a runtime version not yet read
    again since the start (ADR 0019)."""

    def __init__(self, command: str, reason: str) -> None:
        Exception.__init__(self, f"écriture « {command} » refusée : {reason}")
        self.command = command
        self.code = None
        self.stderr = ""
        self.reason = reason


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

    def create_child_card(self, board: str, *, title: str, profile: str, key: str, body: str) -> str:
        """Create a child card of an item, assigned to an agent's profile, with its idempotency key
        `<item>/<station>/<passage>` and a body whose first line is the item's id; return the card's id. It is never
        linked to its root card in the runtime: no parent, no link."""
        ...

    def assign(self, board: str, card: str, lane: str) -> None:
        """Assign the card to another lane or profile."""
        ...

    def schedule(self, board: str, card: str, reason: str | None = None) -> None:
        """Park the card in `scheduled`, with its reason when given."""
        ...

    def unblock(self, board: str, card: str) -> None:
        """Return a blocked or scheduled card to `ready`."""
        ...

    def block(self, board: str, card: str, reason: str | None = None, *, kind: str | None = None) -> None:
        """Block the card, with its reason and its kind (`needs_input`, ...) when given."""
        ...

    def comment(self, board: str, card: str, text: str) -> None:
        """Append a comment to the card."""
        ...

    def complete(self, board: str, card: str, result: str) -> None:
        """Mark the card done, with its result."""
        ...
