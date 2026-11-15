"""The owner's gestures: a comment `/essai <gesture> [note]` of the owner's account on the issue of an item is put in
the answer box once, applied or refused by the engine at its pass, and gets the App's reaction, 👍 or 😕 (story 7 of
the engine's epic, code-my-life/workflow-core#57)."""

from __future__ import annotations

import dataclasses
import sqlite3
import time
from datetime import UTC, datetime

from workflow_core.adapters import runtime_adapter
from workflow_core.engine.answers import AnswerBox
from workflow_core.gestures import Gestures
from workflow_core.intake import GITHUB_POLL_SECONDS, IntakeRunner

from conftest import make_runtime_board
from test_engine import CARD, Launches, engine, essai, root_card, state
from test_engine_owner import WAITING, answer_box, card_row, scheduled_events
from test_intake import OWNER, REPO, Clock, FakeGitHub, issue, issue_url

OTHER_CARD = "t_0e0e0e0e"


def reading(github, box: AnswerBox, *, core=None, workflow=None, clock=time.time, owner=OWNER):
    """The reading of the owner's gestures for the trial workflow, and its journal lines."""
    lines: list[str] = []
    built = Gestures(
        workflows=[workflow or essai()],
        github=github,
        reader=runtime_adapter().make_board_reader(),
        answers=box,
        state=core or state(),
        log=lines.append,
        clock=clock,
        owner=owner,
    )
    return built, lines


def handled(box: AnswerBox, answer: str) -> tuple | None:
    """The answer's item, gesture, note, outcome and wait."""
    with sqlite3.connect(box.path) as db:
        found = db.execute("SELECT item, gesture, note, outcome, wait FROM answers WHERE id = ?", (answer,)).fetchone()
    db.close()
    return found


def answer_ids(box: AnswerBox) -> list[str]:
    if not box.path.is_file():
        return []
    with sqlite3.connect(box.path) as db:
        found = [row[0] for row in db.execute("SELECT id FROM answers ORDER BY rowid")]
    db.close()
    return found


def events(home, kind: str, card: str = CARD) -> list[int]:
    with sqlite3.connect(home / "kanban" / "boards" / "essai" / "kanban.db") as db:
        rows = db.execute(
            "SELECT id FROM task_events WHERE task_id = ? AND kind = ? ORDER BY id", (card, kind)
        ).fetchall()
    db.close()
    return [row[0] for row in rows]


def parked(fake_runtime, box: AnswerBox, launches: Launches, core=None):
    """A root card at `essai-revue` for the issue 1, parked by a first pass: the engine and the wait's id."""
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card(assignee="essai-revue")])
    built, lines = engine(core=core, launches=launches, answers=box)
    built.run_pass()
    (event,) = scheduled_events(fake_runtime.home)
    return built, lines, f"essai:owner:{CARD}:{event}"


