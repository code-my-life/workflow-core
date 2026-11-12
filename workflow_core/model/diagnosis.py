"""The diagnosis of a blocked card, as the core publishes it (ADR 0012, ADR 0018).

The form is factory-live's zod contract `Diagnosis` at commit d47f235 (packages/contracts/src/diagnosis.ts), field by
field: the JSON keys are the same (camelCase), every field is required, a missing value is `null`. A text is either a
code of factory-live's catalogue with its parameters, `{"code": "diagnosis.<...>", "params": {...}}`, or a quote of a
source, as is. Times are ISO 8601 UTC to the millisecond (`2026-10-29T09:47:00.000Z`). The form is published as a
JSON Schema in `schema/diagnosis.schema.json`; within its version, it only gains optional fields.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# The version of the diagnosis contract (ADR 0018).
DIAGNOSIS_VERSION = 1


class FindingCode(StrEnum):
    """The causes the diagnosis recognises; `unexplained` when no rule applies."""

    GUARD_HOOK_TIMEOUT = "guard_hook_timeout"
    GUARD_HOOK_HUNG = "guard_hook_hung"
    GUARD_HOOK_RAISED = "guard_hook_raised"
    PLUGIN_LOAD_FAILED = "plugin_load_failed"
    STOPPED_AFTER_REFUSALS = "stopped_after_refusals"
    STOPPED_WITHOUT_KANBAN_CALL = "stopped_without_kanban_call"
    PROTOCOL_BREAKER = "protocol_breaker"
    ITERATION_BUDGET = "iteration_budget"
    RUNTIME_LIMIT = "runtime_limit"
    TOOL_DEADLINE = "tool_deadline"
    COMMAND_TIMEOUT = "command_timeout"
    GUARD_REFUSAL = "guard_refusal"
    REFUSAL_LOOP = "refusal_loop"
    PROVIDER_QUOTA = "provider_quota"
    PROVIDER_TERMINAL = "provider_terminal"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PR_ACCEPTANCE_INFRA = "pr_acceptance_infra"
    PR_ACCEPTANCE_MISSING = "pr_acceptance_missing"
    SPAWN_FAILED = "spawn_failed"
    HELD_ACTIVE_PR = "held_active_pr"
    TRIAGE_LOOP = "triage_loop"
    QUESTION = "question"
    UNEXPLAINED = "unexplained"


class Actor(StrEnum):
    """Who must act: the owner on the workflow side (setting, plugin), the owner by an answer, the agent, or nobody."""

    YOU_FACTORY = "you_factory"
    YOU_ANSWER = "you_answer"
    AGENT = "agent"
    WAIT = "wait"


class EvidenceSource(StrEnum):
    KANBAN = "kanban"
    TRANSCRIPT = "transcript"
    WEBHOOK = "webhook"
    AGENT_LOG = "agent_log"
    CONFIG = "config"
    PLUGIN = "plugin"
    WORKSPACE = "workspace"


Timestamp = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegative = Annotated[float | int, Field(ge=0)]
Param = str | int | float | bool | None


class _Contract(BaseModel):
    # JSON keys in camelCase, as the zod contract writes them; Python attributes in snake_case.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class Message(_Contract):
    """A text the core makes itself: a code of factory-live's catalogue and its parameters."""

    code: Annotated[str, Field(pattern=r"^diagnosis\.[A-Za-z]+(\.[A-Za-z]+)+$")]
    params: dict[str, Param]


# A text: a code with its parameters, or a quote of a source, as is.
Text = Message | str


def message(code: str, **params: Param) -> Message:
    return Message(code=code, params=params)


class CausePresent(_Contract):
    state: Literal["present"]


class CauseFixed(_Contract):
    state: Literal["fixed"]
    at: Timestamp
    verified: bool
    durable: bool | None


class CauseUnknown(_Contract):
    state: Literal["unknown"]


class CauseNone(_Contract):
    state: Literal["none"]


Cause = Annotated[CausePresent | CauseFixed | CauseUnknown | CauseNone, Field(discriminator="state")]


class Setting(_Contract):
    """The setting at fault: the limit observed at the incident, the value in service, the repository's."""

    profile: str
    key: str
    unit: Literal["seconds", "turns"]
    observed: NonNegative | None
    current: NonNegative | None
    default_value: NonNegative | None
    effective: NonNegative | None
    repo: NonNegative | None


class Evidence(_Contract):
    """A fact the diagnosis rests on: what the core says of it, and the exact quote of the source."""

    source: EvidenceSource
    at: Timestamp | None
    until: Timestamp | None
    run_id: PositiveInt | None
    fact: Text
    quote: Annotated[str, Field(max_length=400)] | None
    count: PositiveInt | None


class Step(_Contract):
    text: Text
    command: str | None
    done: bool


class Subject(_Contract):
    profile: str | None
    plugin: str | None
    tool: str | None


class Finding(_Contract):
    code: FindingCode
    confidence: Literal["certain", "likely"]
    actor: Actor
    cause: Cause
    summary: Text
    why: Text
    subject: Subject
    setting: Setting | None
    evidence: Annotated[list[Evidence], Field(max_length=8)]
    steps: Annotated[list[Step], Field(max_length=6)]
    run_ids: list[PositiveInt]


class RunVerdict(_Contract):
    run_id: PositiveInt
    finding: FindingCode | None
    role: Literal["cause", "consequence"] | None
    text: Text
    session_id: str | None


class EvidenceFile(_Contract):
    name: str
    bytes: NonNegativeInt
    at: Timestamp


class WorkState(_Contract):
    branch: str | None
    commits: Literal["none", "some", "unknown"]
    commits_refused: NonNegativeInt
    pushed: Literal["yes", "no", "unknown"]
    # The core never knows the pull request; factory-live fills it in.
    pr_url: None
    edited_files: NonNegativeInt
    evidence: Annotated[list[EvidenceFile], Field(max_length=50)]


class UnblockAdvice(_Contract):
    when: Literal["now", "after_fix", "answer", "wait", "not_unblockable"]
    template: Text | None
    next_block: Literal["blocked", "triage"] | None


class Episode(_Contract):
    """The runs examined: since the last unblocking, the creation, or a manual reclaim."""

    from_: Timestamp = Field(alias="from")
    run_ids: list[PositiveInt]


class Sources(_Contract):
    """The sources read: a missing one leaves some conclusions only likely."""

    transcript: bool
    agent_log: bool
    config: bool
    workspace: bool


class Diagnosis(_Contract):
    model_config = ConfigDict(json_schema_extra={"x-diagnosis-version": DIAGNOSIS_VERSION})

    computed_at: Timestamp
    episode: Episode
    primary: Finding
    others: Annotated[list[Finding], Field(max_length=4)]
    unblock: UnblockAdvice
    runs: list[RunVerdict]
    work: WorkState | None
    sources: Sources


def diagnosis_json(diagnosis: Diagnosis) -> dict:
    """The diagnosis with the contract's JSON keys."""
    return diagnosis.model_dump(mode="json", by_alias=True)


def diagnosis_schema_text() -> str:
    """The JSON Schema of the diagnosis, as published in schema/diagnosis.schema.json."""
    schema = Diagnosis.model_json_schema(by_alias=True)
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
