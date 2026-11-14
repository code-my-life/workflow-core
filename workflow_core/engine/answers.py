"""The engine's box of the owner's answers: the table `answers` of `engine.db`, in the core's state folder.

An answer is a gesture and its note for an item, received at a time. The engine hands it to the command of the
`owner` station where the item waits, then marks it `applied` with the wait it answered; an answer for an item without
an open wait is marked `refused`. Either way it is handled once (AD-10). Only the core fills the box: no route of the
API writes to it (ADR 0011).
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from workflow_core.clock import utc_now

ENGINE_DB = "engine.db"
# How long a write waits for another connection's lock, in seconds.
LOCK_TIMEOUT_SECONDS = 5.0
SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    id TEXT PRIMARY KEY,
    item TEXT NOT NULL,
    gesture TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    wait TEXT,
    outcome TEXT CHECK (outcome IN ('applied', 'refused')),
    handled_at TEXT
)
"""


@dataclass(frozen=True)
class Answer:
    id: str
    item: str
    gesture: str
    note: str
    received_at: str


class AnswerBox:
    """The answers of `engine.db` at `path`; the folder and the table are created when missing. One connection per
    call: the engine's passes and the tools that fill the box never share one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
        db.execute(SCHEMA)
        return db

    def receive(self, item: str, gesture: str, note: str = "", *, answer_id: str | None = None) -> str:
        """Put an answer in the box and return its id; an answer whose id is already there is kept as it was."""
        answer_id = answer_id or f"a_{secrets.token_hex(8)}"
        db = self._connect()
        try:
            db.execute(
                "INSERT OR IGNORE INTO answers (id, item, gesture, note, received_at) VALUES (?, ?, ?, ?, ?)",
                (answer_id, item, gesture, note, utc_now()),
            )
        finally:
            db.close()
        return answer_id

    def pending(self, prefix: str) -> list[Answer]:
        """The answers not handled yet for the items whose id starts with `prefix`, in the order they came."""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT id, item, gesture, note, received_at FROM answers "
                "WHERE handled_at IS NULL AND substr(item, 1, length(?)) = ? ORDER BY received_at, rowid",
                (prefix, prefix),
            ).fetchall()
        finally:
            db.close()
        return [Answer(*row) for row in rows]

    def is_pending(self, answer_id: str) -> bool:
        """Whether the answer is in the box and not handled yet."""
        db = self._connect()
        try:
            found = db.execute("SELECT 1 FROM answers WHERE id = ? AND handled_at IS NULL", (answer_id,)).fetchone()
            return found is not None
        finally:
            db.close()

    def mark(self, answer_id: str, outcome: str, wait: str | None = None) -> bool:
        """Mark an answer handled, `applied` to a wait or `refused`; False when it was handled already."""
        db = self._connect()
        try:
            cursor = db.execute(
                "UPDATE answers SET outcome = ?, wait = ?, handled_at = ? WHERE id = ? AND handled_at IS NULL",
                (outcome, wait, utc_now(), answer_id),
            )
            return cursor.rowcount == 1
        finally:
            db.close()
