"""The contract test: what the adapter reads is still what the installed runtime provides (ADR 0001, ADR 0019)."""

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.service import build_service

from conftest import FACTORY_CARDS

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
    assert response.json()["items"] == FACTORY_CARDS


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
