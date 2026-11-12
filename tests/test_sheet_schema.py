"""The JSON Schema of the sheet's form, published in schema/ for the clients (ADR 0006)."""

from pathlib import Path

from workflow_core.model.sheet import sheet_schema_text

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
    "shared-agent",
)


def test_the_published_schema_is_the_model_schema():
    published = SCHEMA.read_bytes()
    assert published == sheet_schema_text().encode("utf-8")
    assert published.endswith(b"}\n")
    text = published.decode("utf-8")
    assert not [rule for rule in CROSS_RULES if rule in text]


def test_the_run_verdicts_response_matches_its_published_schema():
    import json

    import jsonschema
    from workflow_core.model.diagnosis import run_verdicts_schema_text

    published = (SCHEMA.parent / "run-verdicts.schema.json").read_bytes()
    assert published == run_verdicts_schema_text().encode("utf-8")
    response = {
        "schema_version": 1,
        "verdicts": [
            {
                "runId": 191,
                "finding": "guard_hook_timeout",
                "role": "consequence",
                "text": {"code": "diagnosis.run.blockedAfterRefusals", "params": {"count": 3}},
                "sessionId": "20261029_093652_fc383d",
            },
            {"runId": 7, "finding": None, "role": None, "text": "texte tel quel", "sessionId": None},
        ],
    }
    jsonschema.Draft202012Validator(json.loads(published)).validate(response)
