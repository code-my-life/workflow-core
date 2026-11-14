"""What the engine notes of the root cards it finds touched by another, in two tables of `engine.db`, in the core's
state folder (ADR 0004, amended).

- `finishes`: the root cards the engine completed itself, noted just before its `complete` and taken back when the
  write fails. A root card done that is not there was finished by another.
- `signals`: one row per anomaly, its key `<key>:finished_elsewhere:<card>` (a root card finished by another, its
  item left alone for good) or `<key>:taken:<card>:<run>` (a run on a root card, once per run). The notifier reads
  them and alerts each one once in the item's issue; it never writes here.

Outside the `engine` package: the notifier reads it, and the engine imports the notifier.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# The engine's base, the answer box's (`workflow_core.engine.answers`), and how long a write waits for another
# connection's lock: repeated here, since importing the engine's package from here would be circular.
ENGINE_DB = "engine.db"
LOCK_TIMEOUT_SECONDS = 5.0
FINISHED_ELSEWHERE = "finished_elsewhere"
TAKEN = "taken"
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS finishes (
        board TEXT NOT NULL,
        card TEXT NOT NULL,
        item TEXT NOT NULL,
        finished_at INTEGER NOT NULL,
        PRIMARY KEY (board, card)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        key TEXT PRIMARY KEY,
        workflow TEXT NOT NULL,
        item TEXT NOT NULL,
        card TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('finished_elsewhere', 'taken')),
        station TEXT NOT NULL,
        detail TEXT NOT NULL,
        issue TEXT,
        opened_at INTEGER NOT NULL
    )
    """,
)

__all__ = ["ENGINE_DB", "FINISHED_ELSEWHERE", "TAKEN", "Signal", "Signals"]


@dataclass(frozen=True)
class Signal:
    key: str
    workflow: str
    item: str
    card: str
    kind: str
    station: str
    detail: str
    issue: str | None
    opened_at: int


class Signals:
    """The engine's notes of `engine.db` at `path`. One connection per call, as the answer box: the engine writes,
    the notifier only reads (`signals_of`), and a base or a table that does not exist yet reads as empty."""

    def __init__(self, path: Path, clock=time.time) -> None:
        self.path = path
        self.clock = clock

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
        for statement in SCHEMA:
            db.execute(statement)
        return db

    def _read(self, query: str, arguments: tuple) -> list[tuple]:
        """Rows read without creating or changing anything: the reader's connection is read-only."""
        if not self.path.is_file():
            return []
        try:
            db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=LOCK_TIMEOUT_SECONDS)
        except sqlite3.OperationalError:
            return []
        try:
            return db.execute(query, arguments).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                return []
            raise
        finally:
            db.close()

    def note_finish(self, board: str, card: str, item: str) -> None:
        """Note that the engine completes this root card, before it writes it."""
        db = self._connect()
        try:
            db.execute(
                "INSERT OR REPLACE INTO finishes (board, card, item, finished_at) VALUES (?, ?, ?, ?)",
                (board, card, item, int(self.clock())),
            )
        finally:
            db.close()

    def forget_finish(self, board: str, card: str) -> None:
        """Take back a finish the board refused to write."""
        db = self._connect()
        try:
            db.execute("DELETE FROM finishes WHERE board = ? AND card = ?", (board, card))
        finally:
            db.close()

    def finished_by_engine(self, board: str, card: str) -> bool:
        rows = self._read("SELECT 1 FROM finishes WHERE board = ? AND card = ?", (board, card))
        return bool(rows)

    def signal(
        self,
        key: str,
        *,
        workflow: str,
        item: str,
        card: str,
        kind: str,
        station: str,
        detail: str,
        issue: str | None,
        opened_at: int | None = None,
    ) -> bool:
        """Note an anomaly once; True when it is new, False when its key was noted already. `opened_at` (Unix
        seconds): when the anomaly happened, compared by the notifier with its switch-on time; now without it."""
        db = self._connect()
        try:
            cursor = db.execute(
                "INSERT OR IGNORE INTO signals (key, workflow, item, card, kind, station, detail, issue, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, workflow, item, card, kind, station, detail, issue, int(opened_at or self.clock())),
            )
            return cursor.rowcount == 1
        finally:
            db.close()

    def abandoned(self, workflow: str, item: str) -> bool:
        """Whether the item was finished by another than the engine: the engine never touches it again."""
        rows = self._read(
            "SELECT 1 FROM signals WHERE workflow = ? AND item = ? AND kind = ?", (workflow, item, FINISHED_ELSEWHERE)
        )
        return bool(rows)

    def signals_of(self, workflow: str) -> list[Signal]:
        """The workflow's anomalies, oldest first."""
        rows = self._read(
            "SELECT key, workflow, item, card, kind, station, detail, issue, opened_at FROM signals "
            "WHERE workflow = ? ORDER BY opened_at, rowid",
            (workflow,),
        )
        return [Signal(*row) for row in rows]
