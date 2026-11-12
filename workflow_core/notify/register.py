"""The notifier's register: `alerts.db` of the core's state folder, the key of every wait already alerted."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from workflow_core.clock import utc_now

REGISTER_FILE = "alerts.db"


class AlertRegister:
    """A missing register is created empty; an unreadable one is set aside as `alerts.db.illisible-<time>` and
    created empty. Either way the marker looked up in the issue keeps any alert from being posted twice."""

    def __init__(self, path: Path, log: Callable[[str], None] = lambda line: None) -> None:
        self.path = path
        self.log = log
        self._db: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, url TEXT NOT NULL, posted_at TEXT NOT NULL)"
            )
            db.execute("SELECT count(*) FROM alerts").fetchone()
        except sqlite3.DatabaseError:
            db.close()
            raise
        return db

    def _open(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._db = self._connect()
            except sqlite3.DatabaseError as error:
                aside = self.path.with_name(f"{self.path.name}.illisible-{time.strftime('%Y%m%dT%H%M%S')}")
                self.path.replace(aside)
                self.log(f"<3>workflow-core: notificateur, registre illisible ({error}) mis de côté : {aside}")
                self._db = self._connect()
        return self._db

    def has(self, key: str) -> bool:
        return self._open().execute("SELECT 1 FROM alerts WHERE key = ?", (key,)).fetchone() is not None

    def note(self, key: str, url: str) -> None:
        self._open().execute(
            "INSERT OR IGNORE INTO alerts (key, url, posted_at) VALUES (?, ?, ?)", (key, url, utc_now())
        )

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
