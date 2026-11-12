"""The rules of the runs (ADR 0012): an exit without a kanban call, after refusals or not, the breaker, the turn
budget, the run's maximum duration, the tool ceiling, a command stopped by its delay, a worker that does not start.

Ported from factory-live at commit d47f235 (packages/domain/src/diagnosis/rules.ts: `stoppedAfterRefusals`,
`stoppedWithoutKanbanCall`, `protocolBreaker`, `iterationBudget`, `runtimeLimit`, `toolDeadline`, `commandTimeout`,
`spawnFailed`). The runtime's texts, defaults and settings come from the adapter; the workflow's profiles folder from
its entry in config/workflows.toml.
"""

from __future__ import annotations

import math
from typing import Any

from workflow_core.diagnosis.episode import Episode, Observation, profile_name
from workflow_core.diagnosis.findings import (
    NONE,
    PRESENT,
    UNKNOWN,
    RawCauseFixed,
    RawEvidence,
    RawFinding,
    RawSetting,
    evidence,
    step,
    subject,
    unique,
)
from workflow_core.diagnosis.support import is_refusal, run_ids_of, runs_at, tools_summary
from workflow_core.model.diagnosis import message
from workflow_core.text import quote


def _text(value: Any) -> str:
    """A text field of a runtime payload; anything else counts as empty."""
    return value if isinstance(value, str) else ""


def _whole(value: float) -> float:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def js_number(value: Any) -> float:
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


def worker_output(episode: Episode, run_id: int) -> str | None:
    """What the worker wrote when it exited: noted on its run, else on its exit event."""
    run = episode.run_of(run_id)
    noted = run.metadata.get("worker_output") if run is not None else None
    if isinstance(noted, str):
        return noted
    event = next(
        (
            event
            for event in episode.events
            if event.run_id == run_id
            and event.kind in ("protocol_violation", "crashed")
            and isinstance(event.payload.get("worker_output"), str)
        ),
        None,
    )
    return event.payload["worker_output"] if event is not None else None


def ended_on_refusals(episode: Episode, run_id: int) -> list[Observation]:
    """A run that ends on refusals, or on kanban calls in error, then exits without handing the card back."""
    last = episode.observations_of_run(run_id)[-10:]
    return [
        item
        for item in last
        if is_refusal(item.outcome)
        or (item.result.tool.startswith("kanban_") and item.outcome.kind in ("error", "pr_acceptance"))
    ]


def stopped_after_refusals(episode: Episode) -> list[RawFinding]:
    runs = [run for run in episode.protocol_violations() if ended_on_refusals(episode, run.id)]
    if not runs:
        return []
    refusals = [item for run in runs for item in ended_on_refusals(episode, run.id)]
    last = refusals[-1]
    return [
        RawFinding(
            code="stopped_after_refusals",
            confidence="certain",
            actor="agent",
            cause=NONE,
            summary=message("diagnosis.summary.stoppedAfterRefusals"),
            why=message("diagnosis.why.stoppedAfterRefusals"),
            subject=subject(episode.profile, None, last.result.tool),
            setting=None,
            evidence=[
                evidence(
                    "transcript",
                    refusals[0].result.at,
                    message("diagnosis.evidence.toolRefusals", count=len(refusals), tools=tools_summary(refusals)),
                    until=last.result.at,
                    quote=quote(last.result.content),
                    count=len(refusals),
                )
            ],
            steps=[],
            run_ids=[run.id for run in runs],
        )
    ]


def stopped_without_kanban_call(episode: Episode) -> list[RawFinding]:
    """The model ends its turn without a kanban call, without any tool refused to it."""
    runs = [run for run in episode.protocol_violations() if not ended_on_refusals(episode, run.id)]
    if not runs:
        return []
    run_ids = {run.id for run in runs}
    nudges = [
        fact
        for fact in episode.logs
        if fact.kind == "stop_nudge" and any(run_id in run_ids for run_id in runs_at(episode, [fact.at]))
    ]
    last_run = runs[-1]
    output = worker_output(episode, last_run.id)
    items: list[RawEvidence] = [
        evidence(
            "kanban",
            last_run.ended_at,
            message("diagnosis.evidence.workerExited"),
            run_id=last_run.id,
            quote=quote(episode.terms.nudge_marker if episode.texts.was_nudged(output) else last_run.error),
            count=len(runs),
        )
    ]
    if nudges:
        items.append(
            evidence(
                "agent_log",
                nudges[0].at,
                message("diagnosis.evidence.stopNudges", count=len(nudges)),
                until=nudges[-1].at,
                quote=nudges[-1].line,
                count=len(nudges),
            )
        )
    # Once is a miss; twice or more, the model must be changed.
    repeated = len(runs) >= 2
    return [
        RawFinding(
            code="stopped_without_kanban_call",
            confidence="certain",
            actor="you_factory" if repeated else "wait",
            cause=NONE,
            summary=message("diagnosis.summary.stoppedWithoutKanbanCall", count=len(runs)),
            why=message("diagnosis.why.stoppedWithoutKanbanCall", nudges=len(nudges) if nudges else None),
            subject=subject(profile_name(last_run)),
            setting=None,
            evidence=items,
            steps=[step(message("diagnosis.step.chooseModel"))] if repeated else [],
            run_ids=[run.id for run in runs],
        )
    ]


