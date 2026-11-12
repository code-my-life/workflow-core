"""The notifier: one alert in its issue for each blocked card of a workflow that asks for it (story 1 of the
notifier's epic), behind a switch, its list of repositories and its switch-on time."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.github import GitHubError, IssueComment
from workflow_core.config import Workflow
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.notify import Notifier, NotifySettings

from conftest import RUNTIME_HOME_VAR, make_board

NOON = datetime(2026, 11, 12, 12, 0, tzinfo=UTC).timestamp()
SINCE = "2026-11-12T13:00:00+01:00"  # noon UTC
LIVE = "https://live.example.ts.net:8446"
ISSUE = "https://github.com/code-my-life/workflow-core/issues/99"

WORKFLOWS = {
    "factory": Workflow(
        key="factory",
        board="factory",
        sheet=Path("factory.json"),
        notify=True,
        notify_skip_assignees=["factory-gate", "factory-release"],
    ),
    "studio": Workflow(key="studio", board="studio", sheet=Path("studio.json")),
}


def card(id: str, *, title: str = "Une carte", body: str | None = None, status: str = "blocked", **more) -> dict:
    return {
        "id": id,
        "title": title,
        "body": body,
        "status": status,
        "assignee": more.get("assignee", "coder"),
        "created_at": int(NOON) - 3600,
        "started_at": None,
        "completed_at": None,
        "block_kind": more.get("block_kind", "needs_input"),
    }


def blocked(id: int, task: str, at: float, reason: str = "Faut-il garder l'ancienne route ?", kind="needs_input"):
    payload = json.dumps({"kind": kind, "reason": reason, "recurrences": 1})
    return {"id": id, "task_id": task, "run_id": None, "kind": "blocked", "payload": payload, "created_at": int(at)}


BOT = "code-my-life-factory[bot]"


class FakeGitHub:
    """Records the comments posted and keeps every issue's comments, served by pages of 100; can refuse a comment or
    fail on a page."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, int, str]] = []
        self.refuse: int | None = None
        self.comments: dict[tuple[str, int], list[IssueComment]] = {}
        self.pages_served = 0
        self.failing_page: int | None = None

    def post_comment(self, repo: str, number: int, body: str) -> str:
        if self.refuse is not None:
            raise GitHubError(self.refuse, "Resource not accessible by integration")
        self.posted.append((repo, number, body))
        kept = self.comments.setdefault((repo, number), [])
        url = f"https://github.com/{repo}/issues/{number}#issuecomment-{len(kept) + 1}"
        kept.append(IssueComment(author=BOT, body=body, url=url))
        return url

    def add(self, repo: str, number: int, author: str, body: str) -> None:
        kept = self.comments.setdefault((repo, number), [])
        kept.append(IssueComment(author=author, body=body, url=f"https://github.com/{repo}#c{len(kept) + 1}"))

    def list_comments(self, repo: str, number: int) -> list[IssueComment]:
        kept = self.comments.get((repo, number), [])
        found: list[IssueComment] = []
        page = 1
        while True:
            if page == self.failing_page:
                raise GitHubError(502, "Bad Gateway")
            self.pages_served += 1
            found += kept[(page - 1) * 100 : page * 100]
            if page * 100 >= len(kept):
                return found
            page += 1


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "runtime"
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(path))
    return path


def environ(**more: str) -> dict[str, str]:
    return {
        "WORKFLOW_CORE_NOTIFY": "on",
        "WORKFLOW_CORE_NOTIFY_SINCE": SINCE,
        "WORKFLOW_CORE_LIVE_URL": LIVE,
        **more,
    }


def notifier(tmp_path, github, env=None, *, mode_ok=True, now=NOON + 600, log=None, workflows=WORKFLOWS) -> Notifier:
    verdict = ContractVerdict(ok=mode_ok, checked_at="2026-11-12T11:00:00+00:00", problems=[] if mode_ok else ["x"])
    return Notifier(
        workflows=workflows,
        reader=runtime_adapter().make_board_reader(),
        state=CoreState(contract=verdict),
        settings=NotifySettings.from_environ(environ() if env is None else env),
        post_comment=github.post_comment,
        read_comments=github.list_comments,
        register_path=tmp_path / "state" / "alerts.db",
        clock=lambda: now,
        log=(log.append if log is not None else lambda line: None),
    )


