"""What Hermès writes, as it writes it (v0.22.2): its defaults, its settings' names and the exact forms of its tool
results (ADR 0012). The rules of the diagnosis read them through the `RuntimeTexts` port, never by name.

Ported from factory-live at commit d47f235 (packages/domain/src/diagnosis/hermesText.ts, the constants and the tool
results; Hermès' sources: `hermes_cli/plugins_dispatch.py`, `agent/tool_executor.py`). The workflow's own words (the
suffixes of its guards, the text of its pull request acceptance check) are given by its entry, never written here.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from workflow_core.adapters.hermes.profiles import DEFAULT_REPO_PROFILES
from workflow_core.adapters.hermes.sessions import resume_session_of
from workflow_core.model.facts import RuntimeTerms, ToolOutcome
from workflow_core.text import js_regex, js_trim

# `plugins.hook_callback_timeout` by default, and its maximum.
HOOK_TIMEOUT_DEFAULT = 30
HOOK_TIMEOUT_MAX = 600
# `timeouts.tools.sequential_call` by default: the ceiling of a tool call, hooks included.
TOOL_DEADLINE_DEFAULT = 420
HOOK_KEY = "plugins.hook_callback_timeout"
DEADLINE_KEY = "timeouts.tools.sequential_call"
MAX_TURNS_KEY = "agent.max_turns"
# `terminal.timeout` by default: the delay of a terminal command.
TERMINAL_TIMEOUT_DEFAULT = 180
# The ceiling of a tool call the diagnosis suggests when a tool passed it.
TOOL_DEADLINE_SUGGESTED = 900
TERMINAL_TOOL = "terminal"
NUDGE_MARKER = "Kanban worker tried to exit without a terminal board call"

TERMS = RuntimeTerms(
    runtime_limit_key="max_runtime_seconds",
    hook_key=HOOK_KEY,
    deadline_key=DEADLINE_KEY,
    hook_timeout_default=HOOK_TIMEOUT_DEFAULT,
    hook_timeout_max=HOOK_TIMEOUT_MAX,
    tool_deadline_default=TOOL_DEADLINE_DEFAULT,
    repo_profiles_default=DEFAULT_REPO_PROFILES,
    max_turns_key=MAX_TURNS_KEY,
    terminal_timeout_default=TERMINAL_TIMEOUT_DEFAULT,
    tool_deadline_suggested=TOOL_DEADLINE_SUGGESTED,
    terminal_tool=TERMINAL_TOOL,
    nudge_marker=NUDGE_MARKER,
)

# A tool's refusal when a `pre_tool_call` hook passes its delay, or has not finished.
HOOK_TIMEOUT_MESSAGE = "pre_tool_call plugin callback timed out or is still running"
HOOK_RAISED = js_regex(r"^pre_tool_call plugin callback (\S+) raised ([\w.]+): ([\s\S]*)$")
TOOL_DEADLINE = js_regex(r"^Error executing tool '([^']+)': timed out after ([\d.]+)s")
COMMAND_TIMEOUT = js_regex(r"timed out after (\d+)\s*s", re.IGNORECASE)
# The errors and outputs of the runs (`task_runs.error`, events' payloads). The em dash is part of Hermès' text: it is
# written by its code point.
PROTOCOL_VIOLATION = js_regex(
    r"^worker exited cleanly \(rc=0\) without kanban_complete, kanban_block or kanban_request_review "
    "\u2014 protocol violation"
)
BUDGET = js_regex(r"Iteration budget exhausted \((\d+)\/(\d+)\)")
RUNTIME = js_regex(r"^elapsed (\d+)s > limit (\d+)s$")
TRUNCATED_ERROR = js_regex(r'^\{\s*"error"\s*:\s*"((?:[^"\\]|\\.)*)')
TRUNCATED_EXIT = js_regex(r'"exit_code"\s*:\s*(-?\d+)')


def _js_parse(text: str) -> Any:
    """JavaScript's `JSON.parse`: no `NaN` nor `Infinity`."""

    def refuse(constant: str) -> None:
        raise ValueError(constant)

    return json.loads(text, parse_constant=refuse)


