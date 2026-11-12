"""The JSON Schema of the diagnosis, published in schema/ for the clients (ADR 0012, ADR 0018).

The field names are those of factory-live's zod contract `Diagnosis` at commit d47f235
(packages/contracts/src/diagnosis.ts), written out here by hand: a renamed field in the model fails this test.
"""

import json
from pathlib import Path

from workflow_core.model.diagnosis import diagnosis_schema_text

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "diagnosis.schema.json"

# Each object of the zod contract, with all its keys: zod makes every one of them required (nullable, not optional).
ZOD_OBJECTS = {
    "Diagnosis": {"computedAt", "episode", "primary", "others", "unblock", "runs", "work", "sources"},
    "Episode": {"from", "runIds"},
    "Sources": {"transcript", "agentLog", "config", "workspace"},
    "Finding": {
        "code",
        "confidence",
        "actor",
        "cause",
        "summary",
        "why",
        "subject",
        "setting",
        "evidence",
        "steps",
        "runIds",
    },
    "Subject": {"profile", "plugin", "tool"},
    "CausePresent": {"state"},
    "CauseFixed": {"state", "at", "verified", "durable"},
    "CauseUnknown": {"state"},
    "CauseNone": {"state"},
    "Setting": {"profile", "key", "unit", "observed", "current", "defaultValue", "effective", "repo"},
    "Evidence": {"source", "at", "until", "runId", "fact", "quote", "count"},
    "Step": {"text", "command", "done"},
    "RunVerdict": {"runId", "finding", "role", "text", "sessionId"},
    "EvidenceFile": {"name", "bytes", "at"},
    "WorkState": {"branch", "commits", "commitsRefused", "pushed", "prUrl", "editedFiles", "evidence"},
    "UnblockAdvice": {"when", "template", "nextBlock"},
    "Message": {"code", "params"},
}
FINDING_CODES = [
    "guard_hook_timeout",
    "guard_hook_hung",
    "guard_hook_raised",
    "plugin_load_failed",
    "stopped_after_refusals",
    "stopped_without_kanban_call",
    "protocol_breaker",
    "iteration_budget",
    "runtime_limit",
    "tool_deadline",
    "command_timeout",
    "guard_refusal",
    "refusal_loop",
    "provider_quota",
    "provider_terminal",
    "provider_unavailable",
    "pr_acceptance_infra",
    "pr_acceptance_missing",
    "spawn_failed",
    "held_active_pr",
    "triage_loop",
    "question",
    "unexplained",
]
ACTORS = ["you_factory", "you_answer", "agent", "wait"]
EVIDENCE_SOURCES = ["kanban", "transcript", "webhook", "agent_log", "config", "plugin", "workspace"]


def published() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_the_published_diagnosis_schema_is_the_model_schema():
    text = SCHEMA.read_bytes()

    assert text == diagnosis_schema_text().encode("utf-8")
    assert text.endswith(b"}\n")


def test_the_field_names_are_those_of_the_factory_live_contract():
    schema = published()
    objects = {"Diagnosis": schema, **schema["$defs"]}

    for name, keys in ZOD_OBJECTS.items():
        assert name in objects, name
        assert set(objects[name]["properties"]) == keys, name
        assert set(objects[name]["required"]) == keys, name
    assert schema["$defs"]["FindingCode"]["enum"] == FINDING_CODES
    assert schema["$defs"]["Actor"]["enum"] == ACTORS
    assert schema["$defs"]["EvidenceSource"]["enum"] == EVIDENCE_SOURCES
    # The bounds of the zod contract.
    assert schema["properties"]["others"]["maxItems"] == 4
    finding = schema["$defs"]["Finding"]["properties"]
    assert finding["evidence"]["maxItems"] == 8
    assert finding["steps"]["maxItems"] == 6
    assert schema["$defs"]["WorkState"]["properties"]["evidence"]["maxItems"] == 50
    quote = schema["$defs"]["Evidence"]["properties"]["quote"]["anyOf"]
    assert {"maxLength": 400, "type": "string"} in quote
    # The core never knows the pull request: `work.prUrl` is always null.
    assert schema["$defs"]["WorkState"]["properties"]["prUrl"]["type"] == "null"
