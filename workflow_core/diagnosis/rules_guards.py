"""The rules of the guards and the hooks (ADR 0012): a guard's hook past its delay, a guard whose abandoned calls
still run, a crashing guard, a plugin that does not load, a guard's refusals, in a loop or once.

Ported from factory-live at commit d47f235 (packages/domain/src/diagnosis/rules.ts: `guardHookTimeout`,
`guardHookHung`, `guardHookRaised`, `pluginLoadFailed`, `guardRefusals`). The runtime's words (settings, defaults,
the command that sets a value) come from the adapter's texts; the workflow's (its guards, its profiles' folder) from
its entry in config/workflows.toml.
"""

from __future__ import annotations

import math

from workflow_core.diagnosis.episode import Episode, Observation
from workflow_core.diagnosis.findings import (
    NONE,
    PRESENT,
    UNKNOWN,
    RawCauseFixed,
    RawEvidence,
    RawFinding,
    RawSetting,
    evidence,
    js_round,
    step,
    subject,
    unique,
)
from workflow_core.diagnosis.guards import effective_hook_limit
from workflow_core.diagnosis.support import (
    GIT_COMMIT,
    hook_of,
    is_refusal,
    normalized_refusal,
    run_ids_of,
    runs_at,
    tools_summary,
)
from workflow_core.model.diagnosis import message
from workflow_core.text import number_text, quote

PRE_TOOL_CALL = "pre_tool_call"


def guard_hook_timeout(episode: Episode) -> list[RawFinding]:
    """A `pre_tool_call` hook passes its delay: the runtime refuses the tool, then every tool for 60 s. Fact: the
    runtime's exact tool result; the journal gives the callback and the delay."""
    terms = episode.terms
    hits = [item for item in episode.observations if item.outcome.kind == "hook_timeout"]
    if not hits:
        return []
    logs = [
        fact
        for fact in episode.logs
        if fact.kind == "hook_timeout" and (fact.hook if fact.hook is not None else PRE_TOOL_CALL) == PRE_TOOL_CALL
    ]
    first_log = logs[0] if logs else None
    callback = first_log.callback if first_log is not None else None
    hook = hook_of(episode, PRE_TOOL_CALL, callback)
    plugin = hook.plugin if hook is not None else None
    first_hit = hits[0]
    gap = first_hit.result.at - first_hit.call.at if first_hit.call is not None else None
    if first_log is not None and first_log.seconds is not None:
        observed: float = first_log.seconds
    elif gap is not None and gap >= 5:
        observed = js_round(gap)
    else:
        observed = terms.hook_timeout_default
    call_input = first_hit.call.input if first_hit.call is not None else None
    if (
        first_hit.call is not None
        and first_hit.call.tool == terms.terminal_tool
        and GIT_COMMIT.search(call_input or "")
    ):
        trigger: str | None = "git commit"
    else:
        trigger = first_hit.call.tool if first_hit.call is not None else None
    profile = episode.input.profile
    name = episode.profile or "?"
    current = profile.hook_timeout if profile is not None else None
    deadline = (
        profile.tool_deadline
        if profile is not None and profile.tool_deadline is not None
        else terms.tool_deadline_default
    )
    # Without an effective delay (hook and tools' ceiling turned off), nothing cuts the hook any more.
    if profile is not None:
        limit = effective_hook_limit(profile, terms)
        effective = limit if limit is not None else math.inf
    else:
        effective = terms.hook_timeout_default
    last_at = max([item.result.at for item in hits] + [fact.at for fact in logs])
    # factory-live leaves `repoHookTimeout` out when the repository cannot be read: `repo_known` says it is there.
    repo_known = profile is not None and profile.repo_read
    repo = profile.repo_hook_timeout if repo_known else None

    # Fixed when the effective delay passes the one that cut, and the configuration moved after.
    verified_in: int | None = None
    if profile is None:
        cause = UNKNOWN
    elif effective <= observed:
        cause = PRESENT
    elif profile.changed_at is not None and profile.changed_at > last_at:
        changed_at = profile.changed_at

        def passed_again(item: Observation) -> bool:
            # Verified by the same call, run again after the fix: passed, or checked longer than the old delay
            # without being cut. An immediate refusal, before the check, proves nothing.
            return (
                item.result.at > changed_at
                and item.call is not None
                and item.call.tool == (first_hit.call.tool if first_hit.call is not None else None)
                and (trigger != "git commit" or bool(GIT_COMMIT.search(item.call.input or "")))
                and item.outcome.kind != "hook_timeout"
                and (item.outcome.kind == "ok" or item.result.at - item.call.at > observed)
            )

        passed = next((item for item in episode.later_observations if passed_again(item)), None)
        if passed is not None and passed.call is not None:
            verified_in = js_round(passed.result.at - passed.call.at)
        cause = RawCauseFixed(
            at=changed_at,
            verified=passed is not None,
            durable=None if not repo_known else repo is not None and repo >= (current if current is not None else 0),
        )
    else:
        cause = UNKNOWN

    key = terms.hook_key
    items: list[RawEvidence] = [
        evidence(
            "transcript",
            first_hit.result.at,
            message("diagnosis.evidence.toolRefusals", count=len(hits), tools=tools_summary(hits)),
            until=hits[-1].result.at,
            quote=quote(first_hit.result.content),
            count=len(hits),
        )
    ]
    if first_log is not None:
        items.append(
            evidence(
                "agent_log",
                first_log.at,
                message(
                    "diagnosis.evidence.hookTimedOut",
                    callback=first_log.callback or "?",
                    seconds=first_log.seconds if first_log.seconds is not None else observed,
                ),
                until=logs[-1].at,
                quote=first_log.line,
                count=len(logs),
            )
        )
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
    if hook is not None:
        items.append(
            evidence(
                "plugin",
                None,
                message("diagnosis.evidence.pluginHook", plugin=hook.plugin, callback=hook.callback, hook=hook.hook),
                quote=quote(hook.source),
            )
        )
    if profile is not None:
        if isinstance(cause, RawCauseFixed) and current is not None:
            items.append(
                evidence(
                    "config",
                    cause.at,
                    message("diagnosis.evidence.settingChanged", key=key, value=current),
                    quote=f"{key.split('.')[-1]}: {number_text(current)}",
                )
            )
        elif current is None:
            items.append(
                evidence(
                    "config",
                    None,
                    message("diagnosis.evidence.settingMissing", key=key, seconds=terms.hook_timeout_default),
                )
            )
        else:
            items.append(evidence("config", None, message("diagnosis.evidence.settingValue", key=key, value=current)))
        items.append(evidence("config", None, message("diagnosis.evidence.toolDeadlineCap", seconds=deadline)))
    if verified_in is not None:
        items.append(evidence("transcript", None, message("diagnosis.evidence.commitPassed", seconds=verified_in)))

    fixed = isinstance(cause, RawCauseFixed)
    profiles_folder = episode.workflow.profiles or terms.repo_profiles_default
    steps = [
        step(
            message("diagnosis.step.setValue", key=key, value=terms.hook_timeout_max, profile=name),
            episode.texts.set_command(name, key, terms.hook_timeout_max),
            fixed,
        ),
        step(
            message("diagnosis.step.reportInRepo", path=f"{profiles_folder}/{name}/config.yaml"),
            None,
            fixed and cause.durable is True,
        ),
        step(message("diagnosis.step.unblockWithTemplate")),
    ]
    tool = first_hit.call.tool if first_hit.call is not None else first_hit.result.tool
    return [
        RawFinding(
            code="guard_hook_timeout",
            confidence="certain" if first_log is not None or hook is not None else "likely",
            actor="you_factory",
            cause=cause,
            summary=message("diagnosis.summary.guardHookTimeout", plugin=plugin, seconds=observed),
            why=message("diagnosis.why.guardHookTimeout", plugin=plugin, trigger=trigger, seconds=observed),
            subject=subject(name, plugin, tool),
            setting=RawSetting(
                profile=name,
                key=key,
                unit="seconds",
                observed=observed,
                current=current,
                default_value=terms.hook_timeout_default,
                effective=effective if profile is not None else None,
                repo=repo,
            ),
            evidence=items[:8],
            steps=steps,
            run_ids=run_ids_of(hits),
        )
    ]


