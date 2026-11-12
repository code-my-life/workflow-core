"""The diagnosis of a card, served by the core's API (ADR 0012): the first rule, `runtime_limit`, contract to route.

The bases are built with the runtime's real schema (tests/fixtures/hermes-schema/kanban.sql), in a temporary folder.
The expected findings are written out by hand, copied from factory-live's `runtimeLimit` rule at commit d47f235
(packages/domain/src/diagnosis/rules.ts), never computed by the core's code.
"""

import json
import re
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import Workflow

from conftest import RUNTIME_HOME_VAR, make_runtime_board

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "diagnosis.schema.json"
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
WORKFLOWS = {
    "factory": Workflow(key="factory", board="factory", sheet=Path("factory.json")),
    "studio": Workflow(key="studio", board="studio", sheet=Path("studio.json")),
}
# 2026-10-26T10:20:00Z: the time of the computation, from the API's clock.
NOW = 1793010000
ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

# A card whose first run went past its 2-hour limit: created 07:33:20, run 1 from 07:34:20, timed out at 09:35:20.
TIMED_OUT_CARD = {
    "id": "t_0042",
    "title": "Le socle publie le diagnostic",
    "assignee": "coder",
    "status": "ready",
    "created_at": 1793000000,
    "max_runtime_seconds": 7200,
}
TIMED_OUT_RUNS = [
    {
        "id": 1,
        "task_id": "t_0042",
        "profile": "coder",
        "status": "timed_out",
        "outcome": "timed_out",
        "started_at": 1793000060,
        "ended_at": 1793007320,
        "metadata": "{}",
    },
]
TIMED_OUT_EVENTS = [
    {"id": 1, "task_id": "t_0042", "run_id": None, "kind": "created", "payload": "{}", "created_at": 1793000000},
    {"id": 2, "task_id": "t_0042", "run_id": 1, "kind": "claimed", "payload": "{}", "created_at": 1793000060},
    {
        "id": 3,
        "task_id": "t_0042",
        "run_id": 1,
        "kind": "timed_out",
        "payload": json.dumps({"elapsed_seconds": 7260, "limit_seconds": 7200}),
        "created_at": 1793007320,
    },
]

# A card that timed out, was blocked, unblocked by hand, then crashed: the timeout belongs to the previous episode.
OLD_TIMEOUT_CARD = {**TIMED_OUT_CARD, "id": "t_0043", "status": "ready"}
OLD_TIMEOUT_RUNS = [
    {**TIMED_OUT_RUNS[0], "id": 11, "task_id": "t_0043"},
    {
        "id": 12,
        "task_id": "t_0043",
        "profile": "coder",
        "status": "crashed",
        "outcome": "crashed",
        "started_at": 1793009000,
        "ended_at": 1793009300,
    },
]
OLD_TIMEOUT_EVENTS = [
    *(
        {**event, "id": event["id"] + 10, "task_id": "t_0043", "run_id": event["run_id"] and event["run_id"] + 10}
        for event in TIMED_OUT_EVENTS
    ),
    {"id": 14, "task_id": "t_0043", "run_id": None, "kind": "blocked", "payload": "{}", "created_at": 1793007400},
    {"id": 15, "task_id": "t_0043", "run_id": None, "kind": "unblocked", "payload": "{}", "created_at": 1793008900},
    {"id": 16, "task_id": "t_0043", "run_id": 12, "kind": "claimed", "payload": "{}", "created_at": 1793009000},
    {"id": 17, "task_id": "t_0043", "run_id": 12, "kind": "crashed", "payload": "{}", "created_at": 1793009300},
]

# A card that only ran and completed: nothing to diagnose.
QUIET_CARD = {"id": "t_0044", "title": "Rien à dire", "assignee": "coder", "status": "done", "created_at": 1793000000}