def test_an_owner_gesture_resumes_the_wait_and_gets_a_thumbs_up(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, wait = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider c'est bon\n\nVu sur l'écran.")
    gestures, lines = reading(serve(fake), box)

    gestures.read_once()
    assert answer_ids(box) == [f"github:{comment}"]
    core.run_pass()
    gestures.react()
    gestures.react()

    assert launches.calls[-1][0] == "revue"
    assert launches.calls[-1][1]["answer"] == {"gesture": "valider", "note": "c'est bon"}
    assert handled(box, f"github:{comment}") == ("essai:1", "valider", "c'est bon", "applied", wait)
    assert card_row(fake_runtime.home) == ("ready", "essai-clore")
    assert fake.reacted == [(comment, "+1")]
    assert fake_runtime.writes() == [
        ["kanban", "--board", "essai", "schedule", CARD, "--", WAITING],
        ["kanban", "--board", "essai", "unblock", CARD],
        ["kanban", "--board", "essai", "assign", CARD, "essai-clore"],
    ]
    assert not any(line.startswith(("<3>", "<4>")) for line in lines), lines


def test_a_gesture_on_an_issue_of_any_issues_repo_is_read_and_reacted_to_there(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, _ = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider")
    # The item's issue is in the second repository of the manifest's list; the fake knows that one alone.
    workflow = dataclasses.replace(essai(), issues_repos=("code-my-life/autre-essai", REPO))
    gestures, lines = reading(serve(fake), box, workflow=workflow)

    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert answer_ids(box) == [f"github:{comment}"]
    assert handled(box, f"github:{comment}")[3] == "applied"
    assert fake.reacted == [(comment, "+1")]
    assert not any(line.startswith(("<3>", "<4>")) for line in lines), lines


def test_a_gesture_read_again_or_after_a_lost_registry_is_not_applied_twice(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, _ = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    github = serve(fake)
    comment = fake.add_comment(1, OWNER, "/essai valider")
    now = time.time()
    gestures, _ = reading(github, box, clock=lambda: now)

    gestures.read_once()
    core.run_pass()
    gestures.react()
    # Read again: from the previous reading on, two minutes earlier, so the same comment comes back.
    gestures.read_once()
    for _ in range(3):
        core.run_pass()
    gestures.react()

    since = datetime.fromtimestamp(now - 120, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert fake.comment_reads == [(1, None), (1, since)]
    assert answer_ids(box) == [f"github:{comment}"]
    assert [station for station, _ in launches.calls] == ["revue", "clore"]
    assert card_row(fake_runtime.home) == ("done", "essai-clore")
    writes = fake_runtime.writes()

    # The registry lost, a new core reads every comment again: the App's reaction is enough.
    box.path.unlink()
    lost = AnswerBox(box.path)
    again, _ = reading(github, lost)
    again.read_once()
    again.react()
    restarted, _ = engine(launches=launches, answers=lost)
    restarted.run_pass()

    assert answer_ids(lost) == []
    assert fake.reacted == [(comment, "+1")]
    assert fake_runtime.writes() == writes


def test_a_gesture_without_an_open_wait_or_older_than_it_gets_a_confused_face(fake_runtime, serve):
    # The item 1 is done, its issue still open; the item 2 waits at the review for its issue 2.
    make_runtime_board(
        fake_runtime.home,
        "essai",
        tasks=[
            root_card(assignee="essai-clore", status="done"),
            root_card(
                id=OTHER_CARD,
                assignee="essai-revue",
                idempotency_key="essai:2",
                title="essai:2 · Second essai",
                body=issue_url(2),
            ),
        ],
    )
    box, launches = answer_box(), Launches()
    core, _ = engine(launches=launches, answers=box)
    core.run_pass()
    writes = fake_runtime.writes()
    assert card_row(fake_runtime.home, OTHER_CARD) == ("scheduled", "essai-revue")
    fake = FakeGitHub([issue(1), issue(2)])
    after_the_end = fake.add_comment(1, OWNER, "/essai valider")
    # Commented before the wait opened: a stale screen.
    stale = fake.add_comment(2, OWNER, "/essai valider", created_at="2020-01-01T00:00:00Z")
    gestures, _ = reading(serve(fake), box)

    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{after_the_end}")[3:] == ("refused", None)
    assert handled(box, f"github:{stale}")[3:] == ("refused", None)
    assert sorted(fake.reacted) == sorted([(after_the_end, "confused"), (stale, "confused")])
    assert launches.calls == []
    assert fake_runtime.writes() == writes
    assert card_row(fake_runtime.home) == ("done", "essai-clore")
    assert card_row(fake_runtime.home, OTHER_CARD) == ("scheduled", "essai-revue")


def test_other_accounts_and_other_workflows_are_ignored(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, _ = parked(fake_runtime, box, launches)
    writes = fake_runtime.writes()
    fake = FakeGitHub([issue(1)])
    fake.add_comment(1, "inconnu", "/essai valider")
    fake.add_comment(1, OWNER, "/factory unblock")
    # The case counts, and the gesture must open the comment.
    fake.add_comment(1, OWNER, "/Essai valider")
    fake.add_comment(1, OWNER, "Je pense que /essai valider suffira")
    gestures, _ = reading(serve(fake), box)

    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert answer_ids(box) == []
    assert fake.reacted == []
    assert launches.calls == []
    assert fake_runtime.writes() == writes
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")


def test_without_the_owner_s_login_no_gesture_is_taken(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    fake.add_comment(1, OWNER, "/essai valider")
    gestures, _ = reading(serve(fake), box, owner=None)

    gestures.read_once()
    gestures.react()

    assert answer_ids(box) == []
    assert fake.reacted == []


def test_an_unknown_gesture_gets_a_confused_face(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, lines, _ = parked(fake_runtime, box, launches)
    writes = fake_runtime.writes()
    fake = FakeGitHub([issue(1)])
    unknown = fake.add_comment(1, OWNER, "/essai inconnu")
    # `ouvrir` is a gesture of the manifest, but given by opening an issue, never by a comment.
    opening = fake.add_comment(1, OWNER, "/essai ouvrir")
    gestures, _ = reading(serve(fake), box)

    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{unknown}")[3:] == ("refused", None)
    assert handled(box, f"github:{opening}")[3:] == ("refused", None)
    assert fake.reacted == [(unknown, "confused"), (opening, "confused")]
    assert launches.calls == []
    assert fake_runtime.writes() == writes
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")
    assert len([line for line in lines if "refusée, geste inconnu du workflow" in line]) == 2


def relance() -> object:
    """The trial workflow whose `preparer` fails until the owner unblocks it."""
    return essai(preparer={"command": ["python", "commands/relance.py"]})


def test_unblock_resumes_a_blocked_card_with_its_note(fake_runtime, serve):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    box, launches = answer_box(), Launches()
    core, _ = engine(launches=launches, answers=box, workflow=relance())
    core.run_pass()
    assert card_row(fake_runtime.home) == ("blocked", "essai-preparer")
    (blocked,) = events(fake_runtime.home, "blocked")
    fake = FakeGitHub([issue(1)])
    github = serve(fake)
    comment = fake.add_comment(1, OWNER, "/essai unblock relance")
    gestures, _ = reading(github, box, workflow=relance())

    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert [call[3] for call in fake_runtime.writes()] == ["block", "unblock", "assign"]
    assert fake_runtime.writes()[-1] == ["kanban", "--board", "essai", "assign", CARD, "essai-revue"]
    assert [(station, payload["answer"]) for station, payload in launches.calls] == [
        ("preparer", None),
        ("preparer", {"gesture": "unblock", "note": "relance"}),
    ]
    assert handled(box, f"github:{comment}")[3:] == ("applied", f"essai:blocked:{CARD}:{blocked}")
    assert fake.reacted == [(comment, "+1")]

    # The card waits at the review now: an `unblock` on a card not blocked is refused.
    core.run_pass()
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")
    writes = fake_runtime.writes()
    again = fake.add_comment(1, OWNER, "/essai unblock")
    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{again}")[3:] == ("refused", None)
    assert fake.reacted == [(comment, "+1"), (again, "confused")]
    assert fake_runtime.writes() == writes


def test_an_unblock_not_written_gets_a_confused_face_and_can_be_given_again(fake_runtime, serve, monkeypatch):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    box, launches = answer_box(), Launches()
    core, lines = engine(launches=launches, answers=box, workflow=relance())
    core.run_pass()
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai unblock relance")
    gestures, _ = reading(serve(fake), box, workflow=relance())
    gestures.read_once()
    fake_runtime.fail("unblock")

    core.run_pass()
    core.run_pass()
    gestures.react()

    # Not written, the unblock is not counted as applied: refused, with a confused face, and the card stays blocked.
    assert handled(box, f"github:{comment}")[3:] == ("refused", None)
    assert fake.reacted == [(comment, "confused")]
    told = f"réponse github:{comment} (unblock) refusée"
    assert any(line.startswith("<3>") and told in line and "n'a pas pu être débloquée" in line for line in lines)
    assert [call[3] for call in fake_runtime.writes()] == ["block", "unblock"]
    assert [station for station, _ in launches.calls] == ["preparer"]
    assert card_row(fake_runtime.home) == ("blocked", "essai-preparer")

    # The owner gives it again, and this time it is written.
    monkeypatch.delenv("FAKE_RUNTIME_FAIL")
    again = fake.add_comment(1, OWNER, "/essai unblock relance")
    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{again}")[3] == "applied"
    assert fake.reacted == [(comment, "confused"), (again, "+1")]
    assert [station for station, _ in launches.calls] == ["preparer", "preparer"]
    assert [call[3] for call in fake_runtime.writes()] == ["block", "unblock", "unblock", "assign"]


def test_redo_is_not_applied_to_the_next_wait(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, wait = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai refaire à reprendre")
    gestures, _ = reading(serve(fake), box)

    gestures.read_once()
    core.run_pass()
    assert card_row(fake_runtime.home) == ("ready", "essai-preparer")
    # `preparer` sends the item back to the review, where it waits again.
    core.run_pass()
    core.run_pass()
    assert len(scheduled_events(fake_runtime.home)) == 2
    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert [station for station, _ in launches.calls] == ["revue", "preparer"]
    assert launches.calls[0][1]["answer"] == {"gesture": "refaire", "note": "à reprendre"}
    assert handled(box, f"github:{comment}")[3:] == ("applied", wait)
    assert answer_ids(box) == [f"github:{comment}"]
    assert card_row(fake_runtime.home) == ("scheduled", "essai-revue")
    assert fake.reacted == [(comment, "+1")]


def test_gestures_wait_in_observer_mode(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core_state = state()
    core, _, wait = parked(fake_runtime, box, launches, core=core_state)
    writes = fake_runtime.writes()
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider")
    gestures, _ = reading(serve(fake), box, core=core_state)

    core_state.contract = state(ok=False).contract
    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert answer_ids(box) == [f"github:{comment}"]
    assert handled(box, f"github:{comment}")[3:] == (None, None)
    assert launches.calls == []
    assert fake_runtime.writes() == writes
    assert fake.reacted == []

    # Back in normal mode: applied once, and reacted to once.
    core_state.contract = state().contract
    core.run_pass()
    gestures.react()
    gestures.read_once()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{comment}")[3:] == ("applied", wait)
    assert [station for station, _ in launches.calls] == ["revue", "clore"]
    assert fake.reacted == [(comment, "+1")]


def test_a_refused_reaction_is_tried_again_after_the_next_reading(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, _ = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider")
    gestures, lines = reading(serve(fake), box)
    gestures.read_once()
    core.run_pass()
    fake.refuse_reactions = 403

    gestures.react()
    gestures.react()
    assert fake.reaction_tries == 1
    assert fake.reacted == []
    assert any(line.startswith("<3>") and "réaction" in line for line in lines), lines

    fake.refuse_reactions = None
    gestures.react()
    assert fake.reaction_tries == 1
    gestures.read_once()
    gestures.react()
    gestures.react()

    assert fake.reaction_tries == 2
    assert fake.reacted == [(comment, "+1")]
    # The comment read again carries the App's reaction: it is not taken again.
    assert answer_ids(box) == [f"github:{comment}"]


def test_a_reaction_to_a_deleted_comment_is_given_up(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core, _, _ = parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider")
    gestures, lines = reading(serve(fake), box)
    gestures.read_once()
    core.run_pass()
    # The comment was deleted meanwhile: GitHub answers 404 to its reaction.
    fake.refuse_reactions = 404

    gestures.react()
    for _ in range(2):
        gestures.read_once()
        gestures.react()
        gestures.react()

    assert fake.reaction_tries == 1
    assert fake.reacted == []
    told = [line for line in lines if f"github:{comment}" in line and "réaction" in line]
    assert len(told) == 1, told
    assert "abandonnée" in told[0] and "404" in told[0]
    assert box.unreacted() == []


def test_a_box_created_before_the_gestures_gets_their_columns(tmp_path):
    path = tmp_path / "engine.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE answers (id TEXT PRIMARY KEY, item TEXT NOT NULL, gesture TEXT NOT NULL, "
            "note TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL, wait TEXT, "
            "outcome TEXT CHECK (outcome IN ('applied', 'refused')), handled_at TEXT)"
        )
        db.execute("INSERT INTO answers (id, item, gesture, received_at) VALUES ('a_1', 'essai:1', 'valider', 'x')")
    db.close()
    box = AnswerBox(path)

    box.receive("essai:1", "valider", answer_id="github:7", source="github", received_at="2026-11-14T10:00:00+00:00")

    assert [(answer.id, answer.source) for answer in box.pending("essai:")] == [
        ("github:7", "github"),
        ("a_1", "local"),
    ]
    assert box.mark("github:7", "refused")
    assert box.unreacted() == [("github:7", "essai:1", "refused")]
    box.mark_reacted("github:7")
    assert box.unreacted() == []


def test_an_unblock_given_before_the_card_s_last_block_is_refused(fake_runtime, serve):
    failing = essai(preparer={"command": ["python", "commands/fails.py"]})
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    box, launches = answer_box(), Launches()
    core, lines = engine(launches=launches, answers=box, workflow=failing)
    core.run_pass()
    (first_block,) = events(fake_runtime.home, "blocked")
    # The first block is old: both gestures were given after it.
    with sqlite3.connect(fake_runtime.home / "kanban" / "boards" / "essai" / "kanban.db") as db:
        db.execute("UPDATE task_events SET created_at = 1000 WHERE id = ?", (first_block,))
    db.close()
    fake = FakeGitHub([issue(1)])
    first = fake.add_comment(1, OWNER, "/essai unblock", created_at="2020-01-01T00:00:00Z")
    second = fake.add_comment(1, OWNER, "/essai unblock encore", created_at="2020-01-01T00:00:01Z")
    gestures, _ = reading(serve(fake), box, workflow=failing)

    gestures.read_once()
    # The first unblock is applied, the command run again fails, and the card is blocked again.
    core.run_pass()
    assert card_row(fake_runtime.home) == ("blocked", "essai-preparer")
    assert len(events(fake_runtime.home, "blocked")) == 2
    # The second one was given before that new block: it does not lift it.
    core.run_pass()
    core.run_pass()
    gestures.react()

    assert handled(box, f"github:{first}")[3:] == ("applied", f"essai:blocked:{CARD}:{first_block}")
    assert handled(box, f"github:{second}")[3:] == ("refused", None)
    assert fake.reacted == [(first, "+1"), (second, "confused")]
    assert [call[3] for call in fake_runtime.writes()] == ["block", "unblock", "block"]
    assert [station for station, _ in launches.calls] == ["preparer", "preparer"]
    assert card_row(fake_runtime.home) == ("blocked", "essai-preparer")
    assert any("<4>" in line and f"github:{second}" in line and "refusée" in line for line in lines)


def test_a_handled_gesture_is_not_reacted_to_in_observer_mode(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    core_state = state()
    core, _, wait = parked(fake_runtime, box, launches, core=core_state)
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai valider")
    gestures, _ = reading(serve(fake), box, core=core_state)
    gestures.read_once()
    # Handled in normal mode.
    core.run_pass()
    assert handled(box, f"github:{comment}")[3:] == ("applied", wait)

    core_state.contract = state(ok=False).contract
    gestures.react()

    assert fake.reacted == []
    assert box.unreacted() == [(f"github:{comment}", "essai:1", "applied")]

    # Back in normal mode, the reaction is posted.
    core_state.contract = state().contract
    gestures.react()
    assert fake.reacted == [(comment, "+1")]
    assert box.unreacted() == []


def test_an_unblock_the_writer_s_gate_refuses_stays_pending(fake_runtime, serve):
    make_runtime_board(fake_runtime.home, "essai", tasks=[root_card()])
    box, launches = answer_box(), Launches()
    core, _ = engine(launches=launches, answers=box, workflow=relance())
    core.run_pass()
    writes = fake_runtime.writes()
    fake = FakeGitHub([issue(1)])
    comment = fake.add_comment(1, OWNER, "/essai unblock relance")
    gestures, _ = reading(serve(fake), box, workflow=relance())
    gestures.read_once()
    refusal = ["version d'Hermès pas encore relue"]
    # Only the writer's gate refuses: the engine itself is not told.
    core.writer.gate = lambda: refusal[0]

    core.run_pass()
    gestures.react()

    # Nothing consumed: the answer still pending, no reaction, the card still blocked.
    assert handled(box, f"github:{comment}")[3:] == (None, None)
    assert fake.reacted == []
    assert fake_runtime.writes() == writes
    assert card_row(fake_runtime.home) == ("blocked", "essai-preparer")

    refusal[0] = None
    core.run_pass()
    gestures.react()
    assert handled(box, f"github:{comment}")[3] == "applied"
    assert fake.reacted == [(comment, "+1")]


def test_the_owner_s_login_ignores_case_for_a_gesture(fake_runtime, serve):
    box, launches = answer_box(), Launches()
    parked(fake_runtime, box, launches)
    fake = FakeGitHub([issue(1)])
    # GitHub's logins ignore case: the login set in the environment may not be written as GitHub writes it.
    comment = fake.add_comment(1, "Proprietaire", "/essai valider")
    fake.add_comment(1, "inconnu", "/essai valider")
    gestures, _ = reading(serve(fake), box, owner="PROPRIETAIRE")

    gestures.read_once()

    assert answer_ids(box) == [f"github:{comment}"]


class Counts:
    """A fake reading of the issues or of the gestures that counts its calls, at the time of the clock."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.read_at: list[float] = []
        self.reacted_at: list[float] = []

    def read_once(self) -> None:
        self.read_at.append(self.clock())

    def react(self) -> None:
        self.reacted_at.append(self.clock())


def test_gestures_are_read_every_ten_minutes_and_reacted_to_at_every_check():
    clock = Clock()
    intake, gestures = Counts(clock), Counts(clock)
    runner = IntakeRunner(intake, gestures, clock=clock)
    checks = (0, 5, 10, 599, 600, 605, 1199, 1200)

    for now in checks:
        clock.now = now
        runner.tick()

    assert GITHUB_POLL_SECONDS == 600
    assert intake.read_at == [0, 600, 1200]
    assert gestures.read_at == [0, 600, 1200]
    assert gestures.reacted_at == list(checks)
