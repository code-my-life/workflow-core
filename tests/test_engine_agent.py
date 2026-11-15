"""The engine at an `agent` station: a child card for the declared profile at each passage, its end moves the root card
on, its block keeps it at its station, and the root card is never taken (ADR 0004, amended; ADR 0016)."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import subprocess
from pathlib import Path

from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.engine.answers import ENGINE_DB
from workflow_core.model.writer import BoardWriteError
from workflow_core.plugin.manifest import PluggedWorkflow, plug
from workflow_core.signals import Signals

from conftest import make_runtime_board
from test_engine import Launches, engine, state
from test_engine_owner import answer_box

AGENT_REPO = Path(__file__).resolve().parent / "fixtures" / "workflows" / "essai-agent"
ROOT = "t_0a0b0c07"
ISSUE = "https://github.com/code-my-life/workflow-essai/issues/7"


def essai_agent(*stations: str) -> PluggedWorkflow:
    """The agent trial workflow, kept to the named stations when some are named."""
    plugged = plug(Workflow(key="essai", board="essai", repo=AGENT_REPO))
    if not stations:
        return plugged
    return dataclasses.replace(plugged, stations=tuple(s for s in plugged.stations if s.key in stations))


def root_card(assignee: str = "essai-brouillon", **fields) -> dict:
    return {
        "id": ROOT,
        "title": "essai:7 · Septième essai",
        "body": ISSUE,
        "assignee": assignee,
        "status": "ready",
        "created_at": 1762900000,
        "idempotency_key": "essai:7",
        **fields,
    }


def creates(fake_runtime) -> list[list[str]]:
    return [call for call in fake_runtime.writes() if call[1] == "--board" and call[3] == "create"]


def child_create(passage: int) -> list[str]:
    return [
        "kanban",
        "--board",
        "essai",
        "create",
        f"essai:7 · brouillon (passage {passage})",
        "--assignee",
        "essai-agent",
        "--idempotency-key",
        f"essai:7/brouillon/{passage}",
        f"--body=essai:7\n{ISSUE}",
        "--json",
    ]


def base(home: Path) -> Path:
    return home / "kanban" / "boards" / "essai" / "kanban.db"


def card(home: Path, card_id: str = ROOT) -> tuple[str, str]:
    """The card's status and assignee."""
    with sqlite3.connect(base(home)) as db:
        found = db.execute("SELECT status, assignee FROM tasks WHERE id = ?", (card_id,)).fetchone()
    db.close()
    return found


def children(home: Path) -> list[tuple[str, str, str]]:
    """The child cards in the base: their key, status and assignee, by key."""
    with sqlite3.connect(base(home)) as db:
        found = db.execute(
            "SELECT idempotency_key, status, assignee FROM tasks WHERE idempotency_key LIKE 'essai:7/%' "
            "ORDER BY idempotency_key"
        ).fetchall()
    db.close()
    return found


