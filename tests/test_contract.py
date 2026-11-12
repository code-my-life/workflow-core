"""The contract test: what the adapter reads is still what the installed runtime provides (ADR 0001, ADR 0019)."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.service import build_service

from conftest import FACTORY_ITEMS, fake_version_command

SECRET = "test-secret-not-a-real-one"
WORKFLOWS = {
    "factory": Workflow(key="factory", board="factory", sheet=Path("factory.json")),
    "studio": Workflow(key="studio", board="studio", sheet=Path("studio.json")),
}


def rebuild_tasks(base: Path, *, drop: str | None = None) -> None:
    """Rewrite the `tasks` table of a base without one column, or without the table at all."""
    with sqlite3.connect(base) as db:
        if drop is None:
            db.execute("DROP TABLE tasks")
        else:
            db.execute(f"ALTER TABLE tasks DROP COLUMN {drop}")
    db.close()


def board_base(home: Path, board: str) -> Path:
    return home / "kanban" / "boards" / board / "kanban.db"


def check(boards=("factory", "studio")):
    return runtime_adapter().check_contract(list(boards))


def test_a_base_missing_a_read_column_breaks_the_contract(runtime_home):
    rebuild_tasks(board_base(runtime_home, "studio"), drop="block_kind")

    verdict = check()

    assert verdict.ok is False
    assert verdict.problems == ["studio: tasks.block_kind absente"]


def test_a_base_with_the_read_schema_keeps_the_contract(runtime_home):
    verdict = check()

    assert verdict.ok is True
    assert verdict.problems == []
    assert datetime.fromisoformat(verdict.checked_at).utcoffset().total_seconds() == 0
    assert verdict.mode == "normal"


def test_a_missing_table_or_an_unknown_status_breaks_the_contract(runtime_home):
    rebuild_tasks(board_base(runtime_home, "factory"))
    with sqlite3.connect(board_base(runtime_home, "studio")) as db:
        db.execute("UPDATE tasks SET status = 'paused'")
        db.execute("UPDATE tasks SET block_kind = 'weather'")
    db.close()

    verdict = check()

    assert verdict.ok is False
    assert verdict.mode == "observer"
    assert verdict.problems == [
        "factory: table tasks absente",
        'studio: valeur de status inconnue : "paused"',
        'studio: valeur de block_kind inconnue : "weather"',
    ]


def test_a_missing_base_breaks_the_contract(runtime_home):
    verdict = check(["factory", "nowhere"])

    assert verdict.ok is False
    assert len(verdict.problems) == 1
    assert verdict.problems[0].startswith("nowhere: base absente")


def test_a_broken_contract_at_startup_logs_an_error_and_keeps_the_observer_mode(runtime_home, capsys):
    rebuild_tasks(board_base(runtime_home, "factory"), drop="status")
    rebuild_tasks(board_base(runtime_home, "studio"))

    app = build_service(secret=SECRET, workflows=WORKFLOWS, adapter=runtime_adapter())

    err = capsys.readouterr().err.splitlines()
    errors = [line for line in err if line.startswith("<3>")]
    assert len(errors) == 1
    assert "factory: tasks.status absente" in errors[0]
    assert "studio: table tasks absente" in errors[0]
    assert app.state.core.mode == "observer"
    assert app.state.core.contract.ok is False

    client = TestClient(app)
    health = client.get("/healthz", headers={"Authorization": f"Bearer {SECRET}"})
    assert (health.status_code, health.json()) == (200, {"status": "ok"})
    items = client.get("/api/workflows/factory/items", headers={"Authorization": f"Bearer {SECRET}"})
    assert (items.status_code, items.json()["error"]) == (503, "board_unavailable")


def test_a_kept_contract_at_startup_logs_an_information_line(runtime_home, capsys):
    app = build_service(secret=SECRET, workflows=WORKFLOWS, adapter=runtime_adapter())

    err = capsys.readouterr().err.splitlines()
    assert [line for line in err if line.startswith("<3>")] == []
    assert any(line.startswith("<6>") and "contrat" in line for line in err)
    assert app.state.core.mode == "normal"
    response = TestClient(app).get("/api/workflows/factory/items", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.json()["items"] == FACTORY_ITEMS


def test_the_check_leaves_every_base_unchanged(runtime_home):
    bases = sorted(runtime_home.rglob("kanban.db"))
    before = {base: base.read_bytes() for base in bases}
    with sqlite3.connect(board_base(runtime_home, "studio")) as db:
        db.execute("UPDATE tasks SET status = 'paused'")
    db.close()
    before[board_base(runtime_home, "studio")] = board_base(runtime_home, "studio").read_bytes()

    for _ in range(3):
        check()

    assert {base: base.read_bytes() for base in bases} == before


def test_the_new_read_tables_and_the_version_line_are_in_the_contract(runtime_home, tmp_path, monkeypatch):
    from workflow_core.adapters.hermes.contract import CONTRACT

    assert set(CONTRACT["task_runs"].columns) == {
        "id",
        "task_id",
        "profile",
        "status",
        "outcome",
        "started_at",
        "ended_at",
        "summary",
        "metadata",
        "error",
    }
    assert set(CONTRACT["task_links"].columns) == {"parent_id", "child_id"}
    assert set(CONTRACT["task_comments"].columns) == {"id", "task_id", "author", "body", "created_at"}
    assert check().ok is True

    rebuild_tasks_column(board_base(runtime_home, "factory"), "task_comments", "body")
    assert check().problems == ["factory: task_comments.body absente"]

    bin_dir = fake_version_command(tmp_path / "other-bin", "Hermes Agent 1.0 · renamed")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    problems = check().problems
    assert problems[0] == "factory: task_comments.body absente"
    assert problems[1].startswith("version : première ligne inattendue")
    assert "Hermes Agent 1.0" in problems[1]


def rebuild_tasks_column(base: Path, table: str, column: str) -> None:
    with sqlite3.connect(base) as db:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    db.close()


def test_the_data_version_read_is_in_the_contract(runtime_home):
    from workflow_core.adapters.hermes import contract

    assert "data_version" in contract.PRAGMAS
    assert check().ok is True

    class TextVersion:
        """A connection whose data_version is not an integer."""

        def execute(self, sql):
            assert sql == "PRAGMA data_version"
            return self

        def fetchone(self):
            return ("seven",)

    assert contract.pragma_problems(TextVersion()) == ["PRAGMA data_version illisible : 'seven'"]


def test_the_card_runs_and_events_read_for_the_diagnosis_are_in_the_contract(runtime_home):
    from workflow_core.adapters.hermes.contract import CONTRACT

    assert set(CONTRACT["tasks"].columns) >= {
        "current_run_id",
        "workspace_path",
        "skills",
        "last_heartbeat_at",
        "branch_name",
        "consecutive_failures",
        "block_recurrences",
        "last_failure_error",
        "max_runtime_seconds",
        "idempotency_key",
        "model_override",
        "provider_override",
    }
    assert {"error", "metadata"} <= set(CONTRACT["task_runs"].columns)
    assert set(CONTRACT["task_events"].columns) == {"id", "task_id", "run_id", "kind", "payload", "created_at"}
    assert check().ok is True

    rebuild_tasks_column(board_base(runtime_home, "studio"), "task_events", "payload")
    assert check().problems == ["studio: task_events.payload absente"]


def test_a_journal_line_of_a_modified_format_breaks_the_contract(runtime_home):
    from workflow_core.adapters.hermes.contract import JOURNAL_KINDS, JOURNAL_LINE

    assert JOURNAL_LINE.pattern.startswith(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) ")
    assert JOURNAL_KINDS == (
        "hook_timeout",
        "hook_abandoned",
        "hook_skipped",
        "hook_raised",
        "plugin_load_failed",
        "stop_nudge",
        "turn_ended",
        "tool_deadline",
        "api_failed",
    )
    logs = runtime_home / "profiles" / "coder" / "logs"
    logs.mkdir(parents=True)
    good = "2026-10-29 09:46:10,161 INFO [20261029_093652_fc383d] agent.tool_executor: tool terminal completed"
    (logs / "agent.log").write_text(f"{good}\nTraceback (most recent call last):\n", encoding="utf-8")
    assert check().ok is True

    # The level after the session: a header the reader no longer understands.
    moved = "2026-10-29 09:46:12,000 [20261029_093652_fc383d] INFO agent.tool_executor: tool terminal completed"
    with (logs / "agent.log").open("a", encoding="utf-8") as journal:
        journal.write(f"{moved}\n")

    assert check().problems == [f"journal de coder : en-tête de ligne inconnu : « {moved[:80]} »"]


def test_a_session_base_of_a_modified_schema_breaks_the_contract(runtime_home):
    from workflow_core.adapters.hermes.contract import SESSION_CONTRACT

    from conftest import make_session_base

    assert set(SESSION_CONTRACT["messages"].columns) >= {"session_id", "role", "content", "tool_calls", "timestamp"}
    base = make_session_base(runtime_home, "coder")
    base.execute("INSERT INTO sessions (id, source, started_at) VALUES ('s1', 'kanban', 1)")
    calls = '[{"id": "c1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]'
    base.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES ('s1', 'assistant', ?, 2)", (calls,)
    )
    assert check().ok is True

    base.execute("DROP INDEX idx_messages_assistant_calls_by_session")
    base.execute("ALTER TABLE messages DROP COLUMN tool_calls")
    base.close()

    assert check().problems == ["agent coder : messages.tool_calls absente"]


def test_a_tool_call_without_its_read_fields_breaks_the_contract(runtime_home):
    from conftest import make_session_base

    base = make_session_base(runtime_home, "coder")
    base.execute("INSERT INTO sessions (id, source, started_at) VALUES ('s1', 'kanban', 1)")
    renamed = '[{"id": "c1", "type": "function", "fn": {"name": "terminal", "arguments": "{}"}}]'
    base.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES ('s1', 'assistant', ?, 2)", (renamed,)
    )
    base.close()

    assert check().problems == ["agent coder : appel d'outil sans function.name dans messages.tool_calls"]
