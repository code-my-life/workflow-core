"""The JSON Schema of the interventions' answer, published in schema/ for the clients (ADR 0011, ADR 0018)."""

from pathlib import Path

from workflow_core.model.api import API_SCHEMAS

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "interventions.schema.json"


def test_the_published_interventions_schema_is_the_model_schema():
    published = SCHEMA.read_bytes()
    assert published == API_SCHEMAS["interventions.schema.json"]().encode("utf-8")
    text = published.decode("utf-8")
    for field in ("opened_at", "severity", "push", "actions", "gesture", "command", "quote"):
        assert f'"{field}"' in text
