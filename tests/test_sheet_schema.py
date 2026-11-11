"""The JSON Schema of the sheet's form, published in schema/ for the clients (ADR 0006)."""

from pathlib import Path

from workflow_core.model import sheet_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "workflow-sheet.schema.json"
CROSS_RULES = (
    "reserved-models-key",
    "duplicate-group",
    "duplicate-station",
    "station-is-group",
    "shared-cell",
    "unknown-group",
    "empty-group",
    "mixed-group",
    "duplicate-edge-id",
    "unknown-edge-end",
    "unknown-model-station",
)


def test_the_published_schema_is_the_model_schema():
    published = SCHEMA.read_bytes()
    assert published == sheet_schema_text().encode("utf-8")
    assert published.endswith(b"}\n")
    text = published.decode("utf-8")
    assert not [rule for rule in CROSS_RULES if rule in text]
