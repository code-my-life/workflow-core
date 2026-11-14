"""The lanes of a workflow plugged by its manifest (`<key>-<station>`) are never a profile: a card assigned to one may
stay ready a long time, so it gets neither the held alert nor the held trigger of the diagnosis. Its blocks still do.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.adapters.github import IssueComment
from workflow_core.api.app import create_app
from workflow_core.config import Workflow
from workflow_core.diagnosis.triggers import trigger_of
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.notify import Notifier, NotifySettings
from workflow_core.plugin.manifest import lanes_of, plug
from workflow_core.service import build_service

from conftest import RUNTIME_HOME_VAR, make_board, make_runtime_board

ROOT = Path(__file__).resolve().parent.parent
ESSAI_REPO = ROOT / "tests" / "fixtures" / "manifests" / "essai"
NOON = datetime(2026, 11, 12, 12, 0, tzinfo=UTC).timestamp()
READY = NOON + 60
ISSUE = "https://github.com/code-my-life/workflow-essai/issues/7"
FACTORY_ISSUE = "https://github.com/code-my-life/workflow-core/issues/99"
BOT = "code-my-life-factory[bot]"
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}

ESSAI = Workflow(key="essai", repo=ESSAI_REPO, notify=True)
FACTORY = Workflow(
    key="factory",
    board="factory",
    sheet=Path("factory.json"),
    notify=True,
    notify_skip_assignees=["factory-gate", "factory-release"],
    notify_relay_agents=["scribe", "coder"],
)


class FakeGitHub:
    """Records the comments posted; every issue starts without a comment."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, int, str]] = []

    def post_comment(self, repo: str, number: int, body: str) -> str:
        self.posted.append((repo, number, body))
        return f"https://github.com/{repo}/issues/{number}#issuecomment-{len(self.posted)}"

    def list_comments(self, repo: str, number: int) -> list[IssueComment]:
        return [
            IssueComment(author=BOT, body=body, url="https://github.com/x")
            for posted_repo, posted_number, body in self.posted
            if (posted_repo, posted_number) == (repo, number)
        ]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "runtime"
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(path))
    return path


def essai_lanes() -> dict[str, frozenset[str]]:
    return {"essai": lanes_of(plug(Workflow(key="essai", repo=ESSAI_REPO)))}


def card(id: str, *, assignee: str, body: str = ISSUE, status: str = "ready", block_kind: str | None = None) -> dict:
    return {
        "id": id,
        "title": "Premier essai",
        "body": body,
        "status": status,
        "assignee": assignee,
        "created_at": int(NOON) - 3600,
        "started_at": None,
        "completed_at": None,
        "block_kind": block_kind,
    }


def event(id: int, task: str, kind: str, at: float, payload: str = "{}") -> dict:
    return {"id": id, "task_id": task, "run_id": None, "kind": kind, "payload": payload, "created_at": int(at)}


def held_events(task: str) -> list[dict]:
    return [event(1, task, "created", READY), event(2, task, "respawn_guarded", READY, '{"reason": "active_pr"}')]


def notifier(tmp_path, github: FakeGitHub, workflows: dict[str, Workflow], lanes, now: float) -> Notifier:
    return Notifier(
        workflows=workflows,
        reader=runtime_adapter().make_board_reader(),
        state=CoreState(contract=ContractVerdict(ok=True, checked_at="2026-11-12T11:00:00+00:00", problems=[])),
        settings=NotifySettings.from_environ(
            {
                "WORKFLOW_CORE_NOTIFY": "on",
                "WORKFLOW_CORE_NOTIFY_SINCE": "2026-11-12T13:00:00+01:00",
                "WORKFLOW_CORE_LIVE_URL": "https://live.example.ts.net:8446",
            }
        ),
        post_comment=github.post_comment,
        read_comments=github.list_comments,
        register_path=tmp_path / "state" / "alerts.db",
        clock=lambda: now,
        log=lambda line: None,
        lanes=lanes,
    )


def test_a_lane_card_ready_for_45_minutes_gets_no_held_alert(home, tmp_path):
    make_board(home, "essai", [card("t_00000001", assignee="essai-revue")], events=held_events("t_00000001"))
    github = FakeGitHub()

    notifier(tmp_path, github, {"essai": ESSAI}, essai_lanes(), READY + 45 * 60).run_pass()

    assert github.posted == []


def api(workflows: dict[str, Workflow], lanes, now: float) -> TestClient:
    reader = runtime_adapter().make_board_reader()
    return TestClient(create_app(secret=SECRET, workflows=workflows, boards=reader, clock=lambda: now, lanes=lanes))


def test_a_lane_card_gets_no_held_trigger(home):
    make_board(home, "essai", [card("t_00000001", assignee="essai-revue")], events=held_events("t_00000001"))
    now = READY + 45 * 60
    client = api({"essai": ESSAI}, essai_lanes(), now)

    found = client.get("/api/workflows/essai/items/t_00000001/diagnosis", headers=GOOD)
    assert found.status_code == 200
    assert found.json()["trigger"] is None
    assert found.json()["diagnosis"] is None
    assert client.get("/api/workflows/essai/diagnoses", headers=GOOD).json()["diagnoses"] == []
    assert client.get("/api/workflows/essai/interventions", headers=GOOD).json()["interventions"] == []
    facts = runtime_adapter().make_board_reader().read_card_facts("essai", "t_00000001")
    assert trigger_of(facts, ESSAI, now, lanes=essai_lanes()["essai"]) is None


