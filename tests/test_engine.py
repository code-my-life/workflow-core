"""The engine's pass: a root card at a `code` station moves to the next lane, under the item's lock, never claimed,
and nothing is run nor written in observer mode (ADR 0004, amended; ADR 0005; ADR 0019)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.plugin.commands import run_command
from workflow_core.plugin.manifest import plug

from conftest import ESSAI_REPO, make_runtime_board

CARD = "t_0a0b0c0d"
ISSUE = "https://github.com/code-my-life/workflow-essai/issues/1"


def root_card(assignee: str = "essai-preparer", **fields) -> dict:
    return {
        "id": CARD,
        "title": "essai:1 · Premier essai",
        "body": ISSUE,
        "assignee": assignee,
        "status": "ready",
        "created_at": 1762900000,
        "idempotency_key": "essai:1",
        **fields,
    }


def state(ok: bool = True) -> CoreState:
    problems = [] if ok else ["essai: tasks.status absente"]
    return CoreState(contract=ContractVerdict(ok=ok, checked_at="2026-11-13T00:00:00+00:00", problems=problems))


class Launches:
    """The station commands the engine launches, run for real (through the fake `uv`), and noted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, repo, command, payload, timeout, log, *, label):
        self.calls.append((payload["station"], payload))
        return run_command(repo, command, payload, timeout, log, label=label)


def engine(core: CoreState | None = None, launches=None, repo: Path = ESSAI_REPO):
    adapter = runtime_adapter()
    lines: list[str] = []
    built = Engine(
        workflows=[plug(Workflow(key="essai", repo=repo))],
        reader=adapter.make_board_reader(),
        writer=adapter.make_board_writer(),
        state=core or state(),
        run=launches or Launches(),
        log=lines.append,
    )
    return built, lines


def assignee_of(home: Path, card: str = CARD, board: str = "essai") -> str:
    with sqlite3.connect(home / "kanban" / "boards" / board / "kanban.db") as db:
        (found,) = db.execute("SELECT assignee FROM tasks WHERE id = ?", (card,)).fetchone()
    db.close()
    return found


def test_a_code_station_advance_moves_the_root_card_to_the_next_lane(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    launches = Launches()
    core, lines = engine(launches=launches)

    core.run_pass()

    assert fake_runtime.writes() == [["kanban", "--board", "essai", "assign", CARD, "essai-revue"]]
    assert assignee_of(fake_runtime.home) == "essai-revue"
    assert launches.calls == [
        (
            "preparer",
            {
                "schema_version": 1,
                "item": {"id": "essai:1", "issue": ISSUE, "title": "essai:1 · Premier essai"},
                "station": "preparer",
                "attempt": 1,
                "answer": None,
            },
        )
    ]
    # The command's error output goes to the journal.
    assert any("sortie d'erreur : préparation de essai:1" in line for line in lines)


def test_a_second_pass_changes_nothing(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    launches = Launches()
    core, lines = engine(launches=launches)
    core.run_pass()
    writes = fake_runtime.writes()

    core.run_pass()
    core.run_pass()

    assert len(launches.calls) == 1
    assert fake_runtime.writes() == writes
    assert assignee_of(fake_runtime.home) == "essai-revue"
    # The owner station is told once in the journal, not at every pass.
    assert len([line for line in lines if "station revue (owner)" in line]) == 1


def test_nothing_is_written_in_observer_mode(fake_runtime):
    # Without a base for its board either: an observer creates nothing.
    launches = Launches()
    observer = state(ok=False)
    core, lines = engine(core=observer, launches=launches)

    core.run_pass()
    core.run_pass()

    assert launches.calls == []
    assert fake_runtime.calls() == []
    assert not (fake_runtime.home / "kanban").exists()
    assert len([line for line in lines if "mode observateur" in line]) == 1

    # Back to normal mode, the journal says so once, and the pass writes again.
    observer.contract = state().contract
    core.run_pass()
    assert len([line for line in lines if "mode normal" in line]) == 1
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "boards", "create", "essai"]]


def test_the_engine_never_claims_a_card(fake_runtime):
    # A board to create, a card to move, a card at an owner station, a child card and another workflow's card.
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            root_card(),
            root_card(id="t_00000002", idempotency_key="essai:2", assignee="essai-revue"),
            root_card(id="t_00000003", idempotency_key="essai:1/preparer/1", assignee="essai-preparer"),
            root_card(id="t_00000004", idempotency_key="factory:9", assignee="essai-preparer"),
        ],
    )
    launches = Launches()
    core, _ = engine(launches=launches)

    for _ in range(3):
        core.run_pass()

    assert [station for station, _ in launches.calls] == ["preparer"]
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "assign", CARD, "essai-revue"]]
    assert not any("claim" in argument for call in fake_runtime.calls() for argument in call)


def test_an_element_locked_by_a_running_pass_is_skipped(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    started = threading.Event()
    release = threading.Event()
    launched: list[str] = []

    def slow(repo, command, payload, timeout, log, *, label):
        launched.append(payload["item"]["id"])
        started.set()
        assert release.wait(30)
        return run_command(repo, command, payload, timeout, log, label=label)

    core, _ = engine(launches=slow)
    first = threading.Thread(target=core.run_pass)
    first.start()
    try:
        assert started.wait(30)
        core.run_pass()
    finally:
        release.set()
        first.join(60)

    assert launched == ["essai:1"]
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "assign", CARD, "essai-revue"]]


def test_the_missing_board_is_created_once(fake_runtime):
    core, lines = engine()

    core.run_pass()
    core.run_pass()

    assert fake_runtime.writes() == [["kanban", "--board", "essai", "boards", "create", "essai"]]
    assert (fake_runtime.home / "kanban" / "boards" / "essai" / "kanban.db").is_file()
    assert any("board essai créé" in line for line in lines)


def test_a_failed_board_creation_is_tried_again_at_the_next_pass(fake_runtime):
    core, lines = engine()
    fake_runtime.fail("boards")

    core.run_pass()

    assert any(line.startswith("<3>") and "boards create <slug>" in line for line in lines)
    fake_runtime.monkeypatch.delenv("FAKE_RUNTIME_FAIL")
    core.run_pass()
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "boards", "create", "essai"]] * 2


def test_a_card_advanced_by_another_pass_is_not_launched_again(fake_runtime):
    # Two items: the first pass is held by the command of the first one, while a second pass moves the second one.
    second = "t_00000002"
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[root_card(), root_card(id=second, idempotency_key="essai:2", created_at=1762900001)],
    )
    started = threading.Event()
    release = threading.Event()
    launched: list[str] = []

    def held_on_the_first(repo, command, payload, timeout, log, *, label):
        launched.append(payload["item"]["id"])
        if payload["item"]["id"] == "essai:1" and threading.current_thread().name == "first pass":
            started.set()
            assert release.wait(30)
        return run_command(repo, command, payload, timeout, log, label=label)

    core, _ = engine(launches=held_on_the_first)
    first = threading.Thread(target=core.run_pass, name="first pass")
    first.start()
    try:
        assert started.wait(30)
        core.run_pass()
    finally:
        release.set()
        first.join(60)

    assert launched == ["essai:1", "essai:2"]
    assert assignee_of(fake_runtime.home, second) == "essai-revue"
    assert sorted(call[4] for call in fake_runtime.writes()) == sorted([CARD, second])