def guard_hook_hung(episode: Episode) -> list[RawFinding]:
    """A hook whose abandoned calls still run: the runtime skips it, the tool is refused."""
    hung = [fact for fact in episode.logs if fact.kind == "hook_abandoned"]
    if not hung:
        return []
    last = hung[-1]
    plugin = episode.texts.plugin_of_module(last.module)
    if plugin is None:
        hook = hook_of(episode, last.hook if last.hook is not None else PRE_TOOL_CALL, last.callback)
        plugin = hook.plugin if hook is not None else None
    return [
        RawFinding(
            code="guard_hook_hung",
            confidence="certain",
            actor="you_factory",
            cause=UNKNOWN,
            summary=message("diagnosis.summary.guardHookHung", plugin=plugin),
            why=message("diagnosis.why.guardHookHung", plugin=plugin, workers=last.count),
            subject=subject(episode.profile, plugin),
            setting=None,
            evidence=[
                evidence(
                    "agent_log",
                    last.at,
                    message("diagnosis.evidence.lastSignals", count=len(hung)),
                    quote=last.line,
                    count=len(hung),
                )
            ],
            steps=[
                *([step(message("diagnosis.step.fixPlugin", plugin=plugin))] if plugin else []),
                step(message("diagnosis.step.reinstall")),
            ],
            run_ids=runs_at(episode, [fact.at for fact in hung]),
        )
    ]


