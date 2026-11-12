"""The diagnosis of a card, served by the core's API (ADR 0012): the first rule, `runtime_limit`, contract to route.

The bases are built with the runtime's real schema (tests/fixtures/hermes-schema/kanban.sql), in a temporary folder.
The expected findings are written out by hand, copied from factory-live's `runtimeLimit` rule at commit d47f235
(packages/domain/src/diagnosis/rules.ts), never computed by the core's code.
"""

import json
import re
import sqlite3
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import Workflow, load_workflows

from conftest import RUNTIME_HOME_VAR, make_runtime_board, make_session_base

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
    # Blocked for a missing capability: one of the six kinds of cards that get a diagnosis.
    "status": "blocked",
    "block_kind": "capability",
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
OLD_TIMEOUT_CARD = {**TIMED_OUT_CARD, "id": "t_0043"}
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
    # Blocked again after the crash: the block the diagnosis tells.
    {"id": 18, "task_id": "t_0043", "run_id": 12, "kind": "blocked", "payload": "{}", "created_at": 1793009310},
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
    assert body["trigger"] == {
        "id": "capability:t_0042:1793000000",
        "kind": "capability",
        "event": None,
        "opened_at": "2026-10-26T07:33:20.000Z",
        "agent": "coder",
    }
    assert diagnosis["others"] == []
    # The full assembly: the run's verdict, the unblocking advice (factory-live's `diagnose` and `advice`).
    assert diagnosis["runs"] == [
        {
            "runId": 1,
            "finding": "runtime_limit",
            "role": "cause",
            "text": {"code": "diagnosis.run.timedOut", "params": {}},
            "sessionId": None,
        }
    ]
    assert diagnosis["work"] is None
    assert diagnosis["unblock"] == {
        "when": "after_fix",
        "template": {"code": "diagnosis.unblock.causeFixed", "params": {"branch": None}},
        "nextBlock": "blocked",
    }
    # No agent profile, journal, session base nor work folder in this runtime home.
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
    assert response.json() == {
        "schema_version": 1,
        "workflow": "factory",
        "item": "t_0044",
        "trigger": None,
        "diagnosis": None,
    }


def test_a_timeout_of_a_previous_episode_is_not_counted(client):
    response = diagnosis_of(client, "t_0043")

    assert response.status_code == 200
    diagnosis = response.json()["diagnosis"]
    # The episode starts at the unblocking: the timeout before it is not counted, nothing else is recognised.
    assert diagnosis["episode"] == {"from": "2026-10-26T10:01:40.000Z", "runIds": [12]}
    assert diagnosis["primary"]["code"] == "unexplained"
    assert "runtime_limit" not in [finding["code"] for finding in diagnosis["others"]]


def test_a_missing_base_is_a_named_error(client, tmp_path):
    (tmp_path / "runtime" / "kanban" / "boards" / "studio" / "kanban.db").unlink()

    response = diagnosis_of(client, "t_0042", workflow="studio")

    assert response.status_code == 503
    assert response.json()["error"] == "board_unavailable"


# --- The full diagnosis, on two boards with their agents' profiles, sessions and journals --------------------------

BLOCKED_AT = 1793100000


def blocked_board(home: Path, board: str, card: str, agent: str) -> None:
    """A board holding one card blocked for an answer by its agent, and one card still running."""
    make_runtime_board(
        home,
        board,
        tasks=[
            {
                "id": card,
                "title": "Une carte qui attend",
                "assignee": agent,
                "status": "blocked",
                "block_kind": "needs_input",
                "created_at": BLOCKED_AT - 600,
            },
            {"id": card[:-1] + "f", "title": "Une carte qui tourne", "status": "running", "created_at": BLOCKED_AT},
        ],
        runs=[
            {
                "id": 1,
                "task_id": card,
                "profile": agent,
                "status": "blocked",
                "outcome": "blocked",
                "started_at": BLOCKED_AT - 500,
                "ended_at": BLOCKED_AT,
                "metadata": json.dumps({"worker_session_id": f"s_{board}"}),
            }
        ],
        events=[
            {"id": 1, "task_id": card, "kind": "created", "payload": "{}", "created_at": BLOCKED_AT - 600},
            {
                "id": 2,
                "task_id": card,
                "run_id": 1,
                "kind": "blocked",
                "payload": json.dumps({"reason": "Quelle durée pour la vidéo ?", "kind": "needs_input"}),
                "created_at": BLOCKED_AT,
            },
        ],
    )
    # Rollback journal, not WAL: a read-only reading then creates no file next to the base.
    with sqlite3.connect(home / "kanban" / "boards" / board / "kanban.db") as db:
        db.execute("PRAGMA journal_mode=delete")
    db.close()


def agent_profile(home: Path, agent: str) -> None:
    """The agent's configuration, its session base with the run's session, and its journal."""
    folder = home / "profiles" / agent
    (folder / "logs").mkdir(parents=True)
    (folder / "config.yaml").write_text("agent:\n  max_turns: 90\nplugins:\n  enabled: []\n", encoding="utf-8")
    base = make_session_base(home, agent)
    session = "s_factory" if agent == "coder" else "s_studio"
    base.execute("INSERT INTO sessions (id, source, started_at) VALUES (?, 'kanban', ?)", (session, BLOCKED_AT - 498))
    base.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'go', ?)",
        (session, BLOCKED_AT - 497),
    )
    base.execute("PRAGMA journal_mode=delete")
    base.close()
    (folder / "logs" / "agent.log").write_text(
        "2026-10-27 10:00:00,000 INFO agent.conversation_loop: Turn ended: reason=stop\n", encoding="utf-8"
    )


