"""The reading of GitHub: an open issue of the owner, in the repository a workflow's manifest declares, is handed to
the workflow's `intake` command; accepted, it becomes the root card of an item, and the decision is said in the issue
by the App, with a hidden marker, once (story 6 of the engine's epic, code-my-life/workflow-core#56)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.github import APP_BOT, GitHubClient, OpenIssue
from workflow_core.config import Workflow
from workflow_core.engine import Engine
from workflow_core.engine.answers import ENGINE_DB
from workflow_core.intake import GITHUB_POLL_SECONDS, Intake, IntakeRegister, IntakeRunner, intake_marker
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.decision import IntakeDecision
from workflow_core.model.manifest import ManifestIntake
from workflow_core.model.state import CoreState
from workflow_core.plugin.commands import run_intake
from workflow_core.plugin.manifest import PluggedWorkflow, plug

from conftest import ESSAI_REPO, FACTORY_CARDS, make_board

REPO = "code-my-life/workflow-essai"
# The owner's account, as the core reads it in WORKFLOW_CORE_OWNER: a made-up login.
OWNER = "proprietaire"
LIVE = "https://live.example.ts.net:8446"


def issue_url(number: int) -> str:
    return f"https://github.com/{REPO}/issues/{number}"


def issue(number: int, title: str = "Premier essai", *, author: str = OWNER, state: str = "open", **more) -> dict:
    """An issue as GitHub's API lists it."""
    return {
        "number": number,
        "html_url": issue_url(number),
        "title": title,
        "body": more.get("body", f"Corps de l'issue {number}"),
        "user": {"login": author},
        "state": state,
        "labels": [{"name": name} for name in more.get("labels", [])],
        **({"pull_request": {"url": "x"}} if more.get("pull_request") else {}),
    }