# The finding factory-live's `runtimeLimit` rule builds for TIMED_OUT_CARD, written out field by field.
EXPECTED_PRIMARY = {
    "code": "runtime_limit",
    "confidence": "certain",
    "actor": "you_factory",
    "cause": {"state": "present"},
    "summary": {"code": "diagnosis.summary.runtimeLimit", "params": {"seconds": 7260}},
    "why": {"code": "diagnosis.why.runtimeLimit", "params": {"seconds": 7260, "limit": 7200}},
    "subject": {"profile": "coder", "plugin": None, "tool": None},
    "setting": {
        "profile": "coder",
        "key": "max_runtime_seconds",
        "unit": "seconds",
        "observed": 7200,
        "current": 7200,
        "defaultValue": None,
        "effective": 7200,
        "repo": None,
    },
    "evidence": [
        {
            "source": "kanban",
            "at": "2026-10-26T09:35:20.000Z",
            "until": None,
            "runId": 1,
            "fact": {"code": "diagnosis.evidence.lastSignals", "params": {"count": 1}},
            "quote": "elapsed 7260s > limit 7200s",
            "count": None,
        }
    ],
    "steps": [
        {
            "text": {"code": "diagnosis.step.raiseLimit", "params": {"key": "max_runtime_seconds", "profile": "coder"}},
            "command": None,
            "done": False,
        }
    ],
    "runIds": [1],
}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    home = tmp_path / "runtime"
    make_runtime_board(
        home,
        "factory",
        tasks=[TIMED_OUT_CARD, OLD_TIMEOUT_CARD, QUIET_CARD],
        runs=TIMED_OUT_RUNS + OLD_TIMEOUT_RUNS,
        events=TIMED_OUT_EVENTS + OLD_TIMEOUT_EVENTS,
    )
    make_runtime_board(home, "studio", tasks=[])
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    app = create_app(
        secret=SECRET, workflows=WORKFLOWS, boards=runtime_adapter().make_board_reader(), clock=lambda: NOW
    )
    return TestClient(app)


def diagnosis_of(client: TestClient, card: str, workflow: str = "factory"):
    return client.get(f"/api/workflows/{workflow}/items/{card}/diagnosis", headers=GOOD)


@pytest.mark.acceptance("test_a_card_past_its_runtime_gets_a_runtime_limit_diagnosis")
def test_a_card_past_its_runtime_gets_a_runtime_limit_diagnosis(client):
    response = diagnosis_of(client, "t_0042")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["workflow"] == "factory"
    assert body["item"] == "t_0042"
    diagnosis = body["diagnosis"]
    assert diagnosis["primary"] == EXPECTED_PRIMARY
    assert diagnosis["computedAt"] == "2026-10-26T10:20:00.000Z"
    assert diagnosis["episode"] == {"from": "2026-10-26T07:33:20.000Z", "runIds": [1]}
    assert diagnosis["others"] == []
    assert diagnosis["runs"] == []
    assert diagnosis["work"] is None
    assert diagnosis["unblock"] == {"when": "after_fix", "template": None, "nextBlock": None}
    assert diagnosis["sources"] == {"transcript": False, "agentLog": False, "config": False, "workspace": False}


def test_the_diagnosis_is_valid_against_the_published_schema(client):
    diagnosis = diagnosis_of(client, "t_0042").json()["diagnosis"]

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(diagnosis)
    times = [diagnosis["computedAt"], diagnosis["episode"]["from"]]
    times += [item[key] for item in diagnosis["primary"]["evidence"] for key in ("at", "until") if item[key]]
    assert len(times) == 3
    assert all(ISO_MS.match(time) for time in times)


def test_the_diagnosis_route_refuses_a_missing_secret(client):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = client.get("/api/workflows/factory/items/t_0042/diagnosis", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"


def test_an_unknown_card_or_workflow_is_a_named_error(client):
    card = diagnosis_of(client, "t_9999")
    assert card.status_code == 404
    assert card.json()["error"] == "unknown_item"
    assert "t_9999" in card.json()["detail"]

    workflow = diagnosis_of(client, "t_0042", workflow="nope")
    assert workflow.status_code == 404
    assert workflow.json()["error"] == "unknown_workflow"

    # The studio board holds no card: the card of another board is unknown there.
    assert diagnosis_of(client, "t_0042", workflow="studio").json()["error"] == "unknown_item"


def test_a_card_without_finding_has_a_null_diagnosis(client):
    response = diagnosis_of(client, "t_0044")

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "workflow": "factory", "item": "t_0044", "diagnosis": None}


def test_a_timeout_of_a_previous_episode_is_not_counted(client):
    response = diagnosis_of(client, "t_0043")

    assert response.status_code == 200
    assert response.json()["diagnosis"] is None


def test_a_missing_base_is_a_named_error(client, tmp_path):
    (tmp_path / "runtime" / "kanban" / "boards" / "studio" / "kanban.db").unlink()

    response = diagnosis_of(client, "t_0042", workflow="studio")

    assert response.status_code == 503
    assert response.json()["error"] == "board_unavailable"
