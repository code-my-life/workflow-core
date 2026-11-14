"""The engine's pass: a root card at a `code` station moves to the next lane, waits on `wait`, is blocked with its
reason on `fail`, and is completed after the last station, under the item's lock, never claimed, and nothing is run
nor written in observer mode (ADR 0004, amended; ADR 0005; ADR 0019)."""

from __future__ import annotations

import dataclasses
import sqlite3
import threading
from pathlib import Path

from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.plugin.commands import run_command
from workflow_core.plugin.manifest import PluggedWorkflow, plug

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


def essai(board: str = "essai", **stations: dict) -> PluggedWorkflow:
    """The trial workflow on a board, each named station changed by its fields (`command`, `timeout_seconds`)."""
    plugged = plug(Workflow(key="essai", board=board, repo=ESSAI_REPO))
    changed = tuple(station.model_copy(update=stations.get(station.key, {})) for station in plugged.stations)
    return dataclasses.replace(plugged, stations=changed)


def engine(core: CoreState | None = None, launches=None, workflow: PluggedWorkflow | None = None, answers=None):
    adapter = runtime_adapter()
    lines: list[str] = []
    built = Engine(
        workflows=[workflow or essai()],
        reader=adapter.make_board_reader(),
        writer=adapter.make_board_writer(),
        state=core or state(),
        run=launches or Launches(),
        log=lines.append,
        answers=answers,
    )
    return built, lines


def assignee_of(home: Path, card: str = CARD, board: str = "essai") -> str:
    with sqlite3.connect(home / "kanban" / "boards" / board / "kanban.db") as db:
        (found,) = db.execute("SELECT assignee FROM tasks WHERE id = ?", (card,)).fetchone()
    db.close()
    return found


def status_of(home: Path, card: str = CARD, board: str = "essai") -> tuple[str, str | None]:
    """The card's status and its block kind."""
    with sqlite3.connect(home / "kanban" / "boards" / board / "kanban.db") as db:
        found = db.execute("SELECT status, block_kind FROM tasks WHERE id = ?", (card,)).fetchone()
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


def test_later_passes_park_the_owner_card_once_and_then_change_nothing(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    launches = Launches()
    core, lines = engine(launches=launches)

    for _ in range(4):
        core.run_pass()

    # The first pass moves the card to the owner station, the second parks it there, the others change nothing.
    assert len(launches.calls) == 1
    waiting = "En attente du propriétaire à la station revue"
    assert fake_runtime.writes() == [
        ["kanban", "--board", "essai", "assign", CARD, "essai-revue"],
        ["kanban", "--board", "essai", "schedule", CARD, waiting],
    ]
    assert assignee_of(fake_runtime.home) == "essai-revue"
    assert status_of(fake_runtime.home) == ("scheduled", None)
    assert len([line for line in lines if f"carte {CARD} en attente du propriétaire" in line]) == 1


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

    waiting = "En attente du propriétaire à la station revue"
    assert [station for station, _ in launches.calls] == ["preparer"]
    assert fake_runtime.writes() == [
        ["kanban", "--board", "essai", "schedule", "t_00000002", waiting],
        ["kanban", "--board", "essai", "assign", CARD, "essai-revue"],
        ["kanban", "--board", "essai", "schedule", CARD, waiting],
    ]
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


def test_a_wait_decision_relaunches_with_the_next_attempt(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    launches = Launches()
    core, lines = engine(launches=launches, workflow=essai(preparer={"command": ["python", "commands/waits.py"]}))

    core.run_pass()
    assert fake_runtime.writes() == []
    assert status_of(fake_runtime.home) == ("ready", None)
    assert assignee_of(fake_runtime.home) == "essai-preparer"

    core.run_pass()
    assert fake_runtime.writes() == []
    assert [(station, payload["attempt"]) for station, payload in launches.calls] == [("preparer", 1), ("preparer", 2)]
    assert any("preparer rend wait (pas encore prêt), carte inchangée" in line for line in lines)


def test_a_fail_a_timeout_or_an_unreadable_output_blocks_the_card_with_its_reason(fake_runtime):
    cases = {
        "essai-fail": ({"command": ["python", "commands/fails.py"]}, "préparation impossible"),
        "essai-delai": ({"command": ["python", "commands/sleeps.py"], "timeout_seconds": 2}, "délai de 2 s dépassé"),
        "essai-illisible": ({"command": ["python", "commands/chatty.py"]}, "sortie illisible"),
    }
    writer = runtime_adapter().make_board_writer()
    for board, (preparer, reason) in cases.items():
        make_runtime_board(fake_runtime.home, board, tasks=[root_card()])
        launches = Launches()
        core, _ = engine(launches=launches, workflow=essai(board, preparer=preparer))
        motive = f"Échec de la station preparer : {reason}"

        core.run_pass()
        blocked = [["kanban", "--board", board, "block", CARD, motive]]
        assert [call for call in fake_runtime.writes() if call[2] == board] == blocked, board
        # Blocked without a kind, and launched no more.
        assert status_of(fake_runtime.home, board=board) == ("blocked", None), board
        core.run_pass()
        assert len(launches.calls) == 1, board

        # Unblocked, the card is ready at its station again, and its command runs again.
        writer.unblock(board, CARD)
        core.run_pass()
        assert [payload["attempt"] for _, payload in launches.calls] == [1, 2], board
        assert [call for call in fake_runtime.writes() if call[2] == board] == [
            *blocked,
            ["kanban", "--board", board, "unblock", CARD],
            *blocked,
        ], board
        # Failing again after an unblock, the card goes to `triage` (the runtime's unblock-loop guard), and the engine
        # launches it no more.
        assert status_of(fake_runtime.home, board=board) == ("triage", None), board
        core.run_pass()
        assert len(launches.calls) == 2, board
    assert not any(call[3] == "complete" for call in fake_runtime.writes())


def test_the_last_station_completes_the_root_card(fake_runtime):
    second = "t_00000002"
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            root_card(assignee="essai-clore"),
            root_card(id=second, idempotency_key="essai:2", created_at=1762900001),
        ],
    )
    launches = Launches()
    core, lines = engine(launches=launches)

    for _ in range(3):
        core.run_pass()

    completes = [call for call in fake_runtime.writes() if call[3] == "complete"]
    assert completes == [["kanban", "--board", "essai", "complete", CARD, "--result", "élément clos"]]
    assert status_of(fake_runtime.home) == ("done", None)
    # The other item went on to the owner station, and waits there: only the last station completes a root card.
    assert status_of(fake_runtime.home, second) == ("scheduled", None)
    assert [station for station, _ in launches.calls] == ["clore", "preparer"]
    assert any(f"carte {CARD} terminée (élément clos)" in line for line in lines)


def test_an_unknown_target_station_blocks_the_card(fake_runtime, tmp_path):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    command = tmp_path / "ailleurs.py"
    command.write_text(
        "import json, sys\njson.load(sys.stdin)\n"
        'print(json.dumps({"decision": "advance", "reason": "ailleurs", "to": "nulle-part"}))\n',
        encoding="utf-8",
    )
    core, _ = engine(workflow=essai(preparer={"command": ["python", str(command)]}))

    core.run_pass()

    motive = "Échec de la station preparer : station « nulle-part » inconnue"
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "block", CARD, motive]]
