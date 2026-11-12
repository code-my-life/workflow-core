"""What several rules share: the tools of a series of results, the hook a callback belongs to, the runs at given
times, what counts as a refusal (ported from factory-live at commit d47f235: context.ts and rules.ts)."""

from __future__ import annotations

from workflow_core.diagnosis.episode import Episode, Observation
from workflow_core.diagnosis.findings import unique
from workflow_core.model.facts import PluginHook, ToolOutcome
from workflow_core.text import js_regex, js_trim

GIT_COMMIT = js_regex(r"\bgit\s+commit\b")
_PATH = js_regex(r"""/[^\s'"`]+""")
_NUMBER = js_regex(r"\b\d+\b")


def locale_key(text: str) -> tuple:
    """An approximation of JavaScript's `localeCompare` (ICU root collation) for tool names: spaces and punctuation
    before digits before letters, letters without case first, lower case before upper case."""

    def primary(char: str) -> tuple[int, str]:
        if char.isalpha():
            return (2, char.lower())
        if char.isdigit():
            return (1, char)
        return (0, char)

    return ([primary(char) for char in text], [char.isupper() for char in text])


def tools_summary(items: list[Observation]) -> str:
    """The count of each tool, the most frequent first: « terminal \u00d712, kanban_complete »."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.result.tool] = counts.get(item.result.tool, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], locale_key(pair[0])))
    # The multiplication sign of factory-live's text, written by its code point.
    return ", ".join(f"{tool} \u00d7{count}" if count > 1 else tool for tool, count in ordered)


def is_refusal(outcome: ToolOutcome) -> bool:
    """Refused before acting: by a guard, by the runtime (a hook past its delay, or crashing)."""
    return outcome.kind in ("hook_timeout", "hook_raised", "guard")


def normalized_refusal(text: str) -> str:
    """The first paragraph of a refusal, without what changes from one call to the next (paths, numbers)."""
    first = text.split("\n")[0]
    return js_trim(_NUMBER.sub("<n>", _PATH.sub("<chemin>", first)))


def hook_of(episode: Episode, hook: str, callback: str | None) -> PluginHook | None:
    """The plugin hook of a callback, from the sources of the agent's active plugins."""
    profile = episode.input.profile
    if profile is None:
        return None
    active = [item for item in profile.hooks if item.hook == hook and item.plugin in profile.plugins]
    if callback:
        named = [item for item in active if item.callback == callback]
        if len(named) == 1:
            return named[0]
    return active[0] if len(active) == 1 else None


def runs_at(episode: Episode, times: list[float]) -> list[int]:
    """The episode's runs active at these instants (a card has one run at a time)."""
    found = []
    for at in times:
        run = next(
            (
                run
                for run in episode.runs
                if run.started_at - 2 <= at <= (run.ended_at if run.ended_at is not None else episode.until) + 2
            ),
            None,
        )
        if run is not None:
            found.append(run.id)
    return unique(found)


def run_ids_of(items: list[Observation]) -> list[int]:
    return unique(item.run_id for item in items if item.run_id is not None)