def github_time(moment: datetime | None = None) -> str:
    """A time as GitHub's API writes it, `2026-11-14T09:30:00Z`; now when none is given."""
    return (moment or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """A local GitHub: the App's tokens, the issues of one repository (pull requests among them, as GitHub lists
    them), and their comments, with their ids, their creation times and their reactions; a posted comment is the
    App's, a reaction posted is the App's, and posting either can be refused. A reading of the comments with `since`
    keeps those created from that time on, and is noted."""

    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues
        self.comments: dict[int, list[dict]] = {}
        self.posted: list[tuple[int, str]] = []
        self.issue_reads = 0
        self.refuse_posts: int | None = None
        # The reactions of each comment, by its id, and those the App posted, in order.
        self.reactions: dict[int, list[dict]] = {}
        self.reacted: list[tuple[int, str]] = []
        self.refuse_reactions: int | None = None
        # Every reaction asked for, refused or not.
        self.reaction_tries = 0
        # The `since` of each reading of an issue's comments (None without it), by the issue's number.
        self.comment_reads: list[tuple[int, str | None]] = []
        # Reentrant: a comment posted is added under the lock, and adding one takes it.
        self.lock = threading.RLock()
        self._next_id = 1000

    def add_comment(self, number: int, author: str, body: str, *, created_at: str | None = None) -> int:
        """Add a comment to the issue, created now unless told otherwise; its id."""
        with self.lock:
            self._next_id += 1
            comment_id = self._next_id
            self.comments.setdefault(number, []).append(
                {
                    "id": comment_id,
                    "user": {"login": author},
                    "body": body,
                    "html_url": f"{issue_url(number)}#issuecomment-{comment_id}",
                    "created_at": created_at or github_time(),
                }
            )
            return comment_id

    def comment_page(self, number: int, since: str | None) -> list[dict]:
        with self.lock:
            self.comment_reads.append((number, since))
            kept = [
                comment for comment in self.comments.get(number, []) if since is None or comment["created_at"] >= since
            ]
            return [
                {**comment, "reactions": {"total_count": len(self.reactions.get(comment["id"], []))}}
                for comment in kept
            ]

    def app_reactions(self, comment_id: int) -> list[str]:
        """The contents of the App's reactions to a comment."""
        return [
            reaction["content"]
            for reaction in self.reactions.get(comment_id, [])
            if reaction["user"]["login"] == APP_BOT
        ]

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, body: object, status: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                url = urlparse(self.path)
                query = {key: values[0] for key, values in parse_qs(url.query).items()}
                if url.path == "/orgs/code-my-life/installation":
                    self._answer({"id": 7})
                elif url.path == f"/repos/{REPO}/issues":
                    with fake.lock:
                        fake.issue_reads += 1
                    wanted = query.get("state", "open")
                    self._answer([item for item in fake.issues if wanted in (item["state"], "all")])
                elif match := re.fullmatch(rf"/repos/{REPO}/issues/(\d+)/comments", url.path):
                    self._answer(fake.comment_page(int(match[1]), query.get("since")))
                elif match := re.fullmatch(rf"/repos/{REPO}/issues/comments/(\d+)/reactions", url.path):
                    with fake.lock:
                        self._answer(list(fake.reactions.get(int(match[1]), [])))
                else:
                    self._answer({"message": "Not Found"}, 404)

            def do_POST(self) -> None:
                url = urlparse(self.path)
                if url.path.startswith("/app/installations/"):
                    self._answer({"token": "ghs_t", "expires_at": "2030-01-01T00:00:00Z", "permissions": {}}, 201)
                    return
                if reaction := re.fullmatch(rf"/repos/{REPO}/issues/comments/(\d+)/reactions", url.path):
                    self._react(int(reaction[1]))
                    return
                match = re.fullmatch(rf"/repos/{REPO}/issues/(\d+)/comments", url.path)
                if match is None:
                    self._answer({"message": "Not Found"}, 404)
                    return
                if fake.refuse_posts is not None:
                    self._answer({"message": "Resource not accessible by integration"}, fake.refuse_posts)
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["body"]
                number = int(match[1])
                with fake.lock:
                    fake.posted.append((number, body))
                    fake.add_comment(number, APP_BOT, body)
                self._answer({"html_url": fake.comments[number][-1]["html_url"]}, 201)

            def _react(self, comment_id: int) -> None:
                content = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["content"]
                with fake.lock:
                    fake.reaction_tries += 1
                if fake.refuse_reactions is not None:
                    self._answer({"message": "Resource not accessible by integration"}, fake.refuse_reactions)
                    return
                with fake.lock:
                    fake.reacted.append((comment_id, content))
                    fake.reactions.setdefault(comment_id, []).append({"content": content, "user": {"login": APP_BOT}})
                self._answer({"content": content}, 201)

            def log_message(self, *args) -> None:
                pass

        return Handler


def state(ok: bool = True) -> CoreState:
    problems = [] if ok else ["essai: tasks.status absente"]
    return CoreState(contract=ContractVerdict(ok=ok, checked_at="2026-11-14T00:00:00+00:00", problems=problems))


def essai(**changes) -> PluggedWorkflow:
    return dataclasses.replace(plug(Workflow(key="essai", board="essai", repo=ESSAI_REPO)), **changes)


class Intakes:
    """The intake commands the reading launches, run for real (through the fake `uv`), and noted."""

    def __init__(self) -> None:
        self.inputs: list[dict] = []

    def __call__(self, repo, command, payload, timeout, log, *, label):
        self.inputs.append(payload)
        return run_intake(repo, command, payload, timeout, log, label=label)

    def numbers(self) -> list[int]:
        return [payload["issue"]["number"] for payload in self.inputs]


def reading(github: GitHubClient, tmp_path: Path, *, workflow=None, core=None, live_url=LIVE, owner=OWNER):
    adapter = runtime_adapter()
    core = core or state()
    writer = adapter.make_board_writer()
    engine = Engine(workflows=[], reader=adapter.make_board_reader(), writer=writer, state=core)
    intakes = Intakes()
    lines: list[str] = []
    built = Intake(
        workflows=[workflow or essai()],
        github=github,
        writer=writer,
        state=core,
        register=IntakeRegister(tmp_path / "state" / ENGINE_DB),
        ensure_board=engine.ensure_board,
        live_url=live_url,
        owner=owner,
        run=intakes,
        log=lines.append,
    )
    return built, intakes, lines


def creates(runtime) -> list[list[str]]:
    return [call for call in runtime.writes() if call[3:4] == ["create"]]


def cards(runtime, board: str = "essai") -> list[tuple]:
    base = runtime.home / "kanban" / "boards" / board / "kanban.db"
    if not base.is_file():
        return []
    with sqlite3.connect(base) as db:
        found = db.execute("SELECT idempotency_key, assignee, body FROM tasks").fetchall()
    db.close()
    return found


def registered(tmp_path: Path) -> list[tuple]:
    path = tmp_path / "state" / ENGINE_DB
    if not path.is_file():
        return []
    with sqlite3.connect(path) as db:
        found = db.execute("SELECT repo, number, item, decision FROM intake ORDER BY number").fetchall()
    db.close()
    return found


def test_an_accepted_issue_creates_one_root_card_and_says_so(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(7, "Premier essai", labels=["essai"])])
    intake, intakes, lines = reading(serve(fake), tmp_path)

    intake.read_once()

    assert intakes.inputs == [
        {
            "schema_version": 1,
            "issue": {
                "repo": REPO,
                "number": 7,
                "url": issue_url(7),
                "title": "Premier essai",
                "body": "Corps de l'issue 7",
                "labels": ["essai"],
            },
        }
    ]
    assert creates(fake_runtime) == [
        [
            "kanban",
            "--board",
            "essai",
            "create",
            "essai:7 · Premier essai",
            "--assignee",
            "essai-preparer",
            "--idempotency-key",
            "essai:7",
            "--body",
            issue_url(7),
            "--json",
        ]
    ]
    assert cards(fake_runtime) == [("essai:7", "essai-preparer", issue_url(7))]
    assert len(fake.posted) == 1
    number, body = fake.posted[0]
    assert number == 7
    assert body == (
        "✅ Cette issue devient l'élément `essai:7` du workflow `essai` ; il commence à la station `preparer`.\n\n"
        f"[Voir l'élément dans factory-live]({LIVE}/workflows/essai)\n\n"
        "Motif : issue d'essai acceptée\n\n"
        f"<!-- workflow-core:intake {REPO}#7 accept -->"
    )
    assert registered(tmp_path) == [(REPO, 7, "essai:7", "accept")]
    assert not any(line.startswith(("<3>", "<4>")) for line in lines), lines


