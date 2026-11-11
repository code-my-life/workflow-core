"""A fake runtime home for the tests: board bases built in a temporary folder, never the machine's own."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

RUNTIME_HOME_VAR = "HER" + "MES_HOME"

# The columns of the runtime's `tasks` table the core reads, plus one it does not, as the real table has many.
TASKS_TABLE = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    assignee TEXT,
    status TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    block_kind TEXT
)
"""

FACTORY_CARDS = [
    {
        "id": "t_0001",
        "title": "Le juge refuse une fiche invalide",
        "body": "Issue #5",
        "status": "done",
        "assignee": "implementer",
        "created_at": 1762800000,
        "started_at": 1762800100,
        "completed_at": 1762803600,
        "block_kind": None,
    },
    {
        "id": "t_0002",
        "title": "Le service sert les cartes",
        "body": None,
        "status": "blocked",
        "assignee": None,
        "created_at": 1762810000,
        "started_at": None,
        "completed_at": None,
        "block_kind": "needs_input",
    },
]

STUDIO_CARDS = [
    {
        "id": "t_0100",
        "title": "Vidéo de lancement",
        "body": "Script",
        "status": "running",
        "assignee": "scriptwriter",
        "created_at": 1762820000,
        "started_at": 1762820500,
        "completed_at": None,
        "block_kind": None,
    },
]

COLUMNS = ("id", "title", "body", "status", "assignee", "created_at", "started_at", "completed_at", "block_kind")


def make_board(home: Path, board: str, cards: list[dict]) -> Path:
    """Create `<home>/kanban/boards/<board>/kanban.db` in WAL mode, as the runtime does, holding these cards."""
    folder = home / "kanban" / "boards" / board
    folder.mkdir(parents=True)
    base = folder / "kanban.db"
    with sqlite3.connect(base) as db:
        db.execute("PRAGMA journal_mode=wal")
        db.execute(TASKS_TABLE)
        db.executemany(
            f"INSERT INTO tasks ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [tuple(card[column] for column in COLUMNS) for card in cards],
        )
    db.close()
    return base


@pytest.fixture
def runtime_home(tmp_path, monkeypatch) -> Path:
    """A runtime home with the boards `factory` and `studio`, named by the runtime's own variable."""
    home = tmp_path / "runtime"
    make_board(home, "factory", FACTORY_CARDS)
    make_board(home, "studio", STUDIO_CARDS)
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    return home
