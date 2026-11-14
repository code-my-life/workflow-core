"""The write cycle of the contract test: every command the writer runs, done once on a throwaway board and read back
(ADR 0001, amended; ADR 0019).

The board is `contrat-<Unix time>`, never a board in service. Its cards are assigned to names that are not profiles,
so Hermès' dispatcher never launches them. After each step, the board is read again by the read-only reader and the
expected state is checked. The board is removed at the end, even after a failed step; a `contrat-*` board left by an
interrupted cycle is removed first.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable

from workflow_core.adapters.hermes.board import HermesBoardReader
from workflow_core.adapters.hermes.writer import HermesBoardWriter
from workflow_core.model.board import BoardUnavailableError, Item
from workflow_core.model.writer import BoardWriteError

THROWAWAY_PREFIX = "contrat-"
# Assignees that are not profiles: the dispatcher never launches their cards.
FIRST_ASSIGNEE = "contrat-premier"
SECOND_ASSIGNEE = "contrat-second"
KEY = "contrat:1"
BODY = "Carte du cycle d'écriture du test de contrat."
COMMENT = "Commentaire du cycle d'écriture."
RESULT = "cycle d'écriture tenu"


class WriteCycleError(Exception):
    """A step of the write cycle that failed: its command, or the state read back after it."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"cycle d'écriture, pas « {step} » : {detail}")
        self.step = step
        self.detail = detail


def _do[T](step: str, action: Callable[[], T]) -> T:
    try:
        return action()
    except BoardWriteError as error:
        raise WriteCycleError(step, str(error)) from error


def _items(reader: HermesBoardReader, board: str, step: str) -> list[Item]:
    try:
        return reader.read_items(board)
    except BoardUnavailableError as error:
        raise WriteCycleError(step, f"relecture impossible : {error}") from error


def _read(reader: HermesBoardReader, board: str, card: str, step: str) -> Item:
    found = next((item for item in _items(reader, board, step) if item.id == card), None)
    if found is None:
        raise WriteCycleError(step, f"carte {card} absente à la relecture")
    return found


def _expect(step: str, what: str, found: object, expected: object) -> None:
    if found != expected:
        raise WriteCycleError(step, f"{what} relu « {found} », attendu « {expected} »")


def _steps(writer: HermesBoardWriter, reader: HermesBoardReader, board: str) -> None:
    def create(step: str, **fields) -> str:
        return _do(step, lambda: writer.create_card(board, **fields))

    first = create("create", title="contrat : première carte", assignee=FIRST_ASSIGNEE, idempotency_key=KEY, body=BODY)
    card = _read(reader, board, first, "create")
    _expect("create", "statut", card.status, "ready")
    _expect("create", "assigné", card.assignee, FIRST_ASSIGNEE)
    _expect("create", "clé", card.idempotency_key, KEY)
    _expect("create", "corps", card.body, BODY)

    again = create(
        "create (même clé)", title="contrat : première carte", assignee=FIRST_ASSIGNEE, idempotency_key=KEY, body=BODY
    )
    _expect("create (même clé)", "carte", again, first)
    keyed = [item.id for item in _items(reader, board, "create (même clé)") if item.idempotency_key == KEY]
    _expect("create (même clé)", "cartes de la clé", keyed, [first])

    # The child card of an `agent` station relies on this: a card created under an unfinished parent waits in `todo`.
    child = create("create --parent", title="contrat : carte fille", assignee=SECOND_ASSIGNEE, parent=first)
    card = _read(reader, board, child, "create --parent")
    _expect("create --parent", "statut", card.status, "todo")
    _expect("create --parent", "parents", card.parents, [first])

    _do("assign", lambda: writer.assign(board, first, SECOND_ASSIGNEE))
    _expect("assign", "assigné", _read(reader, board, first, "assign").assignee, SECOND_ASSIGNEE)

    _do("schedule", lambda: writer.schedule(board, first))
    _expect("schedule", "statut", _read(reader, board, first, "schedule").status, "scheduled")

    _do("unblock", lambda: writer.unblock(board, first))
    _expect("unblock", "statut", _read(reader, board, first, "unblock").status, "ready")

    _do("block", lambda: writer.block(board, first, "needs_input"))
    card = _read(reader, board, first, "block")
    _expect("block", "statut", card.status, "blocked")
    _expect("block", "type de blocage", card.block_kind, "needs_input")

    _do("unblock (après block)", lambda: writer.unblock(board, first))
    _expect("unblock (après block)", "statut", _read(reader, board, first, "unblock (après block)").status, "ready")

    _do("comment", lambda: writer.comment(board, first, COMMENT))
    bodies = [comment.body for comment in _read(reader, board, first, "comment").comments]
    if COMMENT not in bodies:
        raise WriteCycleError("comment", f"commentaire absent à la relecture ({bodies})")

    _do("complete", lambda: writer.complete(board, first, RESULT))
    _expect("complete", "statut", _read(reader, board, first, "complete").status, "done")


def run_write_cycle(
    writer: HermesBoardWriter, reader: HermesBoardReader, *, now: Callable[[], float] = time.time
) -> str:
    """Run the write cycle on a new throwaway board, read back after each step, and remove the board; return its
    slug. A failed step raises `WriteCycleError`, after the board is removed."""
    for slug in _do("boards list", writer.list_boards):
        if slug.startswith(THROWAWAY_PREFIX):
            _do("boards rm (board laissé)", lambda slug=slug: writer.remove_board(slug))
    board = f"{THROWAWAY_PREFIX}{int(now())}"
    try:
        _do("boards create", lambda: writer.create_board(board))
        _steps(writer, reader, board)
    except BaseException:
        # A failed removal does not hide the failed step.
        with contextlib.suppress(BoardWriteError):
            writer.remove_board(board)
        raise
    _do("boards rm", lambda: writer.remove_board(board))
    if board in _do("boards list", writer.list_boards):
        raise WriteCycleError("boards rm", f"board {board} toujours listé")
    return board
