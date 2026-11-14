"""The engine at an `owner` station: the card waits for the owner, the owner's answer resumes it once, and an answer
without an open wait is refused (ADR 0004, amended; ADR 0005; ADR 0019)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from workflow_core.engine.answers import ENGINE_DB, AnswerBox
from workflow_core.engine.engine import open_wait

from conftest import make_runtime_board
from test_engine import CARD, Launches, engine, root_card, state

REPO_ROOT = Path(__file__).resolve().parent.parent

WAITING = "En attente du propriétaire à la station revue"


def board_base(home: Path, board: str = "essai") -> Path:
    return home / "kanban" / "boards" / board / "kanban.db"


def card_row(home: Path, card: str = CARD) -> tuple[str, str]:
    with sqlite3.connect(board_base(home)) as db:
        found = db.execute("SELECT status, assignee FROM tasks WHERE id = ?", (card,)).fetchone()
    db.close()
    return found


def scheduled_events(home: Path, card: str = CARD) -> list[int]:
    with sqlite3.connect(board_base(home)) as db:
        rows = db.execute(
            "SELECT id FROM task_events WHERE task_id = ? AND kind = 'scheduled' ORDER BY id", (card,)
        ).fetchall()
    db.close()
    return [row[0] for row in rows]


def test_an_owner_station_schedules_the_card_with_its_wait_id(fake_runtime):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-revue")])
    launches = Launches()
    core, lines = engine(launches=launches)

    core.run_pass()

    assert fake_runtime.writes() == [["kanban", "--board", "essai", "schedule", CARD, WAITING]]
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")
    (event,) = scheduled_events(fake_runtime.home)
    wait = f"essai:owner:{CARD}:{event}"
    assert any(f"attente {wait} ouverte" in line for line in lines)
    (item,) = core.reader.read_items("essai")
    assert open_wait(core.workflows[0], item) == wait

    # A second pass calls nothing: the card waits.
    core.run_pass()
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "schedule", CARD, WAITING]]
    assert launches.calls == []


def answer_box() -> AnswerBox:
    return AnswerBox(Path(os.environ["WORKFLOW_CORE_STATE_DIR"]) / ENGINE_DB)


def handled(box: AnswerBox, answer: str) -> tuple[str | None, str | None]:
    """The answer's outcome and the wait it was applied to."""
    with sqlite3.connect(box.path) as db:
        found = db.execute("SELECT outcome, wait FROM answers WHERE id = ?", (answer,)).fetchone()
    db.close()
    return found


def parked(fake_runtime, box: AnswerBox, launches: Launches):
    """A root card at `essai-revue`, parked by a first pass: the engine and the wait's id."""
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-revue")])
    core, lines = engine(launches=launches, answers=box)
    core.run_pass()
    (event,) = scheduled_events(fake_runtime.home)
    return core, lines, f"essai:owner:{CARD}:{event}"


def test_an_answer_resumes_the_card_once(fake_runtime):
    box, launches = answer_box(), Launches()
    core, lines, wait = parked(fake_runtime, box, launches)
    answer = box.receive("essai:1", "valider")

    core.run_pass()

    assert launches.calls == [
        (
            "revue",
            {
                "schema_version": 1,
                "item": {"id": "essai:1", "issue": root_card()["body"], "title": "essai:1 · Premier essai"},
                "station": "revue",
                "attempt": 1,
                "answer": {"gesture": "valider", "note": ""},
            },
        )
    ]
    assert fake_runtime.writes() == [
        ["kanban", "--board", "essai", "schedule", CARD, WAITING],
        ["kanban", "--board", "essai", "unblock", CARD],
        ["kanban", "--board", "essai", "assign", CARD, "essai-clore"],
    ]
    assert handled(box, answer) == ("applied", wait)
    assert any(f"réponse {answer} (valider) appliquée à l'attente {wait}" in line for line in lines)

    # Read again at the next passes, the answer is never handed to a command again: `clore` ends the item.
    for _ in range(3):
        core.run_pass()
    assert [station for station, _ in launches.calls] == ["revue", "clore"]
    assert box.pending("essai:") == []
    assert card_row(fake_runtime.home) == ("done", "essai-clore")


def test_an_answer_without_an_open_wait_is_refused(fake_runtime):
    # A blocked card at the owner station has no open wait, and the second item has no card at all.
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-revue", status="blocked")])
    box, launches = answer_box(), Launches()
    core, lines = engine(launches=launches, answers=box)
    blocked = box.receive("essai:1", "valider")
    unknown = box.receive("essai:9", "valider")

    core.run_pass()
    core.run_pass()

    assert fake_runtime.writes() == []
    assert launches.calls == []
    assert handled(box, blocked) == ("refused", None)
    assert handled(box, unknown) == ("refused", None)
    assert len([line for line in lines if "refusée, aucune attente ouverte" in line]) == 2


