"""The rules of the provider and of the work (ADR 0012): the provider's quota, a provider rejecting the key or the
model, a silent provider or a failing fallback chain, the pull request acceptance check, a ready card held by an
active pull request, a triage loop.

Ported from factory-live at commit d47f235 (packages/domain/src/diagnosis/rules.ts: `providerQuota`,
`providerTerminal`, `providerUnavailable`, `prAcceptance`, `heldActivePr`, `triageLoop`). The runtime's texts and
commands come from the adapter; the workflow's agents and acceptance text from its entry in config/workflows.toml. A
value the workflow does not declare leaves the rule without it.
"""

from __future__ import annotations

from workflow_core.diagnosis.episode import Episode, profile_name
from workflow_core.diagnosis.findings import (
    NONE,
    PRESENT,
    UNKNOWN,
    RawFinding,
    evidence,
    js_round,
    step,
    subject,
    unique,
)
from workflow_core.diagnosis.rules_runs import js_number, text_of, worker_output
from workflow_core.model.diagnosis import message
from workflow_core.text import quote


def provider_quota(episode: Episode) -> list[RawFinding]:
    """The provider reached its quota: nothing to do but wait."""
    limited = [run for run in episode.runs if run.outcome == "rate_limited"]
    cooldowns = [
        event for event in episode.events_of("respawn_guarded") if event.payload.get("reason") == "rate_limit_cooldown"
    ]
    if not limited and not cooldowns:
        return []
    failures = [fact for fact in episode.logs if fact.kind == "api_failed"]
    provider = failures[-1].plugin if failures else None
    last = limited[-1] if limited else None
    if last is not None and last.ended_at is not None:
        at = last.ended_at
    else:
        at = cooldowns[-1].created_at if cooldowns else None
    terms = episode.terms
    return [
        RawFinding(
            code="provider_quota",
            confidence="certain",
            actor="wait",
            cause=NONE,
            summary=message("diagnosis.summary.providerQuota", provider=provider),
            why=message("diagnosis.why.providerQuota", provider=provider),
            subject=subject(episode.profile),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    at,
                    message("diagnosis.evidence.apiErrors", count=max(len(limited), 1), provider=provider),
                    run_id=last.id if last is not None else None,
                    quote=quote(last.error if last is not None and last.error is not None else terms.cooldown_quote),
                    count=len(limited) + len(cooldowns),
                )
            ],
            steps=[step(message("diagnosis.step.wait", minutes=terms.rate_limit_cooldown_minutes))],
            run_ids=[run.id for run in limited],
        )
    ]


def provider_terminal(episode: Episode) -> list[RawFinding]:
    """The provider rejects the key or the model."""
    crashes = [
        event
        for event in episode.events
        if event.kind in ("crashed", "gave_up")
        and (event.payload.get("terminal_provider") is True or event.payload.get("exit_kind") == "terminal_provider")
    ]
    if not crashes:
        return []
    last = crashes[-1]
    return [
        RawFinding(
            code="provider_terminal",
            confidence="certain",
            actor="you_factory",
            cause=PRESENT,
            summary=message("diagnosis.summary.providerTerminal", provider=None),
            why=message("diagnosis.why.providerTerminal", provider=None),
            subject=subject(episode.profile),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    last.created_at,
                    message("diagnosis.evidence.workerOutput"),
                    run_id=last.run_id,
                    quote=quote(text_of(last.payload.get("worker_output")) or text_of(last.payload.get("error"))),
                )
            ],
            steps=[step(message("diagnosis.step.checkProvider", provider=None))],
            run_ids=unique(event.run_id for event in crashes if event.run_id is not None),
        )
    ]


def provider_unavailable(episode: Episode) -> list[RawFinding]:
    """The provider no longer answers (the progress safeguard), or the whole fallback chain fails."""
    hits = []
    for run in episode.runs:
        if run.outcome != "crashed":
            continue
        output = worker_output(episode, run.id)
        failure = episode.texts.provider_failure_of(output if output is not None else run.error)
        if failure is not None:
            hits.append((run, failure))
    if not hits:
        return []
    run, (mode, seconds) = hits[-1]
    output = worker_output(episode, run.id)
    return [
        RawFinding(
            code="provider_unavailable",
            confidence="certain",
            actor="you_factory",
            cause=UNKNOWN,
            summary=message("diagnosis.summary.providerUnavailable", mode=mode, seconds=seconds),
            why=message("diagnosis.why.providerUnavailable", mode=mode, seconds=seconds),
            subject=subject(profile_name(run)),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    run.ended_at,
                    message("diagnosis.evidence.workerOutput"),
                    run_id=run.id,
                    quote=quote(output if output is not None else run.error),
                    count=len(hits),
                )
            ],
            steps=[
                step(message("diagnosis.step.checkProvider", provider=None)),
                step(message("diagnosis.step.chooseModel")),
            ],
            run_ids=[hit[0].id for hit in hits],
        )
    ]