class ReposGitHub:
    """The issues of several repositories, as the core's client hands them, and the comments it posts."""

    def __init__(self, issues: dict[str, list[int]]) -> None:
        self.issues = issues
        self.read: list[str] = []
        self.posted: list[tuple[str, int]] = []

    def list_open_issues(self, repo: str) -> list[OpenIssue]:
        self.read.append(repo)
        return [
            OpenIssue(
                number=number,
                url=f"https://github.com/{repo}/issues/{number}",
                title=f"Essai {number}",
                body="",
                author=OWNER,
                labels=(),
            )
            for number in self.issues.get(repo, [])
        ]

    def list_comments(self, repo: str, number: int) -> list:
        return []

    def post_comment(self, repo: str, number: int, body: str) -> str:
        self.posted.append((repo, number))
        return f"https://github.com/{repo}/issues/{number}#issuecomment-1"


def test_every_issues_repo_of_the_manifest_is_read(fake_runtime, tmp_path):
    other = "code-my-life/workflow-essai-bis"
    github = ReposGitHub({REPO: [7], other: [12]})
    intake, intakes, lines = reading(github, tmp_path, workflow=essai(issues_repos=(REPO, other)))

    intake.read_once()

    assert github.read == [REPO, other]
    assert [(payload["issue"]["repo"], payload["issue"]["number"]) for payload in intakes.inputs] == [
        (REPO, 7),
        (other, 12),
    ]
    assert cards(fake_runtime) == [
        ("essai:7", "essai-preparer", f"https://github.com/{REPO}/issues/7"),
        ("essai:12", "essai-preparer", f"https://github.com/{other}/issues/12"),
    ]
    assert github.posted == [(REPO, 7), (other, 12)]
    assert registered(tmp_path) == [(REPO, 7, "essai:7", "accept"), (other, 12, "essai:12", "accept")]
    assert not any(line.startswith(("<3>", "<4>")) for line in lines), lines


def test_an_issue_read_twice_or_after_a_lost_registry_is_not_taken_again(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(7)])
    # A marker written by another account than the App's does not count.
    fake.add_comment(7, OWNER, intake_marker(REPO, 7, "accept"))
    intake, intakes, _ = reading(serve(fake), tmp_path)

    intake.read_once()
    assert intakes.numbers() == [7]
    assert len(fake.posted) == 1
    writes = fake_runtime.writes()

    intake.read_once()
    (tmp_path / "state" / ENGINE_DB).unlink()
    intake.read_once()

    assert intakes.numbers() == [7]
    assert fake_runtime.writes() == writes
    assert len(fake.posted) == 1
    assert cards(fake_runtime) == [("essai:7", "essai-preparer", issue_url(7))]
    # The App's marker was enough, and the issue is noted again.
    assert registered(tmp_path) == [(REPO, 7, None, "accept")]


