"""The notifier's register: `alerts.db` of the core's state folder, the key of every wait already alerted."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from workflow_core.clock import utc_now

REGISTER_FILE = "alerts.db"


class AlertRegister:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None

    def _open(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            db.execute(
                "CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, url TEXT NOT NULL, posted_at TEXT NOT NULL)"
            )
            self._db = db
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