def test_a_blocked_factory_card_gets_one_alert_on_its_issue(home, tmp_path):
    make_board(
        home,
        "factory",
        [card("t_ab12cd34", title="Le juge refuse une fiche", body=f"Issue : {ISSUE}")],
        events=[blocked(5509, "t_ab12cd34", NOON + 60)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github).run_pass()

    assert len(github.posted) == 1
    repo, number, body = github.posted[0]
    assert (repo, number) == ("code-my-life/workflow-core", 99)
    assert "### ⏸ Décision attendue" in body
    assert "Workflow `factory`" in body
    assert "élément `factory:workflow-core#99`" in body
    assert "carte `t_ab12cd34` : Le juge refuse une fiche" in body
    assert "> Faut-il garder l'ancienne route ?" in body
    assert "/factory unblock" in body
    assert f"{LIVE}/issues/workflow-core/99" in body
    assert "Alerte du socle workflow-core" in body
    assert body.endswith("<!-- workflow-core:attente factory:needs_input:t_ab12cd34:5509 -->")


def test_a_second_pass_posts_nothing_more(home, tmp_path):
    make_board(home, "factory", [card("t_ab12cd34", body=ISSUE)], events=[blocked(7, "t_ab12cd34", NOON + 60)])
    make_board(home, "studio", [])
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)

    alerts.run_pass()
    alerts.run_pass()
    notifier(tmp_path, github).run_pass()  # a restarted service keeps its register

    assert len(github.posted) == 1


def test_dependency_blocks_scripted_lanes_other_boards_and_cards_without_issue_get_no_alert(home, tmp_path):
    make_board(
        home,
        "factory",
        [
            card("t_00000001", body=ISSUE, block_kind="dependency"),
            card("t_00000002", body=ISSUE, assignee="factory-gate"),
            card("t_00000003", body=ISSUE, assignee="factory-release"),
            card("t_00000004", body="Pas d'adresse ici"),
        ],
        events=[blocked(n, f"t_0000000{n}", NOON + 60) for n in (1, 2, 3, 4)],
    )
    make_board(home, "studio", [card("t_00000005", body=ISSUE)], events=[blocked(5, "t_00000005", NOON + 60)])
    github = FakeGitHub()
    log: list[str] = []
    alerts = notifier(tmp_path, github, log=log)

    alerts.run_pass()
    alerts.run_pass()

    assert github.posted == []
    without_issue = [line for line in log if "t_00000004" in line]
    assert len(without_issue) == 1
    assert "sans issue" in without_issue[0]


def test_nothing_is_read_or_posted_while_the_switch_is_off(home, tmp_path):
    # No board at all: a pass that read anything would log an unreadable board.
    github = FakeGitHub()
    for env in ({}, {**environ(), "WORKFLOW_CORE_NOTIFY": "off"}, {**environ(), "WORKFLOW_CORE_NOTIFY": "yes"}):
        log: list[str] = []
        notifier(tmp_path, github, env, log=log).run_pass()
        assert log == []
    assert github.posted == []
    assert not (tmp_path / "state" / "alerts.db").exists()


def test_only_the_listed_repositories_get_alerts(home, tmp_path):
    other = "https://github.com/code-my-life/factory-live/issues/12"
    make_board(
        home,
        "factory",
        [card("t_00000001", body=other), card("t_00000002", body=ISSUE)],
        events=[blocked(1, "t_00000001", NOON + 60), blocked(2, "t_00000002", NOON + 60)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github, environ(WORKFLOW_CORE_NOTIFY_REPOS="code-my-life/workflow-core")).run_pass()

    assert [(repo, number) for repo, number, _ in github.posted] == [("code-my-life/workflow-core", 99)]


def test_nothing_is_posted_in_observer_mode(home, tmp_path):
    make_board(home, "factory", [card("t_00000001", body=ISSUE)], events=[blocked(1, "t_00000001", NOON + 60)])
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github, mode_ok=False).run_pass()

    assert github.posted == []


def test_a_refused_comment_is_retried_on_the_next_pass(home, tmp_path):
    make_board(home, "factory", [card("t_00000001", body=ISSUE)], events=[blocked(1, "t_00000001", NOON + 60)])
    make_board(home, "studio", [])
    github = FakeGitHub()
    github.refuse = 403
    log: list[str] = []
    alerts = notifier(tmp_path, github, log=log)

    alerts.run_pass()
    assert github.posted == []
    assert any("403" in line and "t_00000001" in line for line in log)

    github.refuse = None
    alerts.run_pass()
    alerts.run_pass()
    assert len(github.posted) == 1