def test_a_refused_issue_gets_the_reason_and_no_card(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(8, "[refus] essai")])
    intake, intakes, _ = reading(serve(fake), tmp_path)

    intake.read_once()
    intake.read_once()

    assert intakes.numbers() == [8]
    assert fake.posted == [
        (
            8,
            "🚫 Cette issue n'est pas prise par le workflow `essai` : titre marqué [refus].\n\n"
            f"<!-- workflow-core:intake {REPO}#8 refuse -->",
        )
    ]
    assert creates(fake_runtime) == []
    assert cards(fake_runtime) == []
    assert registered(tmp_path) == [(REPO, 8, None, "refuse")]


def test_only_the_owner_s_open_issues_with_the_right_label_are_taken(fake_runtime, serve, tmp_path):
    fake = FakeGitHub(
        [
            issue(1, author="inconnu"),
            issue(2, pull_request=True),
            issue(3, state="closed"),
            issue(4, labels=["test"]),
            issue(5),
            # GitHub's label names ignore case: `Test` is the test label too.
            issue(6, labels=["Test"]),
        ]
    )
    github = serve(fake)
    intake, intakes, _ = reading(github, tmp_path)

    intake.read_once()
    assert intakes.numbers() == [5]

    labelled, labelled_intakes, _ = reading(github, tmp_path, workflow=essai(issue_label="test"))
    labelled.read_once()
    assert labelled_intakes.numbers() == [4, 6]
    assert sorted(number for number, _ in fake.posted) == [4, 5, 6]


def test_without_the_owner_s_login_no_issue_is_taken(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(5)])
    intake, intakes, _ = reading(serve(fake), tmp_path, owner=None)

    intake.read_once()

    assert intakes.inputs == []
    assert fake.posted == []
    assert fake_runtime.writes() == []
    assert registered(tmp_path) == []


def test_the_owner_s_login_is_read_from_the_environment(fake_runtime, monkeypatch, capsys):
    import workflow_core.service as service

    plugged = {"essai": Workflow(key="essai", board="essai", repo=ESSAI_REPO)}
    monkeypatch.setenv("WORKFLOW_CORE_OWNER_LOGIN", OWNER)
    app = service.build_service(secret="s", workflows=plugged, adapter=runtime_adapter(), github=NoIssues())
    assert (app.state.intake.owner, app.state.gestures.owner) == (OWNER, OWNER)
    assert "WORKFLOW_CORE_OWNER_LOGIN" not in capsys.readouterr().err

    # Without it, the rest of the core runs; the journal says it once, at the start.
    monkeypatch.delenv("WORKFLOW_CORE_OWNER_LOGIN")
    app = service.build_service(secret="s", workflows=plugged, adapter=runtime_adapter(), github=NoIssues())
    assert (app.state.intake.owner, app.state.gestures.owner) == (None, None)
    assert [workflow.key for workflow in app.state.engine.workflows] == ["essai"]
    app.state.intake.read_once()
    app.state.gestures.read_once()
    told = [line for line in capsys.readouterr().err.splitlines() if "WORKFLOW_CORE_OWNER_LOGIN" in line]
    assert len(told) == 1
    assert "aucune issue" in told[0] and "aucun geste" in told[0]


def test_an_unreadable_or_late_intake_is_retried(fake_runtime, serve, tmp_path):
    chatty = ManifestIntake(command=["python", "commands/chatty.py"], timeout_seconds=60)
    sleeps = ManifestIntake(command=["python", "commands/sleeps.py"], timeout_seconds=1)
    for command in (chatty, sleeps):
        fake = FakeGitHub([issue(7)])
        intake, intakes, lines = reading(serve(fake), tmp_path, workflow=essai(intake=command))

        intake.read_once()
        intake.read_once()

        assert intakes.numbers() == [7, 7]
        assert creates(fake_runtime) == []
        assert fake.posted == []
        assert registered(tmp_path) == []
        assert any(line.startswith("<4>") and "intake sans décision" in line for line in lines), lines
    assert any("délai de 1 s dépassé" in line for line in lines), lines
    # An id with a trailing newline is no decision: it would make the key `essai:7\n`.
    with pytest.raises(ValidationError):
        IntakeDecision.model_validate({"decision": "accept", "id": "7\n"})


