"""The JSON Schema of the declared data's answer, published in schema/ for the clients (ADR 0011, issue #36)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from workflow_core.model.api import API_SCHEMAS

from test_declared_data import CARD, RUN, TWINS, factory, write  # noqa: F401 (the fixture)

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "declared-data.schema.json"
GOOD = {"Authorization": "Bearer test-secret-not-a-real-one"}


def test_the_published_declared_data_schema_is_the_model_schema():
    published = SCHEMA.read_bytes()
    assert published == API_SCHEMAS["declared-data.schema.json"]().encode("utf-8")
    assert published.endswith(b"}\n")


def test_every_response_is_valid_against_the_published_schema(request):
    built = request.getfixturevalue("factory")
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    write(built["board"] / "twins" / "factory-live-24.json", json.dumps(TWINS))
    write(built["board"] / "evidence" / CARD / "gate" / "abc1234" / "01-format.txt", "formatted")
    answers = [
        ("pr", "factory-live#24"),
        ("card", CARD),
        ("run", str(RUN)),
        ("agent", "coder"),
        ("pr", "factory-live#25"),
    ]
    for subject, item in answers:
        response = built["client"].get(
            "/api/workflows/factory/data", params={"subject": subject, "id": item}, headers=GOOD
        )
        assert response.status_code == 200
        validator.validate(response.json())
    write(built["board"] / "twins" / "factory-live-24.json", "{")
    validator.validate(
        built["client"]
        .get("/api/workflows/factory/data", params={"subject": "pr", "id": "factory-live#24"}, headers=GOOD)
        .json()
    )
    # The schema is not a formality: a value of an unknown type, or a status out of its five states, is refused.
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {
                "schema_version": 1,
                "workflow": "f",
                "subject": "pr",
                "id": "a#1",
                "data": [{"name": "n", "label": "l", "values": [{"type": "image", "label": "x"}]}],
            }
        )
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {
                "schema_version": 1,
                "workflow": "f",
                "subject": "pr",
                "id": "a#1",
                "data": [
                    {
                        "name": "n",
                        "label": "l",
                        "values": [{"type": "status", "label": "x", "status": "fine", "text": ""}],
                    }
                ],
            }
        )