class FakeAgent:
    """An agent of the runtime, as a profile would act on its card: it completes, blocks or unblocks a child card
    through the same fake runtime command, and notes its calls."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.calls: list[list[str]] = []

    def child(self, passage: int = 1) -> str:
        with sqlite3.connect(base(self.home)) as db:
            (found,) = db.execute(
                "SELECT id FROM tasks WHERE idempotency_key = ?", (f"essai:7/brouillon/{passage}",)
            ).fetchone()
        db.close()
        return found

    def _run(self, *arguments: str) -> None:
        call = ["kanban", "--board", "essai", *arguments]
        self.calls.append(call)
        subprocess.run(["hermes", *call], check=True, capture_output=True, text=True, env=os.environ)

    def complete(self, passage: int = 1) -> None:
        self._run("complete", self.child(passage), "--result", "brouillon écrit")

    def block(self, passage: int = 1) -> None:
        self._run("block", self.child(passage), "il manque la source")

    def unblock(self, passage: int = 1) -> None:
        self._run("unblock", self.child(passage))


def never_touched(fake_runtime, agent: FakeAgent | None = None) -> None:
    """C6: no claim, no link, no child card created with a parent, and no call of the agent on the root card."""
    calls = fake_runtime.writes()
    assert not any(argument in ("claim", "link") for call in calls for argument in call)
    assert not any("--parent" in call for call in creates(fake_runtime))
    if agent is not None:
        assert not any(ROOT in call for call in agent.calls)


def test_an_agent_station_creates_one_child_card_for_the_declared_profile(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()

    assert creates(fake_runtime) == [child_create(1)]
    assert fake_runtime.writes() == [child_create(1)]
    # The child is ready for its profile, tied to the root card by its key only; the root card stays where it was.
    assert children(fake_runtime.home) == [("essai:7/brouillon/1", "ready", "essai-agent")]
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    (item,) = [item for item in core.reader.read_items("essai") if item.idempotency_key == "essai:7/brouillon/1"]
    assert item.parents == [] and item.body.splitlines()[0] == "essai:7"
    assert any("carte fille" in line and "(passage 1), confiée à essai-agent" in line for line in lines)
    never_touched(fake_runtime)


class FailsAfterCreate:
    """A writer that creates the child card, then fails, as a pass broken between the creation and the reading."""

    def __init__(self, writer) -> None:
        self.writer = writer

    def __getattr__(self, name):
        return getattr(self.writer, name)

    def create_child_card(self, board, **fields):
        self.writer.create_child_card(board, **fields)
        raise BoardWriteError("hermes kanban --board <board> create <title>", 1, "panne simulée")


class HidesChildren:
    """A reader whose reads miss the child cards while `hide` is set: a stale reading."""

    def __init__(self, reader) -> None:
        self.reader = reader
        self.hide = False

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def read_items(self, board):
        items = self.reader.read_items(board)
        return [item for item in items if not (self.hide and "/" in (item.idempotency_key or ""))]


def test_a_replayed_pass_does_not_create_a_second_child(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    adapter = runtime_adapter()
    reader = HidesChildren(adapter.make_board_reader())
    lines: list[str] = []
    core = Engine(
        workflows=[essai_agent()],
        reader=reader,
        writer=FailsAfterCreate(adapter.make_board_writer()),
        state=state(),
        run=Launches(),
        log=lines.append,
    )

    # The pass breaks once the child is created: the failure goes to the journal.
    core.run_pass()
    assert any(line.startswith("<3>") and "panne simulée" in line for line in lines)
    assert children(fake_runtime.home) == [("essai:7/brouillon/1", "ready", "essai-agent")]

    # The next passes, the child open, create nothing.
    for _ in range(3):
        core.run_pass()
    assert creates(fake_runtime) == [child_create(1)]

    # A stale reading that misses the child creates it again: its key returns the same card.
    reader.hide = True
    core.run_pass()
    reader.hide = False
    core.run_pass()
    assert creates(fake_runtime) == [child_create(1), child_create(1)]
    assert children(fake_runtime.home) == [("essai:7/brouillon/1", "ready", "essai-agent")]
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    never_touched(fake_runtime)


def test_a_done_child_moves_the_root_card_on(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    agent = FakeAgent(fake_runtime.home)
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()
    agent.complete()
    core.run_pass()

    assert card(fake_runtime.home) == ("ready", "essai-revue")
    assert fake_runtime.writes()[-1] == ["kanban", "--board", "essai", "assign", ROOT, "essai-revue"]
    assert any(f"carte {ROOT} passée à essai-revue" in line for line in lines)
    assert len(creates(fake_runtime)) == 1
    never_touched(fake_runtime, agent)


def test_a_done_child_at_the_last_station_completes_the_root_card(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    agent = FakeAgent(fake_runtime.home)
    core, _ = engine(launches=Launches(), workflow=essai_agent("brouillon"))

    core.run_pass()
    agent.complete()
    core.run_pass()
    core.run_pass()

    assert card(fake_runtime.home) == ("done", "essai-brouillon")
    root_completes = [call for call in fake_runtime.writes() if call[3] == "complete" and ROOT in call]
    child = agent.child()
    assert root_completes == [
        ["kanban", "--board", "essai", "complete", ROOT, f"--result=carte fille {child} terminée"]
    ]
    # The root card was completed by the engine, never by the agent.
    assert [call[3] for call in agent.calls] == ["complete"]
    never_touched(fake_runtime, agent)


def test_a_blocked_child_keeps_the_root_card_at_its_station(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    agent = FakeAgent(fake_runtime.home)
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()
    agent.block()
    written = len(fake_runtime.writes())
    for _ in range(3):
        core.run_pass()

    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    assert len(fake_runtime.writes()) == written
    blocked = [line for line in lines if line.startswith("<4>") and "bloquée à la station brouillon" in line]
    assert len(blocked) == 1
    assert "il manque la source" in blocked[0]

    # Unblocked, then done: the root card moves on.
    agent.unblock()
    core.run_pass()
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    agent.complete()
    core.run_pass()
    assert card(fake_runtime.home) == ("ready", "essai-revue")
    assert len(creates(fake_runtime)) == 1
    never_touched(fake_runtime, agent)


def test_an_archived_child_leaves_the_root_card_and_starts_no_passage(fake_runtime):
    child = "t_0a0b0c71"
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            root_card(),
            root_card(
                id=child,
                title="essai:7 · brouillon (passage 1)",
                body="essai:7",
                assignee="essai-agent",
                status="archived",
                idempotency_key="essai:7/brouillon/1",
            ),
        ],
        events=[{"task_id": child, "kind": "created", "payload": "{}", "created_at": 1762900001}],
    )
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    for _ in range(3):
        core.run_pass()

    assert fake_runtime.writes() == []
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    assert len([line for line in lines if f"carte fille {child} archivée" in line]) == 1


def test_a_second_passage_gets_a_new_child(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    agent = FakeAgent(fake_runtime.home)
    box = answer_box()
    core, _ = engine(launches=Launches(), workflow=essai_agent(), answers=box)

    core.run_pass()
    agent.complete()
    core.run_pass()
    core.run_pass()
    assert card(fake_runtime.home) == ("scheduled", "essai-revue")

    # The owner sends the item back to its draft: the done child of the first passage is not the current one.
    box.receive("essai:7", "refaire")
    core.run_pass()
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    core.run_pass()

    assert creates(fake_runtime) == [child_create(1), child_create(2)]
    assert children(fake_runtime.home) == [
        ("essai:7/brouillon/1", "done", "essai-agent"),
        ("essai:7/brouillon/2", "ready", "essai-agent"),
    ]
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")

    # The second child done, the root card goes to the review again.
    agent.complete(passage=2)
    core.run_pass()
    assert card(fake_runtime.home) == ("ready", "essai-revue")
    never_touched(fake_runtime, agent)


def test_the_agent_never_touches_the_root_card(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    agent = FakeAgent(fake_runtime.home)
    box = answer_box()
    core, _ = engine(launches=Launches(), workflow=essai_agent(), answers=box)

    # The whole item: a draft, blocked then unblocked, done, reviewed, closed.
    core.run_pass()
    agent.block()
    core.run_pass()
    agent.unblock()
    agent.complete()
    core.run_pass()
    core.run_pass()
    box.receive("essai:7", "valider")
    core.run_pass()
    core.run_pass()

    assert card(fake_runtime.home) == ("done", "essai-clore")
    assert [call[3] for call in fake_runtime.writes() if ROOT in call] == [
        "assign",
        "schedule",
        "unblock",
        "assign",
        "complete",
    ]
    assert not any("claim" in argument for call in fake_runtime.calls() for argument in call)
    never_touched(fake_runtime, agent)


def signals() -> list[tuple[str, str, str, str, str | None]]:
    """The anomalies noted in the engine's base: key, kind, station, detail and issue, oldest first."""
    return [
        (signal.key, signal.kind, signal.station, signal.detail, signal.issue)
        for signal in Signals(Path(os.environ["WORKFLOW_CORE_STATE_DIR"]) / ENGINE_DB).signals_of("essai")
    ]


