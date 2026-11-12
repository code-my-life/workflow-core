"""The rules of the diagnosis, pure functions of an episode's facts, without any AI model (ADR 0012).

Ported from factory-live's rules at commit d47f235 (packages/domain/src/diagnosis/rules.ts). Each rule reads precise
facts and returns zero, one or several findings; none reads the agent's free text. The runtime's own words a finding
quotes (the name of a setting) come from the adapter's texts.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from workflow_core.diagnosis.episode import Episode, profile_name
from workflow_core.diagnosis.findings import PRESENT, UNKNOWN, RawFinding, RawSetting, evidence, step, subject
from workflow_core.diagnosis.rules_guards import (
    guard_hook_hung,
    guard_hook_raised,
    guard_hook_timeout,
    guard_refusals,
    plugin_load_failed,
)
from workflow_core.model.diagnosis import message

Rule = Callable[[Episode], list[RawFinding]]


def _whole(value: float) -> int | float:
    # JSON has one number type: 7260.0 is written 7260, as factory-live writes it.
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _number(value: Any) -> int | float:
    """JavaScript's `Number(value ?? 0)` for the values a payload holds; what it cannot read is 0."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return _whole(float(value)) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            number = float(value.strip() or 0)
        except ValueError:
            return 0
        return _whole(number) if math.isfinite(number) else 0
    return 0


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _unique(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def runtime_limit(episode: Episode) -> list[RawFinding]:
    """A run stopped because it went past the card's maximum duration (`runtimeLimit`).

    The duration and the limit come from the `timed_out` events of the episode. Reading them from a run's error text
    comes with the rules of the runs.
    """
    hits = [
        (event.created_at, event.run_id, _number(event.payload.get("elapsed_seconds")), _whole(limit))
        for event in episode.events_of("timed_out")
        if _is_number(limit := event.payload.get("limit_seconds"))
    ]
    if not hits:
        return []
    at, run_id, elapsed, limit = hits[-1]
    run = episode.run_of(run_id)
    name = profile_name(run) if run is not None else (episode.profile or "?")
    current = episode.card.max_runtime_seconds
    key = episode.terms.runtime_limit_key
    return [
        RawFinding(
            code="runtime_limit",
            confidence="certain",
            actor="you_factory",
            cause=UNKNOWN if current is not None and current > limit else PRESENT,
            summary=message("diagnosis.summary.runtimeLimit", seconds=elapsed),
            why=message("diagnosis.why.runtimeLimit", seconds=elapsed, limit=limit),
            subject=subject(name),
            setting=RawSetting(
                profile=name,
                key=key,
                unit="seconds",
                observed=limit,
                current=current,
                default_value=None,
                effective=current,
                repo=None,
            ),
            evidence=[
                evidence(
                    "kanban",
                    at,
                    message("diagnosis.evidence.lastSignals", count=len(hits)),
                    run_id=run_id,
                    quote=f"elapsed {elapsed}s > limit {limit}s",
                )
            ],
            steps=[step(message("diagnosis.step.raiseLimit", key=key, profile=name))],
            run_ids=_unique([hit[1] for hit in hits if hit[1] is not None]),
        )
    ]


# In factory-live's order (`RULES`). The other rules come with the next stories of the epic.
RULES: tuple[Rule, ...] = (
    guard_hook_timeout,
    guard_hook_hung,
    guard_hook_raised,
    plugin_load_failed,
    runtime_limit,
    guard_refusals,
)


def findings_of(episode: Episode) -> list[RawFinding]:
    """Every finding of the ported rules, in their order."""
    return [finding for rule in RULES for finding in rule(episode)]
