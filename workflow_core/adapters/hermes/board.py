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

from workflow_core.model.board import BoardUnavailableError, Card, Item

# The variable that locates Hermès' home; only this adapter reads it.
HOME_VARIABLE = "HERMES_HOME"
DEFAULT_HOME = "~/.hermes"

# The columns of Hermès' tables that the core reads. The contract test checks them (contract.py).
CARD_COLUMNS = ("id", "title", "body", "status", "assignee", "created_at", "started_at", "completed_at", "block_kind")
RUN_COLUMNS = ("id", "task_id", "profile", "status", "outcome", "started_at", "ended_at", "summary")
LINK_COLUMNS = ("parent_id", "child_id")
COMMENT_COLUMNS = ("id", "task_id", "author", "body", "created_at")


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
            # Autocommit mode: a read transaction is opened only where asked, with BEGIN, and closed at once.
            db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, isolation_level=None)
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

    def read_items(self, board: str) -> list[Item]:
        """The cards of the board with their runs, links and comments, read in one short read transaction."""
        with self.open_board(board) as db:
            try:
                db.execute("BEGIN")
                try:
                    cards = db.execute(
                        f"SELECT {', '.join(CARD_COLUMNS)} FROM tasks ORDER BY created_at, id"
                    ).fetchall()
                    runs = db.execute(f"SELECT {', '.join(RUN_COLUMNS)} FROM task_runs ORDER BY id").fetchall()
                    links = db.execute(f"SELECT {', '.join(LINK_COLUMNS)} FROM task_links ORDER BY 1, 2").fetchall()
                    comments = db.execute(
                        f"SELECT {', '.join(COMMENT_COLUMNS)} FROM task_comments ORDER BY id"
                    ).fetchall()
                finally:
                    db.execute("COMMIT")
            except sqlite3.Error as error:
                raise BoardUnavailableError(board, str(error)) from error

        by_card: dict[str, dict[str, list]] = {
            row["id"]: {"runs": [], "parents": [], "children": [], "comments": []} for row in cards
        }
        for row in runs:
            if row["task_id"] in by_card:
                by_card[row["task_id"]]["runs"].append({k: row[k] for k in RUN_COLUMNS if k != "task_id"})
        for row in links:
            if row["child_id"] in by_card:
                by_card[row["child_id"]]["parents"].append(row["parent_id"])
            if row["parent_id"] in by_card:
                by_card[row["parent_id"]]["children"].append(row["child_id"])
        for row in comments:
            if row["task_id"] in by_card:
                by_card[row["task_id"]]["comments"].append({k: row[k] for k in COMMENT_COLUMNS if k != "task_id"})
        return [Item(**dict(row), **by_card[row["id"]]) for row in cards]


class HermesBoardWatcher:
    """Watches Hermès boards for writes, cheaply enough to be asked every 300 ms.

    It keeps one read-only connection per board and reads `PRAGMA data_version`, a counter SQLite moves when another
    connection commits to the base. Every call names its board.
    """

    def __init__(self, reader: HermesBoardReader) -> None:
        self.reader = reader
        self._connections: dict[str, sqlite3.Connection] = {}
        self._versions: dict[str, int] = {}

    def _connection(self, name: str) -> sqlite3.Connection:
        db = self._connections.get(name)
        if db is None:
            path = self.reader.base_path(name)
            if not path.is_file():
                raise BoardUnavailableError(name, f"no base at {path}")
            try:
                db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, isolation_level=None)
            except sqlite3.Error as error:
                raise BoardUnavailableError(name, str(error)) from error
            self._connections[name] = db
        return db

    def changed(self, board: str) -> bool:
        """True at the first call for a board, then each time another connection has written to its base."""
        db = self._connection(board)
        try:
            (version,) = db.execute("PRAGMA data_version").fetchone()
        except sqlite3.Error as error:
            self._forget(board)
            raise BoardUnavailableError(board, str(error)) from error
        if self._versions.get(board) == version:
            return False
        self._versions[board] = version
        return True

    def _forget(self, board: str) -> None:
        db = self._connections.pop(board, None)
        self._versions.pop(board, None)
        if db is not None:
            db.close()

    def close(self) -> None:
        for board in list(self._connections):
            self._forget(board)


def make_board_watcher(environ: dict[str, str] | None = None) -> HermesBoardWatcher:
    """A watcher of the boards of the Hermès home named by `HERMES_HOME`."""
    return HermesBoardWatcher(make_board_reader(environ))


def make_board_reader(environ: dict[str, str] | None = None) -> HermesBoardReader:
    """The board reader of the Hermès home named by `HERMES_HOME`, `~/.hermes` by default."""
    return HermesBoardReader(hermes_home(environ))
