"""The rules of the diagnosis, pure functions of an episode's facts, without any AI model (ADR 0012).

Ported from factory-live's rules at commit d47f235 (packages/domain/src/diagnosis/rules.ts). Each rule reads precise
facts and returns zero, one or several findings; none reads the agent's free text. The runtime's own words a finding
quotes (the name of a setting) come from the adapter's texts.
"""

from __future__ import annotations

from collections.abc import Callable

from workflow_core.diagnosis.episode import Episode
from workflow_core.diagnosis.findings import RawFinding
from workflow_core.diagnosis.rules_guards import (
    guard_hook_hung,
    guard_hook_raised,
    guard_hook_timeout,
    guard_refusals,
    plugin_load_failed,
)
from workflow_core.diagnosis.rules_runs import (
    command_timeout,
    iteration_budget,
    protocol_breaker,
    runtime_limit,
    spawn_failed,
    stopped_after_refusals,
    stopped_without_kanban_call,
    tool_deadline,
)

Rule = Callable[[Episode], list[RawFinding]]

# In factory-live's order (`RULES`). The rules of the provider and of the work come with the next story.
RULES: tuple[Rule, ...] = (
    guard_hook_timeout,
    guard_hook_hung,
    guard_hook_raised,
    plugin_load_failed,
    stopped_after_refusals,
    stopped_without_kanban_call,
    protocol_breaker,
    iteration_budget,
    runtime_limit,
    tool_deadline,
    command_timeout,
    guard_refusals,
    spawn_failed,
)


def findings_of(episode: Episode) -> list[RawFinding]:
    """Every finding of the ported rules, in their order."""
    return [finding for rule in RULES for finding in rule(episode)]
