"""Where an item stands (#140): the station of its card, read from the workflow's sheet by a pure rule."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from workflow_core.adapters import runtime_adapter
from workflow_core.api.app import create_app
from workflow_core.config import load_workflows

from conftest import RUNTIME_HOME_VAR, make_board

SECRET = "test-secret-not-a-real-one"
GOOD = {"Authorization": f"Bearer {SECRET}"}


def station(key: str, col: int, kind: str, **extra) -> dict:
    return {"key": key, "label": key, "icon": "dot", "kind": kind, "col": col, "row": 0, **extra}


def card(card_id: str, status: str, assignee: str | None) -> dict:
    return {
        "id": card_id,
        "title": card_id,
        "body": None,
        "status": status,
        "assignee": assignee,
        "created_at": 1762900000,
        "started_at": None,
        "completed_at": None,
        "block_kind": None,
    }


CARDS = [
    card("t_redaction", "running", "redacteur"),
    card("t_lane", "blocked", "essai-validation"),
    card("t_finie", "done", "redacteur"),
    card("t_ailleurs", "todo", "inconnu"),
]


def serve(tmp_path: Path, monkeypatch, stations: list[dict]) -> TestClient:
    """A workflow `essai` on its own board, with this sheet."""
    home = tmp_path / "runtime"
    make_board(home, "essai", CARDS)
    monkeypatch.setenv(RUNTIME_HOME_VAR, str(home))
    sheet = tmp_path / "essai.json"
    sheet.write_text(
        json.dumps({"item": {"type": "dossier", "label": "Dossier"}, "stations": stations, "edges": [], "models": []}),
        encoding="utf-8",
    )
    config = tmp_path / "workflows.toml"
    config.write_text(f'[[workflow]]\nkey = "essai"\nboard = "essai"\nsheet = "{sheet}"\n', encoding="utf-8")
    app = create_app(secret=SECRET, workflows=load_workflows(config), boards=runtime_adapter().make_board_reader())
    return TestClient(app)


def stations_served(client: TestClient) -> dict[str, str | None]:
    response = client.get("/api/workflows/essai/items", headers=GOOD)
    assert response.status_code == 200
    return {item["id"]: item["station"] for item in response.json()["items"]}


def test_an_item_stands_at_the_station_of_its_card(tmp_path, monkeypatch):
    client = serve(
        tmp_path,
        monkeypatch,
        [
            station("redaction", 0, "ai", agents=["redacteur"]),
            station("validation", 1, "owner"),
            station("fin", 2, "end"),
        ],
    )

    assert stations_served(client) == {
        "t_redaction": "redaction",
        "t_lane": "validation",
        "t_finie": "fin",
        "t_ailleurs": None,
    }


def test_without_agents_the_lane_name_and_the_end_still_place_an_item(tmp_path, monkeypatch):
    client = serve(
        tmp_path,
        monkeypatch,
        [station("redaction", 0, "ai"), station("validation", 1, "owner"), station("fin", 2, "end")],
    )

    assert stations_served(client) == {
        "t_redaction": None,
        "t_lane": "validation",
        "t_finie": "fin",
        "t_ailleurs": None,
    }


def test_a_missing_or_refused_sheet_places_no_item(tmp_path, monkeypatch):
    client = serve(
        tmp_path,
        monkeypatch,
        [station("redaction", 0, "ai", agents=["redacteur"]), station("bis", 1, "ai", agents=["redacteur"])],
    )

    assert set(stations_served(client).values()) == {None}