def guard_hook_raised(episode: Episode) -> list[RawFinding]:
    """A guard that raises an exception at each call: the runtime refuses the tool by caution."""
    hits = [item for item in episode.observations if item.outcome.kind == "hook_raised"]
    if not hits:
        return []
    first_hit = hits[0]
    hook = hook_of(episode, PRE_TOOL_CALL, first_hit.outcome.callback)
    plugin = hook.plugin if hook is not None else None
    return [
        RawFinding(
            code="guard_hook_raised",
            confidence="certain" if plugin else "likely",
            actor="you_factory",
            cause=UNKNOWN,
            summary=message("diagnosis.summary.guardHookRaised", plugin=plugin),
            why=message("diagnosis.why.guardHookRaised", plugin=plugin),
            subject=subject(episode.profile, plugin, first_hit.result.tool),
            setting=None,
            evidence=[
                evidence(
                    "transcript",
                    first_hit.result.at,
                    message("diagnosis.evidence.toolRefusals", count=len(hits), tools=tools_summary(hits)),
                    until=hits[-1].result.at,
                    quote=quote(first_hit.result.content),
                    count=len(hits),
                )
            ],
            steps=[
                *([step(message("diagnosis.step.fixPlugin", plugin=plugin))] if plugin else []),
                step(message("diagnosis.step.reinstall")),
            ],
            run_ids=run_ids_of(hits),
        )
    ]


def plugin_load_failed(episode: Episode) -> list[RawFinding]:
    """A plugin that does not load at the agent's start: its checks no longer run."""
    failures = [fact for fact in episode.logs if fact.kind == "plugin_load_failed" and fact.plugin]
    found = []
    for plugin in unique(fact.plugin for fact in failures):
        facts = [fact for fact in failures if fact.plugin == plugin]
        last = facts[-1]
        found.append(
            RawFinding(
                code="plugin_load_failed",
                confidence="certain",
                actor="you_factory",
                cause=PRESENT,
                summary=message("diagnosis.summary.pluginLoadFailed", plugin=plugin),
                why=message("diagnosis.why.pluginLoadFailed", plugin=plugin),
                subject=subject(episode.profile, plugin),
                setting=None,
                evidence=[
                    evidence(
                        "agent_log",
                        last.at,
                        message("diagnosis.evidence.lastSignals", count=len(facts)),
                        quote=last.line,
                        count=len(facts),
                    )
                ],
                steps=[step(message("diagnosis.step.reinstall"))],
                run_ids=runs_at(episode, [fact.at for fact in facts]),
            )
        )
    return found


def _refusal_key(item: Observation) -> str:
    outcome = item.outcome
    return f"{outcome.plugin}\0{normalized_refusal(outcome.message or '')}" if outcome.kind == "guard" else ""


def guard_refusals(episode: Episode) -> list[RawFinding]:
    """A guard's refusals: the same one at least three times in a session is a loop, for the owner to answer; a
    refusal that ends the blocking run without a loop is for the agent to correct."""
    refusals = [item for item in episode.observations if item.outcome.kind == "guard"]
    if not refusals:
        return []
    per_session: dict[str, list[Observation]] = {}
    for item in refusals:
        per_session.setdefault(f"{item.session_id}\0{_refusal_key(item)}", []).append(item)
    loops = [items for items in per_session.values() if len(items) >= 3]
    if loops:
        # The latest loop; the count covers the whole episode, every session.
        latest = sorted(loops, key=lambda items: -items[-1].result.at)[0]
        sample = latest[-1]
        every = [item for item in refusals if _refusal_key(item) == _refusal_key(sample)]
        plugin = sample.outcome.plugin
        return [
            RawFinding(
                code="refusal_loop",
                confidence="certain",
                actor="you_answer",
                cause=NONE,
                summary=message("diagnosis.summary.refusalLoop", plugin=plugin, count=len(every)),
                why=message("diagnosis.why.refusalLoop", plugin=plugin, count=len(every)),
                subject=subject(episode.profile, plugin, sample.result.tool),
                setting=None,
                evidence=[
                    evidence(
                        "transcript",
                        every[0].result.at if every else None,
                        message("diagnosis.evidence.guardMessage", plugin=plugin, count=len(every)),
                        until=sample.result.at,
                        quote=quote(sample.result.content),
                        count=len(every),
                    )
                ],
                steps=[
                    step(message("diagnosis.step.giveCommand")),
                    step(message("diagnosis.step.unblockWithTemplate")),
                ],
                run_ids=run_ids_of(every),
            )
        ]
    blocking = episode.blocking_run_id
    if blocking is None:
        return []
    refused = [item for item in episode.observations_of_run(blocking) if is_refusal(item.outcome)]
    if not refused or refused[-1].outcome.kind != "guard":
        return []
    last = refused[-1]
    plugin = last.outcome.plugin
    same = [item for item in refusals if _refusal_key(item) == _refusal_key(last)]
    return [
        RawFinding(
            code="guard_refusal",
            confidence="certain",
            actor="agent",
            cause=NONE,
            summary=message("diagnosis.summary.guardRefusal", plugin=plugin),
            why=message("diagnosis.why.guardRefusal", plugin=plugin),
            subject=subject(episode.profile, plugin, last.result.tool),
            setting=None,
            evidence=[
                evidence(
                    "transcript",
                    last.result.at,
                    message("diagnosis.evidence.guardMessage", plugin=plugin, count=len(same)),
                    run_id=last.run_id,
                    quote=quote(last.result.content),
                    count=len(same),
                )
            ],
            steps=[],
            run_ids=[blocking],
        )
    ]