def _full(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    # JavaScript's `$` without the `m` flag is the very end of the text.
    match = pattern.search(text)
    return match if match is not None and match.end() == len(text) else None


def _js_round(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return math.floor(value + 0.5) if math.isfinite(value) else None


def _record_of(trimmed: str) -> dict | None:
    """The JSON object of a tool result. A content cut by the adapter is no longer valid JSON: then the start of
    `error`, or the exit code, is kept, enough to classify the result."""
    try:
        parsed = _js_parse(trimmed)
    except ValueError:
        truncated = TRUNCATED_ERROR.search(trimmed)
        # Cut before the first word of the error: nothing to classify.
        if truncated and truncated[1]:
            try:
                return {"error": _js_parse(f'"{truncated[1]}"')}
            except ValueError:
                return None
        exit_code = TRUNCATED_EXIT.search(trimmed)
        return None if exit_code is None else {"exit_code": int(exit_code[1]), "output": trimmed}
    return parsed if isinstance(parsed, dict) else None


def _is_124(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 124


class HermesTexts:
    """Hermès' texts, for the rules of the diagnosis (`RuntimeTexts`)."""

    terms = TERMS

    def classify_tool_result(self, content: str, guard_suffixes: list[str], pr_acceptance: str | None) -> ToolOutcome:
        trimmed = js_trim(content)
        deadline = TOOL_DEADLINE.search(trimmed)
        if deadline:
            return ToolOutcome(kind="tool_deadline", tool=deadline[1] or "?", seconds=_js_round(deadline[2]))
        if not trimmed.startswith("{"):
            return ToolOutcome(kind="ok")
        record = _record_of(trimmed)
        if record is None:
            return ToolOutcome(kind="ok")
        error = record.get("error")
        if isinstance(error, str):
            # Exact equality: a result that quotes this text (kanban_show of a blocked card) is not one.
            if error == HOOK_TIMEOUT_MESSAGE:
                return ToolOutcome(kind="hook_timeout")
            raised = _full(HOOK_RAISED, error)
            if raised:
                return ToolOutcome(kind="hook_raised", callback=raised[1] or "?", error=f"{raised[2]}: {raised[3]}")
            if guard_suffixes:
                # The workflow's guards prefix each refusal with their name: « <name><suffix> : … ».
                suffixes = "|".join(re.escape(suffix) for suffix in guard_suffixes)
                guard = _full(js_regex(rf"^([a-z][a-z0-9-]*(?:{suffixes})) : ([\s\S]+)$"), error)
                if guard:
                    return ToolOutcome(kind="guard", plugin=guard[1] or "?", message=js_trim(guard[2] or ""))
            if pr_acceptance:
                acceptance = _full(
                    js_regex(rf"^{re.escape(pr_acceptance)} (infra|missing|pending|failed)\b:?\s*([\s\S]*)$"), error
                )
                if acceptance:
                    return ToolOutcome(
                        kind="pr_acceptance", classification=acceptance[1] or "?", detail=js_trim(acceptance[2] or "")
                    )
            return ToolOutcome(kind="error", message=error)
        if _is_124(record.get("exit_code")):
            output = record.get("output")
            timed_out = COMMAND_TIMEOUT.search(output if isinstance(output, str) else "")
            return ToolOutcome(kind="command_timeout", seconds=float(timed_out[1]) if timed_out else None)
        return ToolOutcome(kind="ok")

    def set_command(self, agent: str, key: str, value: float) -> str:
        return f"hermes -p {agent} config set {key} {_number_text(value)}"

    def is_protocol_violation(self, error: str | None) -> bool:
        return bool(error) and PROTOCOL_VIOLATION.search(error) is not None

    def was_nudged(self, output: str | None) -> bool:
        return bool(output) and NUDGE_MARKER in output

    def budget_of(self, text: str | None) -> tuple[int, int] | None:
        match = BUDGET.search(text) if text else None
        return (int(match[1]), int(match[2])) if match else None

    def runtime_of(self, text: str | None) -> tuple[int, int] | None:
        match = _full(RUNTIME, js_trim(text)) if text else None
        return (int(match[1]), int(match[2])) if match else None

    def resume_session_of(self, text: str | None) -> str | None:
        return resume_session_of(text)

    def is_breaker_error(self, error: str) -> bool:
        return "protocol violation" in error

    def is_workspace_error(self, error: str) -> bool:
        return error.startswith("workspace:")

    def plugin_of_module(self, module: str | None) -> str | None:
        """A plugin's name from its Python module (`hermes_plugins.my_plugin` gives `my-plugin`)."""
        if not module:
            return None
        name = module.split(".")[-1]
        return name.replace("_", "-") if name else None


def _number_text(value: float) -> str:
    # A number as JavaScript writes it in a template: 600, not 600.0.
    return str(int(value)) if float(value).is_integer() else str(value)


TEXTS = HermesTexts()