def set_card(home: Path, **fields) -> None:
    """Change the root card in the base, as another than the engine would."""
    with sqlite3.connect(base(home)) as db:
        db.execute(
            f"UPDATE tasks SET {', '.join(f'{name} = ?' for name in fields)} WHERE id = ?", (*fields.values(), ROOT)
        )
    db.close()


def test_a_root_card_taken_by_an_agent_is_an_anomaly(fake_runtime):
    run = {
        "task_id": ROOT,
        "profile": "essai-agent",
        "status": "done",
        "started_at": 1762900010,
        "ended_at": 1762900020,
    }
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()], runs=[run])
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()
    core.run_pass()
    # A restarted engine does not tell it again.
    engine(launches=Launches(), workflow=essai_agent())[0].run_pass()

    anomalies = [line for line in lines if line.startswith("<4>") and "anomalie" in line]
    assert anomalies == [
        f"<4>workflow-core: moteur, essai:7 : anomalie : carte racine {ROOT} prise par essai-agent (run 1)"
    ]
    assert signals() == [
        (
            f"essai:taken:{ROOT}:1",
            "taken",
            "brouillon",
            f"La carte racine {ROOT} a été prise par essai-agent (run 1, terminé).",
            ISSUE,
        )
    ]
    # The run over, the item is still handled: its child card is asked for once.
    assert creates(fake_runtime) == [child_create(1)]
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")
    never_touched(fake_runtime)


