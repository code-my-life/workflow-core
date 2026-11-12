"""The notifier: one alert in its issue for each blocked card of a workflow that asks for it (story 1 of the
notifier's epic), behind a switch, its list of repositories and its switch-on time."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.github import GitHubError
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


class FakeGitHub:
    """Records the comments posted; can refuse them."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, int, str]] = []
        self.refuse: int | None = None

    def post_comment(self, repo: str, number: int, body: str) -> str:
        if self.refuse is not None:
            raise GitHubError(self.refuse, "Resource not accessible by integration")
        self.posted.append((repo, number, body))
        return f"https://github.com/{repo}/issues/{number}#issuecomment-{len(self.posted)}"


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
