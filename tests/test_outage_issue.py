"""The outage issue: after the watchdog writes an outage of /healthz, an issue is opened or commented in
code-my-life/workflow-core, and closed at the recovery (ADR 0019)."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

import pytest
from workflow_core.adapters.github import AppTokens, GitHubClient
from workflow_core.notify.outage import MARKER, TITLE, run_once

ROOT = Path(__file__).resolve().parent.parent
BOT = "code-my-life-factory[bot]"
REPO = "/repos/code-my-life/workflow-core"
START = datetime(2026, 11, 11, 20, 0, 0, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeGitHub(BaseHTTPRequestHandler):
    """The App's token endpoints and the issues of one repository; can answer 502 to everything."""

    issues: ClassVar[list[dict]] = []
    calls: ClassVar[list[tuple[str, str]]] = []
    failing: ClassVar[bool] = False

    def _answer(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _issue(self, path: str) -> dict | None:
        number = int(path.split("/")[5])
        return next((issue for issue in FakeGitHub.issues if issue["number"] == number), None)

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        if path == "/orgs/code-my-life/installation" and method == "GET":
            return self._answer(200, {"id": 42})
        if path == "/app/installations/42/access_tokens" and method == "POST":
            return self._answer(201, {"token": "ghs_test", "expires_at": "2030-01-01T00:00:00Z", "permissions": {}})
        FakeGitHub.calls.append((method, self.path))
        if FakeGitHub.failing:
            return self._answer(502, {"message": "Bad Gateway"})
        if path == f"{REPO}/issues" and method == "GET":
            return self._answer(200, [{k: v for k, v in i.items() if k != "comments"} for i in FakeGitHub.issues])
        if path == f"{REPO}/issues" and method == "POST":
            sent = self._body()
            issue = {
                "number": 100 + len(FakeGitHub.issues),
                "title": sent["title"],
                "body": sent["body"],
                "state": "open",
                "closed_at": None,
                "user": {"login": BOT},
                "comments": [],
            }
            FakeGitHub.issues.append(issue)
            return self._answer(201, issue)
        if path.startswith(f"{REPO}/issues/") and path.endswith("/comments") and method == "POST":
            issue = self._issue(path)
            issue["comments"].append(self._body()["body"])
            return self._answer(201, {"html_url": f"https://github.com/c/w/issues/{issue['number']}#c"})
        if path.startswith(f"{REPO}/issues/") and method == "PATCH":
            issue = self._issue(path)
            issue["state"] = self._body()["state"]
            return self._answer(200, issue)
        return self._answer(404, {"message": "Not Found"})

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PATCH(self) -> None:
        self._route("PATCH")

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def github(tmp_path):
    FakeGitHub.issues = []
    FakeGitHub.calls = []
    FakeGitHub.failing = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    key = tmp_path / "app-key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    tokens = AppTokens(
        environ={},
        env_file=env_file,
        cache_dir=tmp_path / "state",
        api_url=f"http://127.0.0.1:{server.server_address[1]}",
    )
    yield GitHubClient(tokens)
    server.shutdown()


def watchdog(state: Path, first_failure_at: str | None, reported: bool) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "watchdog.json").write_text(
        json.dumps({"first_failure_at": first_failure_at, "reported": reported}) + "\n", encoding="utf-8"
    )


def passage(state: Path, client: GitHubClient, now: datetime) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = run_once(state_dir=state, client=client, clock=lambda: now, out=out)
    return code, out.getvalue().splitlines()


def noted(state: Path) -> int | None:
    return json.loads((state / "outage-issue.json").read_text(encoding="utf-8"))["issue"]


def outage_issue(number: int, state: str, closed_at: datetime | None = None, login: str = BOT) -> dict:
    return {
        "number": number,
        "title": TITLE,
        "body": f"Panne.\n\n{MARKER}",
        "state": state,
        "closed_at": iso(closed_at) if closed_at else None,
        "user": {"login": login},
        "comments": [],
    }


def test_an_outage_written_by_the_watchdog_opens_an_issue(tmp_path, github):
    state = tmp_path / "state"
    watchdog(state, "2026-11-11T20:00:00+00:00", True)

    code, _ = passage(state, github, START + timedelta(minutes=5))

    assert code == 0
    assert len(FakeGitHub.issues) == 1
    issue = FakeGitHub.issues[0]
    assert issue["title"] == "Le socle workflow-core ne répond plus"
    assert "2026-11-11T20:00:00+00:00" in issue["body"]
    assert "<!-- workflow-core:panne -->" in issue["body"]
    assert noted(state) == issue["number"]

    calls = len(FakeGitHub.calls)
    assert passage(state, github, START + timedelta(minutes=6))[0] == 0
    assert len(FakeGitHub.calls) == calls


