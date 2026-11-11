"""The core's HTTP API: the cards of the declared workflows, behind the shared secret (ADR 0011)."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import load_workflows
from workflow_core.service import build_service

from conftest import FACTORY_ITEMS, STUDIO_ITEMS

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}
ROUTES = ("/healthz", "/api/workflows/factory/items", "/api/workflows/nope/items", "/nowhere")


@pytest.fixture
def client(runtime_home) -> TestClient:
    app = create_app(
        secret=SECRET,
        workflows=load_workflows(ROOT / "config" / "workflows.toml"),
        boards=runtime_adapter().make_board_reader(),
    )
    return TestClient(app)


def test_the_factory_cards_are_served_with_the_secret(client):
    response = client.get("/api/workflows/factory/items", headers=GOOD)

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "workflow": "factory", "items": FACTORY_ITEMS}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}, {"Authorization": f"Basic {SECRET}"}],
    ids=["none", "wrong", "no-scheme", "basic"],
)
def test_every_route_refuses_a_missing_or_wrong_secret(client, route, headers):
    response = client.get(route, headers=headers)

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert body["detail"]
    assert SECRET not in response.text


def test_healthz_answers_with_the_secret(client):
    response = client.get("/healthz", headers=GOOD)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_an_undeclared_workflow_or_a_missing_base_is_a_named_error(client, runtime_home):
    # `video` and `nope` are declared nowhere in config/workflows.toml.
    for key in ("video", "nope"):
        response = client.get(f"/api/workflows/{key}/items", headers=GOOD)
        assert response.status_code == 404
        assert response.json()["error"] == "unknown_workflow"
        assert key in response.json()["detail"]

    (runtime_home / "kanban" / "boards" / "factory" / "kanban.db").unlink()
    response = client.get("/api/workflows/factory/items", headers=GOOD)
    assert response.status_code == 503
    assert response.json()["error"] == "board_unavailable"
    assert "factory" in response.json()["detail"]


SHEETS = Path(__file__).resolve().parent / "fixtures" / "sheets"
NEW_ROUTES = ("/api/workflows", "/api/workflows/factory/sheet", "/api/workflows/studio/sheet", "/api/machine")


@pytest.fixture
def sheets(tmp_path) -> dict[str, Path]:
    """The two current sheets, copied where the test can break them."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    paths = {}
    for name in ("factory", "studio"):
        paths[name] = folder / f"{name}.json"
        paths[name].write_text((SHEETS / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8")
    return paths


@pytest.fixture
def service(runtime_home, sheets, tmp_path) -> TestClient:
    """The service as `python -m workflow_core` builds it, both workflows declared, their sheets in a temp folder."""
    config = tmp_path / "workflows.toml"
    config.write_text(
        "".join(
            f'[[workflow]]\nkey = "{name}"\nboard = "{name}"\nsheet = "{path}"\n\n' for name, path in sheets.items()
        ),
        encoding="utf-8",
    )
    return TestClient(build_service(secret=SECRET, workflows=load_workflows(config), adapter=runtime_adapter()))


def test_both_workflows_are_served_with_their_judged_sheets(service, sheets):
    listed = service.get("/api/workflows", headers=GOOD)
    assert listed.status_code == 200
    assert listed.json() == {
        "schema_version": 1,
        "workflows": [
            {"key": "factory", "board": "factory", "sheet_valid": True},
            {"key": "studio", "board": "studio", "sheet_valid": True},
        ],
    }
    for name, path in sheets.items():
        sheet = service.get(f"/api/workflows/{name}/sheet", headers=GOOD)
        assert sheet.status_code == 200
        assert sheet.json() == {"schema_version": 1, "workflow": name, "sheet": json.loads(path.read_text())}

    studio = service.get("/api/workflows/studio/items", headers=GOOD)
    assert studio.status_code == 200
    assert studio.json() == {"schema_version": 1, "workflow": "studio", "items": STUDIO_ITEMS}


def test_an_invalid_declared_sheet_is_an_error_naming_the_rule(service, sheets):
    broken = json.loads(sheets["studio"].read_text())
    broken["stations"].append({**broken["stations"][0], "col": 9, "row": 9})
    sheets["studio"].write_text(json.dumps(broken), encoding="utf-8")

    response = service.get("/api/workflows/studio/sheet", headers=GOOD)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_sheet"
    assert response.json()["detail"].startswith("duplicate-station")
    listed = service.get("/api/workflows", headers=GOOD).json()["workflows"]
    assert [workflow["sheet_valid"] for workflow in listed] == [True, False]

    sheets["studio"].write_text("{", encoding="utf-8")
    unreadable = service.get("/api/workflows/studio/sheet", headers=GOOD)
    sheets["factory"].unlink()
    missing = service.get("/api/workflows/factory/sheet", headers=GOOD)
    for response in (unreadable, missing):
        assert response.status_code == 503
        assert response.json()["error"] == "sheet_unavailable"


def test_the_items_carry_their_runs_links_and_comments(service):
    response = service.get("/api/workflows/factory/items", headers=GOOD)

    assert response.status_code == 200
    items = response.json()["items"]
    assert items == FACTORY_ITEMS
    assert {item["id"]: item["children"] for item in items} == {"t_0001": ["t_0002"], "t_0002": []}


def test_the_machine_state_reports_version_contract_mode_and_last_reads(service):
    before = service.get("/api/machine", headers=GOOD)
    assert before.status_code == 200
    state = before.json()
    assert state["schema_version"] == 1
    assert state["runtime_version"] == "0.22.2"
    assert state["contract"]["ok"] is True
    assert state["contract"]["problems"] == []
    assert datetime.fromisoformat(state["contract"]["checked_at"]).utcoffset() == timedelta(0)
    assert state["mode"] == "normal"
    assert state["boards"] == {"factory": {"last_read_at": None}, "studio": {"last_read_at": None}}

    service.get("/api/workflows/factory/items", headers=GOOD)
    boards = service.get("/api/machine", headers=GOOD).json()["boards"]
    assert datetime.fromisoformat(boards["factory"]["last_read_at"]).utcoffset() == timedelta(0)
    assert boards["studio"] == {"last_read_at": None}


def test_a_failed_read_keeps_the_last_successful_read_time(service, runtime_home):
    assert service.get("/api/workflows/factory/items", headers=GOOD).status_code == 200
    first = service.get("/api/machine", headers=GOOD).json()["boards"]["factory"]["last_read_at"]
    assert first is not None

    (runtime_home / "kanban" / "boards" / "factory" / "kanban.db").unlink()
    time.sleep(1.1)
    assert service.get("/api/workflows/factory/items", headers=GOOD).status_code == 503

    assert service.get("/api/machine", headers=GOOD).json()["boards"]["factory"]["last_read_at"] == first


@pytest.mark.parametrize("route", NEW_ROUTES)
def test_every_new_route_refuses_a_missing_secret(service, route):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = service.get(route, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"
