"""A fake runtime home for the tests: board bases built in a temporary folder, never the machine's own."""

from __future__ import annotations

import json
import os
import re
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
    block_kind TEXT,
    idempotency_key TEXT,
    current_run_id INTEGER,
    workspace_path TEXT,
    skills TEXT,
    model_override TEXT,
    provider_override TEXT,
    last_heartbeat_at INTEGER,
    branch_name TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    block_recurrences INTEGER NOT NULL DEFAULT 0,
    last_failure_error TEXT,
    max_runtime_seconds INTEGER
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

# The runtime's tables of runs, links, comments and events: the columns the core reads, plus some it does not.
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
    """CREATE TABLE task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER, kind TEXT NOT NULL, payload TEXT,
        created_at INTEGER NOT NULL
    )""",
)
RUN_COLUMNS = ("id", "task_id", "profile", "status", "outcome", "started_at", "ended_at", "summary")
COMMENT_COLUMNS = ("id", "task_id", "author", "body", "created_at")
EVENT_COLUMNS = ("id", "task_id", "run_id", "kind", "payload", "created_at")

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
    events: list[dict] = (),
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
        db.executemany(
            f"INSERT INTO task_events ({', '.join(EVENT_COLUMNS)}) VALUES ({', '.join('?' * len(EVENT_COLUMNS))})",
            [tuple(event.get(column) for column in EVENT_COLUMNS) for event in events],
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


@pytest.fixture(autouse=True)
def own_state_dir(tmp_path, monkeypatch):
    """Every test gets its own state folder: no test ever reads or writes the machine's core state (its event
    journal, its register of alerts, its token cache)."""
    monkeypatch.setenv("WORKFLOW_CORE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def runtime_home(tmp_path, monkeypatch) -> Path:
    """A runtime home with the boards `factory` and `studio`, named by the runtime's own variable, and a fake runtime
    command first on the PATH: the tests never run the machine's runtime."""
    home = tmp_path / "runtime"
    make_board(home, "factory", FACTORY_CARDS, FACTORY_RUNS, FACTORY_LINKS, FACTORY_COMMENTS)
    make_board(home, "studio", STUDIO_CARDS, STUDIO_RUNS)
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    # The core's own state (its event journal) stays in the test's folder too.
    monkeypatch.setenv("WORKFLOW_CORE_STATE_DIR", str(tmp_path / "state"))
    bin_dir = fake_version_command(tmp_path / "runtime-bin")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return home


# The schema of a board's base as the runtime 0.22.2 creates it, without data (copied from factory-live at d47f235).
RUNTIME_SCHEMA = Path(__file__).resolve().parent / "fixtures" / "hermes-schema" / "kanban.sql"


def make_runtime_board(
    home: Path, board: str, *, tasks: list[dict], runs: list[dict] = (), events: list[dict] = ()
) -> Path:
    """Create `<home>/kanban/boards/<board>/kanban.db` with the runtime's real schema, holding these rows. Each row
    names only the columns it sets; the others keep the schema's defaults."""
    folder = home / "kanban" / "boards" / board
    folder.mkdir(parents=True)
    base = folder / "kanban.db"
    with sqlite3.connect(base) as db:
        db.execute("PRAGMA journal_mode=wal")
        db.executescript(RUNTIME_SCHEMA.read_text(encoding="utf-8"))
        for table, rows in (("tasks", tasks), ("task_runs", runs), ("task_events", events)):
            for row in rows:
                columns = ", ".join(row)
                db.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    db.close()
    return base


def runtime_schema(name: str, tables: list[str]) -> str:
    """The statements of tests/fixtures/hermes-schema/<name>.sql that create these tables and their indexes, as
    factory-live's `schemaFor` keeps them: the full schema also holds tables its virtual tables create themselves."""
    sql = (Path(__file__).resolve().parent / "fixtures" / "hermes-schema" / f"{name}.sql").read_text(encoding="utf-8")
    kept = []
    for statement in re.split(r";\s*\n", sql):
        statement = re.sub(r"^(\s*--.*\n)+", "", statement).strip()
        table = re.match(r"^CREATE TABLE (\w+)", statement)
        index = re.match(r"^CREATE INDEX \w+\s+ON (\w+)", statement)
        if (table and table[1] in tables) or (index and index[1] in tables):
            kept.append(f"{statement};")
    return "\n".join(kept)


def make_session_base(home: Path, agent: str) -> sqlite3.Connection:
    """Create `<home>/profiles/<agent>/state.db` in WAL mode with the runtime's `sessions` and `messages` tables, and
    return a writing connection for the test to fill."""
    folder = home / "profiles" / agent
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "state.db", isolation_level=None)
    db.execute("PRAGMA journal_mode=wal")
    db.executescript(runtime_schema("state", ["sessions", "messages"]))
    return db


DIAGNOSIS_REFERENCES = Path(__file__).resolve().parent / "fixtures" / "diagnosis"


def reference_cases() -> list[dict]:
    """The 13 reference outputs of factory-live's diagnosis, copied by the story of the agents' journals."""
    paths = sorted(path for path in DIAGNOSIS_REFERENCES.glob("*.json") if path.name != "source.json")
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def diagnosis_input_of(data: dict):
    """The core's diagnosis input from the `input` of a reference output: factory-live's `DiagnosisInput`, in
    camelCase."""
    from workflow_core.model.facts import (
        CardDetail,
        DiagnosisInput,
        EventFact,
        EvidenceFile,
        LogFact,
        PluginHook,
        ProfileFacts,
        RunFact,
        SessionFacts,
        ToolCallFact,
        ToolResultFact,
    )

    def snake(name: str) -> str:
        return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()

    def event(item: dict) -> EventFact:
        return EventFact(
            id=item["id"],
            run_id=item["runId"],
            kind=item["kind"],
            payload=item["payload"],
            created_at=item["createdAt"],
        )

    task = data["task"]
    card = CardDetail(**{snake(key): value for key, value in task.items()})
    runs = [RunFact(**{snake(k): v for k, v in run.items() if k != "taskId"}) for run in data["runs"]]
    sessions = None
    if data["sessions"] is not None:
        sessions = [
            SessionFacts(
                id=session["id"],
                run_id=session["runId"],
                calls=[ToolCallFact(**{snake(k): v for k, v in call.items()}) for call in session["calls"]],
                results=[ToolResultFact(**{snake(k): v for k, v in result.items()}) for result in session["results"]],
            )
            for session in data["sessions"]
        ]
    logs = (
        None if data["logs"] is None else [LogFact(**{snake(k): v for k, v in fact.items()}) for fact in data["logs"]]
    )
    profile = None
    if data["profile"] is not None:
        raw = data["profile"]
        profile = ProfileFacts(
            profile=raw["profile"],
            hook_timeout=raw["hookTimeout"],
            tool_deadline=raw["toolDeadline"],
            terminal_timeout=raw["terminalTimeout"],
            max_turns=raw["maxTurns"],
            changed_at=raw["changedAt"],
            # factory-live leaves `repoHookTimeout` out when the repository cannot be read.
            repo_read="repoHookTimeout" in raw,
            repo_hook_timeout=raw.get("repoHookTimeout"),
            plugins=raw["plugins"],
            hooks=[PluginHook(**hook) for hook in raw["hooks"]],
            longest_commands=raw.get("longestCommands", {}),
        )
    evidence = None if data["evidence"] is None else [EvidenceFile(**item) for item in data["evidence"]]
    return DiagnosisInput(
        now=data["now"],
        board=data["board"],
        card=card,
        runs=runs,
        events=[event(item) for item in data["events"]],
        anchor=None if data["anchor"] is None else event(data["anchor"]),
        sessions=sessions,
        logs=logs,
        profile=profile,
        evidence=evidence,
        branch=data["branch"],
        pr_url=data["prUrl"],
        owner=data.get("owner"),
    )
