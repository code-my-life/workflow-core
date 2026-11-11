"""The core's HTTP API: the cards of the declared workflows, behind the shared secret (ADR 0011)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import load_workflows

from conftest import FACTORY_CARDS

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
    assert response.json() == {"schema_version": 1, "workflow": "factory", "items": FACTORY_CARDS}


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
    # `studio` has a base in the runtime home but is not declared in config/workflows.toml at this story.
    for key in ("studio", "nope"):
        response = client.get(f"/api/workflows/{key}/items", headers=GOOD)
        assert response.status_code == 404
        assert response.json()["error"] == "unknown_workflow"
        assert key in response.json()["detail"]

    (runtime_home / "kanban" / "boards" / "factory" / "kanban.db").unlink()
    response = client.get("/api/workflows/factory/items", headers=GOOD)
    assert response.status_code == 503
    assert response.json()["error"] == "board_unavailable"
    assert "factory" in response.json()["detail"]