def pr_acceptance(episode: Episode) -> list[RawFinding]:
    """The runtime's pull request check refuses to hand the card back: GitHub unreachable, or an incomplete contract.
    Recognised by the workflow's acceptance text only: without one, no finding."""
    if not episode.workflow.pr_acceptance:
        return []
    results = [item for item in episode.observations if item.outcome.kind == "pr_acceptance"]
    events = episode.events_of("pr_acceptance")
    passed = [event for event in events if event.payload.get("ok") is True]
    last_ok = passed[-1] if passed else None
    findings = []
    for classification in ("infra", "missing"):
        from_events = [
            event
            for event in events
            if event.payload.get("classification") == classification and (last_ok is None or event.id > last_ok.id)
        ]
        from_results = [item for item in results if item.outcome.classification == classification]
        count = max(len(from_events), len(from_results))
        if count == 0:
            continue
        sample = from_results[-1] if from_results else None
        if sample is not None:
            detail = sample.outcome.detail or ""
        else:
            detail = text_of(from_events[-1].payload.get("detail")) if from_events else ""
        checks = episode.texts.lacks_required_checks(detail)
        infra = classification == "infra"
        if sample is not None:
            at, run_id = sample.result.at, sample.run_id
        elif from_events:
            at, run_id = from_events[-1].created_at, from_events[-1].run_id
        else:
            at, run_id = None, None
        findings.append(
            RawFinding(
                code="pr_acceptance_infra" if infra else "pr_acceptance_missing",
                confidence="certain",
                actor="you_factory" if infra or checks else "agent",
                cause=PRESENT if infra or checks else NONE,
                summary=message(
                    "diagnosis.summary.prAcceptanceInfra" if infra else "diagnosis.summary.prAcceptanceMissing"
                ),
                why=message("diagnosis.why.prAcceptanceInfra")
                if infra
                else message("diagnosis.why.prAcceptanceMissing", checks=checks),
                subject=subject(episode.profile, None, episode.terms.complete_tool),
                setting=None,
                evidence=[
                    evidence(
                        "transcript" if sample is not None else "kanban",
                        at,
                        message("diagnosis.evidence.prAcceptance", classification=classification, count=count),
                        run_id=run_id,
                        quote=quote(sample.result.content if sample is not None else detail),
                        count=count,
                    )
                ],
                steps=[step(message("diagnosis.step.updateGh"))]
                if infra
                else [step(message("diagnosis.step.configureChecks"))]
                if checks
                else [],
                run_ids=unique(
                    [item.run_id for item in from_results if item.run_id is not None]
                    + [event.run_id for event in from_events if event.run_id is not None]
                ),
            )
        )
    return findings


def held_active_pr(episode: Episode) -> list[RawFinding]:
    """A ready card the runtime's `active_pr` guard holds: it needs a relay."""
    input = episode.input
    card = input.card
    if card.status != "ready":
        return []
    held = [
        event
        for event in input.events
        if event.kind == "respawn_guarded" and event.payload.get("reason") == "active_pr"
    ]
    if not held:
        return []
    first = held[0]
    owner = input.owner or card.assignee or episode.workflow.default_agent
    relay = episode.workflow.relay_agent
    minutes = max(1, js_round((input.now - first.created_at) / 60))
    command = episode.texts.relay_command(input.board, card.id, relay, owner) if relay and owner else None
    return [
        RawFinding(
            code="held_active_pr",
            confidence="certain",
            actor="you_factory",
            cause=PRESENT,
            summary=message("diagnosis.summary.heldActivePr", minutes=minutes),
            why=message("diagnosis.why.heldActivePr"),
            subject=subject(card.assignee),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    first.created_at,
                    message("diagnosis.evidence.lastSignals", count=len(held)),
                    until=held[-1].created_at,
                    quote=episode.terms.held_quote,
                    count=len(held),
                )
            ],
            steps=[step(message("diagnosis.step.relay", owner=owner or "?"), command)],
            run_ids=[],
        )
    ]


def triage_loop(episode: Episode) -> list[RawFinding]:
    """Two blocks of the same type in a row: the card is in triage, unblocking no longer applies."""
    card = episode.input.card
    loops = [event for event in episode.input.events if event.kind == "block_loop_detected"]
    if card.status != "triage" or not loops:
        return []
    loop = loops[-1]
    recurrences = loop.payload.get("recurrences")
    return [
        RawFinding(
            code="triage_loop",
            confidence="certain",
            actor="you_factory",
            cause=PRESENT,
            summary=message("diagnosis.summary.triageLoop"),
            why=message("diagnosis.why.triageLoop"),
            subject=subject(card.assignee),
            setting=None,
            evidence=[
                evidence(
                    "kanban",
                    loop.created_at,
                    message(
                        "diagnosis.evidence.lastSignals", count=js_number(recurrences if recurrences is not None else 2)
                    ),
                    run_id=loop.run_id,
                    quote=quote(text_of(loop.payload.get("reason"))),
                )
            ],
            steps=[step(message("diagnosis.step.leaveTriage"))],
            run_ids=[loop.run_id] if loop.run_id is not None else [],
        )
    ]