@pytest.fixture
def two_boards(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "runtime-home"
    blocked_board(home, "factory", "t_000000a1", "coder")
    blocked_board(home, "studio", "t_000000b1", "scriptwriter")
    agent_profile(home, "coder")
    agent_profile(home, "scriptwriter")
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    return home


@pytest.fixture
def full_client(two_boards) -> TestClient:
    workflows = load_workflows(ROOT / "config" / "workflows.toml")
    app = create_app(
        secret=SECRET, workflows=workflows, boards=runtime_adapter().make_board_reader(), clock=lambda: BLOCKED_AT + 60
    )
    return TestClient(app)


def test_a_blocked_card_of_each_board_has_its_full_diagnosis(full_client):
    schema = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    for workflow, card, agent in (("factory", "t_000000a1", "coder"), ("studio", "t_000000b1", "scriptwriter")):
        body = diagnosis_of(full_client, card, workflow=workflow).json()

        assert body["trigger"] == {
            "id": f"needs_input:{card}:2",
            "kind": "needs_input",
            "event": 2,
            "opened_at": "2026-10-27T11:20:00.000Z",
            "agent": agent,
        }
        diagnosis = body["diagnosis"]
        schema.validate(diagnosis)
        # An awaited answer: the agent's question, for the owner to answer.
        assert (diagnosis["primary"]["code"], diagnosis["primary"]["actor"]) == ("question", "you_answer")
        assert diagnosis["unblock"]["when"] == "answer"
        assert diagnosis["runs"] == [
            {
                "runId": 1,
                "finding": "question",
                "role": "cause",
                "text": {"code": "diagnosis.run.question", "params": {}},
                "sessionId": f"s_{workflow}",
            }
        ]
        assert diagnosis["sources"] == {"transcript": True, "agentLog": True, "config": True, "workspace": False}


def test_the_diagnoses_route_lists_the_triggers_of_a_board(full_client):
    for workflow, card in (("factory", "t_000000a1"), ("studio", "t_000000b1")):
        response = full_client.get(f"/api/workflows/{workflow}/diagnoses", headers=GOOD)

        assert response.status_code == 200
        body = response.json()
        assert (body["schema_version"], body["workflow"]) == (1, workflow)
        assert [(entry["item"], entry["trigger"]["kind"]) for entry in body["diagnoses"]] == [(card, "needs_input")]
        single = diagnosis_of(full_client, card, workflow=workflow).json()
        assert body["diagnoses"][0] == {"item": card, "trigger": single["trigger"], "diagnosis": single["diagnosis"]}


def test_a_card_that_is_not_a_trigger_has_no_diagnosis(full_client):
    body = diagnosis_of(full_client, "t_000000af").json()

    assert (body["trigger"], body["diagnosis"]) == (None, None)


def test_an_unreadable_source_is_said_not_raised(full_client, two_boards):
    # No session base, a journal that is a folder, no configuration: the diagnosis says it, with a 200.
    coder = two_boards / "profiles" / "coder"
    (coder / "state.db").unlink()
    (coder / "logs" / "agent.log").unlink()
    (coder / "logs" / "agent.log").mkdir()
    (coder / "config.yaml").unlink()

    response = diagnosis_of(full_client, "t_000000a1")

    assert response.status_code == 200
    assert response.json()["diagnosis"]["sources"] == {
        "transcript": False,
        "agentLog": False,
        "config": False,
        "workspace": False,
    }


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def test_computing_the_diagnoses_writes_nothing_in_the_runtime_home(full_client, two_boards):
    before = snapshot(two_boards)

    for workflow in ("factory", "studio"):
        assert full_client.get(f"/api/workflows/{workflow}/diagnoses", headers=GOOD).status_code == 200
    for workflow, card in (("factory", "t_000000a1"), ("studio", "t_000000b1")):
        assert diagnosis_of(full_client, card, workflow=workflow).status_code == 200

    assert snapshot(two_boards) == before


def test_the_existing_routes_answer_as_before(runtime_home):
    from conftest import FACTORY_ITEMS, STUDIO_ITEMS

    app = create_app(secret=SECRET, workflows=WORKFLOWS, boards=runtime_adapter().make_board_reader())
    client = TestClient(app)

    assert client.get("/api/workflows/factory/items", headers=GOOD).json() == {
        "schema_version": 1,
        "workflow": "factory",
        "items": FACTORY_ITEMS,
    }
    assert client.get("/api/workflows/studio/items", headers=GOOD).json() == {
        "schema_version": 1,
        "workflow": "studio",
        "items": STUDIO_ITEMS,
    }
    listed = client.get("/api/workflows", headers=GOOD).json()
    assert listed == {
        "schema_version": 1,
        "workflows": [
            {"key": "factory", "board": "factory", "sheet_valid": False, "gestures": []},
            {"key": "studio", "board": "studio", "sheet_valid": False, "gestures": []},
        ],
    }
    machine = client.get("/api/machine", headers=GOOD).json()
    assert set(machine) == {"schema_version", "runtime_version", "contract", "mode", "boards"}


def test_the_diagnosis_of_a_card_is_the_same_from_any_thread(two_boards):
    import threading

    workflows = load_workflows(ROOT / "config" / "workflows.toml")
    app = create_app(
        secret=SECRET, workflows=workflows, boards=runtime_adapter().make_board_reader(), clock=lambda: BLOCKED_AT + 60
    )
    client = TestClient(app)
    answers: list = []

    def ask() -> None:
        answers.append(diagnosis_of(client, "t_000000a1").json())

    for _ in range(3):
        thread = threading.Thread(target=ask)
        thread.start()
        thread.join()

    assert answers[0]["diagnosis"]["sources"]["transcript"] is True
    assert answers[1:] == [answers[0], answers[0]]
