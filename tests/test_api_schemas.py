"""The JSON Schema of each response of the API, published in schema/ for the clients (ADR 0006, ADR 0011)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.config import load_workflows
from workflow_core.events import EventJournal
from workflow_core.model.api import API_SCHEMAS
from workflow_core.service import build_service

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schema"
SHEETS = Path(__file__).resolve().parent / "fixtures" / "sheets"
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
PUBLISHED = (
    "workflows.schema.json",
    "sheet-response.schema.json",
    "items.schema.json",
    "machine.schema.json",
    "event.schema.json",
    "error.schema.json",
    "live.schema.json",
    "activity.schema.json",
    "usage.schema.json",
    "declared-data.schema.json",
)


def validator(name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_every_published_api_schema_is_its_model_schema():
    assert sorted(API_SCHEMAS) == sorted(PUBLISHED)
    for name, text in API_SCHEMAS.items():
        published = (SCHEMAS / name).read_bytes()
        assert published == text().encode("utf-8"), name
        assert published.endswith(b"}\n")


@pytest.fixture
def service(runtime_home, tmp_path) -> TestClient:
    config = tmp_path / "workflows.toml"
    entries = []
    for name in ("factory", "studio"):
        sheet = tmp_path / f"{name}.json"
        sheet.write_text((SHEETS / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8")
        entries.append(f'[[workflow]]\nkey = "{name}"\nboard = "{name}"\nsheet = "{sheet}"\n')
    config.write_text("\n".join(entries), encoding="utf-8")
    return TestClient(build_service(secret=SECRET, workflows=load_workflows(config), adapter=runtime_adapter()))


def test_every_response_is_valid_against_its_published_schema(service, tmp_path):
    answers = {
        "workflows.schema.json": [service.get("/api/workflows", headers=GOOD)],
        "sheet-response.schema.json": [
            service.get(f"/api/workflows/{k}/sheet", headers=GOOD) for k in ("factory", "studio")
        ],
        "items.schema.json": [service.get(f"/api/workflows/{k}/items", headers=GOOD) for k in ("factory", "studio")],
        "machine.schema.json": [service.get("/api/machine", headers=GOOD)],
        "error.schema.json": [
            service.get("/api/workflows/factory/items"),
            service.get("/api/workflows/nope/items", headers=GOOD),
            service.get("/nowhere", headers=GOOD),
        ],
    }
    for name, responses in answers.items():
        check = validator(name)
        for response in responses:
            check.validate(response.json())
    assert [r.status_code for r in answers["error.schema.json"]] == [401, 404, 404]

    # The stream's events: the reset that opens an epoch, and a changed event carrying a full item.
    items = service.get("/api/workflows/factory/items", headers=GOOD).json()["items"]
    journal = EventJournal.rebuild(tmp_path / "events.db", 1762900000, {"factory": []})
    events = [journal.reset_event(), *journal.record("factory", items)]
    journal.close()
    check = validator("event.schema.json")
    for event in events:
        check.validate(event)
    assert [event["kind"] for event in events] == ["reset", "changed", "changed"]

    # The schema is not a formality: an item without its events, or an unknown kind of event, is refused.
    broken = {**items[0]}
    del broken["events"]
    with pytest.raises(jsonschema.ValidationError):
        validator("items.schema.json").validate({"schema_version": 1, "workflow": "factory", "items": [broken]})
    with pytest.raises(jsonschema.ValidationError):
        check.validate({**events[1], "kind": "renamed"})


def test_the_live_activity_and_usage_responses_match_their_schemas(runtime_home, tmp_path, monkeypatch):
    from conftest import make_session_base
    from test_events import board_base, write
    from test_live import CARD, SESSION, STARTED, terminal_call

    monkeypatch.setenv("HER" + "MES_TZ", "UTC")
    write(
        board_base(runtime_home, "factory"),
        "INSERT INTO tasks (id, title, status, assignee, created_at, current_run_id)"
        " VALUES (?, 'x', 'running', 'coder', ?, 3)",
        CARD,
        STARTED,
    )
    write(
        board_base(runtime_home, "factory"),
        "INSERT INTO task_runs (id, task_id, profile, status, started_at, metadata)"
        " VALUES (3, ?, 'coder', 'running', ?, ?)",
        CARD,
        STARTED,
        json.dumps({"worker_session_id": SESSION}),
    )
    base = make_session_base(runtime_home, "coder")
    base.execute(
        "INSERT INTO sessions (id, source, model, started_at) VALUES (?, 'kanban', 'auto', ?)", (SESSION, STARTED)
    )
    base.execute(
        "INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, ?)",
        (SESSION, terminal_call("c1", "npm test"), STARTED + 5),
    )
    base.execute(
        "INSERT INTO session_model_usage (session_id, model, billing_provider, api_call_count, last_seen)"
        " VALUES (?, 'glm-5.3', 'freellmapi', 2, ?)",
        (SESSION, STARTED),
    )
    base.close()
    config = tmp_path / "workflows.toml"
    sheet = tmp_path / "factory.json"
    sheet.write_text((SHEETS / "factory.json").read_text(encoding="utf-8"), encoding="utf-8")
    config.write_text(f'[[workflow]]\nkey = "factory"\nboard = "factory"\nsheet = "{sheet}"\n', encoding="utf-8")
    service = TestClient(build_service(secret=SECRET, workflows=load_workflows(config), adapter=runtime_adapter()))

    live = service.get("/api/workflows/factory/live", headers=GOOD).json()
    activity = service.get("/api/workflows/factory/activity?limit=10", headers=GOOD).json()
    usage = service.get("/api/workflows/factory/usage?days=90", headers=GOOD).json()
    validator("live.schema.json").validate(live)
    validator("activity.schema.json").validate(activity)
    validator("usage.schema.json").validate(usage)
    assert [agent["state"] for agent in live["agents"]] == ["tool"]
    assert activity["facts"]
    assert usage["models"][0]["calls"] == 2
    for route in ("/api/workflows/factory/activity?limit=201", "/api/workflows/factory/usage?days=91"):
        refused = service.get(route, headers=GOOD)
        assert refused.status_code == 422
        validator("error.schema.json").validate(refused.json())

    # The stream's live and activity events carry these same states.
    journal = EventJournal.rebuild(tmp_path / "events.db", 1762900000, {"factory": []})
    events = [
        journal.note("live", "factory", CARD, live["agents"][0]),
        journal.note("activity", "factory", CARD, activity["facts"][-1]),
    ]
    journal.close()
    check = validator("event.schema.json")
    for event in events:
        check.validate(event)