def test_an_open_outage_issue_is_commented_not_duplicated(tmp_path, github):
    state = tmp_path / "state"
    FakeGitHub.issues = [outage_issue(7, "open")]
    watchdog(state, "2026-11-11T20:00:00+00:00", True)

    passage(state, github, START + timedelta(minutes=5))

    assert len(FakeGitHub.issues) == 1
    assert FakeGitHub.issues[0]["comments"] == [
        "Le socle ne répond toujours pas depuis 2026-11-11T20:00:00+00:00 (5 min).\n\n<sub>Chien de garde du socle "
        "workflow-core</sub>"
    ]
    assert noted(state) == 7


def test_an_outage_issue_closed_within_a_day_is_reopened(tmp_path, github):
    now = START + timedelta(minutes=5)
    state = tmp_path / "state"
    FakeGitHub.issues = [outage_issue(7, "closed", now - timedelta(hours=3))]
    watchdog(state, "2026-11-11T20:00:00+00:00", True)

    passage(state, github, now)

    assert len(FakeGitHub.issues) == 1
    assert FakeGitHub.issues[0]["state"] == "open"
    assert len(FakeGitHub.issues[0]["comments"]) == 1
    assert noted(state) == 7

    # Closed 25 hours ago: a new issue.
    other = tmp_path / "other"
    FakeGitHub.issues = [outage_issue(7, "closed", now - timedelta(hours=25))]
    watchdog(other, "2026-11-11T20:00:00+00:00", True)
    passage(other, github, now)
    assert [issue["state"] for issue in FakeGitHub.issues] == ["closed", "open"]
    assert noted(other) == FakeGitHub.issues[1]["number"]


def test_an_issue_marked_by_another_account_is_never_taken(tmp_path, github):
    state = tmp_path / "state"
    FakeGitHub.issues = [outage_issue(7, "open", login="inconnu")]
    watchdog(state, "2026-11-11T20:00:00+00:00", True)

    passage(state, github, START + timedelta(minutes=5))

    assert FakeGitHub.issues[0]["comments"] == []
    assert len(FakeGitHub.issues) == 2
    assert noted(state) == FakeGitHub.issues[1]["number"]


def test_the_recovery_comments_and_closes_the_issue(tmp_path, github):
    state = tmp_path / "state"
    watchdog(state, "2026-11-11T20:00:00+00:00", True)
    passage(state, github, START + timedelta(minutes=5))
    number = noted(state)

    watchdog(state, None, False)
    passage(state, github, START + timedelta(minutes=8))

    issue = FakeGitHub.issues[0]
    assert issue["number"] == number
    assert issue["comments"][-1].startswith("Le socle répond de nouveau, après 8 min de panne")
    assert issue["state"] == "closed"
    assert noted(state) is None

    calls = len(FakeGitHub.calls)
    passage(state, github, START + timedelta(minutes=9))
    assert len(FakeGitHub.calls) == calls


def test_a_github_failure_is_retried_on_the_next_passage(tmp_path, github):
    state = tmp_path / "state"
    watchdog(state, "2026-11-11T20:00:00+00:00", True)
    FakeGitHub.failing = True

    code, lines = passage(state, github, START + timedelta(minutes=5))

    assert code == 0
    assert len(lines) == 1
    assert lines[0].startswith("<3>")
    assert "502" in lines[0]
    assert not (state / "outage-issue.json").exists() or noted(state) is None

    FakeGitHub.failing = False
    passage(state, github, START + timedelta(minutes=6))
    assert len(FakeGitHub.issues) == 1
    assert noted(state) == FakeGitHub.issues[0]["number"]


def test_the_watchdog_unit_runs_the_outage_command_after_the_probe():
    lines = (ROOT / "systemd" / "workflow-core-watchdog.service").read_text(encoding="utf-8").splitlines()
    # The probe first, then the outage command once it has ended (ExecStartPost of a oneshot unit).
    commands = [line for line in lines if line.startswith(("ExecStart=", "ExecStartPost="))]

    assert commands == [
        "ExecStart=/usr/bin/python3 %h/perso/workflow-core/scripts/healthz_watchdog.py",
        "ExecStartPost=%h/perso/workflow-core/.venv/bin/python -m workflow_core.notify.outage",
    ]
    assert "Type=oneshot" in lines
