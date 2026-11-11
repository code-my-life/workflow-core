"""Read the cards of a Hermès board, read-only (ADR 0001, ADR 0003).

Each board has its SQLite base under `<Hermès home>/kanban/boards/<board>/kanban.db`. The base is opened with the
SQLite URI `mode=ro`: SQLite refuses every write on the connection, and the base file is never modified.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from workflow_core.model.board import BoardUnavailableError, Card

# The variable that locates Hermès' home; only this adapter reads it.
HOME_VARIABLE = "HERMES_HOME"
DEFAULT_HOME = "~/.hermes"

# The columns of Hermès' `tasks` table that the core reads.
CARD_COLUMNS = ("id", "title", "body", "status", "assignee", "created_at", "started_at", "completed_at", "block_kind")


def hermes_home(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get(HOME_VARIABLE) or DEFAULT_HOME).expanduser()


class HermesBoardReader:
    """Reads Hermès boards by name. It holds Hermès' home only: no current board, no open connection."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def base_path(self, board: str) -> Path:
        if not board or "/" in board or board in {".", ".."}:
            raise BoardUnavailableError(board, "not a board name")
        return self.home / "kanban" / "boards" / board / "kanban.db"

    @contextmanager
    def open_board(self, board: str) -> Iterator[sqlite3.Connection]:
        """A read-only connection to the named board's base, closed on exit."""
        path = self.base_path(board)
        if not path.is_file():
            raise BoardUnavailableError(board, f"no base at {path}")
        try:
            db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        except sqlite3.Error as error:
            raise BoardUnavailableError(board, str(error)) from error
        try:
            db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()

    def read_cards(self, board: str) -> list[Card]:
        with self.open_board(board) as db:
            try:
                rows = db.execute(f"SELECT {', '.join(CARD_COLUMNS)} FROM tasks ORDER BY created_at, id").fetchall()
            except sqlite3.Error as error:
                raise BoardUnavailableError(board, str(error)) from error
        return [Card(**dict(row)) for row in rows]


def make_board_reader(environ: dict[str, str] | None = None) -> HermesBoardReader:
    """The board reader of the Hermès home named by `HERMES_HOME`, `~/.hermes` by default."""
    return HermesBoardReader(hermes_home(environ))