def test_a_root_card_with_a_run_in_progress_is_left_alone_while_it_runs(fake_runtime):
    run = {"task_id": ROOT, "profile": "essai-agent", "status": "running", "started_at": 1762900010}
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[root_card(assignee="essai-revue", status="scheduled")],
        runs=[run],
        events=[{"task_id": ROOT, "kind": "scheduled", "payload": "{}", "created_at": 1762900005}],
    )
    box = answer_box()
    answer = box.receive("essai:7", "valider")
    launches = Launches()
    core, lines = engine(launches=launches, workflow=essai_agent(), answers=box)

    for _ in range(3):
        core.run_pass()

    # Nothing written, nothing launched, and the owner's answer neither applied nor refused.
    assert fake_runtime.writes() == []
    assert launches.calls == []
    assert box.is_pending(answer)
    anomalies = [line for line in lines if line.startswith("<4>") and "anomalie" in line]
    assert anomalies == [
        f"<4>workflow-core: moteur, essai:7 : anomalie : carte racine {ROOT} prise par essai-agent (run 1), "
        "élément laissé tant que le run dure"
    ]
    assert signals() == [
        (
            f"essai:taken:{ROOT}:1",
            "taken",
            "revue",
            f"La carte racine {ROOT} a été prise par essai-agent (run 1, en cours).",
            ISSUE,
        )
    ]

    # The run over, the item is handled as usual, without a second line or note for the same run.
    with sqlite3.connect(base(fake_runtime.home)) as db:
        db.execute("UPDATE task_runs SET status = 'done', ended_at = 1762900030")
    db.close()
    core.run_pass()
    assert launches.calls[0][0] == "revue"
    assert not box.is_pending(answer)
    assert len([line for line in lines if "anomalie" in line]) == 1
    assert len(signals()) == 1


def finished_elsewhere(fake_runtime, assignee: str) -> list[str]:
    """A root card done at the lane, that the engine did not finish: two passes, then the card reopened and a
    restarted engine's pass. The engine's journal lines."""
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[root_card(assignee=assignee, status="done", completed_at=1762900020)],
    )
    box = answer_box()
    core, lines = engine(launches=Launches(), workflow=essai_agent(), answers=box)
    core.run_pass()
    core.run_pass()
    set_card(fake_runtime.home, status="ready")
    launches = Launches()
    again, more = engine(launches=launches, workflow=essai_agent(), answers=box)
    again.run_pass()
    assert launches.calls == []
    assert more == ["workflow-core: moteur, mode normal : les passes lancent les commandes et écrivent"]
    return lines


