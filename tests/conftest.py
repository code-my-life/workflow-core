"""A fake runtime home for the tests: board bases built in a temporary folder, never the machine's own."""

from __future__ import annotations

import os
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

# The runtime's tables of runs, links and comments: the columns the core reads, plus some it does not.
OTHER_TABLES = (
    """CREATE TABLE task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, profile TEXT, step_key TEXT,
        status TEXT NOT NULL, started_at INTEGER NOT NULL, ended_at INTEGER, outcome TEXT, summary TEXT,
        metadata TEXT, error TEXT
    )""",
    "CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id))",
    """CREATE TABLE task_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
)
RUN_COLUMNS = ("id", "task_id", "profile", "status", "outcome", "started_at", "ended_at", "summary")
COMMENT_COLUMNS = ("id", "task_id", "author", "body", "created_at")

FACTORY_RUNS = [
    {
        "id": 1,
        "task_id": "t_0001",
        "profile": "implementer",
        "status": "crashed",
        "outcome": "crashed",
        "started_at": 1762800100,
        "ended_at": 1762800200,
        "summary": None,
    },
    {
        "id": 2,
        "task_id": "t_0001",
        "profile": "implementer",
        "status": "done",
        "outcome": "completed",
        "started_at": 1762800300,
        "ended_at": 1762803600,
        "summary": "PR ouverte",
    },
]
FACTORY_LINKS = [("t_0001", "t_0002")]
FACTORY_COMMENTS = [
    {"id": 1, "task_id": "t_0002", "author": "gate", "body": "Il manque la preuve de C3.", "created_at": 1762811000},
]
STUDIO_RUNS = [
    {
        "id": 1,
        "task_id": "t_0100",
        "profile": "scriptwriter",
        "status": "running",
        "outcome": None,
        "started_at": 1762820500,
        "ended_at": None,
        "summary": None,
    },
]


def _without_task(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key != "task_id"} for row in rows]


# The items the API serves for the factory board: each card with its runs, links and comments.
FACTORY_ITEMS = [
    {**FACTORY_CARDS[0], "runs": _without_task(FACTORY_RUNS), "parents": [], "children": ["t_0002"], "comments": []},
    {
        **FACTORY_CARDS[1],
        "runs": [],
        "parents": ["t_0001"],
        "children": [],
        "comments": _without_task(FACTORY_COMMENTS),
    },
]
STUDIO_ITEMS = [{**STUDIO_CARDS[0], "runs": _without_task(STUDIO_RUNS), "parents": [], "children": [], "comments": []}]

# The first line of the runtime's version output, as Hermès 0.22.2 prints it.
VERSION_LINE = "Hermes Agent v0.22.2 (2026.9.24) · stable · local d923a5eb487a"


def make_board(
    home: Path,
    board: str,
    cards: list[dict],
    runs: list[dict] = (),
    links: list[tuple[str, str]] = (),
    comments: list[dict] = (),
) -> Path:
    """Create `<home>/kanban/boards/<board>/kanban.db` in WAL mode, as the runtime does, holding these rows."""
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
        for table in OTHER_TABLES:
            db.execute(table)
        db.executemany(
            f"INSERT INTO task_runs ({', '.join(RUN_COLUMNS)}) VALUES ({', '.join('?' * len(RUN_COLUMNS))})",
            [tuple(run[column] for column in RUN_COLUMNS) for run in runs],
        )
        db.executemany("INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)", list(links))
        marks = ", ".join("?" * len(COMMENT_COLUMNS))
        db.executemany(
            f"INSERT INTO task_comments ({', '.join(COMMENT_COLUMNS)}) VALUES ({marks})",
            [tuple(comment[column] for column in COMMENT_COLUMNS) for comment in comments],
        )
    db.close()
    return base


def fake_version_command(folder: Path, first_line: str = VERSION_LINE) -> Path:
    """A fake runtime command in `folder` that prints this version output; put `folder` first on the PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    command = folder / "hermes"
    command.write_text(f"#!/bin/sh\nprintf '%s\\n' '{first_line}' 'Install method: git'\n", encoding="utf-8")
    command.chmod(0o755)
    return folder


@pytest.fixture
def runtime_home(tmp_path, monkeypatch) -> Path:
    """A runtime home with the boards `factory` and `studio`, named by the runtime's own variable, and a fake runtime
    command first on the PATH: the tests never run the machine's runtime."""
    home = tmp_path / "runtime"
    make_board(home, "factory", FACTORY_CARDS, FACTORY_RUNS, FACTORY_LINKS, FACTORY_COMMENTS)
    make_board(home, "studio", STUDIO_CARDS, STUDIO_RUNS)
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    bin_dir = fake_version_command(tmp_path / "runtime-bin")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return home