def protocol_breaker(episode: Episode) -> list[RawFinding]:
    """The runtime's breaker: after three exits without a kanban call, the card blocks without a type."""
    breakers = [
        event
        for event in episode.events_of("gave_up")
        if js_number(event.payload.get("protocol_violations")) > 0
        or episode.texts.is_breaker_error(_text(event.payload.get("error")))
    ]
    if not breakers:
        return []
    gave_up = breakers[-1]
    violations = episode.protocol_violations()
    count = max(js_number(gave_up.payload.get("protocol_violations")), len(violations))
    return [
        RawFinding(
            code="protocol_breaker",
            confidence="certain",
            actor="you_factory",
            cause=NONE,
            summary=message("diagnosis.summary.protocolBreaker", count=count),
            why=message("diagnosis.why.protocolBreaker", count=count),
            subject=subject(episode.profile),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    gave_up.created_at,
                    message("diagnosis.evidence.breaker", count=count),
                    quote=quote(_text(gave_up.payload.get("error"))),
                )
            ],
            steps=[],
            run_ids=[run.id for run in violations],
        )
    ]


def iteration_budget(episode: Episode) -> list[RawFinding]:
    sources = [
        (run.ended_at if run.ended_at is not None else episode.until, run.id, run.error) for run in episode.runs
    ] + [
        (event.created_at, event.run_id, _text(event.payload.get("error")))
        for event in episode.events
        if event.kind in ("timed_out", "gave_up")
    ]
    hits = [(at, run_id, text, budget) for at, run_id, text in sources if (budget := episode.texts.budget_of(text))]
    if not hits:
        return []
    at, run_id, text, (used, maximum) = hits[-1]
    profile = episode.input.profile
    run = episode.run_of(run_id)
    name = profile_name(run) if run is not None else (episode.profile or "?")
    facts = profile if profile is not None and profile.profile == name else None
    current = facts.max_turns if facts is not None else None
    # Fixed when the turns' ceiling passes the one that cut, set after the stop.
    if facts is None:
        cause = UNKNOWN
    elif current is None or current <= maximum:
        cause = PRESENT
    elif facts.changed_at is not None and facts.changed_at > at:
        cause = RawCauseFixed(at=facts.changed_at, verified=False, durable=None)
    else:
        cause = UNKNOWN
    key = episode.terms.max_turns_key
    return [
        RawFinding(
            code="iteration_budget",
            confidence="certain",
            actor="you_factory",
            cause=cause,
            summary=message("diagnosis.summary.iterationBudget", max=maximum),
            why=message("diagnosis.why.iterationBudget", used=used, max=maximum),
            subject=subject(name),
            setting=RawSetting(
                profile=name,
                key=key,
                unit="turns",
                observed=maximum,
                current=current,
                default_value=None,
                effective=current,
                repo=None,
            ),
            evidence=[
                evidence(
                    "kanban",
                    at,
                    message("diagnosis.evidence.budget", used=used, max=maximum),
                    run_id=run_id,
                    quote=quote(text),
                    count=len(hits),
                )
            ],
            steps=[step(message("diagnosis.step.raiseLimit", key=key, profile=name))],
            run_ids=unique(hit[1] for hit in hits if hit[1] is not None),
        )
    ]


def runtime_limit(episode: Episode) -> list[RawFinding]:
    """A run stopped because it went past the card's maximum duration: read in a run's error, or in a `timed_out`
    event of the episode."""
    hits = [
        (run.ended_at if run.ended_at is not None else episode.until, run.id, limits)
        for run in episode.runs
        if (limits := episode.texts.runtime_of(run.error)) is not None
    ]
    for event in episode.events_of("timed_out"):
        limit = event.payload.get("limit_seconds")
        if isinstance(limit, int | float) and not isinstance(limit, bool):
            hits.append(
                (event.created_at, event.run_id, (js_number(event.payload.get("elapsed_seconds")), _whole(limit)))
            )
    if not hits:
        return []
    at, run_id, (elapsed, limit) = hits[-1]
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
            run_ids=unique(hit[1] for hit in hits if hit[1] is not None),
        )
    ]