def test_a_card_created_without_its_comment_gets_it_on_the_next_read(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(7)])
    fake.refuse_posts = 403
    intake, intakes, lines = reading(serve(fake), tmp_path)

    intake.read_once()
    assert cards(fake_runtime) == [("essai:7", "essai-preparer", issue_url(7))]
    assert fake.posted == []
    assert registered(tmp_path) == []
    assert any(line.startswith("<3>") and "commentaire non posté" in line for line in lines), lines

    fake.refuse_posts = None
    intake.read_once()

    assert intakes.numbers() == [7, 7]
    assert len(creates(fake_runtime)) == 2
    assert cards(fake_runtime) == [("essai:7", "essai-preparer", issue_url(7))]
    assert len(fake.posted) == 1
    assert intake_marker(REPO, 7, "accept") in fake.posted[0][1]
    assert registered(tmp_path) == [(REPO, 7, "essai:7", "accept")]


def test_nothing_is_created_or_posted_in_observer_mode(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([issue(7), issue(8, "[refus] essai")])
    intake, intakes, lines = reading(serve(fake), tmp_path, core=state(ok=False))

    intake.read_once()
    intake.read_once()

    assert fake.issue_reads == 2
    assert intakes.inputs == []
    assert fake_runtime.writes() == []
    assert fake.posted == []
    assert registered(tmp_path) == []
    # Once for each issue taken, whatever the number of readings.
    assert len([line for line in lines if "en attente du mode normal" in line]) == 2


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_github_is_read_at_start_then_every_ten_minutes(fake_runtime, serve, tmp_path):
    fake = FakeGitHub([])
    intake, _, _ = reading(serve(fake), tmp_path)
    clock = Clock()
    runner = IntakeRunner(intake, clock=clock)
    assert GITHUB_POLL_SECONDS == 600

    read_at = []
    for now in (0, 599, 600, 1199, 1200):
        clock.now = now
        if runner.tick():
            read_at.append(now)

    assert read_at == [0, 600, 1200]
    assert fake.issue_reads == 3

    # Without any workflow plugged by its manifest, the reading never starts.
    intake.workflows = []
    idle = IntakeRunner(intake, clock=clock)
    asyncio.run(idle.start())
    assert idle._task is None


class NoIssues:
    """A GitHub client that holds no issue and no comment."""

    def list_open_issues(self, repo: str) -> list:
        return []

    def list_comments(self, repo: str, number: int) -> list:
        return []

    def list_comments_since(self, repo: str, number: int, since: str | None = None) -> list:
        return []

    def post_comment(self, repo: str, number: int, body: str) -> str:
        raise AssertionError("nothing to post")


def test_the_service_reads_github_only_with_a_workflow_plugged_by_its_manifest(fake_runtime, monkeypatch):
    import workflow_core.service as service

    started: list[IntakeRunner] = []

    class Recorded(IntakeRunner):
        async def start(self) -> None:
            started.append(self)
            await super().start()

    monkeypatch.setattr(service, "IntakeRunner", Recorded)
    make_board(fake_runtime.home, "factory", FACTORY_CARDS)
    plugged = {"essai": Workflow(key="essai", board="essai", repo=ESSAI_REPO)}
    sheet_only = {"factory": Workflow(key="factory", board="factory", sheet=Path("factory.json"))}

    app = service.build_service(secret="s", workflows=plugged, adapter=runtime_adapter(), github=NoIssues())
    assert [workflow.key for workflow in app.state.intake.workflows] == ["essai"]
    assert app.state.intake.workflows[0].issues_repos == (REPO,)
    # The owner's gestures are read with the issues, by the same runner.
    assert [workflow.key for workflow in app.state.gestures.workflows] == ["essai"]
    with TestClient(app):
        assert len(started) == 1
        assert started[0].intake is app.state.intake
        assert started[0].gestures is app.state.gestures
        assert started[0]._task is not None

    started.clear()
    app = service.build_service(secret="s", workflows=sheet_only, adapter=runtime_adapter(), github=NoIssues())
    assert app.state.intake.workflows == []
    with TestClient(app):
        assert len(started) == 1
        assert started[0]._task is None