def test_a_wait_opened_before_the_switch_on_time_is_never_alerted(home, tmp_path):
    make_board(
        home,
        "factory",
        [card("t_00000001", body=ISSUE), card("t_00000002", body=ISSUE)],
        events=[blocked(1, "t_00000001", NOON - 60), blocked(2, "t_00000002", NOON + 60)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)

    alerts.run_pass()
    alerts.run_pass()
    assert [body.splitlines()[-1] for _, _, body in github.posted] == [
        "<!-- workflow-core:attente factory:needs_input:t_00000002:2 -->"
    ]

    # Switched on without a time, or with an unreadable one: nothing is read or posted, and the journal says it.
    for env in (environ(WORKFLOW_CORE_NOTIFY_SINCE=""), environ(WORKFLOW_CORE_NOTIFY_SINCE="midi")):
        settings = NotifySettings.from_environ(env)
        assert settings.on is False
        assert any("WORKFLOW_CORE_NOTIFY_SINCE" in line for line in settings.describe())
        other = FakeGitHub()
        notifier(tmp_path / "other", other, env).run_pass()
        assert other.posted == []


MARKER = "<!-- workflow-core:attente factory:needs_input:t_00000001:1 -->"
WORKFLOW_CORE = "code-my-life/workflow-core"


def one_blocked_card(home) -> None:
    make_board(home, "factory", [card("t_00000001", body=ISSUE)], events=[blocked(1, "t_00000001", NOON + 60)])
    make_board(home, "studio", [])


def test_an_alert_already_in_the_issue_is_not_posted_again(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    github.add(WORKFLOW_CORE, 99, BOT, f"### ⏸ Décision attendue\n\n{MARKER}")
    log: list[str] = []
    alerts = notifier(tmp_path, github, log=log)

    alerts.run_pass()

    assert github.posted == []
    assert any("existait déjà" in line and "#c1" in line for line in log)
    # The key is noted: the next pass does not read the issue again.
    served = github.pages_served
    alerts.run_pass()
    assert github.pages_served == served
    assert github.posted == []


def test_the_marker_is_found_past_the_first_hundred_comments(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    for n in range(1, 231):
        github.add(WORKFLOW_CORE, 99, BOT if n == 150 else "visiteur", MARKER if n == 150 else f"commentaire {n}")

    notifier(tmp_path, github).run_pass()

    assert github.posted == []
    assert github.pages_served == 3


def test_a_marker_written_by_another_account_does_not_count(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    github.add(WORKFLOW_CORE, 99, "inconnu", MARKER)

    notifier(tmp_path, github).run_pass()

    assert len(github.posted) == 1
    assert github.posted[0][2].endswith(MARKER)


def test_nothing_is_posted_until_every_page_of_comments_is_read(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    for n in range(1, 151):
        github.add(WORKFLOW_CORE, 99, "visiteur", f"commentaire {n}")
    github.failing_page = 2
    log: list[str] = []
    alerts = notifier(tmp_path, github, log=log)

    alerts.run_pass()
    assert github.posted == []
    assert len([line for line in log if "502" in line]) == 1

    github.failing_page = None
    alerts.run_pass()
    alerts.run_pass()
    assert len(github.posted) == 1


def test_a_card_blocked_again_gets_a_new_alert(home, tmp_path):
    make_board(home, "factory", [card("t_00000001", body=ISSUE)], events=[blocked(10, "t_00000001", NOON + 60)])
    make_board(home, "studio", [])
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)
    alerts.run_pass()

    with sqlite3.connect(home / "kanban" / "boards" / "factory" / "kanban.db") as db:
        db.execute(
            "INSERT INTO task_events (id, task_id, kind, payload, created_at) VALUES (12, 't_00000001', 'unblocked', "
            "'{}', ?)",
            (int(NOON) + 120,),
        )
        event = blocked(14, "t_00000001", NOON + 180, reason="Et maintenant ?")
        db.execute(
            "INSERT INTO task_events (id, task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(event.values()),
        )
    db.close()
    alerts.run_pass()
    alerts.run_pass()

    assert [body.splitlines()[-1] for _, _, body in github.posted] == [
        "<!-- workflow-core:attente factory:needs_input:t_00000001:10 -->",
        "<!-- workflow-core:attente factory:needs_input:t_00000001:14 -->",
    ]


def test_a_lost_or_unreadable_register_posts_nothing_again(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    register = tmp_path / "state" / "alerts.db"
    notifier(tmp_path, github).run_pass()
    assert len(github.posted) == 1

    # Register deleted, service recreated on the same bases and the same GitHub.
    register.unlink()
    notifier(tmp_path, github).run_pass()
    assert len(github.posted) == 1

    # Register replaced by a file that is not a SQLite base: set aside, recreated, nothing posted again.
    register.write_bytes(b"ceci n'est pas une base")
    log: list[str] = []
    alerts = notifier(tmp_path, github, log=log)
    alerts.run_pass()
    assert len(github.posted) == 1
    aside = list(register.parent.glob("alerts.db.illisible-*"))
    assert len(aside) == 1
    assert aside[0].read_bytes() == b"ceci n'est pas une base"
    assert any("illisible" in line for line in log)
    with sqlite3.connect(register) as db:
        assert db.execute("SELECT count(*) FROM alerts").fetchone() == (1,)
    db.close()


def test_a_rebuilt_event_journal_posts_nothing_again(home, tmp_path):
    one_blocked_card(home)
    github = FakeGitHub()
    alerts = notifier(tmp_path, github)
    alerts.run_pass()
    assert len(github.posted) == 1

    # The service's event journal deleted, then the service recreated: a new epoch, and no new alert.
    from fastapi.testclient import TestClient
    from workflow_core.service import build_service

    journal = tmp_path / "state" / "events.db"
    for _ in range(2):
        app = build_service(
            secret="s",
            workflows=WORKFLOWS,
            adapter=runtime_adapter(),
            environ={**environ(), "WORKFLOW_CORE_STATE_DIR": str(tmp_path / "state")},
            post_comment=github.post_comment,
            read_comments=github.list_comments,
            clock=lambda: NOON + 600,
        )
        with TestClient(app):
            app.state.notifier.run_pass()
        journal.unlink(missing_ok=True)
    assert len(github.posted) == 1


READY = NOON + 60
HELD_WORKFLOWS = {
    "factory": Workflow(
        key="factory",
        board="factory",
        sheet=Path("factory.json"),
        notify=True,
        notify_skip_assignees=["factory-gate", "factory-release"],
        notify_relay_agents=["scribe", "coder"],
    ),
}


def event(id: int, task: str, kind: str, at: float, payload: dict | None = None) -> dict:
    return {
        "id": id,
        "task_id": task,
        "run_id": None,
        "kind": kind,
        "payload": json.dumps(payload or {}),
        "created_at": int(at),
    }


def held_card(id: str = "t_00000001", assignee: str = "coder") -> dict:
    return card(id, title="Le juge refuse une fiche", body=ISSUE, status="ready", block_kind=None, assignee=assignee)


def guarded(id: int, task: str, at: float, reason: str = "active_pr") -> dict:
    return event(id, task, "respawn_guarded", at, {"reason": reason})


def test_a_card_held_by_active_pr_for_thirty_minutes_gets_one_alert(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card()],
        events=[event(1, "t_00000001", "created", READY), guarded(2, "t_00000001", READY)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()
    alerts = notifier(tmp_path, github, now=READY + 31 * 60, workflows=HELD_WORKFLOWS)

    alerts.run_pass()
    alerts.run_pass()

    assert len(github.posted) == 1
    repo, number, body = github.posted[0]
    assert (repo, number) == ("code-my-life/workflow-core", 99)
    assert "### ⚠️ Carte prête mais retenue" in body
    assert "Workflow `factory` · élément `factory:workflow-core#99` · carte `t_00000001` : Le juge" in body
    assert "Prête depuis 31 min" in body
    assert "la garde `active_pr` la retient" in body
    relay = "kanban --board factory reassign t_00000001 scribe"
    back = "kanban --board factory reassign t_00000001 coder"
    assert relay in body
    assert back in body
    assert body.index(relay) < body.index(back)
    assert f"{LIVE}/issues/workflow-core/99" in body
    assert body.endswith(f"<!-- workflow-core:attente factory:held_active_pr:t_00000001:{int(READY)} -->")


def test_the_held_delay_is_read_from_the_workflow_entry(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card()],
        events=[event(1, "t_00000001", "created", READY), guarded(2, "t_00000001", READY)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github, now=READY + 29 * 60, workflows=HELD_WORKFLOWS).run_pass()
    assert github.posted == []

    sooner = {"factory": HELD_WORKFLOWS["factory"].model_copy(update={"held_after_minutes": 20})}
    notifier(tmp_path, github, now=READY + 29 * 60, workflows=sooner).run_pass()
    assert len(github.posted) == 1
    assert "Prête depuis 29 min" in github.posted[0][2]


def test_a_ready_card_without_the_active_pr_guard_gets_no_alert(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card("t_00000001"), held_card("t_00000002")],
        events=[
            event(1, "t_00000001", "created", READY),
            guarded(2, "t_00000001", READY, reason="rate_limit_cooldown"),
            event(3, "t_00000002", "created", READY),
        ],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github, now=READY + 60 * 60, workflows=HELD_WORKFLOWS).run_pass()

    assert github.posted == []


def test_the_relaunch_command_relays_through_another_agent(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card(assignee="scribe")],
        events=[event(1, "t_00000001", "created", READY), guarded(2, "t_00000001", READY)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github, now=READY + 31 * 60, workflows=HELD_WORKFLOWS).run_pass()

    body = github.posted[0][2]
    relay = "kanban --board factory reassign t_00000001 coder"
    back = "kanban --board factory reassign t_00000001 scribe"
    assert body.index(relay) < body.index(back)

    # Without relay agents in its entry, the alert leaves the command out and points at the factory's RUNBOOK.
    bare = {"factory": HELD_WORKFLOWS["factory"].model_copy(update={"notify_relay_agents": []})}
    other = FakeGitHub()
    notifier(tmp_path / "bare", other, now=READY + 31 * 60, workflows=bare).run_pass()
    assert "reassign" not in other.posted[0][2]
    assert "« Incidents connus »" in other.posted[0][2]


def test_a_card_held_again_gets_a_new_alert(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card()],
        events=[event(1, "t_00000001", "created", READY), guarded(2, "t_00000001", READY)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()
    notifier(tmp_path, github, now=READY + 31 * 60, workflows=HELD_WORKFLOWS).run_pass()

    again = READY + 40 * 60
    with sqlite3.connect(home / "kanban" / "boards" / "factory" / "kanban.db") as db:
        for row in (event(3, "t_00000001", "unblocked", again), guarded(4, "t_00000001", again)):
            db.execute(
                "INSERT INTO task_events (id, task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
    db.close()
    alerts = notifier(tmp_path, github, now=again + 31 * 60, workflows=HELD_WORKFLOWS)
    alerts.run_pass()
    alerts.run_pass()

    assert [body.splitlines()[-1] for _, _, body in github.posted] == [
        f"<!-- workflow-core:attente factory:held_active_pr:t_00000001:{int(READY)} -->",
        f"<!-- workflow-core:attente factory:held_active_pr:t_00000001:{int(again)} -->",
    ]


def test_a_held_alert_already_in_the_issue_is_not_posted_again(home, tmp_path):
    make_board(
        home,
        "factory",
        [held_card()],
        events=[event(1, "t_00000001", "created", READY), guarded(2, "t_00000001", READY)],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()
    notifier(tmp_path, github, now=READY + 31 * 60, workflows=HELD_WORKFLOWS).run_pass()

    (tmp_path / "state" / "alerts.db").unlink()
    notifier(tmp_path, github, now=READY + 45 * 60, workflows=HELD_WORKFLOWS).run_pass()

    assert len(github.posted) == 1


def test_a_card_blocked_after_giving_up_gets_an_alert_of_its_trigger(home, tmp_path):
    # The runtime's breaker gave up after a block: the trigger is `gave_up`, and the alert has the generic title.
    make_board(
        home,
        "factory",
        [card("t_00000001", body=ISSUE)],
        events=[
            blocked(1, "t_00000001", NOON + 60),
            event(2, "t_00000001", "gave_up", NOON + 120, {"reason": "trop d'échecs de suite"}),
        ],
    )
    make_board(home, "studio", [])
    github = FakeGitHub()

    notifier(tmp_path, github).run_pass()

    assert len(github.posted) == 1
    body = github.posted[0][2]
    assert body.startswith("### ⏸ Bloquée\n")
    assert "> trop d'échecs de suite" in body
    assert body.endswith("<!-- workflow-core:attente factory:gave_up:t_00000001:2 -->")
