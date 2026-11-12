"""The findings of the rules, in Unix seconds, and their form in the contract (ADR 0012).

As in factory-live at commit d47f235, the rules build findings with times in seconds and lists of any length
(packages/domain/src/diagnosis/types.ts); `finding_view` gives the contract's form: ISO 8601 times, bounded lists,
positive identifiers (`findingView`, apps/server/src/application/diagnosis.ts).
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflow_core.clock import iso_ms
from workflow_core.model.diagnosis import (
    Actor,
    CauseFixed,
    CauseNone,
    CausePresent,
    CauseUnknown,
    Evidence,
    EvidenceSource,
    Finding,
    FindingCode,
    Setting,
    Step,
    Subject,
    Text,
)


class _Raw(BaseModel):
    model_config = ConfigDict(frozen=True)


class RawCauseFixed(_Raw):
    state: Literal["fixed"] = "fixed"
    at: float
    verified: bool
    durable: bool | None


RawCause = Annotated[CausePresent | RawCauseFixed | CauseUnknown | CauseNone, Field(discriminator="state")]

PRESENT = CausePresent(state="present")
UNKNOWN = CauseUnknown(state="unknown")
NONE = CauseNone(state="none")


class RawEvidence(_Raw):
    source: EvidenceSource
    at: float | None
    until: float | None = None
    run_id: float | None = None
    fact: Text
    quote: str | None = None
    count: float | None = None


class RawSetting(_Raw):
    profile: str
    key: str
    unit: Literal["seconds", "turns"]
    observed: float | None
    current: float | None
    default_value: float | None
    # `inf` when nothing cuts the limit any more: JSON writes it `null`, as JavaScript does.
    effective: float | None
    repo: float | None


class RawFinding(_Raw):
    code: FindingCode
    confidence: Literal["certain", "likely"]
    actor: Actor
    cause: RawCause
    summary: Text
    why: Text
    subject: Subject
    setting: RawSetting | None
    evidence: list[RawEvidence]
    steps: list[Step]
    run_ids: list[int]


def evidence(source: str, at: float | None, fact: Text, **fields) -> RawEvidence:
    """A fact of the diagnosis; `until`, `run_id`, `quote` and `count` are `None` unless given."""
    return RawEvidence(source=source, at=at, fact=fact, **fields)


def step(text: Text, command: str | None = None, done: bool = False) -> Step:
    return Step(text=text, command=command, done=done)


def subject(profile: str | None, plugin: str | None = None, tool: str | None = None) -> Subject:
    return Subject(profile=profile, plugin=plugin, tool=tool)


def unique(values) -> list:
    return list(dict.fromkeys(values))


def js_round(value: float) -> int:
    """JavaScript's `Math.round`: halves go up."""
    return math.floor(value + 0.5)


def _positive(value: float | None) -> int | None:
    return js_round(value) if value is not None and math.isfinite(value) and value > 0 else None


def _number(value: float | None) -> float | int | None:
    """A number as JSON holds it: an integral value is written without decimals, `inf` is `null`."""
    if value is None or not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() else value


def finding_view(finding: RawFinding) -> Finding:
    """The finding in the contract's form: ISO times, at most 8 proofs and 6 steps, positive identifiers."""
    cause = finding.cause
    if isinstance(cause, RawCauseFixed):
        cause = CauseFixed(state="fixed", at=iso_ms(cause.at), verified=cause.verified, durable=cause.durable)
    setting = finding.setting
    return Finding(
        code=finding.code,
        confidence=finding.confidence,
        actor=finding.actor,
        cause=cause,
        summary=finding.summary,
        why=finding.why,
        subject=finding.subject,
        setting=None
        if setting is None
        else Setting(
            profile=setting.profile,
            key=setting.key,
            unit=setting.unit,
            observed=_number(setting.observed),
            current=_number(setting.current),
            default_value=_number(setting.default_value),
            effective=_number(setting.effective),
            repo=_number(setting.repo),
        ),
        evidence=[
            Evidence(
                source=item.source,
                at=None if item.at is None else iso_ms(item.at),
                until=None if item.until is None else iso_ms(item.until),
                run_id=_positive(item.run_id),
                fact=item.fact,
                quote=item.quote,
                count=_positive(item.count),
            )
            for item in finding.evidence[:8]
        ],
        steps=list(finding.steps[:6]),
        run_ids=list(finding.run_ids),
    )