def test_a_root_card_finished_by_another_is_an_anomaly(fake_runtime):
    lines = finished_elsewhere(fake_runtime, "essai-brouillon")

    assert fake_runtime.writes() == []
    anomalies = [line for line in lines if line.startswith("<4>") and "anomalie" in line]
    assert anomalies == [
        f"<4>workflow-core: moteur, essai:7 : anomalie : carte racine {ROOT} terminée à la station brouillon "
        "par un autre que le moteur, élément abandonné par le moteur"
    ]
    assert signals() == [
        (
            f"essai:finished_elsewhere:{ROOT}",
            "finished_elsewhere",
            "brouillon",
            f"La carte racine {ROOT} a été terminée à la station brouillon par un autre que le moteur.",
            ISSUE,
        )
    ]


def test_a_root_card_finished_by_another_at_the_last_station_is_an_anomaly(fake_runtime):
    lines = finished_elsewhere(fake_runtime, "essai-clore")

    assert fake_runtime.writes() == []
    anomalies = [line for line in lines if line.startswith("<4>") and "anomalie" in line]
    assert anomalies == [
        f"<4>workflow-core: moteur, essai:7 : anomalie : carte racine {ROOT} terminée à la station clore "
        "par un autre que le moteur, élément abandonné par le moteur"
    ]
    assert [signal[0] for signal in signals()] == [f"essai:finished_elsewhere:{ROOT}"]


def test_a_root_card_finished_by_the_engine_is_never_an_anomaly(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-clore")])
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()
    core.run_pass()
    assert card(fake_runtime.home) == ("done", "essai-clore")
    restarted, more = engine(launches=Launches(), workflow=essai_agent())
    restarted.run_pass()

    assert not any("anomalie" in line for line in lines + more)
    assert signals() == []


def test_a_finish_the_board_refuses_is_not_kept_as_the_engine_s(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-clore")])
    fake_runtime.fail("complete")
    core, _ = engine(launches=Launches(), workflow=essai_agent())
    core.run_pass()
    assert card(fake_runtime.home) == ("ready", "essai-clore")

    # Then finished by another: an anomaly, the engine's refused finish not counted as its own.
    set_card(fake_runtime.home, status="done")
    core.run_pass()
    assert [signal[0] for signal in signals()] == [f"essai:finished_elsewhere:{ROOT}"]


def test_no_child_is_created_in_observer_mode(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    core, _ = engine(core=state(ok=False), launches=Launches(), workflow=essai_agent())

    core.run_pass()
    core.run_pass()

    assert fake_runtime.calls() == []
    assert children(fake_runtime.home) == []
    assert card(fake_runtime.home) == ("ready", "essai-brouillon")


def test_a_run_the_runtime_opens_on_a_lane_is_not_an_agent(fake_runtime):
    # The runtime opens a run of its own when a card at a lane is scheduled, blocked or completed: its profile is the
    # lane, which is never a launchable profile, so it is no anomaly and the item is handled as usual.
    run = {
        "task_id": ROOT,
        "profile": "essai-revue",
        "status": "scheduled",
        "started_at": 1762900010,
        "ended_at": 1762900010,
    }
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[root_card(assignee="essai-revue", status="scheduled")],
        runs=[run],
        events=[{"task_id": ROOT, "kind": "scheduled", "payload": "{}", "created_at": 1762900005}],
    )
    core, lines = engine(launches=Launches(), workflow=essai_agent())

    core.run_pass()

    assert [line for line in lines if "anomalie" in line] == []
    assert signals() == []


class CompletesThenTimesOut:
    """A writer whose `complete` is written by the runtime, then passes its delay: the core never hears back."""

    def __init__(self, writer) -> None:
        self.writer = writer

    def __getattr__(self, name):
        return getattr(self.writer, name)

    def complete(self, board, card, result):
        self.writer.complete(board, card, result)
        raise BoardWriteError("hermes kanban --board <board> complete <card> --result <result>", None, "délai dépassé")


def test_a_finish_past_its_delay_that_the_runtime_wrote_is_kept_as_the_engine_s(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-clore")])
    core, lines = engine(launches=Launches(), workflow=essai_agent())
    core.writer = CompletesThenTimesOut(core.writer)

    core.run_pass()
    assert card(fake_runtime.home) == ("done", "essai-clore")
    core.run_pass()

    # The card read again is done: the engine's own finish, never finished by another.
    assert signals() == []
    assert not any("anomalie" in line for line in lines)
