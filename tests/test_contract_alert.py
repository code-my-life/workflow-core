"""The broken contract's issue: at startup, a failed contract test opens, comments or reopens an issue in
code-my-life/workflow-core, the one write allowed in observer mode; a contract restored closes it (ADR 0019)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.config import Workflow
from workflow_core.service import build_service

from conftest import make_board

BOT = "code-my-life-factory[bot]"
MARKER = "<!-- workflow-core:contrat -->"
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
TITLE = "Le contrat du socle avec " + "Her" + "mès est cassé"
ISSUE = "https://github.com/code-my-life/workflow-core/issues/99"
WORKFLOWS = {
    "factory": Workflow(
        key="factory", board="factory", sheet=Path("factory.json"), notify=True, notify_skip_assignees=[]
    ),
}


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeGitHub:
    """The issues of one repository, and every call the core makes."""

    def __init__(self, issues: list[dict] = ()) -> None:
        self.issues = [dict(issue, comments=[]) for issue in issues]
        self.calls: list[tuple[str, ...]] = []

    def list_issues(self, repo: str) -> list[dict]:
        self.calls.append(("list_issues", repo))
        return [dict(issue) for issue in self.issues]

    def create_issue(self, repo: str, title: str, body: str) -> int:
        self.calls.append(("create_issue", repo))
        number = 200 + len(self.issues)
        self.issues.append(
            {
                "number": number,
                "title": title,
                "body": body,
                "state": "open",
                "closed_at": None,
                "user": {"login": BOT},
                "comments": [],
            }
        )
        return number

    def set_issue_state(self, repo: str, number: int, state: str) -> None:
        self.calls.append(("set_issue_state", repo, str(number), state))
        issue = next(issue for issue in self.issues if issue["number"] == number)
        issue["state"] = state
        issue["closed_at"] = iso(NOW) if state == "closed" else None

    def post_comment(self, repo: str, number: int, body: str) -> str:
        self.calls.append(("post_comment", repo, str(number)))
        next(issue for issue in self.issues if issue["number"] == number)["comments"].append(body)
        return f"https://github.com/{repo}/issues/{number}#c"

    def list_comments(self, repo: str, number: int) -> list:
        self.calls.append(("list_comments", repo, str(number)))
        return []


def contract_issue(number: int, *, state: str = "open", closed: datetime | None = None, author: str = BOT) -> dict:
    return {
        "number": number,
        "title": TITLE,
        "body": f"…\n\n{MARKER}",
        "state": state,
        "closed_at": iso(closed) if closed else None,
        "user": {"login": author},
    }


def break_the_contract(home: Path) -> None:
    with sqlite3.connect(home / "kanban" / "boards" / "factory" / "kanban.db") as db:
        db.execute("ALTER TABLE tasks DROP COLUMN block_kind")
    db.close()


def start(tmp_path: Path, github: FakeGitHub, **environ: str) -> None:
    app = build_service(
        secret="s",
        workflows=WORKFLOWS,
        adapter=runtime_adapter(),
        environ={"WORKFLOW_CORE_STATE_DIR": str(tmp_path / "state"), **environ},
        github=github,
        clock=lambda: NOW.timestamp(),
        now=lambda: NOW,
    )
    with TestClient(app):
        pass


def noted(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "state" / "contract-issue.json").read_text())


def test_a_broken_contract_at_start_opens_the_contract_issue(runtime_home, tmp_path):
    break_the_contract(runtime_home)
    github = FakeGitHub()

    start(tmp_path, github)

    assert [call[0] for call in github.calls] == ["list_issues", "create_issue"]
    issue = github.issues[0]
    assert issue["title"] == TITLE
    assert issue["body"].endswith(MARKER)
    assert "- factory: tasks.block_kind absente" in issue["body"]
    assert "0.22.2" in issue["body"]
    assert noted(tmp_path) == {"issue": 200}


def test_an_open_contract_issue_is_commented_not_duplicated(runtime_home, tmp_path):
    break_the_contract(runtime_home)
    github = FakeGitHub([contract_issue(7, author="inconnu"), contract_issue(8)])

    start(tmp_path, github)

    assert [call for call in github.calls if call[0] != "list_issues"] == [
        ("post_comment", "code-my-life/workflow-core", "8")
    ]
    assert "factory: tasks.block_kind absente" in github.issues[1]["comments"][0]
    assert noted(tmp_path) == {"issue": 8}

    # Closed less than 24 hours ago: reopened and commented. More than 24 hours ago: a new one.
    for closed, expected in ((NOW - timedelta(hours=3), 9), (NOW - timedelta(hours=25), 201)):
        (tmp_path / "state" / "contract-issue.json").unlink()
        github = FakeGitHub([contract_issue(9, state="closed", closed=closed)])
        start(tmp_path, github)
        assert noted(tmp_path) == {"issue": expected}
        if expected == 9:
            assert ("set_issue_state", "code-my-life/workflow-core", "9", "open") in github.calls
            assert len(github.issues[0]["comments"]) == 1
        else:
            assert [call[0] for call in github.calls] == ["list_issues", "create_issue"]


def test_a_restored_contract_closes_the_contract_issue(runtime_home, tmp_path):
    break_the_contract(runtime_home)
    github = FakeGitHub()
    start(tmp_path, github)
    assert noted(tmp_path) == {"issue": 200}

    # The base restored, the core started again.
    shutil.rmtree(runtime_home / "kanban" / "boards" / "factory")
    make_board(runtime_home, "factory", [])
    start(tmp_path, github)

    assert github.issues[0]["state"] == "closed"
    assert github.issues[0]["comments"][-1].startswith("Le contrat avec " + "Her" + "mès tient de nouveau")
    assert noted(tmp_path) == {"issue": None}

    # A further start with the contract kept calls nothing.
    calls = len(github.calls)
    start(tmp_path, github)
    assert len(github.calls) == calls


def test_observer_mode_writes_nothing_but_the_contract_issue(runtime_home, tmp_path):
    # A blocked card with an issue, the notifier switched on: in observer mode, only the contract issue's calls.
    shutil.rmtree(runtime_home / "kanban" / "boards" / "factory")
    card = {
        "id": "t_00000001",
        "title": "Une carte",
        "body": ISSUE,
        "status": "blocked",
        "assignee": "coder",
        "created_at": int(NOW.timestamp()) - 600,
        "started_at": None,
        "completed_at": None,
        "block_kind": "needs_input",
    }
    event = {
        "id": 1,
        "task_id": "t_00000001",
        "run_id": None,
        "kind": "blocked",
        "payload": json.dumps({"reason": "?"}),
        "created_at": int(NOW.timestamp()) - 60,
    }
    make_board(runtime_home, "factory", [card], events=[event])
    with sqlite3.connect(runtime_home / "kanban" / "boards" / "factory" / "kanban.db") as db:
        db.execute("ALTER TABLE task_runs DROP COLUMN summary")
    db.close()
    base = runtime_home / "kanban" / "boards" / "factory" / "kanban.db"
    before = base.read_bytes()

    for switch in ({"WORKFLOW_CORE_NOTIFY": "on", "WORKFLOW_CORE_NOTIFY_SINCE": iso(NOW - timedelta(hours=1))}, {}):
        github = FakeGitHub()
        (tmp_path / "state" / "contract-issue.json").unlink(missing_ok=True)
        start(tmp_path, github, **switch)
        assert [call[0] for call in github.calls] == ["list_issues", "create_issue"]

    assert base.read_bytes() == before


def test_an_unreadable_runtime_version_is_written_in_the_contract_issue(runtime_home, tmp_path, monkeypatch):
    # The runtime's command missing from the PATH: the version cannot be read, and the issue says so.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    github = FakeGitHub()

    start(tmp_path, github)

    body = github.issues[0]["body"]
    assert "lue : `illisible`" in body
    assert "--version" in body


def test_a_recently_closed_contract_issue_beyond_the_first_page_is_reopened(runtime_home, tmp_path):
    import subprocess
    import threading
    from http.server import ThreadingHTTPServer

    from workflow_core.adapters.github import AppTokens, GitHubClient

    from test_outage_issue import FakeGitHub as HttpGitHub
    from test_outage_issue import PagedGitHub

    break_the_contract(runtime_home)
    HttpGitHub.calls = []
    HttpGitHub.failing = False
    recent = [
        {
            "number": n,
            "title": f"Issue {n}",
            "body": "Rien.",
            "state": "closed",
            "closed_at": iso(NOW),
            "user": {"login": BOT},
            "comments": [],
        }
        for n in range(1, 101)
    ]
    contract = {**contract_issue(150, state="closed", closed=NOW - timedelta(hours=3)), "comments": []}
    HttpGitHub.issues = [*recent, contract]
    server = ThreadingHTTPServer(("127.0.0.1", 0), PagedGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    client = GitHubClient(
        AppTokens(
            environ={},
            env_file=env_file,
            cache_dir=tmp_path / "tokens",
            api_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
    )
    try:
        start(tmp_path, client)
    finally:
        server.shutdown()

    assert len(HttpGitHub.issues) == 101
    assert HttpGitHub.issues[100]["state"] == "open"
    assert len(HttpGitHub.issues[100]["comments"]) == 1
    assert noted(tmp_path) == {"issue": 150}