def test_a_held_factory_card_still_gets_its_alert_and_trigger(home, tmp_path):
    make_board(
        home,
        "factory",
        [card("t_00000001", assignee="coder", body=FACTORY_ISSUE)],
        events=held_events("t_00000001"),
    )
    now = READY + 45 * 60
    github = FakeGitHub()

    notifier(tmp_path, github, {"factory": FACTORY}, essai_lanes(), now).run_pass()

    assert len(github.posted) == 1
    assert "### ⚠️ Carte prête mais retenue" in github.posted[0][2]
    found = api({"factory": FACTORY}, essai_lanes(), now).get(
        "/api/workflows/factory/items/t_00000001/diagnosis", headers=GOOD
    )
    assert found.json()["trigger"]["kind"] == "held_active_pr"


def test_the_lanes_of_a_plugged_workflow_come_from_its_manifest():
    lanes = lanes_of(plug(Workflow(key="essai", repo=ESSAI_REPO)))

    assert lanes == frozenset({"essai-preparer", "essai-revue", "essai-clore"})


@pytest.mark.parametrize(("kind", "heading"), [("needs_input", "Décision attendue"), ("capability", "Outil ou accès")])
def test_a_blocked_lane_card_keeps_its_block_trigger_and_alert(home, tmp_path, kind, heading):
    blocked = json.dumps({"kind": kind, "reason": "Quelle branche garder ?", "recurrences": 1})
    make_board(
        home,
        "essai",
        [card("t_00000001", assignee="essai-preparer", status="blocked", block_kind=kind)],
        events=[event(1, "t_00000001", "created", READY), event(2, "t_00000001", "blocked", READY + 60, blocked)],
    )
    now = READY + 45 * 60
    github = FakeGitHub()

    notifier(tmp_path, github, {"essai": ESSAI}, essai_lanes(), now).run_pass()

    assert len(github.posted) == 1
    repo, number, body = github.posted[0]
    assert (repo, number) == ("code-my-life/workflow-essai", 7)
    assert heading in body
    assert body.endswith(f"<!-- workflow-core:attente essai:{kind}:t_00000001:2 -->")
    found = api({"essai": ESSAI}, essai_lanes(), now).get(
        "/api/workflows/essai/items/t_00000001/diagnosis", headers=GOOD
    )
    assert found.json()["trigger"]["kind"] == kind


def test_the_lane_of_an_agent_station_is_among_the_lanes(tmp_path):
    repo = tmp_path / "essai-repo"
    shutil.copytree(ESSAI_REPO, repo)
    manifest = json.loads((repo / "workflow-manifest.json").read_text(encoding="utf-8"))
    sheet = json.loads((repo / "workflow-sheet.json").read_text(encoding="utf-8"))
    manifest["stations"].insert(1, {"key": "ecrire", "nature": "agent", "profile": "redacteur"})
    sheet["stations"].append({"key": "ecrire", "label": "Écrire", "icon": "dot", "kind": "ai", "col": 1, "row": 1})
    sheet["edges"].append({"from": "preparer", "to": "ecrire", "kind": "line"})
    (repo / "workflow-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "workflow-sheet.json").write_text(json.dumps(sheet), encoding="utf-8")

    lanes = lanes_of(plug(Workflow(key="essai", repo=repo)))

    assert "essai-ecrire" in lanes
    assert lanes == frozenset({"essai-preparer", "essai-ecrire", "essai-revue", "essai-clore"})


def test_the_service_gives_the_lanes_of_its_plugged_workflows_to_the_api_and_the_notifier(runtime_home, tmp_path):
    repo = tmp_path / "essai-repo"
    shutil.copytree(ESSAI_REPO, repo)
    make_runtime_board(
        runtime_home,
        "essai",
        tasks=[
            {
                "id": "t_00000001",
                "title": "essai:7 · Premier essai",
                "body": ISSUE,
                "assignee": "essai-revue",
                "status": "ready",
                "created_at": int(NOON) - 3600,
                "idempotency_key": "essai:7",
            }
        ],
        events=[
            {"id": 1, "task_id": "t_00000001", "kind": "created", "payload": "{}", "created_at": int(NOON) - 3600},
            {
                "id": 2,
                "task_id": "t_00000001",
                "kind": "respawn_guarded",
                "payload": '{"reason": "active_pr"}',
                "created_at": int(NOON) - 3600,
            },
        ],
    )
    workflows = {"factory": FACTORY, "essai": Workflow(key="essai", repo=repo)}

    app = build_service(secret=SECRET, workflows=workflows, adapter=runtime_adapter(), clock=lambda: NOON)
    found = TestClient(app).get("/api/workflows/essai/items/t_00000001/diagnosis", headers=GOOD)

    assert found.status_code == 200
    assert found.json()["trigger"] is None
    assert app.state.notifier.lanes["essai"] == frozenset({"essai-preparer", "essai-revue", "essai-clore"})
