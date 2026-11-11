"""The rules of the diagnosis, pure functions of an episode's facts, without any AI model (ADR 0012).

Ported from factory-live's rules at commit d47f235 (packages/domain/src/diagnosis/rules.ts). Each rule reads precise
facts and returns zero, one or several findings; none reads the agent's free text. The runtime's own words a finding
quotes (the name of a setting) come from the adapter's `RuntimeTerms`.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from workflow_core.clock import iso_ms
from workflow_core.diagnosis.episode import Episode, profile_name
from workflow_core.model.diagnosis import (
    CausePresent,
    CauseUnknown,
    Evidence,
    Finding,
    Setting,
    Step,
    Subject,
    message,
)
from workflow_core.model.facts import RuntimeTerms

Rule = Callable[[Episode, RuntimeTerms], list[Finding]]


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


def runtime_limit(episode: Episode, terms: RuntimeTerms) -> list[Finding]:
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
    key = terms.runtime_limit_key
    return [
        Finding(
            code="runtime_limit",
            confidence="certain",
            actor="you_factory",
            cause=CauseUnknown(state="unknown")
            if current is not None and current > limit
            else CausePresent(state="present"),
            summary=message("diagnosis.summary.runtimeLimit", seconds=elapsed),
            why=message("diagnosis.why.runtimeLimit", seconds=elapsed, limit=limit),
            subject=Subject(profile=name, plugin=None, tool=None),
            setting=Setting(
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
                Evidence(
                    source="kanban",
                    at=iso_ms(at),
                    until=None,
                    run_id=run_id if run_id is not None and run_id > 0 else None,
                    fact=message("diagnosis.evidence.lastSignals", count=len(hits)),
                    quote=f"elapsed {elapsed}s > limit {limit}s",
                    count=None,
                )
            ],
            steps=[Step(text=message("diagnosis.step.raiseLimit", key=key, profile=name), command=None, done=False)],
            run_ids=_unique([hit[1] for hit in hits if hit[1] is not None]),
        )
    ]


# In factory-live's order. The other rules come with the next stories of the epic.
RULES: tuple[Rule, ...] = (runtime_limit,)
