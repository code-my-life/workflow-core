"""The guards and their delays: what the runtime really leaves to a plugin's hook, and the timeouts its journal notes.

Ported from factory-live at commit d47f235 (packages/domain/src/diagnosis/guards.ts). The runtime's defaults come
from the adapter's terms.
"""

from __future__ import annotations

from workflow_core.model.agents import GuardRisk
from workflow_core.model.facts import LogFact, ProfileFacts, RuntimeTerms


def effective_hook_limit(profile: ProfileFacts, terms: RuntimeTerms) -> float | None:
    """The delay the runtime really leaves to a hook: the hook's setting (its default when absent or negative, its
    maximum at most, none at 0), never more than a tool call's ceiling, which holds it (its default when absent, none
    at 0 or less). `None`: no delay cuts the hook."""
    own = profile.hook_timeout
    if own is None or own < 0:
        hook = terms.hook_timeout_default
    elif own == 0:
        hook = None
    else:
        hook = min(own, terms.hook_timeout_max)
    deadline = profile.tool_deadline
    tool = terms.tool_deadline_default if deadline is None else deadline if deadline > 0 else None
    if hook is None:
        return tool
    return hook if tool is None else min(hook, tool)


def guard_risks(profiles: list[ProfileFacts], terms: RuntimeTerms) -> list[GuardRisk]:
    """The guards at risk (ported from factory-live's `guardRisks`, release/0.12.0): an active plugin plugged to a hook
    whose code runs a command of at least the hook's effective delay. Grouped by guard and delays, one line each, the
    agents in the order given; sorted by guard, then by longest command (longest first), then by delay."""
    risks: dict[tuple[str, float, float], GuardRisk] = {}
    for profile in profiles:
        limit = effective_hook_limit(profile, terms)
        if limit is None:
            continue
        for plugin in profile.plugins:
            longest = profile.longest_commands.get(plugin)
            if longest is None or longest < limit:
                continue
            if not any(hook.plugin == plugin for hook in profile.hooks):
                continue
            key = (plugin, float(longest), float(limit))
            known = risks.get(key)
            if known is not None:
                known.agents.append(profile.profile)
            else:
                risks[key] = GuardRisk(
                    plugin=plugin, agents=[profile.profile], longest_seconds=longest, limit_seconds=limit
                )
    return sorted(risks.values(), key=lambda risk: (risk.plugin, -risk.longest_seconds, risk.limit_seconds))


def hook_timeouts_by_plugin(logs: list[LogFact], profile: ProfileFacts) -> dict[str, int]:
    """The hook timeouts noted in an agent's journal, guard by guard. The runtime names the callback, not the plugin:
    a callback two active plugins share is counted for none."""
    counts: dict[str, int] = {}
    for fact in logs:
        if fact.kind != "hook_timeout":
            continue
        owners = {
            hook.plugin
            for hook in profile.hooks
            if hook.callback == fact.callback
            and (fact.hook is None or hook.hook == fact.hook)
            and hook.plugin in profile.plugins
        }
        if len(owners) != 1:
            continue
        (plugin,) = owners
        if plugin:
            counts[plugin] = counts.get(plugin, 0) + 1
    return counts