def test_redo_sends_the_card_back_to_preparer(fake_runtime):
    box, launches = answer_box(), Launches()
    core, _, wait = parked(fake_runtime, box, launches)
    answer = box.receive("essai:1", "refaire", "à reprendre")

    core.run_pass()
    assert fake_runtime.writes()[1:] == [
        ["kanban", "--board", "essai", "unblock", CARD],
        ["kanban", "--board", "essai", "assign", CARD, "essai-preparer"],
    ]
    assert handled(box, answer) == ("applied", wait)
    assert launches.calls[0][1]["answer"] == {"gesture": "refaire", "note": "à reprendre"}

    # At the next pass, `preparer` runs again and sends the card back to the review.
    core.run_pass()
    assert [station for station, _ in launches.calls] == ["revue", "preparer"]
    assert card_row(fake_runtime.home) == ("ready", "essai-revue")


def test_answers_wait_in_observer_mode(fake_runtime):
    box, launches = answer_box(), Launches()
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-revue")])
    core_state = state()
    core, _ = engine(core=core_state, launches=launches, answers=box)
    core.run_pass()
    writes = fake_runtime.writes()
    answer = box.receive("essai:1", "valider")

    core_state.contract = state(ok=False).contract
    core.run_pass()
    core.run_pass()

    assert fake_runtime.writes() == writes
    assert launches.calls == []
    assert [pending.id for pending in box.pending("essai:")] == [answer]
    assert handled(box, answer) == (None, None)

    # The first pass in normal mode applies it.
    core_state.contract = state().contract
    core.run_pass()
    assert fake_runtime.writes()[len(writes) :] == [
        ["kanban", "--board", "essai", "unblock", CARD],
        ["kanban", "--board", "essai", "assign", CARD, "essai-clore"],
    ]
    assert handled(box, answer)[0] == "applied"


def test_a_wait_survives_a_restart(fake_runtime):
    box, launches = answer_box(), Launches()
    _, _, wait = parked(fake_runtime, box, launches)
    answer = box.receive("essai:1", "valider")

    # A new engine, as after a restart of the service, with the same state folder.
    restarted, _ = engine(launches=launches, answers=answer_box())
    (item,) = restarted.reader.read_items("essai")
    assert open_wait(restarted.workflows[0], item) == wait
    restarted.run_pass()

    assert handled(box, answer) == ("applied", wait)
    assert card_row(fake_runtime.home) == ("ready", "essai-clore")
    assert [station for station, _ in launches.calls] == ["revue"]


def test_the_test_machine_tool_puts_an_answer_in_the_box(tmp_path):
    folder = tmp_path / "etat-essai"

    done = subprocess.run(
        [
            sys.executable,
            "scripts/inject_answer.py",
            "--state-dir",
            str(folder),
            "--item",
            "essai:2",
            "--gesture",
            "valider",
            "--note",
            "vu",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    (answer,) = AnswerBox(folder / ENGINE_DB).pending("essai:")
    assert (answer.id, answer.item, answer.gesture, answer.note) == (done.stdout.strip(), "essai:2", "valider", "vu")


def test_an_answer_applied_by_another_pass_is_not_handed_again(fake_runtime):
    box, launches = answer_box(), Launches()
    core, _, wait = parked(fake_runtime, box, launches)
    # A gesture `revue` does not know: its command returns `wait`, and the card stays waiting.
    answer = box.receive("essai:1", "plus-tard")
    stale = box.pending("essai:")

    core.run_pass()
    assert handled(box, answer) == ("applied", wait)
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")

    class StaleBox(AnswerBox):
        """A pass that read the answers before the other pass applied this one."""

        def pending(self, prefix: str):
            return stale

    other, _ = engine(launches=launches, answers=StaleBox(box.path))
    other.run_pass()

    assert [station for station, _ in launches.calls] == ["revue"]
    assert fake_runtime.writes() == [["kanban", "--board", "essai", "schedule", CARD, WAITING]]


def test_a_decision_not_written_after_its_answer_is_told_in_the_journal(fake_runtime):
    box, launches = answer_box(), Launches()
    core, lines, wait = parked(fake_runtime, box, launches)
    answer = box.receive("essai:1", "valider")
    fake_runtime.fail("assign")

    core.run_pass()

    assert handled(box, answer) == ("applied", wait)
    told = f"réponse {answer} (valider) appliquée à l'attente {wait}, mais la décision de revue n'a pas pu être écrite"
    assert any(line.startswith("<3>") and told in line for line in lines)
