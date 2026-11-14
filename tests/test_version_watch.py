"""The runtime's version, read again while the core runs: a changed or unreadable version puts the core in observer
mode until a restart, and no write passes before a first read confirms the version (ADR 0019)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.model.writer import BoardWriteRefusedError
from workflow_core.service import build_service

from conftest import ESSAI_REPO, VERSION_LINE, make_runtime_board

SECRET = "test-secret-not-a-real-one"
CARD = "t_0a0b0c0d"
ESSAI = {"essai": Workflow(key="essai", repo=ESSAI_REPO)}
CHANGED = "Hermes Agent v0.22.3 (2026.11.1) · stable · local 0123456789ab"


class Clock:
    """The clock of the version reads, moved by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ready_root_card(home) -> None:
    make_runtime_board(
        home,
        "essai",
        tasks=[
            {
                "id": CARD,
                "title": "essai:1 · Premier essai",
                "body": "https://github.com/code-my-life/workflow-essai/issues/1",
                "assignee": "essai-preparer",
                "status": "ready",
                "created_at": 1762900000,
                "idempotency_key": "essai:1",
            }
        ],
    )


def assignee_of(home) -> str:
    with sqlite3.connect(home / "kanban" / "boards" / "essai" / "kanban.db") as db:
        (found,) = db.execute("SELECT assignee FROM tasks WHERE id = ?", (CARD,)).fetchone()
    db.close()
    return found


def start(clock: Clock | None = None):
    return build_service(secret=SECRET, workflows=ESSAI, adapter=runtime_adapter(), version_clock=clock or Clock())


def machine(app) -> dict:
    response = TestClient(app).get("/api/machine", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 200
    return response.json()


def version_reads(calls: list[list[str]]) -> int:
    return calls.count(["--version"])


def test_a_changed_version_switches_to_observer_within_five_minutes(fake_runtime, capsys):
    ready_root_card(fake_runtime.home)
    clock = Clock()
    app = start(clock)
    watch = app.state.version_watch
    watch.tick()
    assert machine(app)["mode"] == "normal"

    fake_runtime.set_version(CHANGED)
    clock.now += 299
    watch.tick()
    assert machine(app)["mode"] == "normal"
    clock.now += 1
    watch.tick()

    served = machine(app)
    assert served["mode"] == "observer"
    assert served["contract"]["ok"] is False
    assert served["contract"]["problems"] == ["version d'Hermès changée : 0.22.2 → 0.22.3"]
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("<3>")]
    assert errors == [
        "<3>workflow-core: version d'Hermès changée : 0.22.2 → 0.22.3 ; socle en observateur jusqu'au redémarrage"
    ]
    # The next pass of the engine runs nothing: no call to the runtime, the card stays where it is.
    before = fake_runtime.calls()
    app.state.engine.run_pass()
    assert fake_runtime.calls() == before
    assert assignee_of(fake_runtime.home) == "essai-preparer"
    # The observer mode stays, and the version is no longer read.
    fake_runtime.set_version(VERSION_LINE)
    clock.now += 600
    watch.tick()
    assert machine(app)["mode"] == "observer"
    assert fake_runtime.calls() == before


def test_an_unreadable_version_switches_to_observer(fake_runtime, capsys):
    clock = Clock()
    app = start(clock)
    watch = app.state.version_watch
    watch.tick()

    fake_runtime.set_version("Hermes Agent 1.0 · renamed")
    clock.now += 300
    watch.tick()

    served = machine(app)
    assert served["mode"] == "observer"
    assert served["contract"]["problems"] == ["version d'Hermès changée : 0.22.2 → illisible"]
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("<3>")]
    assert errors == [
        "<3>workflow-core: version d'Hermès changée : 0.22.2 → illisible ; socle en observateur jusqu'au redémarrage"
    ]
    with pytest.raises(BoardWriteRefusedError) as refused:
        app.state.engine.writer.assign("essai", CARD, "essai-revue")
    assert str(refused.value).endswith("refusée : mode observateur")
    # A later read of a good version does not bring the normal mode back: only a restart does.
    fake_runtime.set_version(VERSION_LINE)
    clock.now += 300
    watch.tick()
    assert machine(app)["mode"] == "observer"


def test_no_write_before_a_version_check_after_a_restart(fake_runtime):
    ready_root_card(fake_runtime.home)

    # Started, the service's writer refuses a write until the version is read again, without calling the runtime.
    app = start()
    before = fake_runtime.calls()
    with pytest.raises(BoardWriteRefusedError) as refused:
        app.state.engine.writer.assign("essai", CARD, "essai-revue")
    assert str(refused.value) == (
        "écriture « hermes kanban --board <board> assign <card> <lane> » refusée : "
        "version du runtime pas encore relue depuis le démarrage"
    )
    assert fake_runtime.calls() == before
    assert app.state.core.mode == "normal"

    # After a read that confirms the version, it passes.
    app.state.version_watch.tick()
    app.state.engine.writer.assign("essai", CARD, "essai-revue")
    assert fake_runtime.calls()[len(before) :] == [
        ["--version"],
        ["kanban", "--board", "essai", "assign", CARD, "essai-revue"],
    ]

    # After another restart, the engine's first pass reads the version before it writes.
    app.state.engine.writer.assign("essai", CARD, "essai-preparer")
    app = start()
    before = fake_runtime.calls()
    app.state.engine.run_pass()
    assert fake_runtime.calls()[len(before) :] == [
        ["--version"],
        ["kanban", "--board", "essai", "assign", CARD, "essai-revue"],
    ]
    assert assignee_of(fake_runtime.home) == "essai-revue"


def test_an_unchanged_version_keeps_the_normal_mode(fake_runtime):
    clock = Clock()
    app = start(clock)
    watch = app.state.version_watch
    before = fake_runtime.calls()

    for _ in range(3):
        watch.tick()
        assert machine(app)["mode"] == "normal"
        clock.now += 300

    assert version_reads(fake_runtime.calls()[len(before) :]) == 3
    assert app.state.core.contract.ok is True
    assert app.state.core.contract.problems == []
    assert watch.write_refusal() is None


def test_a_version_read_that_raises_switches_to_observer(capsys):
    from workflow_core.model.contract import ContractVerdict
    from workflow_core.model.state import CoreState
    from workflow_core.version_watch import VersionWatch

    def broken() -> str | None:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    state = CoreState(contract=ContractVerdict(ok=True, checked_at="2026-11-14T00:00:00+00:00", problems=[]))
    watch = VersionWatch(state, broken, every=300, clock=lambda: 0.0)

    watch.tick()

    assert state.mode == "observer"
    assert len(state.contract.problems) == 1
    assert state.contract.problems[0].startswith("relecture de version impossible : UnicodeDecodeError: ")
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("<3>")]
    assert len(errors) == 1 and "socle en observateur jusqu'au redémarrage" in errors[0]
    assert watch.ensure_confirmed() is False