def tool_deadline(episode: Episode) -> list[RawFinding]:
    """A tool call cut by the runtime's ceiling, hooks included."""
    terms = episode.terms
    hits = [item for item in episode.observations if item.outcome.kind == "tool_deadline"]
    if not hits:
        return []
    first_hit = hits[0]
    tool, seconds = first_hit.outcome.tool, first_hit.outcome.seconds
    profile = episode.input.profile
    name = episode.profile or "?"
    current = profile.tool_deadline if profile is not None else None
    last_at = hits[-1].result.at
    if profile is None:
        cause = UNKNOWN
    elif (current if current is not None else terms.tool_deadline_default) <= (
        seconds if seconds is not None else math.nan
    ):
        cause = PRESENT
    elif profile.changed_at is not None and profile.changed_at > last_at:
        cause = RawCauseFixed(at=profile.changed_at, verified=False, durable=None)
    else:
        cause = UNKNOWN
    key = terms.deadline_key
    suggested = terms.tool_deadline_suggested
    items = [
        evidence(
            "transcript",
            first_hit.result.at,
            message("diagnosis.evidence.toolRefusals", count=len(hits), tools=tools_summary(hits)),
            until=last_at,
            quote=quote(first_hit.result.content),
            count=len(hits),
        )
    ]
    if first_hit.call is not None:
        items.append(
            evidence(
                "transcript",
                first_hit.call.at,
                message("diagnosis.evidence.trigger", tool=first_hit.call.tool),
                run_id=first_hit.run_id,
                quote=quote(first_hit.call.input),
            )
        )
    folder = episode.workflow.profiles or terms.repo_profiles_default
    return [
        RawFinding(
            code="tool_deadline",
            confidence="certain",
            actor="you_factory",
            cause=cause,
            summary=message("diagnosis.summary.toolDeadline", tool=tool, seconds=seconds),
            why=message("diagnosis.why.toolDeadline", tool=tool, seconds=seconds),
            subject=subject(name, None, tool),
            setting=RawSetting(
                profile=name,
                key=key,
                unit="seconds",
                observed=seconds,
                current=current,
                default_value=terms.tool_deadline_default,
                effective=current if current is not None else terms.tool_deadline_default,
                repo=None,
            ),
            evidence=items,
            steps=[
                step(
                    message("diagnosis.step.setValue", key=key, value=suggested, profile=name),
                    episode.texts.set_command(name, key, suggested),
                    isinstance(cause, RawCauseFixed),
                ),
                step(message("diagnosis.step.reportInRepo", path=f"{folder}/{name}/config.yaml")),
            ],
            run_ids=run_ids_of(hits),
        )
    ]


def command_timeout(episode: Episode) -> list[RawFinding]:
    """A terminal command stopped by its delay: the agent must cut it or run it in the background."""
    blocking = episode.blocking_run_id
    hits = [
        item
        for item in episode.observations
        if item.outcome.kind == "command_timeout" and (blocking is None or item.run_id == blocking)
    ]
    if not hits:
        return []
    last = hits[-1]
    profile = episode.input.profile
    if last.outcome.seconds is not None:
        seconds = last.outcome.seconds
    elif profile is not None:
        seconds = (
            profile.terminal_timeout if profile.terminal_timeout is not None else episode.terms.terminal_timeout_default
        )
    else:
        seconds = 0
    terminal = episode.terms.terminal_tool
    call_input = last.call.input if last.call is not None else None
    return [
        RawFinding(
            code="command_timeout",
            confidence="certain",
            actor="agent",
            cause=NONE,
            summary=message("diagnosis.summary.commandTimeout", seconds=seconds),
            why=message("diagnosis.why.commandTimeout", seconds=seconds),
            subject=subject(episode.profile, None, terminal),
            setting=None,
            evidence=[
                evidence(
                    "transcript",
                    last.result.at,
                    message("diagnosis.evidence.trigger", tool=terminal),
                    run_id=last.run_id,
                    quote=quote(call_input if call_input is not None else last.result.content),
                    count=len(hits),
                )
            ],
            steps=[],
            run_ids=run_ids_of(hits),
        )
    ]


def spawn_failed(episode: Episode) -> list[RawFinding]:
    """The worker does not start."""
    failures = [
        event
        for event in episode.events
        if event.kind == "spawn_failed"
        or (event.kind == "gave_up" and event.payload.get("trigger_outcome") == "spawn_failed")
    ]
    if not failures:
        return []
    last = failures[-1]
    error = _text(last.payload.get("error"))
    return [
        RawFinding(
            code="spawn_failed",
            confidence="certain",
            actor="you_factory",
            cause=PRESENT,
            summary=message("diagnosis.summary.spawnFailed"),
            why=message("diagnosis.why.spawnFailed"),
            subject=subject(episode.profile),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    last.created_at,
                    message("diagnosis.evidence.spawnError"),
                    run_id=last.run_id,
                    quote=quote(error),
                    count=len(failures),
                )
            ],
            steps=[step(message("diagnosis.step.fixWorkspace"))] if episode.texts.is_workspace_error(error) else [],
            run_ids=unique(event.run_id for event in failures if event.run_id is not None),
        )
    ]
